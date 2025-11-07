import os
import google.generativeai as genai
from flask import Blueprint, request, Response, jsonify, session
from config import get_db
import json
import traceback

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
# ==========================================================
SYSTEM_PROMPT = """
你是一位專業的實習申請顧問，專長在協助學生撰寫要寄給實習廠商的自我介紹與申請訊息。
請在所有回覆中遵守以下原則：
1. 依據指定語氣設定（專業正式／親切隨和／謹慎的／學術的）維持一致語氣。
2. 將學生提供的履歷重點整理成可直接寄給廠商的訊息，強調技能、成果與申請動機。
3. 禁止加入道歉語、AI 身分或與申請無關的敘述。
4. 全文使用繁體中文，可搭配必要的英文專有名詞。
5. 以具體行動與可量化成果為核心，段落清晰，符合寄給廠商的禮節與期待。
"""

# ==========================================================
# AI 修改履歷 API
# ==========================================================
@ai_bp.route('/api/revise-resume', methods=['POST'])
def revise_resume():
    if not api_key or not model:
        return jsonify({"error": "AI 服務未正確配置 API Key。"}), 500

    try:
        data = request.get_json()
        user_resume_text = data.get('resumeText')
        edit_style = data.get('style', 'polish')
        tone_style = data.get('tone', 'professional')

        if not user_resume_text:
            return jsonify({"error": "請提供履歷文本。"}), 400

    except Exception as e:
        print(f"請求解析錯誤: {e}")
        return jsonify({"error": "無效的請求格式。"}), 400

    try:
        final_prompt = ""

        # --- 語氣設定 ---
        if tone_style == 'friendly':
            tone_prompt = "語氣必須親切隨和。"
        elif tone_style == 'cautious':
            tone_prompt = "語氣必須專業、謹慎且精確。"
        elif tone_style == 'academic':
            tone_prompt = "語氣必須嚴謹、客觀且具學術性。"
        else:
            tone_prompt = "語氣必須專業正式且符合商業履歷標準。規則：1. 避免個人感悟或心態描述。2. 強調具體行動與成果。"

        # --- 任務設定 ---
        if edit_style == 'keyword_focus':
            keyword_prompt = f"[任務] 從以下履歷文本中提取 5-7 個最核心的技能和成就關鍵字。[原始文本] {user_resume_text}"
            keyword_response = model.generate_content(f"{SYSTEM_PROMPT}\n{keyword_prompt}")
            keywords = keyword_response.text.strip()
            print(f"偵測任務: 關鍵字導向 (關鍵字: {keywords}), 語氣: {tone_style}")

            final_prompt = f"""{SYSTEM_PROMPT}
[任務] 你是一位頂尖的人力資源專家。請根據 [核心關鍵字] 重寫 [原始文本]。
[關鍵規則] 1. 突出並強調 [核心關鍵字] 相關的技能與成就。
2. {tone_prompt}
3. 使用強動詞開頭的行動句。
4. 量化成果。
5. 禁止包含任何原始文本之外的解釋或評論。
[核心關鍵字] {keywords}
[原始文本] {user_resume_text}
[修改後的文本]
"""
        elif edit_style == 'concise':
            print(f"偵測任務: 文案精簡, 語氣: {tone_style}")
            final_prompt = f"""{SYSTEM_PROMPT}
[任務] 將以下 [原始文本] 改寫得極度精簡、清楚且成就導向。
[規則]
1. {tone_prompt}
2. 每句話必須以行動動詞開頭。
3. 刪除所有贅字與非成就型描述。
4. 保留核心資訊並強化成效。
5. 禁止包含任何原始文本之外的解釋或評論。
[原始文本] {user_resume_text}
[修改後的文本]
"""
        else:
            print(f"偵測任務: 履歷美化, 語氣: {tone_style}")
            final_prompt = f"""{SYSTEM_PROMPT}
[任務] 專業地美化並潤飾以下 [原始文本]。
[規則]
1. {tone_prompt}
2. 使用強動詞開頭的行動句。
3. 盡可能量化成果並修正文法。
4. 禁止包含任何原始文本之外的解釋或評論。
[原始文本] {user_resume_text}
[修改後的文本]
"""

        # --- 串流輸出 ---
        def generate_stream():
            try:
                response_stream = model.generate_content(final_prompt, stream=True)
                for chunk in response_stream:
                    if chunk.text:
                        yield chunk.text
            except Exception as e:
                print(f"串流處理中發生錯誤: {e}")
                yield f"AI 服務處理失敗: {e}"

        headers = {
            'Content-Type': 'text/plain; charset=utf-8',
            'Transfer-Encoding': 'chunked',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
        }
        return Response(generate_stream(), headers=headers)

    except Exception as e:
        print(f"Gemini API 呼叫失敗： {e}")
        return jsonify({"error": f"AI 服務處理失敗: {e}"}), 500


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
        resume_text = data.get('resumeText', '').strip()

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        if not resume_text:
            cursor.execute("""
                SELECT filepath, original_filename
                FROM resumes
                WHERE user_id = %s AND status = 'approved'
                ORDER BY created_at DESC
                LIMIT 1
            """, (student_id,))
            resume_record = cursor.fetchone()

            if resume_record:
                return jsonify({
                    "success": False,
                    "error": "請提供履歷文字內容，或請先上傳並審核通過履歷檔案。"
                }), 400

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

        prompt = f"""{SYSTEM_PROMPT}
你是一位專業的實習顧問，請根據學生的履歷內容，推薦最適合的實習志願序（最多5個）。

【學生履歷內容】
{resume_text}

【可選的公司和職缺資訊】
{companies_text}

【任務要求】
1. 分析學生的技能、經驗與興趣。
2. 匹配最適合的公司與職缺。
3. 按適合度排序，推薦最多5個志願（由最適合至較適合）。
4. 每個推薦需包含：公司ID、職缺ID、推薦理由。

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

        print(f"🔍 AI 推薦志願序 - 學生ID: {student_id}, 履歷長度: {len(resume_text)}")

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
            return jsonify({"success": False, "error": "AI 無法生成有效推薦，請確認履歷內容是否足夠詳細。"}), 400

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
