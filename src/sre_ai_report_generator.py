# -*- coding: utf-8 -*-

"""
全球运维与 AI 周报生成核心脚本 (sre_ai_report_generator.py)

**重要说明：**
1. 已根据用户提供的 6 个数据库设计图，使用英文 Field Name (如 'title', 'summary', 'official_link') 作为 Notion 属性键。
2. AI (Gemini) 的 JSON 输出结构已相应调整，确保数据能精确映射到 Notion 字段。
"""

import os
import requests
import json
import time
from datetime import datetime, timedelta

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
NOTION_DB_REPORT = os.environ.get("NOTION_DB_REPORT") # 1. 周报主表 (Report)
NOTION_DB_SRE_DYNAMICS = os.environ.get("NOTION_DB_SRE_DYNAMICS") # 2. 运维行业动态 (SRE_Dynamics)
NOTION_DB_FAILURE_INCIDENTS = os.environ.get("NOTION_DB_FAILURE_INCIDENTS") # 3. 全球故障信息 (Failure_Incidents)
NOTION_DB_AI_NEWS = os.environ.get("NOTION_DB_AI_NEWS") # 4. AI 前沿资讯 (AI_News)
NOTION_DB_AI_LEARNING = os.environ.get("NOTION_DB_AI_LEARNING") # 5. AI 学习推荐 (AI_Learning)
NOTION_DB_AI_BUSINESS = os.environ.get("NOTION_DB_AI_BUSINESS") # 6. AI 商业机会 (AI_Business_Opportunity)

# SendGrid Configuration
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
gmail_emails_str = os.environ.get("GMAIL_RECIPIENT_EMAILS")
GMAIL_RECIPIENT_EMAILS = [email.strip() for email in gmail_emails_str.split(',')] if gmail_emails_str else []
FROM_EMAIL = GMAIL_RECIPIENT_EMAILS[0] if GMAIL_RECIPIENT_EMAILS else None

# Gemini Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- Timing & Robustness Constants ---
REQUEST_TIMEOUT_SECONDS = 120 
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
                wait_time = INITIAL_RETRY_SLEEP * (2 ** attempt)
                print(f"Gemini API Call Failed (Transient Error: {e}). Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print(f"Gemini API Call Failed after {MAX_RETRIES} attempts: {e}")
                error_message = f"AI Analysis Failed (Final attempt timeout/error): {e}"
                send_email_notification(GMAIL_RECIPIENT_EMAILS, "SRE/AI 报告生成失败 (API 错误)", error_message)
                return None
        
        except ValueError as e:
            print(f"Gemini API Content Check Failed: {e}")
            error_message = f"AI Analysis Failed (Missing content): {e}"
            send_email_notification(GMAIL_RECIPIENT_EMAILS, "SRE/AI 报告生成失败 (AI内容错误)", error_message)
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
        print(f"Failed properties were: {properties}")
        print("Hint: This is usually due to property key names not matching your Notion database column headers (English Field Name) exactly.")


# --- Modular Prompts and Schemas (Using English Field Names) ---

def _get_overall_summary():
    """Step 1: Get Report Metadata and Overall Summary (for Report Master DB)."""
    task_name = "Report Master"
    
    # 获取本周的起始和结束日期 (假设周报为过去 7 天)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=6)
    
    report_week_start = start_date.strftime("%Y-%m-%d")
    report_week_end = end_date.strftime("%Y-%m-%d")
    
    # 按照 Report 表的字段设计 JSON Schema
    schema = {
        "title": f"全球运维与 AI 周报 ({report_week_start} - {report_week_end})",
        "report_week_start": report_week_start,
        "report_week_end": report_week_end,
        "status": "Draft",
        "overall_summary": "本周全球 SRE 领域主要关注 AIOps 的落地和云成本优化，AI 领域重点是多模态模型的商用进展...",
    }
    
    prompt = f"""
请根据可联网搜索到的过去一周（{report_week_start} 至 {report_week_end}）的行业新闻和技术进展，生成周报的**标题**和**本周总体摘要**（overall_summary）。
周报的主题是全球 SRE 运维和人工智能领域。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
JSON 结构中的 'title'、'report_week_start' 和 'report_week_end' 请使用我提供的预设值。
JSON 结构: {{
    "title": "{schema['title']}",
    "report_week_start": "{schema['report_week_start']}",
    "report_week_end": "{schema['report_week_end']}",
    "status": "Draft",
    "overall_summary": "此处填写本周运维与AI领域的综合性总结"
}}
"""
    raw_text = _gemini_api_call(prompt)
    data = _parse_gemini_response(raw_text, task_name)
    
    if data:
        # 整合数据以供 Notion 写入
        report_properties = {
            "title": { "title": [{"text": {"content": data.get('title', 'N/A')}}] },
            "report_week_start": { "date": {"start": data.get('report_week_start', report_week_start)} },
            "report_week_end": { "date": {"start": data.get('report_week_end', report_week_end)} },
            "status": { "select": {"name": data.get('status', 'Draft')} },
        }
        _create_notion_page(NOTION_DB_REPORT, report_properties)
        data['report_week_start'] = report_week_start # 确保日期在返回结果中
        data['report_week_end'] = report_week_end
    
    return data


def _get_sre_dynamics():
    """Step 2: Get SRE Dynamics data (for SRE_Dynamics DB)."""
    task_name = "SRE Dynamics"
    # 严格按照 SRE_Dynamics 表的 Field Name 设计 JSON Schema
    schema = {
        "sreDynamics": [
            {
                "title": "Google 发布下一代 SRE 实践指南", 
                "summary": "指南强调了 SLI/SLO 的动态调整和混沌工程...", 
                "source_company": "Google",
                "release_date": datetime.now().strftime("%Y-%m-%d"),
                "official_link": "https://example.com/sre-guide",
                "focus_areas": ["AIOps", "Chaos Engineering"],
                "analysis_content": "该报告表明 SRE 正在从被动响应转向主动弹性设计..."
            },
        ]
    }
    prompt = f"""
请根据可联网搜索到的信息，提供至少 **2-3 条** 全球 SRE 和云原生领域的关键技术进展或最佳实践。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
注意：'release_date' 必须是 YYYY-MM-DD 格式，'focus_areas' 必须是字符串数组。
JSON 结构: {json.dumps(schema, indent=4, ensure_ascii=False)}
"""
    raw_text = _gemini_api_call(prompt)
    data = _parse_gemini_response(raw_text, task_name)
    
    if data and data.get('sreDynamics'):
        for item in data['sreDynamics']:
            # 使用英文 Field Name
            dynamic_properties = {
                "title": { "title": [{"text": {"content": item.get('title', 'N/A')}}] },
                "summary": { "rich_text": [{"text": {"content": item.get('summary', 'N/A')}}] },
                "source_company": { "rich_text": [{"text": {"content": item.get('source_company', 'N/A')}}] },
                "release_date": { "date": {"start": item.get('release_date', datetime.now().strftime("%Y-%m-%d"))} },
                "official_link": { "url": item.get('official_link', None) },
                "focus_areas": { "multi_select": [{"name": area} for area in item.get('focus_areas', [])] },
                "analysis_content": { "rich_text": [{"text": {"content": item.get('analysis_content', 'N/A')}}] },
            }
            _create_notion_page(NOTION_DB_SRE_DYNAMICS, dynamic_properties)
    return data


def _get_failure_incidents():
    """Step 3: Get Global Failure Incidents data (for Failure_Incidents DB)."""
    task_name = "Failure Incidents"
    # 严格按照 Failure_Incidents 表的 Field Name 设计 JSON Schema
    schema = {
        "failureIncidents": [
            {
                "incident_title": "数据库连接池饱和导致全球服务中断", 
                "company": "大型云服务商", 
                "incident_date": "2025-09-01T10:00:00Z", # 使用 ISO 8601 Timestamp 格式
                "official_link": "https://example.com/incident-report-001",
                "overview": "服务中断 30 分钟，影响全球多个区域。",
                "root_cause": "数据库连接池饱和，未能及时扩容",
                "timeline": "10:00 - 发现告警；10:15 - 紧急扩容；10:30 - 服务恢复。",
                "improvement_measures": "实施连接池弹性伸缩机制并限制连接数。",
                "lessons_learned": "在高并发场景下，连接池的动态管理至关重要。"
            },
        ]
    }
    prompt = f"""
请根据可联网搜索到的信息，提供至少 **2 条** 过去一周发生的具有影响力的、公开披露的全球性服务故障。
必须包含所有字段：incident_title, company, official_link (链接), overview, root_cause, improvement_measures, incident_date (务必使用 ISO 8601 Timestamp 格式，如 YYYY-MM-DDTHH:MM:SSZ)。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
JSON 结构: {json.dumps(schema, indent=4, ensure_ascii=False)}
"""
    raw_text = _gemini_api_call(prompt)
    data = _parse_gemini_response(raw_text, task_name)
    
    if data and data.get('failureIncidents'):
        for item in data['failureIncidents']:
            # 使用英文 Field Name
            incident_properties = {
                "incident_title": { "title": [{"text": {"content": item.get('incident_title', 'N/A')}}] },
                "company": { "rich_text": [{"text": {"content": item.get('company', 'N/A')}}] },
                "incident_date": { "date": {"start": item.get('incident_date', datetime.now().isoformat() + 'Z')} }, # Notion Date Type with time
                "official_link": { "url": item.get('official_link', None) },
                "overview": { "rich_text": [{"text": {"content": item.get('overview', 'N/A')}}] },
                "root_cause": { "rich_text": [{"text": {"content": item.get('root_cause', 'N/A')}}] },
                "timeline": { "rich_text": [{"text": {"content": item.get('timeline', 'N/A')}}] },
                "improvement_measures": { "rich_text": [{"text": {"content": item.get('improvement_measures', 'N/A')}}] },
                "lessons_learned": { "rich_text": [{"text": {"content": item.get('lessons_learned', 'N/A')}}] },
            }
            _create_notion_page(NOTION_DB_FAILURE_INCIDENTS, incident_properties)
    return data


def _get_ai_news():
    """Step 4: Get AI News data (for AI_News DB)."""
    task_name = "AI News"
    # 严格按照 AI_News 表的 Field Name 设计 JSON Schema
    schema = {
        "aiNews": [
            {
                "title": "OpenAI 推出 GPT-5，具备原生多模态能力", 
                "summary": "新模型在长文本理解和图像生成方面取得突破性进展...", 
                "source": "OpenAI 官网",
                "publish_date": datetime.now().strftime("%Y-%m-%d"),
                "news_link": "https://example.com/gpt5",
                "category": "Model Release (模型发布)",
                "analysis": "GPT-5 的发布加速了多模态在商业应用中的普及。"
            },
        ]
    }
    prompt = f"""
请根据可联网搜索到的信息，提供至少 **2-3 条** 关于模型、算法、监管或硬件的重大 AI 前沿资讯。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
注意：'publish_date' 必须是 YYYY-MM-DD 格式。
JSON 结构: {json.dumps(schema, indent=4, ensure_ascii=False)}
"""
    raw_text = _gemini_api_call(prompt)
    data = _parse_gemini_response(raw_text, task_name)
    
    if data and data.get('aiNews'):
        for item in data['aiNews']:
            # 使用英文 Field Name
            news_properties = {
                "title": { "title": [{"text": {"content": item.get('title', 'N/A')}}] },
                "summary": { "rich_text": [{"text": {"content": item.get('summary', 'N/A')}}] },
                "source": { "rich_text": [{"text": {"content": item.get('source', 'N/A')}}] },
                "publish_date": { "date": {"start": item.get('publish_date', datetime.now().strftime("%Y-%m-%d"))} },
                "news_link": { "url": item.get('news_link', None) },
                "category": { "select": {"name": item.get('category', 'N/A')} },
                "analysis": { "rich_text": [{"text": {"content": item.get('analysis', 'N/A')}}] },
            }
            _create_notion_page(NOTION_DB_AI_NEWS, news_properties)
    return data


def _get_ai_learning():
    """Step 5: Get AI Learning data (for AI_Learning DB)."""
    task_name = "AI Learning"
    # 严格按照 AI_Learning 表的 Field Name 设计 JSON Schema
    schema = {
        "aiLearning": [
            {
                "material_name": "《深度学习系统设计》", 
                "description": "深入理解大型模型训练与推理的架构。", 
                "type": "Book (书籍)",
                "difficulty": "Advanced (高级)",
                "link": "https://example.com/deep-learning-book",
                "tags": ["LLM", "System Design"]
            },
        ]
    }
    prompt = f"""
请根据可联网搜索到的信息，提供至少 **2 个** 值得推荐的学习资源，包括名称、类型（如：Book (书籍), Course (课程), Video Series (视频系列)）、难度（如：Beginner (初级), Intermediate (中级), Advanced (高级)）和链接。
资源主题应围绕 SRE、AIOps 或前沿 AI 技术。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
注意：'tags' 必须是字符串数组。
JSON 结构: {json.dumps(schema, indent=4, ensure_ascii=False)}
"""
    raw_text = _gemini_api_call(prompt)
    data = _parse_gemini_response(raw_text, task_name)
    
    if data and data.get('aiLearning'):
        for item in data['aiLearning']:
            # 使用英文 Field Name
            learning_properties = {
                "material_name": { "title": [{"text": {"content": item.get('material_name', 'N/A')}}] },
                "description": { "rich_text": [{"text": {"content": item.get('description', 'N/A')}}] },
                "type": { "select": {"name": item.get('type', 'N/A')} },
                "difficulty": { "select": {"name": item.get('difficulty', 'N/A')} },
                "link": { "url": item.get('link', None) },
                "tags": { "multi_select": [{"name": tag} for tag in item.get('tags', [])] },
            }
            _create_notion_page(NOTION_DB_AI_LEARNING, learning_properties)
    return data


def _get_ai_business():
    """Step 6: Get AI Business Opportunity data (for AI_Business_Opportunity DB)."""
    task_name = "AI Business Opportunity"
    # 严格按照 AI_Business_Opportunity 表的 Field Name 设计 JSON Schema
    schema = {
        "aiBusinessOpportunity": [
            {
                "opportunity_title": "基于 RAG 的垂直知识库 SaaS", 
                "description": "为特定行业（如医疗）提供定制化的 RAG 解决方案，解决企业内部知识检索效率问题。", 
                "potential_market": "医疗行业, 零售电商",
                "value_proposition": "提供高准确率和低成本的知识检索服务，显著提高专家工作效率。",
                "trend_reference": "多模态大模型的推理能力增强",
                "estimated_effort": "Medium (中)",
            },
        ]
    }
    prompt = f"""
请根据可联网搜索到的信息，提供至少 **2 个** 基于当前 AI 技术的潜在商业化方向，包括商机标题、详细描述、潜在市场、价值主张、支撑趋势和预估投入（如：Low (低), Medium (中), High (高)）。
请严格按照以下 JSON 结构返回数据，**不允许添加任何 Markdown 格式或额外文本**。
JSON 结构: {json.dumps(schema, indent=4, ensure_ascii=False)}
"""
    raw_text = _gemini_api_call(prompt)
    data = _parse_gemini_response(raw_text, task_name)
    
    if data and data.get('aiBusinessOpportunity'):
        for item in data['aiBusinessOpportunity']:
            # 使用英文 Field Name
            biz_properties = {
                "opportunity_title": { "title": [{"text": {"content": item.get('opportunity_title', 'N/A')}}] },
                "description": { "rich_text": [{"text": {"content": item.get('description', 'N/A')}}] },
                "potential_market": { "rich_text": [{"text": {"content": item.get('potential_market', 'N/A')}}] },
                "value_proposition": { "rich_text": [{"text": {"content": item.get('value_proposition', 'N/A')}}] },
                "trend_reference": { "rich_text": [{"text": {"content": item.get('trend_reference', 'N/A')}}] },
                "estimated_effort": { "select": {"name": item.get('estimated_effort', 'N/A')} }
            }
            _create_notion_page(NOTION_DB_AI_BUSINESS, biz_properties)
    return data

# --- HTML Email Formatting ---

def _format_html_report(all_data):
    """Format the complete analysis data into an HTML report for email."""
    
    # 提取顶层信息
    report_week_start = all_data.get('overallSummaryData', {}).get('report_week_start', datetime.now().strftime('%Y-%m-%d'))
    report_week_end = all_data.get('overallSummaryData', {}).get('report_week_end', datetime.now().strftime('%Y-%m-%d'))
    report_title = all_data.get('overallSummaryData', {}).get('title', f"全球运维与 AI 周报 ({report_week_start} - {report_week_end})")
    overall_summary = all_data.get('overallSummaryData', {}).get('overall_summary', 'N/A')

    def list_to_html(title, data_key, display_fields):
        """Generates HTML table for lists from collected data."""
        # display_fields 格式: {"中文表头": "英文 Field Name"}
        items = all_data.get(data_key, [])
        if not items: return ""
        
        html = f'<div class="section"><h2 class="section-title">{title}</h2><div class="table-container">'
        html += '<table class="data-table"><thead><tr>'
        for cn_header in display_fields.keys():
            html += f'<th>{cn_header}</th>'
        html += '</tr></thead><tbody>'
        
        for item in items:
            html += '<tr>'
            for cn_header, en_key in display_fields.items():
                value = item.get(en_key, 'N/A')
                
                # 特殊处理链接字段
                if en_key in ['official_link', 'news_link', 'link']:
                    value = f'<a href="{value}" target="_blank">查看链接</a>' if value else 'N/A'
                # 特殊处理 Array of Strings (Multi-Select)
                elif isinstance(value, list):
                    value = ", ".join(value)
                
                if cn_header in ['动态标题', '故障标题', '标题', '资源名称', '商机标题']:
                    html += f'<td><strong>{value}</strong></td>'
                else:
                    html += f'<td>{value}</td>'
            html += '</tr>'
            
        html += '</tbody></table></div></div>'
        return html

    # 使用中文显示名称和英文 Field Name 映射
    sre_dynamics_html = list_to_html("2. 运维行业动态 (SRE Dynamics)", 'sreDynamics', 
                                     {"动态标题": "title", "摘要": "summary", "发布机构/公司": "source_company", "链接": "official_link"})
    
    failure_incidents_html = list_to_html("3. 全球故障信息 (Failure Incidents)", 'failureIncidents', 
                                          {"故障标题": "incident_title", "公司": "company", "概览": "overview", "根因": "root_cause", "日期": "incident_date", "链接": "official_link"})

    ai_news_html = list_to_html("4. AI 前沿资讯 (AI News)", 'aiNews',
                                {"标题": "title", "摘要": "summary", "来源": "source", "类别": "category", "链接": "news_link"})

    ai_learning_html = list_to_html("5. AI 学习推荐 (AI Learning)", 'aiLearning',
                                    {"资源名称": "material_name", "类型": "type", "难度": "difficulty", "推荐理由": "description", "链接": "link"})

    ai_business_html = list_to_html("6. AI 商业机会 (AI Business Opportunity)", 'aiBusinessOpportunity',
                                    {"商机标题": "opportunity_title", "描述": "description", "潜在市场": "potential_market", "预估投入": "estimated_effort"})

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{report_title}</title>
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
                <h1>{report_title}</h1>
                <p>覆盖日期: {report_week_start} - {report_week_end} | 由 Gemini AI 驱动</p>
            </div>

            <div class="section">
                <h2 class="section-title">1. 本周总体摘要 (Overall Summary)</h2>
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
    print("\n--- Step 1/6: Getting Overall Summary and Report Date (Report Master) ---")
    summary_data = _get_overall_summary()
    if not summary_data:
        print("Fatal: Could not get Overall Summary. Aborting all subsequent steps.")
        return
    
    all_report_data['overallSummaryData'] = summary_data
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
    # 注意：故障事件不需要依赖 Report Date
    incident_data = _get_failure_incidents()
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
    subject = all_report_data['overallSummaryData'].get('title', "SRE/AI 周报")
    send_email_notification(GMAIL_RECIPIENT_EMAILS, f"【周报】{subject}", html_report)
    
    print("\nScript finished. All available data has been processed and notified.")

if __name__ == "__main__":
    main()
