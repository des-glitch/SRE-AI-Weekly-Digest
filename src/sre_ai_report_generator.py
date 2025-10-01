# -*- coding: utf-8 -*-

"""
全球运维与 AI 周报生成核心脚本 (sre_ai_report_generator.py)

功能：
1. **模块化调用**：拆分为 6 个独立的 AI 调用，每次调用只获取一个模块的数据。
2. **超长超时**：每个 API 请求的超时时间设置为 120 秒，确保 AI 有充足时间响应。
3. **独立存储**：每次成功获取数据后，立即写入对应的 Notion 数据库。
4. **邮件通知**：收集所有数据后，格式化为 HTML 并发送邮件。

更新：
- 结构大重构，将单次复杂调用拆分为 6 次简单的调用。
- **单个请求超时设置为 120 秒。**
- **重试等待时间设置为 60 秒 (第一次), 120 秒 (第二次), ...**

运行环境依赖：
pip install requests notion-client sendgrid
"""

import os
import requests
import json
import time
from datetime import datetime

# 外部库导入
try:
    from notion_client import Client
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except ImportError:
    print("Warning: Missing required libraries (notion-client or sendgrid). Please run: pip install requests notion-client sendgrid")

# --- Configuration & Environment Variables ---

# Notion 数据库 IDs
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DB_REPORT = os.environ.get("NOTION_DB_REPORT") # 周报主表
NOTION_DB_SRE_DYNAMICS = os.environ.get("NOTION_DB_SRE_DYNAMICS") # 运维行业动态
NOTION_DB_FAILURE_INCIDENTS = os.environ.get("NOTION_DB_FAILURE_INCIDENTS") # 全球故障信息
NOTION_DB_AI_NEWS = os.environ.get("NOTION_DB_AI_NEWS") # AI 前沿资讯
NOTION_DB_AI_LEARNING = os.environ.get("NOTION_DB_AI_LEARNING") # AI 学习推荐
NOTION_DB_AI_BUSINESS = os.environ.get("NOTION_DB_AI_BUSINESS") # AI 商业机会

# SendGrid Configuration
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
gmail_emails_str = os.environ.get("GMAIL_RECIPIENT_EMAILS")
GMAIL_RECIPIENT_EMAILS = [email.strip() for email in gmail_emails_str.split(',')] if gmail_emails_str else []
FROM_EMAIL = GMAIL_RECIPIENT_EMAILS[0] if GMAIL_RECIPIENT_EMAILS else None

# Gemini Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- Timing & Robustness Constants ---
# 单个 API 请求的最大等待时间 (120秒)
REQUEST_TIMEOUT_SECONDS = 120 
# 第一次重试的等待时间 (60秒) - 已根据用户要求修改
INITIAL_RETRY_SLEEP = 60 
MAX_RETRIES = 3 

# --- Initialize Clients ---
notion = Client(auth=NOTION_TOKEN) if NOTION_TOKEN else None


# --- SendGrid Email Function ---
def send_email_notification(to_list, subject, message_text):
    """Send an email using the SendGrid API with HTML content."""
    if not SENDGRID_API_KEY or not FROM_EMAIL or not to_list:
        print("Email configuration missing (Key, From, or To), skipping email.")
        return
        
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        for to_email in to_list:
            message = Mail(
                from_email=FROM_EMAIL,
                to_emails=to_email.strip(),
                subject=subject,
                html_content=message_text
            )
            sg.send(message)
            print(f"Successfully sent email to: {to_email.strip()}")
            
    except Exception as e:
        print(f"Failed to send email via SendGrid: {e}")

# --- Core Gemini API Call Helper ---

def _gemini_api_call(prompt_text):
    """
    Handles API call, request timeout (120s), and exponential backoff retries (60s, 120s...).
    Returns raw response text or None on failure.
    """
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY not set.")
        return None
        
    headers = { "Content-Type": "application/json" }
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key={GEMINI_API_KEY}"
    
    for attempt in range(MAX_RETRIES):
        try:
            print(f"Starting Gemini API call (Attempt {attempt + 1}/{MAX_RETRIES}) with timeout {REQUEST_TIMEOUT_SECONDS}s...")
            response = requests.post(
                api_url, 
                headers=headers, 
                # 启用 Google Search Grounding
                data=json.dumps({"contents": [{"parts": [{"text": prompt_text}]}], "tools": [{"google_search": {}}]}),
                timeout=REQUEST_TIMEOUT_SECONDS 
            )
            
            # 检查是否有 5xx 或 429 (Too Many Requests) 错误
            if response.status_code >= 500 or response.status_code == 429:
                raise requests.exceptions.RequestException(f"Transient error: Status {response.status_code}")
                
            response.raise_for_status() # 对 4xx 客户端错误抛出异常
            result_json = response.json()

            # 增强的鲁棒性检查和内容提取
            candidate = result_json.get('candidates', [None])[0]
            if not candidate:
                raise ValueError("Gemini response is missing 'candidates' array or it is empty.")
            
            raw_text = candidate.get('content', {}).get('parts', [{}])[0].get('text')
            
            if not raw_text:
                raise ValueError("Gemini response content is missing the 'text' part.")

            # 调试输出
            print(f"Successfully retrieved Gemini response. Raw text length: {len(raw_text)}")
            return raw_text
        
        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                # 指数退避：60s, 120s, 240s...
                wait_time = INITIAL_RETRY_SLEEP * (2 ** attempt)
                print(f"Gemini API Call Failed (Transient Error: {e}). Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print(f"Gemini API Call Failed after {MAX_RETRIES} attempts: {e}")
                # 发送邮件通知最终失败
                error_message = f"AI Analysis Failed (Final attempt timeout/error): {e}"
                send_email_notification(GMAIL_RECIPIENT_EMAILS, "SRE/AI 报告生成失败 (API 错误)", error_message)
                return None
        
        except ValueError as e:
            print(f"Gemini API Content Check Failed: {e}")
            return None
            
        except Exception as e:
            print(f"Gemini API Call Failed unexpectedly: {e}")
            return None
    
    return None

def _parse_gemini_response(raw_text, task_name):
    """Parses the raw text response into a dictionary."""
    if not raw_text:
        return None
    try:
        # 尝试查找完整的 JSON 对象 (处理AI可能添加的前导/尾随文本)
        json_start_index = raw_text.find('{')
        json_end_index = raw_text.rfind('}')
        if json_start_index == -1 or json_end_index == -1:
            raise ValueError("Could not find complete JSON structure.")
            
        json_text = raw_text[json_start_index : json_end_index + 1]
        analysis_data = json.loads(json_text)
        print(f"Successfully parsed JSON data for {task_name}.")
        return analysis_data
    except (json.JSONDecodeError, ValueError) as e:
        print(f"JSON Parse Failed for {task_name}: {e}")
        print("--- FULL RAW TEXT (JSON PARSE FAILED) ---")
        print(raw_text) 
        print("---------------------------------------")
        # 通知邮件中加入原始文本，便于调试
        error_message = f"AI 返回的 JSON 格式错误 ({task_name}): {e}\n\n请检查 AI 响应的原始文本:\n{raw_text[:2000]}"
        send_email_notification(GMAIL_RECIPIENT_EMAILS, f"SRE/AI 报告生成失败 (JSON解析错误) - {task_name}", error_message)
        return None

# --- Notion Saving Helpers ---

def _create_notion_page(db_id, properties):
    """Helper function to create a page in a specific Notion database."""
    if not notion:
        print("Notion client not initialized. Skipping save.")
        return
    if not db_id:
        print(f"Notion DB ID is missing. Skipping page creation.")
        return
    try:
        notion.pages.create(
            parent={"database_id": db_id},
            properties=properties
        )
    except Exception as e:
        print(f"Failed to create Notion page in DB {db_id}: {e}")

# --- Modular Prompts and Schemas ---

def _get_overall_summary():
    """Step 1: Get Report Metadata and Overall Summary."""
    task_name = "Overall Summary"
    schema = {
        "reportDate": datetime.now().strftime("%Y-%m-%d"),
        "overallSummary": "本周全球 SRE 领域主要关注 AIOps 的落地和云成本优化，AI 领域重点是多模态模型的商用进展...",
    }
    
    prompt = f"""
请根据可联网搜索到的过去一周（七天）的行业新闻和技术进展，生成周报的**报告日期**和**本周总体摘要**。
周报的主题是全球 SRE 运维和人工智能领域。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
JSON 结构: {json.dumps(schema, indent=4, ensure_ascii=False)}
"""
    raw_text = _gemini_api_call(prompt)
    return _parse_gemini_response(raw_text, task_name)


def _get_sre_dynamics():
    """Step 2: Get SRE Dynamics data."""
    task_name = "SRE Dynamics"
    schema = {
        "sreDynamics": [
            {"title": "Google 发布下一代 SRE 实践指南", "summary": "指南强调了 SLI/SLO 的动态调整和混沌工程...", "source": "https://example.com/sre-guide"},
            {"title": "大规模云故障案例分析", "summary": "分析了某云服务商因配置管理不当导致的 1 小时全球中断...", "source": "https://example.com/cloud-incident"}
        ]
    }
    prompt = f"""
请根据可联网搜索到的信息，提供至少 **2-3 条** 全球 SRE 和云原生领域的关键技术进展或最佳实践。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
JSON 结构: {json.dumps(schema, indent=4, ensure_ascii=False)}
"""
    raw_text = _gemini_api_call(prompt)
    data = _parse_gemini_response(raw_text, task_name)
    if data and data.get('sreDynamics'):
        for item in data['sreDynamics']:
            dynamic_properties = {
                "动态标题": { "title": [{"text": {"content": item.get('title', 'N/A')}}] },
                "摘要": { "rich_text": [{"text": {"content": item.get('summary', 'N/A')}}] },
                "来源链接": { "url": item.get('source', None) }
            }
            _create_notion_page(NOTION_DB_SRE_DYNAMICS, dynamic_properties)
    return data


def _get_failure_incidents(report_date):
    """Step 3: Get Global Failure Incidents data."""
    task_name = "Failure Incidents"
    schema = {
        "failureIncidents": [
            {"incidentID": "INC-2025-09-01-001", "impact": "全球服务中断 30 分钟", "rootCause": "数据库连接池饱和", "mitigation": "紧急扩容并限制连接数", "reportedTime": "2025-09-01T10:00:00Z"},
            {"incidentID": "INC-2025-09-05-002", "impact": "部分地区延迟增加", "rootCause": "CDN 节点缓存污染", "mitigation": "强制刷新 CDN 缓存", "reportedTime": "2025-09-05T15:30:00Z"}
        ]
    }
    prompt = f"""
请根据可联网搜索到的信息，提供至少 **2 条** 过去一周发生的具有影响力的、公开披露的全球性服务故障（如云服务商、大型 SaaS 或互联网公司）。
必须包含故障编号 (incidentID)、影响 (impact)、根本原因 (rootCause)、缓解措施 (mitigation) 和报告时间 (reportedTime, 务必使用 ISO 8601 格式，如 T20:00:00Z)。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
JSON 结构: {json.dumps(schema, indent=4, ensure_ascii=False)}
"""
    raw_text = _gemini_api_call(prompt)
    data = _parse_gemini_response(raw_text, task_name)
    if data and data.get('failureIncidents'):
        for item in data['failureIncidents']:
            incident_properties = {
                "故障编号": { "title": [{"text": {"content": item.get('incidentID', 'N/A')}}] },
                "影响": { "rich_text": [{"text": {"content": item.get('impact', 'N/A')}}] },
                "根本原因": { "rich_text": [{"text": {"content": item.get('rootCause', 'N/A')}}] },
                "缓解措施": { "rich_text": [{"text": {"content": item.get('mitigation', 'N/A')}}] },
                # 检查并使用报告时间，如果时间格式不对，退回到报告日期
                "报告时间": {
                    "date": {"start": item.get('reportedTime', report_date) if item.get('reportedTime') else report_date}
                }
            }
            _create_notion_page(NOTION_DB_FAILURE_INCIDENTS, incident_properties)
    return data


def _get_ai_news():
    """Step 4: Get AI News data."""
    task_name = "AI News"
    schema = {
        "aiNews": [
            {"title": "OpenAI 推出 GPT-5，具备原生多模态能力", "summary": "新模型在长文本理解和图像生成方面取得突破性进展...", "source": "https://example.com/gpt5"},
            {"title": "欧盟通过 AI 法案最终版本", "summary": "对高风险 AI 应用提出严格监管要求...", "source": "https://example.com/eu-ai-act"}
        ]
    }
    prompt = f"""
请根据可联网搜索到的信息，提供至少 **2-3 条** 关于模型、算法、监管或硬件的重大 AI 前沿资讯。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
JSON 结构: {json.dumps(schema, indent=4, ensure_ascii=False)}
"""
    raw_text = _gemini_api_call(prompt)
    data = _parse_gemini_response(raw_text, task_name)
    if data and data.get('aiNews'):
        for item in data['aiNews']:
            news_properties = {
                "资讯标题": { "title": [{"text": {"content": item.get('title', 'N/A')}}] },
                "摘要": { "rich_text": [{"text": {"content": item.get('summary', 'N/A')}}] },
                "来源链接": { "url": item.get('source', None) }
            }
            _create_notion_page(NOTION_DB_AI_NEWS, news_properties)
    return data


def _get_ai_learning():
    """Step 5: Get AI Learning data."""
    task_name = "AI Learning"
    schema = {
        "aiLearning": [
            {"resourceName": "《深度学习系统设计》", "type": "书籍", "reason": "深入理解大型模型训练与推理的架构。", "link": "https://example.com/deep-learning-book"},
            {"resourceName": "AIOps 落地实践系列课程", "type": "在线课程", "reason": "教授如何使用 ML 预测系统故障和自动化运维。", "link": "https://example.com/aiops-course"}
        ]
    }
    prompt = f"""
请根据可联网搜索到的信息，提供至少 **2 个** 值得推荐的学习资源，包括名称、类型（书籍/课程/博客）、推荐理由和链接。
资源主题应围绕 SRE、AIOps 或前沿 AI 技术。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
JSON 结构: {json.dumps(schema, indent=4, ensure_ascii=False)}
"""
    raw_text = _gemini_api_call(prompt)
    data = _parse_gemini_response(raw_text, task_name)
    if data and data.get('aiLearning'):
        for item in data['aiLearning']:
            learning_properties = {
                "资源名称": { "title": [{"text": {"content": item.get('resourceName', 'N/A')}}] },
                "类型": { "select": {"name": item.get('type', 'N/A')} },
                "推荐理由": { "rich_text": [{"text": {"content": item.get('reason', 'N/A')}}] },
                "链接": { "url": item.get('link', None) }
            }
            _create_notion_page(NOTION_DB_AI_LEARNING, learning_properties)
    return data


def _get_ai_business():
    """Step 6: Get AI Business Opportunity data."""
    task_name = "AI Business Opportunity"
    schema = {
        "aiBusinessOpportunity": [
            {"field": "医疗诊断", "opportunity": "基于大模型的辅助诊疗工具，可以快速分析医学影像。", "marketPotential": "高", "risk": "中"},
            {"field": "智能客服", "opportunity": "集成 RAG 技术的企业级知识库问答系统，替代传统工单。", "marketPotential": "中高", "risk": "低"}
        ]
    }
    prompt = f"""
请根据可联网搜索到的信息，提供至少 **2 个** 基于当前 AI 技术的潜在商业化方向，包括领域、机会描述、市场潜力（高/中高/中/低）和风险评估（高/中/低）。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
JSON 结构: {json.dumps(schema, indent=4, ensure_ascii=False)}
"""
    raw_text = _gemini_api_call(prompt)
    data = _parse_gemini_response(raw_text, task_name)
    if data and data.get('aiBusinessOpportunity'):
        for item in data['aiBusinessOpportunity']:
            biz_properties = {
                "领域": { "title": [{"text": {"content": item.get('field', 'N/A')}}] },
                "机会描述": { "rich_text": [{"text": {"content": item.get('opportunity', 'N/A')}}] },
                "市场潜力": { "select": {"name": item.get('marketPotential', 'N/A')} },
                "风险": { "select": {"name": item.get('risk', 'N/A')} }
            }
            _create_notion_page(NOTION_DB_AI_BUSINESS, biz_properties)
    return data

# --- HTML Email Formatting ---

def _format_html_report(all_data):
    """Format the complete analysis data into an HTML report for email."""
    
    # 提取顶层信息
    report_date = all_data.get('overallSummaryData', {}).get('reportDate', datetime.now().strftime('%Y年%m月%d日'))
    overall_summary = all_data.get('overallSummaryData', {}).get('overallSummary', 'N/A')

    def list_to_html(title, data_key, keys_to_display):
        """Generates HTML table for lists from collected data."""
        items = all_data.get(data_key, [])
        if not items: return ""
        
        html = f'<div class="section"><h2 class="section-title">{title}</h2><div class="table-container">'
        html += '<table class="data-table"><thead><tr>'
        for key in keys_to_display:
            html += f'<th>{key}</th>'
        html += '</tr></thead><tbody>'
        
        for item in items:
            html += '<tr>'
            for key_cn, key_en in keys_to_display.items():
                value = item.get(key_en, 'N/A')
                if key_en in ['source', 'link']: # Handle URL links
                    value = f'<a href="{value}" target="_blank">查看链接</a>' if value else 'N/A'
                
                if key_en == 'incidentID':
                    html += f'<td><strong>{value}</strong></td>'
                else:
                    html += f'<td>{value}</td>'
            html += '</tr>'
            
        html += '</tbody></table></div></div>'
        return html

    sre_dynamics_html = list_to_html("运维行业动态 (SRE Dynamics)", 'sreDynamics', 
                                     {"标题": "title", "摘要": "summary", "来源": "source"})
    
    failure_incidents_html = list_to_html("全球故障信息 (Failure Incidents)", 'failureIncidents', 
                                          {"故障编号": "incidentID", "影响": "impact", "根本原因": "rootCause", "缓解措施": "mitigation", "报告时间": "reportedTime"})

    ai_news_html = list_to_html("AI 前沿资讯 (AI News)", 'aiNews',
                                {"标题": "title", "摘要": "summary", "来源": "source"})

    ai_learning_html = list_to_html("AI 学习推荐 (AI Learning)", 'aiLearning',
                                    {"资源名称": "resourceName", "类型": "type", "推荐理由": "reason", "链接": "link"})

    ai_business_html = list_to_html("AI 商业机会 (AI Business Opportunity)", 'aiBusinessOpportunity',
                                    {"领域": "field", "机会描述": "opportunity", "市场潜力": "marketPotential", "风险": "risk"})

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>全球运维与 AI 周报 - {report_date}</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Inter', sans-serif; margin: 0; padding: 20px; background-color: #f4f7f6; color: #333; }}
            .container {{ max-width: 900px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.05); padding: 30px; }}
            .header {{ text-align: center; border-bottom: 2px solid #e0e0e0; padding-bottom: 20px; margin-bottom: 20px; }}
            .header h1 {{ font-size: 28px; color: #1a1a1a; margin: 0; }}
            .header p {{ color: #777; font-size: 14px; margin-top: 5px; }}
            .section {{ margin-bottom: 30px; }}
            .section-title {{ font-size: 22px; color: #3498db; border-left: 4px solid #3498db; padding-left: 10px; margin-bottom: 15px; font-weight: bold; }}
            .content p {{ line-height: 1.8; font-size: 16px; }}
            .data-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            .data-table th, .data-table td {{ padding: 10px; border: 1px solid #e0e0e0; text-align: left; font-size: 13px; vertical-align: top; }}
            .data-table th {{ background-color: #f0f0f0; font-weight: 600; }}
            .data-table tr:nth-child(even) {{ background-color: #fafafa; }}
            .table-container {{ overflow-x: auto; }}
            a {{ color: #3498db; text-decoration: none; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>全球运维与 AI 周报</h1>
                <p>生成日期: {report_date} | 由 Gemini AI 驱动</p>
            </div>

            <div class="section">
                <h2 class="section-title">本周总体摘要</h2>
                <div class="content">
                    <p>{overall_summary}</p>
                </div>
            </div>

            {sre_dynamics_html}
            {failure_incidents_html}
            {ai_news_html}
            {ai_learning_html}
            {ai_business_html}

            <div class="section">
                <p style="text-align: center; color: #999; font-size: 12px; margin-top: 40px;">
                    数据已同步到 Notion 数据库。
                </p>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content

def main():
    """Main function to orchestrate the sequential process."""
    print("Starting the SRE/AI weekly report generation...")
    if not GEMINI_API_KEY or not NOTION_TOKEN:
        print("Required API keys/tokens are missing. Aborting.")
        return

    # Dictionary to hold all collected data for the final email report
    all_report_data = {
        'overallSummaryData': None,
        'sreDynamics': [],
        'failureIncidents': [],
        'aiNews': [],
        'aiLearning': [],
        'aiBusinessOpportunity': []
    }
    
    # --- Step 1: Get Overall Summary & Report Date (Mandatory First Step) ---
    print("\n--- Step 1/6: Getting Overall Summary and Report Date ---")
    summary_data = _get_overall_summary()
    if not summary_data:
        print("Fatal: Could not get Overall Summary. Aborting all subsequent steps.")
        return
    
    # Save to Report Master DB
    all_report_data['overallSummaryData'] = summary_data
    report_date = summary_data.get('reportDate', datetime.now().strftime("%Y-%m-%d"))
    overall_summary = summary_data.get('overallSummary', 'N/A')
    
    report_properties = {
        "标题": { "title": [{"text": {"content": f"全球运维与 AI 周报 - {report_date}"}}] },
        "报告日期": { "date": {"start": report_date} },
        "摘要": { "rich_text": [{"text": {"content": overall_summary}}] }
    }
    _create_notion_page(NOTION_DB_REPORT, report_properties)
    print("Step 1 Complete: Report Master page created.")

    # --- Step 2 to 6: Sequential Data Collection and Saving ---
    
    # 2. SRE Dynamics
    print("\n--- Step 2/6: Getting SRE Dynamics ---")
    sre_data = _get_sre_dynamics()
    if sre_data and sre_data.get('sreDynamics'):
        all_report_data['sreDynamics'] = sre_data['sreDynamics']
    print("Step 2 Complete.")

    # 3. Failure Incidents
    print("\n--- Step 3/6: Getting Failure Incidents ---")
    incident_data = _get_failure_incidents(report_date)
    if incident_data and incident_data.get('failureIncidents'):
        all_report_data['failureIncidents'] = incident_data['failureIncidents']
    print("Step 3 Complete.")

    # 4. AI News
    print("\n--- Step 4/6: Getting AI News ---")
    ai_news_data = _get_ai_news()
    if ai_news_data and ai_news_data.get('aiNews'):
        all_report_data['aiNews'] = ai_news_data['aiNews']
    print("Step 4 Complete.")

    # 5. AI Learning
    print("\n--- Step 5/6: Getting AI Learning ---")
    ai_learning_data = _get_ai_learning()
    if ai_learning_data and ai_learning_data.get('aiLearning'):
        all_report_data['aiLearning'] = ai_learning_data['aiLearning']
    print("Step 5 Complete.")

    # 6. AI Business Opportunity
    print("\n--- Step 6/6: Getting AI Business Opportunity ---")
    ai_biz_data = _get_ai_business()
    if ai_biz_data and ai_biz_data.get('aiBusinessOpportunity'):
        all_report_data['aiBusinessOpportunity'] = ai_biz_data['aiBusinessOpportunity']
    print("Step 6 Complete.")
    
    # --- Final Step: Send Email Notification ---
    print("\n--- Final Step: Formatting and sending email notification ---")
    html_report = _format_html_report(all_report_data)
    subject = f"【SRE/AI 周报】全球运维与 AI 行业分析 - {report_date}"
    send_email_notification(GMAIL_RECIPIENT_EMAILS, subject, html_report)
    
    print("\nScript finished. All available data has been processed and notified.")

if __name__ == "__main__":
    main()
