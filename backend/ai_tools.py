import os
import google.generativeai as genai
from flask import Blueprint, request, Response, jsonify, session
from config import get_db
import json
import traceback

# --- 初始化 AI Blueprint ---
ai_bp = Blueprint('ai_bp', __name__)

# --- 初始化 Google GenAI ---

# 從環境變數中讀取 API Key (這會由主 app.py 載入)
api_key = os.getenv('GEMINI_API_KEY')

# 檢查 API Key 是否存在
if not api_key:
  print("AI 模組警告：在環境變數中找不到 GEMINI_API_KEY。")
  model = None # 將 model 設為 None
else:
  # 設定 Google Gen AI
  genai.configure(api_key=api_key)
  # 初始化模型
  model = genai.GenerativeModel('gemini-1.5-flash')

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
6. 全程使用純文字，禁止產生星號、井字號、底線或其他 Markdown 標記符號。
"""

# ==========================================================
# AI 處理的 API 端點
# ==========================================================
@ai_bp.route('/api/revise-resume', methods=['POST'])
def revise_resume():
    
    # 檢查 API Key 是否在啟動時成功載入
    if not api_key or not model:
        return jsonify({"error": "AI 服務未正確配置 API Key。"}), 500

    # 接收履歷文本、任務風格、語氣風格
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
        
        # --- 步驟一：定義語氣風格 (Tone) ---
        
        if tone_style == 'friendly':
            tone_prompt = "語氣必須親切隨和。"
        
        elif tone_style == 'cautious':
            tone_prompt = "語氣必須專業、謹慎且精確。"
        
        elif tone_style == 'academic':
            tone_prompt = "語氣必須嚴謹、客觀且具學術性。"
            
        else:
            # 預設 ('professional') 語氣的專業強化 (針對履歷情境)
            tone_prompt = "語氣必須專業正式且符合商業履歷標準。規則：1. 避免個人感悟、心態或哲學性描述。2. 強調具體行動和成就。"


        # --- 步驟二：定義主要任務 (Task) ---
        
        if edit_style == 'keyword_focus':
            # --- 選項 1: 關鍵字導向 (兩步驟) ---
            keyword_prompt = f"[任務] 從以下履歷文本中提取 5-7 個最核心的技能和成就關鍵字。[規則] 以逗號 (,) 分隔所有關鍵字，並在**一行中**輸出。[原始文本] {user_resume_text} [關鍵字列表]"
            keyword_response = model.generate_content(keyword_prompt)
            keywords = keyword_response.text.strip()
            print(f"偵測任務: 關鍵字導向 (關鍵字: {keywords}), 語氣: {tone_style}")

            final_prompt = f"{SYSTEM_PROMPT}\n\n[任務] 你是一位頂尖的人力資源專家。請根據 [核心關鍵字] 重寫 [原始文本]。[關鍵規則] 1. **必須**突出並強調 [核心關鍵字] 相關的技能和成就。 2. **{tone_prompt}** [規則] 1. 使用強動詞開頭的行動句。 2. 量化成果。 3. 禁止包含任何原始文本之外的解釋或評論。[核心關鍵字] {keywords} [原始文本] {user_resume_text} [修改後的文本]"
        
        elif edit_style == 'concise':
            # --- 選項 2: 文案精簡 (一步驟) ---
            print(f"偵測任務: 文案精簡, 語氣: {tone_style}")
            final_prompt = f"{SYSTEM_PROMPT}\n\n[任務] 將以下 [原始文本] 改寫得**極度精簡、清楚明瞭且成就導向**。[規則] 1. **{tone_prompt}** 2. **每句話必須以行動動詞開頭**。 3. 刪除所有贅字、口語化和非成就型描述。 4. 保留並強化核心資訊。 5. 禁止包含任何原始文本之外的解釋或評論。[原始文本] {user_resume_text} [修改後的文本]"

        else: # 'polish' (預設)
            # --- 選項 3: 履歷美化 (預設) (一步驟) ---
            print(f"偵測任務: 履歷美化, 語氣: {tone_style}")
            final_prompt = f"{SYSTEM_PROMPT}\n\n[任務] 專業地**美化並潤飾**以下 [原始文本]。[規則] 1. **{tone_prompt}** 2. 使用強動詞開頭的行動句。 3. 盡可能量化成果。 4. 修正文法。 5. 禁止包含任何原始文本之外的解釋或評論。[原始文本] {user_resume_text} [修改後的文本]"

        # --- 統一的串流輸出 ---
        
        def generate_stream():
            try:
                response_stream = model.generate_content(final_prompt, stream=True)
                for chunk in response_stream:
                    if chunk.text:
                        yield chunk.text
            except Exception as e:
                print(f"串流處理中發生錯誤: {e}")
                yield f"AI 服務處理失败: {e}"

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
# AI 推薦志願序 API 端點 (*** 已修改 ***)
# ==========================================================
@ai_bp.route('/api/recommend-preferences', methods=['POST'])
def recommend_preferences():
    """
    AI 推薦適合的志願序選項
    根據學生的履歷內容和公司職缺資訊進行匹配分析
    """
    print("\n--- 收到 /api/recommend-preferences 請求 ---") # 新增日誌
    
    # 檢查 API Key
    if not api_key or not model:
        print("❌ 錯誤: AI 服務未配置 (500)") # 新增日誌
        return jsonify({"success": False, "error": "AI 服務未正確配置 API Key。", "error_code": "AI_NOT_CONFIGURED"}), 500
    
    # 權限檢查
    if "user_id" not in session or session.get("role") != "student":
        print(f"❌ 錯誤: 權限不足 (403) - Session: {session}") # 新增日誌
        return jsonify({"success": False, "error": "只有學生可以使用此功能。", "error_code": "AUTH_NOT_STUDENT"}), 403
    
    student_id = session["user_id"]
    print(f"ℹ️ 學生 ID: {student_id} 請求推薦") # 新增日誌
    
    conn = None
    cursor = None
    ai_response_text = "" # 預先宣告，以便 finally 中使用
    
    try:
        # 接收履歷文字與學業成績摘要
        try:
            data = request.get_json()
            if data is None:
                # 如果前端傳了 'application/json' 但 body 是空的
                print("❌ 錯誤: 收到的 JSON 為 None (400)") # 新增日誌
                return jsonify({"success": False, "error": "無效的請求：未收到任何 JSON 資料。", "error_code": "JSON_IS_NONE"}), 400
        except Exception as json_e:
            # 如果前端傳來的 JSON 格式錯誤
            print(f"❌ 錯誤: JSON 解析失敗 (400) - {json_e}") # 新增日誌
            return jsonify({"success": False, "error": f"無效的請求：JSON 格式錯誤。 {str(json_e)}", "error_code": "JSON_PARSE_ERROR"}), 400
        
        resume_text = data.get('resumeText', '').strip()
        grades_text = data.get('gradesText', '').strip()
        print(f"ℹ️ 收到履歷長度: {len(resume_text)}, 收到成績長度: {len(grades_text)}") # 新增日誌
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 如果沒有提供履歷文字，嘗試從資料庫取得最新的履歷
        if not resume_text:
            print("⚠️ 警告: 未收到 resumeText，嘗試從資料庫查詢...") # 新增日誌
            cursor.execute("""
                SELECT filepath, original_filename
                FROM resumes
                WHERE user_id = %s AND status = 'approved'
                ORDER BY created_at DESC
                LIMIT 1
            """, (student_id,))
            resume_record = cursor.fetchone()
            
            if resume_record:
                # 這裡可以讀取履歷檔案內容（需要額外的庫來解析PDF/DOCX）
                # 目前先提示用戶需要提供履歷文字
                print("❌ 錯誤: 找到履歷檔案，但未實作檔案讀取 (400)") # 新增日誌
                return jsonify({
                    "success": False,
                    "error": "請提供履歷文字內容，或請先上傳並審核通過履歷檔案。",
                    "error_code": "RESUME_FILE_NOT_READ" # 新增錯誤代碼
                }), 400
            else:
                print("❌ 錯誤: 資料庫中找不到已審核的履歷 (400)") # 新增日誌
                return jsonify({
                    "success": False,
                    "error": "找不到您已審核的履歷，請先上傳履歷。",
                    "error_code": "RESUME_NOT_FOUND_APPROVED" # 新增錯誤代碼
                }), 400
        
        print("✅ 履歷檢查通過。開始查詢職缺...") # 新增日誌
        
        # 取得所有已審核通過的公司和職缺
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
            print("❌ 錯誤: 資料庫中沒有可選的職缺 (400)") # 新增日誌
            return jsonify({
                "success": False,
                "error": "目前沒有可選的公司和職缺。",
                "error_code": "NO_JOBS_AVAILABLE" # 新增錯誤代碼
            }), 400
        
        print(f"ℹ️ 找到 {len(companies_jobs)} 筆職缺資料。開始整理...") # 新增日誌
        
        # 整理公司和職缺資訊為結構化資料
        companies_info = {}
        for item in companies_jobs:
            company_id = item['company_id']
            if company_id not in companies_info:
                companies_info[company_id] = {
                    'company_id': company_id,
                    'company_name': item['company_name'],
                    'company_description': item['company_description'] or '',
                    'company_address': item['company_address'] or '',
                    'jobs': []
                }
            
            companies_info[company_id]['jobs'].append({
                'job_id': item['job_id'],
                'job_title': item['job_title'],
                'job_description': item['job_description'] or '',
                'job_period': item['job_period'] or '',
                'job_work_time': item['job_work_time'] or '',
                'job_remark': item['job_remark'] or ''
            })
        
        # 構建 AI 提示詞
        companies_text = ""
        for company in companies_info.values():
            jobs_text = "\n".join([
                f"  - 職缺ID: {job['job_id']}, 職缺名稱: {job['job_title']}, "
                f"描述: {job['job_description']}, 實習期間: {job['job_period']}, "
                f"工作時間: {job['job_work_time']}, 備註: {job['job_remark']}"
                for job in company['jobs']
            ])
            companies_text += f"""
公司ID: {company['company_id']}
公司名稱: {company['company_name']}
公司描述: {company['company_description']}
公司地址: {company['company_address']}
職缺列表:
{jobs_text}
---
"""
        # 建立學生背景資訊（履歷 + 成績）
        academic_info = grades_text if grades_text else "未提供"
        preference_info = (
            "【學生背景條件】\n"
            "請嚴格依據以下資訊，從【可選的公司和職缺資訊】中匹配並排序最適合的志願序。\n"
            f"履歷重點：\n{resume_text}\n"
            f"學業成績摘要：\n{academic_info}\n"
        )

        prompt = f"""{SYSTEM_PROMPT}
你是一位專業的實習顧問，請根據學生提供的【學生背景條件】（履歷與學業成績摘要），推薦最適合的實習志願序（最多5個）。

{preference_info}

【可選的公司和職缺資訊】
{companies_text}

【任務要求】
1. 分析並比對【學生背景條件】與【可選的公司和職缺資訊】。
2. 匹配最符合其技能、經驗與學業強項的公司與職缺。
3. 按適合度排序，推薦最多5個志願（由最適合至較適合）。
4. 每個推薦需包含：公司ID、職缺ID、公司名稱、職缺名稱、推薦理由（說明如何對應學生履歷與成績）。

【輸出格式】
請以 JSON 格式輸出：
{{
  "recommendations": [
    {{
      "order": 1,
      "company_id": "公司ID (字串或數字皆可)",
      "job_id": "職缺ID (字串或數字皆可)",
      "company_name": "公司名稱",
      "job_title": "職缺名稱",
      "reason": "推薦理由"
    }},
    ...
  ]
}}
"""

        print(f"🔍 AI 推薦志願序 - 學生ID: {student_id}, 履歷長度: {len(resume_text)}, 成績摘要長度: {len(grades_text)}")
        print("--- 正在呼叫 Gemini API ---") # 新增日誌

        response = model.generate_content(prompt)
        ai_response_text = response.text.strip()
        print("--- 收到 Gemini API 回應 ---") # 新增日G

        # 強化 JSON 清理
        if ai_response_text.startswith('```json'):
            ai_response_text = ai_response_text[7:]
        if ai_response_text.startswith('```'):
            ai_response_text = ai_response_text[3:]
        if ai_response_text.endswith('```'):
            ai_response_text = ai_response_text[:-3]
        ai_response_text = ai_response_text.strip()
        
        # 確保 JSON 從 { 開始
        json_start_index = ai_response_text.find('{')
        if json_start_index != -1:
            ai_response_text = ai_response_text[json_start_index:]

        print("ℹ️ 正在解析 AI 回傳的 JSON...") # 新增日誌
        recommendations_data = json.loads(ai_response_text)
        recommendations = recommendations_data.get('recommendations', [])
        print(f"ℹ️ AI 推薦了 {len(recommendations)} 筆資料，開始驗證...") # 新增日誌

        valid = []
        for rec in recommendations:
            cid, jid = rec.get('company_id'), rec.get('job_id')
            
            # 轉換為資料庫比對用的整數
            try:
                cid_int = int(cid)
                jid_int = int(jid)
            except (ValueError, TypeError):
                print(f"⚠️ 警告: AI 回傳了無效的 ID: company_id={cid}, job_id={jid} (已跳過)") # 新增日誌
                continue # 跳過這筆無效的推薦

            cursor.execute("""
                SELECT ij.id, ij.title, ic.company_name
                FROM internship_jobs ij
                JOIN internship_companies ic ON ij.company_id = ic.id
                WHERE ij.id = %s AND ij.company_id = %s 
                AND ij.is_active = TRUE AND ic.status = 'approved'
            """, (jid_int, cid_int))
            job_check = cursor.fetchone()
            
            if job_check:
                valid.append({
                    'order': rec.get('order'),
                    'company_id': cid_int, # 存儲整數 ID
                    'job_id': jid_int,     # 存儲整數 ID
                    'company_name': rec.get('company_name', job_check['company_name']),
                    'job_title': rec.get('job_title', job_check['title']),
                    'reason': rec.get('reason', '')
                })
            else:
                print(f"⚠️ 警告: AI 推薦的 ID (C:{cid_int}, J:{jid_int}) 在資料庫中不存在或未啟用 (已跳過)") # 新增日誌

        if not valid:
            print("❌ 錯誤: AI 推薦的職缺經資料庫驗證後全部失效 (400)") # 新增日誌
            return jsonify({
                "success": False, 
                "error": "AI 無法生成有效推薦，可能是職缺不符或推薦 ID 有誤。", # 調整錯誤訊息
                "error_code": "NO_VALID_RECOMMENDATIONS" # 新增錯誤代碼
            }), 400

        print(f"✅ AI 推薦成功 - 共 {len(valid)} 個有效推薦") # 新增日誌
        return jsonify({"success": True, "recommendations": valid})

    except json.JSONDecodeError as e:
        print(f"❌ 嚴重錯誤: JSON 解析失敗 (500)") # 新增日誌
        print(f"   錯誤: {e}")
        print(f"   AI 原始回應: {ai_response_text}")
        return jsonify({
            "success": False, 
            "error": "AI 回應格式錯誤，請稍後再試。", 
            "error_code": "AI_JSON_DECODE_ERROR",
            "ai_response": ai_response_text # 將錯誤的 AI 回應傳給前端，方便除錯
        }), 500
    except Exception as e:
        print(f"❌ 嚴重錯誤: 未知的伺服器錯誤 (500)") # 新增日誌
        traceback.print_exc()
        return jsonify({
            "success": False, 
            "error": f"AI 服務處理失敗: {str(e)}", 
            "error_code": "INTERNAL_SERVER_ERROR"
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        print("--- 請求 /api/recommend-preferences 處理完畢 ---\n") # 新增日誌