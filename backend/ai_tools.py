import os
import google.generativeai as genai
from flask import Blueprint, request, Response, jsonify, session
from config import get_db
import json
import traceback
from PyPDF2 import PdfReader

# --- 初始化 AI Blueprint ---
ai_bp = Blueprint('ai_bp', __name__)

# --- 初始化 Google GenAI ---
api_key = os.getenv('GEMINI_API_KEY')

if not api_key:
    print("AI 模組警告：在環境變數中找不到 GEMINI_API_KEY。")
    model = None
else:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')

# ==========================================================
# 🧠 系統提示詞（System Prompt）
# (保持不變，但 AI 推薦時會忽略「履歷重點整理」的描述)
# ==========================================================
SYSTEM_PROMPT = """
你是一位專業的實習申請顧問，專長在協助學生撰寫要寄給實習廠商的自我介紹與申請訊息。
請在所有回覆中遵守以下原則：
1. 依據指定語氣設定（專業正式／親切隨和／謹慎的／學術的）維持一致語氣。
2. 將學生提供的履歷重點整理成可直接寄給廠商的訊息，強調技能、成果與申請動機。
3. 禁止加入道歉語、AI 身分或與申請無關的敘述。
4. 全文使用繁體中文，可搭配必要的英文專有名詞。
5. 以具體行動與可量化成果為核心，段落清晰，符合寄給廠商的禮節與期待。
6. 全程使用純文字，禁止產生星號、井字號、底線或其他 Markdown 標記符號。
"""

# ----------------------------------------------------------
# Helper: 讀取 PDF 履歷文字
# ----------------------------------------------------------
def extract_pdf_text(pdf_path: str) -> str:
    if not pdf_path or not os.path.exists(pdf_path):
        print(f"❗ 找不到履歷檔案：{pdf_path}")
        return ""

    try:
        reader = PdfReader(pdf_path)
        pages_text = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            pages_text.append(page_text.strip())
        combined = "\n".join(filter(None, pages_text)).strip()
        if not combined:
            print(f"❗ PDF 解析結果為空：{pdf_path}")
        return combined
    except Exception as exc:
        print(f"❌ PDF 解析失敗：{exc}")
        return ""

# ==========================================================
# AI 推薦志願序 API 
# ==========================================================
@ai_bp.route('/api/recommend-preferences', methods=['POST'])
def recommend_preferences():
    if not api_key or not model:
        return jsonify({"success": False, "error": "AI 服務未正確配置 API Key。"}), 500

    if "user_id" not in session or session.get("role") != "student":
        return jsonify({"success": False, "error": "只有學生可以使用此功能。"}), 403

    student_id = session["user_id"]
    conn = None
    cursor = None

    try:
        data = request.get_json() or {}
        # 💡 從前端接收偏好條件
        transportation_filter = data.get('transportationFilter', 'any')
        distance_filter = data.get('distanceFilter', 'any')
        salary_filter = data.get('salaryFilter', 'any')

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 取得學生最新「審核通過」的履歷檔案
        cursor.execute(
            """
            SELECT filepath, original_filename
            FROM resumes
            WHERE user_id = %s AND status = 'approved'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (student_id,),
        )
        resume_record = cursor.fetchone()
        if not resume_record:
            return jsonify({
                "success": False,
                "error": "尚未找到審核通過的履歷檔案，請先完成上傳與審核再使用 AI 推薦。"
            }), 400

        resume_path = resume_record.get('filepath')
        resume_text = extract_pdf_text(resume_path)
        if not resume_text:
            return jsonify({
                "success": False,
                "error": "無法讀取履歷檔案內容，請確認檔案為可解析的 PDF。"
            }), 500

        # 避免過長導致超出模型限制，保留前 6000 字元
        resume_text = resume_text[:6000]

        cursor.execute("""
            SELECT 
                ic.id AS company_id,
                ic.company_name,
                ic.description AS company_description,
                ic.location AS company_address,
                ij.id AS job_id,
                ij.title AS job_title,
                ij.description AS job_description,
                ij.period AS job_period,
                ij.work_time AS job_work_time,
                ij.remark AS job_remark
            FROM internship_companies ic
            JOIN internship_jobs ij ON ic.id = ij.company_id
            WHERE ic.status = 'approved' AND ij.is_active = TRUE
            ORDER BY ic.company_name, ij.title
        """)
        companies_jobs = cursor.fetchall()

        if not companies_jobs:
            return jsonify({
                "success": False,
                "error": "目前沒有可選的公司和職缺。"
            }), 400

        companies_info = {}
        for item in companies_jobs:
            cid = item['company_id']
            if cid not in companies_info:
                companies_info[cid] = {
                    'company_id': cid,
                    'company_name': item['company_name'],
                    'company_description': item['company_description'] or '',
                    'company_address': item['company_address'] or '',
                    'jobs': []
                }
            companies_info[cid]['jobs'].append({
                'job_id': item['job_id'],
                'job_title': item['job_title'],
                'job_description': item['job_description'] or '',
                'job_period': item['job_period'] or '',
                'job_work_time': item['job_work_time'] or '',
                'job_remark': item['job_remark'] or ''
            })

        companies_text = ""
        for c in companies_info.values():
            jobs_text = "\n".join([
                f"  - 職缺ID: {j['job_id']}, 職缺名稱: {j['job_title']}, 描述: {j['job_description']}, 實習期間: {j['job_period']}, 工作時間: {j['job_work_time']}, 備註: {j['job_remark']}"
                for j in c['jobs']
            ])
            companies_text += f"""
公司ID: {c['company_id']}
公司名稱: {c['company_name']}
公司描述: {c['company_description']}
公司地址: {c['company_address']}
職缺列表:
{jobs_text}
---
"""
        distance_map = {
            'any': '不限距離',
            'close': '通勤 30 分鐘內',
            'medium': '通勤 1 小時內',
            'far': '超過 1 小時'
        }
        transportation_map = {
            'any': '不限交通方式',
            'public': '以大眾運輸為主',
            'car': '以汽車或機車為主',
            'bike': '以自行車或步行為主'
        }
        salary_map = {
            'any': '不限薪資類型',
            'monthly': '月薪',
            'hourly': '時薪',
            'stipend': '獎金或津貼',
            'unpaid': '無薪資'
        }

        preference_lines = [
            f"距離遠近偏好：{distance_map.get(distance_filter, '不限距離')}",
            f"交通工具偏好：{transportation_map.get(transportation_filter, '不限交通方式')}",
            f"實習薪資偏好：{salary_map.get(salary_filter, '不限薪資類型')}"
        ]
        preference_info = "【學生實習偏好條件】\n" + "\n".join(preference_lines) + "\n請嚴格依據上述偏好條件，從【可選的公司和職缺資訊】中篩選並排序最適合的志願序。"

        # 💡 關鍵修改：移除對履歷日記的提及
        prompt = f"""{SYSTEM_PROMPT}
你是一位專業的實習顧問，請根據學生提供的【學生實習偏好條件】，推薦最適合的實習志願序（最多5個）。

{preference_info}

【學生履歷重點（系統自動擷取）】
{resume_text}

【可選的公司和職缺資訊】
{companies_text}

【任務要求】
1. 分析並比對【學生實習偏好條件】、【學生履歷重點】與【可選的公司和職缺資訊】。
2. 匹配最符合這些條件的公司與職缺。
3. 按適合度排序，推薦最多5個志願（由最適合至較適合）。
4. 每個推薦需包含：公司ID、職缺ID、推薦理由 (理由必須明確說明如何符合偏好條件)。

【輸出格式】
請以 JSON 格式輸出：
{{
  "recommendations": [
    {{
      "order": 1,
      "company_id": 公司ID,
      "job_id": 職缺ID,
      "company_name": "公司名稱",
      "job_title": "職缺名稱",
      "reason": "推薦理由"
    }},
    ...
  ]
}}
"""

        # 💡 將 print 訊息更新
        print(
            "🔍 AI 推薦志願序 - "
            f"學生ID: {student_id}, 距離: {distance_filter}, 交通: {transportation_filter}, 薪資: {salary_filter}, "
            f"履歷長度: {len(resume_text)}"
        )

        response = model.generate_content(prompt)
        ai_response_text = response.text.strip()

        if ai_response_text.startswith('```json'):
            ai_response_text = ai_response_text[7:]
        if ai_response_text.startswith('```'):
            ai_response_text = ai_response_text[3:]
        if ai_response_text.endswith('```'):
            ai_response_text = ai_response_text[:-3]
        ai_response_text = ai_response_text.strip()

        recommendations_data = json.loads(ai_response_text)
        recommendations = recommendations_data.get('recommendations', [])

        valid = []
        for rec in recommendations:
            cid, jid = rec.get('company_id'), rec.get('job_id')
            cursor.execute("""
                SELECT ij.id, ij.title, ic.company_name
                FROM internship_jobs ij
                JOIN internship_companies ic ON ij.company_id = ic.id
                WHERE ij.id = %s AND ij.company_id = %s 
                AND ij.is_active = TRUE AND ic.status = 'approved'
            """, (jid, cid))
            job_check = cursor.fetchone()
            if job_check:
                valid.append({
                    'order': rec.get('order'),
                    'company_id': cid,
                    'job_id': jid,
                    'company_name': rec.get('company_name', job_check['company_name']),
                    'job_title': rec.get('job_title', job_check['title']),
                    'reason': rec.get('reason', '')
                })

        if not valid:
            return jsonify({"success": False, "error": "AI 無法生成有效推薦，請嘗試放寬篩選條件。"}), 400

        print(f"✅ AI 推薦成功 - 共 {len(valid)} 個推薦")
        return jsonify({"success": True, "recommendations": valid})

    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析錯誤: {e}")
        return jsonify({"success": False, "error": "AI 回應格式錯誤，請稍後再試。"}), 500
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": f"AI 服務處理失敗: {str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()