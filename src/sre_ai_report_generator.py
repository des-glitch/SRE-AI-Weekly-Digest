# -*- coding: utf-8 -*-

"""
全球运维与 AI 周报生成核心脚本 (sre_ai_report_generator.py)

功能：
1. 调用 Gemini API，获取 SRE 行业动态、全球故障信息、AI 前沿资讯、AI 学习推荐和商业机会。
2. 将数据解析后，分别写入 6 个不同的 Notion 数据库。
3. 将完整报告格式化为 HTML，通过 SendGrid 发送邮件通知。

更新：
- 增加了对 Gemini API 响应结构的鲁棒性检查。
- **增强了调试输出，在 API 调用成功和 JSON 解析失败时打印原始文本。**

运行环境依赖：
pip install requests notion-client sendgrid
"""

import os
import requests
import json
import time
from datetime import datetime
from notion_client import Client
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# --- Configuration & Environment Variables ---

# Notion 数据库 IDs (需要用户在 GitHub Secrets 中配置)
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

# --- Initialize Clients ---
notion = Client(auth=NOTION_TOKEN)


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

# --- Gemini API Call ---
def _get_gemini_analysis(max_retries=3):
    """
    Call Gemini API to get the structured SRE/AI report data with exponential backoff retries.
    Adds robust content extraction and debugging output.
    """
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY not set. Cannot call AI.")
        return None
        
    # --- Prompt and Schema Definition ---
    json_schema = {
        "reportDate": datetime.now().strftime("%Y-%m-%d"),
        "overallSummary": "本周全球 SRE 领域主要关注 AIOps 的落地和云成本优化，AI 领域重点是多模态模型的商用进展...",
        
        "sreDynamics": [
            {"title": "Google 发布下一代 SRE 实践指南", "summary": "指南强调了 SLI/SLO 的动态调整和混沌工程...", "source": "https://example.com/sre-guide"},
            {"title": "大规模云故障案例分析", "summary": "分析了某云服务商因配置管理不当导致的 1 小时全球中断...", "source": "https://example.com/cloud-incident"}
        ],
        
        "failureIncidents": [
            {"incidentID": "INC-2025-09-01-001", "impact": "全球服务中断 30 分钟", "rootCause": "数据库连接池饱和", "mitigation": "紧急扩容并限制连接数", "reportedTime": "2025-09-01T10:00:00Z"},
            {"incidentID": "INC-2025-09-05-002", "impact": "部分地区延迟增加", "rootCause": "CDN 节点缓存污染", "mitigation": "强制刷新 CDN 缓存", "reportedTime": "2025-09-05T15:30:00Z"}
        ],
        
        "aiNews": [
            {"title": "OpenAI 推出 GPT-5，具备原生多模态能力", "summary": "新模型在长文本理解和图像生成方面取得突破性进展...", "source": "https://example.com/gpt5"},
            {"title": "欧盟通过 AI 法案最终版本", "summary": "对高风险 AI 应用提出严格监管要求...", "source": "https://example.com/eu-ai-act"}
        ],
        
        "aiLearning": [
            {"resourceName": "《深度学习系统设计》", "type": "书籍", "reason": "深入理解大型模型训练与推理的架构。", "link": "https://example.com/deep-learning-book"},
            {"resourceName": "AIOps 落地实践系列课程", "type": "在线课程", "reason": "教授如何使用 ML 预测系统故障和自动化运维。", "link": "https://example.com/aiops-course"}
        ],
        
        "aiBusinessOpportunity": [
            {"field": "医疗诊断", "opportunity": "基于大模型的辅助诊疗工具，可以快速分析医学影像。", "marketPotential": "高", "risk": "中"},
            {"field": "智能客服", "opportunity": "集成 RAG 技术的企业级知识库问答系统，替代传统工单。", "marketPotential": "中高", "risk": "低"}
        ]
    }
    
    prompt_prefix = f"""
你是一名资深的技术专家和行业分析师，擅长全球 SRE 运维和人工智能领域。
请根据可联网搜索到的过去一周（七天）的行业新闻和技术进展，生成一份结构化周报。
**核心要求：** 请不要在JSON结构的前后添加任何额外文本、解释或免责声明。请将所有分析结果以**严格的JSON格式**返回。
**任务列表：**
1. **SRE Dynamics (运维行业动态)**：至少 2-3 条全球 SRE 和云原生领域的关键技术进展或最佳实践。
2. **Failure Incidents (全球故障信息)**：至少 2 条过去一周发生的具有影响力的、公开披露的全球性服务故障（如云服务商、大型 SaaS 或互联网公司），必须包含故障编号 (incidentID)、影响 (impact)、根本原因 (rootCause)、缓解措施 (mitigation) 和报告时间 (reportedTime)。
3. **AI News (AI 前沿资讯)**：至少 2-3 条关于模型、算法、监管或硬件的重大新闻。
4. **AI Learning (AI 学习推荐)**：至少 2 个值得推荐的学习资源，包括名称、类型（书籍/课程/博客）、推荐理由和链接。
5. **AI Business Opportunity (AI 商业机会)**：至少 2 个基于当前 AI 技术的潜在商业化方向，包括领域、机会描述、市场潜力和风险评估。
6. **Report Summary (周报摘要)**：给出本周 SRE 和 AI 领域的总体摘要。

JSON对象的结构如下：
"""
    # --- End Prompt Definition ---
    
    prompt_text = f"{prompt_prefix}{json.dumps(json_schema, indent=4, ensure_ascii=False)}"
    
    headers = { "Content-Type": "application/json" }
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key={GEMINI_API_KEY}"
    
    for attempt in range(max_retries):
        try:
            print(f"Starting Gemini API call (Attempt {attempt + 1}/{max_retries})...")
            response = requests.post(
                api_url, 
                headers=headers, 
                data=json.dumps({"contents": [{"parts": [{"text": prompt_text}]}], "tools": [{"google_search": {}}]}),
                timeout=30 # 设置超时时间
            )
            
            # 检查是否有 5xx 或 429 (Too Many Requests) 错误
            if response.status_code >= 500 or response.status_code == 429:
                raise requests.exceptions.RequestException(f"Transient error: Status {response.status_code}")
                
            response.raise_for_status() # 对 4xx 客户端错误抛出异常
            
            result_json = response.json()

            # --- 增强的鲁棒性检查和内容提取 ---
            candidate = result_json.get('candidates', [None])[0]
            if not candidate:
                raise ValueError("Gemini response is missing 'candidates' array or it is empty.")
            
            content = candidate.get('content')
            if not content:
                safety_ratings = candidate.get('safetyRatings', 'N/A')
                raise ValueError(f"Gemini response content is missing. Safety Ratings: {safety_ratings}")
            
            parts = content.get('parts', [None])
            if not parts or not parts[0] or not parts[0].get('text'):
                raise ValueError("Gemini response content is missing the 'text' part in the expected structure.")
            
            raw_text = parts[0]['text']
            
            # --- 调试输出：成功获取 AI 文本的前 1000 字符 ---
            print(f"Successfully retrieved Gemini response. Raw text length: {len(raw_text)}")
            print("--- Start of Raw Gemini Text (First 1000 chars) ---")
            print(raw_text[:1000])
            print("--- End of Raw Gemini Text (First 1000 chars) ---")
            
            return raw_text
        
        except requests.exceptions.RequestException as e:
            # 只在非最后一次尝试时进行重试
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt + 1
                print(f"Gemini API Call Failed (Transient Error: {e}). Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print(f"Gemini API Call Failed after {max_retries} attempts: {e}")
                return None
        
        except ValueError as e:
            # 捕获结构检查失败
            print(f"Gemini API Content Check Failed: {e}")
            return None
            
        except Exception as e:
            # 捕获其他非请求错误
            print(f"Gemini API Call Failed unexpectedly: {e}")
            return None
    
    return None

def _parse_gemini_response(raw_text):
    """Parses the raw text response into a dictionary, printing full raw text on failure."""
    if not raw_text:
        return None
    try:
        # Attempt to find the full JSON object
        json_start_index = raw_text.find('{')
        json_end_index = raw_text.rfind('}')
        if json_start_index == -1 or json_end_index == -1:
            raise ValueError("Could not find complete JSON structure.")
            
        json_text = raw_text[json_start_index : json_end_index + 1]
        analysis_data = json.loads(json_text)
        print("Successfully parsed JSON data.")
        return analysis_data
    except (json.JSONDecodeError, ValueError) as e:
        print(f"JSON Parse Failed: {e}")
        # --- 调试输出：打印完整的原始文本 ---
        print("--- FULL RAW TEXT (JSON PARSE FAILED) ---")
        print(raw_text) 
        print("---------------------------------------")
        # 通知邮件中加入原始文本，便于调试
        error_message = f"AI 返回的 JSON 格式错误: {e}\n\n请检查 AI 响应的原始文本:\n{raw_text[:2000]}"
        send_email_notification(GMAIL_RECIPIENT_EMAILS, "SRE/AI 报告生成失败 (JSON解析错误)", error_message)
        return None

# --- Notion Saving Helpers (Unchanged) ---

def _create_notion_page(db_id, properties):
    """Helper function to create a page in a specific Notion database."""
    if not db_id:
        print(f"Notion DB ID for {db_id} is missing. Skipping page creation.")
        return
    try:
        notion.pages.create(
            parent={"database_id": db_id},
            properties=properties
        )
        # print(f"Successfully created page in DB: {db_id}") # 避免输出过多日志
    except Exception as e:
        # 此处保留详细错误日志，方便排查权限/ID问题
        print(f"Failed to create Notion page in DB {db_id}: {e}")

def _save_to_notion(data):
    """Saves data into all 6 Notion databases."""
    
    report_date = data.get('reportDate', datetime.now().strftime("%Y-%m-%d"))
    
    # 1. Report (周报主表) - Single summary page
    report_properties = {
        "标题": { 
            "title": [
                {"text": {"content": f"全球运维与 AI 周报 - {report_date}"}}
            ]
        },
        "报告日期": {
            "date": {"start": report_date}
        },
        "摘要": {
            "rich_text": [
                {"text": {"content": data.get('overallSummary', 'N/A')}}
            ]
        }
    }
    _create_notion_page(NOTION_DB_REPORT, report_properties)

    # 2. SRE Dynamics (运维行业动态) - Multiple pages
    for item in data.get('sreDynamics', []):
        dynamic_properties = {
            "动态标题": { 
                "title": [{"text": {"content": item.get('title', 'N/A')}}]
            },
            "摘要": {
                "rich_text": [{"text": {"content": item.get('summary', 'N/A')}}]
            },
            "来源链接": {
                "url": item.get('source', None)
            }
        }
        _create_notion_page(NOTION_DB_SRE_DYNAMICS, dynamic_properties)

    # 3. Failure Incidents (全球故障信息) - Multiple pages, uses "title" for IncidentID
    for item in data.get('failureIncidents', []):
        incident_properties = {
            "故障编号": { 
                "title": [{"text": {"content": item.get('incidentID', 'N/A')}}]
            },
            "影响": {
                "rich_text": [{"text": {"content": item.get('impact', 'N/A')}}]
            },
            "根本原因": {
                "rich_text": [{"text": {"content": item.get('rootCause', 'N/A')}}]
            },
            "缓解措施": {
                "rich_text": [{"text": {"content": item.get('mitigation', 'N/A')}}]
            },
            "报告时间": {
                "date": {"start": item.get('reportedTime', report_date)}
            }
        }
        _create_notion_page(NOTION_DB_FAILURE_INCIDENTS, incident_properties)

    # 4. AI News (AI 前沿资讯) - Multiple pages
    for item in data.get('aiNews', []):
        news_properties = {
            "资讯标题": { 
                "title": [{"text": {"content": item.get('title', 'N/A')}}]
            },
            "摘要": {
                "rich_text": [{"text": {"content": item.get('summary', 'N/A')}}]
            },
            "来源链接": {
                "url": item.get('source', None)
            }
        }
        _create_notion_page(NOTION_DB_AI_NEWS, news_properties)
        
    # 5. AI Learning (AI 学习推荐) - Multiple pages
    for item in data.get('aiLearning', []):
        learning_properties = {
            "资源名称": { 
                "title": [{"text": {"content": item.get('resourceName', 'N/A')}}]
            },
            "类型": { 
                "select": {"name": item.get('type', 'N/A')}
            },
            "推荐理由": {
                "rich_text": [{"text": {"content": item.get('reason', 'N/A')}}]
            },
            "链接": {
                "url": item.get('link', None)
            }
        }
        _create_notion_page(NOTION_DB_AI_LEARNING, learning_properties)

    # 6. AI Business Opportunity (AI 商业机会) - Multiple pages
    for item in data.get('aiBusinessOpportunity', []):
        biz_properties = {
            "领域": { 
                "title": [{"text": {"content": item.get('field', 'N/A')}}]
            },
            "机会描述": {
                "rich_text": [{"text": {"content": item.get('opportunity', 'N/A')}}]
            },
            "市场潜力": { 
                "select": {"name": item.get('marketPotential', 'N/A')}
            },
            "风险": { 
                "select": {"name": item.get('risk', 'N/A')}
            }
        }
        _create_notion_page(NOTION_DB_AI_BUSINESS, biz_properties)

# --- HTML Email Formatting (Unchanged) ---
def _format_html_report(data):
    """Format the analysis data into a nice-looking HTML report for email."""
    report_date = data.get('reportDate', datetime.now().strftime('%Y年%m月%d日'))
    
    def list_to_html(title, items, keys_to_display):
        """Generates HTML table for lists like SRE Dynamics or Failure Incidents."""
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
                
                # 特别处理故障编号作为主键
                if key_en == 'incidentID':
                    html += f'<td><strong>{value}</strong></td>'
                else:
                    html += f'<td>{value}</td>'
            html += '</tr>'
            
        html += '</tbody></table></div></div>'
        return html

    sre_dynamics_html = list_to_html("运维行业动态 (SRE Dynamics)", data.get('sreDynamics', []), 
                                     {"标题": "title", "摘要": "summary", "来源": "source"})
    
    failure_incidents_html = list_to_html("全球故障信息 (Failure Incidents)", data.get('failureIncidents', []), 
                                          {"故障编号": "incidentID", "影响": "impact", "根本原因": "rootCause", "缓解措施": "mitigation", "报告时间": "reportedTime"})

    ai_news_html = list_to_html("AI 前沿资讯 (AI News)", data.get('aiNews', []),
                                {"标题": "title", "摘要": "summary", "来源": "source"})

    ai_learning_html = list_to_html("AI 学习推荐 (AI Learning)", data.get('aiLearning', []),
                                    {"资源名称": "resourceName", "类型": "type", "推荐理由": "reason", "链接": "link"})

    ai_business_html = list_to_html("AI 商业机会 (AI Business Opportunity)", data.get('aiBusinessOpportunity', []),
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
                    <p>{data.get('overallSummary', 'N/A')}</p>
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
    """Main function to orchestrate the entire process."""
    print("Starting the SRE/AI weekly report generation...")
    if not GEMINI_API_KEY or not NOTION_TOKEN:
        print("Required API keys/tokens are missing. Aborting.")
        return

    # 1. Get analysis from Gemini
    print("Starting Gemini API call for SRE/AI analysis...")
    raw_response_text = _get_gemini_analysis()
    
    analysis_data = _parse_gemini_response(raw_response_text)
    
    if not analysis_data:
        print("Failed to get or parse AI analysis. Aborting.")
        return

    # 2. Save data to Notion (6 databases)
    print("Saving data to Notion databases...")
    _save_to_notion(analysis_data)
    
    # 3. Send email notification
    print("Formatting and sending email notification...")
    html_report = _format_html_report(analysis_data)
    subject = f"【SRE/AI 周报】全球运维与 AI 行业分析 - {analysis_data.get('reportDate')}"
    send_email_notification(GMAIL_RECIPIENT_EMAILS, subject, html_report)
    
    print("Script finished.")

if __name__ == "__main__":
    main()
