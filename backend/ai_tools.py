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
        
        # 'creative' (活潑有創意) 已被移除

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

            final_prompt = f"[任務] 你是一位頂尖的人力資源專家。請根據 [核心關鍵字] 重寫 [原始文本]。[關鍵規則] 1. **必須**突出並強調 [核心關鍵字] 相關的技能和成就。 2. **{tone_prompt}** [規則] 1. 使用強動詞開頭的行動句。 2. 量化成果。 3. 禁止包含任何原始文本之外的解釋或評論。[核心關鍵字] {keywords} [原始文本] {user_resume_text} [修改後的文本]"
        
        elif edit_style == 'concise':
            # --- 選項 2: 文案精簡 (一步驟) ---
            # 強化文案精簡任務，強制其以成就導向
            print(f"偵測任務: 文案精簡, 語氣: {tone_style}")
            final_prompt = f"[任務] 將以下 [原始文本] 改寫得**極度精簡、清楚明瞭且成就導向**。[規則] 1. **{tone_prompt}** 2. **每句話必須以行動動詞開頭**。 3. 刪除所有贅字、口語化和非成就型描述。 4. 保留並強化核心資訊。 5. 禁止包含任何原始文本之外的解釋或評論。[原始文本] {user_resume_text} [修改後的文本]"

        else: # 'polish' (預設)
            # --- 選項 3: 履歷美化 (預設) (一步驟) ---
            print(f"偵測任務: 履歷美化, 語氣: {tone_style}")
            # 修正原始程式碼中 tone_prompt 的引用錯誤 ($ 改為 {})
            final_prompt = f"[任務] 專業地**美化並潤飾**以下 [原始文本]。[規則] 1. **{tone_prompt}** 2. 使用強動詞開頭的行動句。 3. 盡可能量化成果。 4. 修正文法。 5. 禁止包含任何原始文本之外的解釋或評論。[原始文本] {user_resume_text} [修改後的文本]"

        # --- 統一的串流輸出 ---
        
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
# AI 推薦志願序 API 端點
# ==========================================================
@ai_bp.route('/api/recommend-preferences', methods=['POST'])
def recommend_preferences():
    """
    AI 推薦適合的志願序選項
    根據學生的履歷內容和公司職缺資訊進行匹配分析
    """
    
    # 檢查 API Key
    if not api_key or not model:
        return jsonify({"success": False, "error": "AI 服務未正確配置 API Key。"}), 500
    
    # 權限檢查
    if "user_id" not in session or session.get("role") != "student":
        return jsonify({"success": False, "error": "只有學生可以使用此功能。"}), 403
    
    student_id = session["user_id"]
    conn = None
    cursor = None
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # ==========================================================
        # 自動從資料庫獲取學生的履歷和成績資料
        # ==========================================================
        
        # 1. 獲取學生基本資訊
        cursor.execute("SELECT * FROM Student_Info WHERE StuID=%s", (student_id,))
        student_info = cursor.fetchone() or {}
        
        # 2. 獲取課程成績
        cursor.execute("SELECT CourseName, Credits, Grade FROM Course_Grades WHERE StuID=%s", (student_id,))
        grades = cursor.fetchall() or []
        
        # 3. 獲取證照
        cursor.execute("SELECT CertName, CertType FROM Student_Certifications WHERE StuID=%s", (student_id,))
        certifications = cursor.fetchall() or []
        
        # 4. 獲取語言能力
        cursor.execute("SELECT Language, Level FROM Student_LanguageSkills WHERE StuID=%s", (student_id,))
        languages = cursor.fetchall() or []
        
        # 5. 整理履歷重點文字
        resume_parts = []
        
        # 基本資訊
        if student_info:
            if student_info.get('Major'):
                resume_parts.append(f"主修：{student_info.get('Major')}")
            if student_info.get('Skills'):
                resume_parts.append(f"技能：{student_info.get('Skills')}")
        
        # 證照
        if certifications:
            cert_names = [c.get('CertName', '') for c in certifications if c.get('CertName')]
            if cert_names:
                resume_parts.append(f"證照：{', '.join(cert_names)}")
        
        # 語言能力
        if languages:
            lang_list = [f"{l.get('Language', '')} {l.get('Level', '')}" for l in languages if l.get('Language')]
            if lang_list:
                resume_parts.append(f"語言能力：{', '.join(lang_list)}")
        
        resume_text = "\n".join(resume_parts) if resume_parts else "（無履歷資料）"
        
        # 6. 整理學業成績摘要
        grades_parts = []
        
        # 計算 GPA（如果有成績資料）
        if grades:
            grade_points = {'A+': 4.3, 'A': 4.0, 'A-': 3.7, 'B+': 3.3, 'B': 3.0, 'B-': 2.7, 
                           'C+': 2.3, 'C': 2.0, 'C-': 1.7, 'D': 1.0, 'F': 0.0}
            total_points = 0
            total_credits = 0
            
            key_courses = []
            for grade in grades:
                course_name = grade.get('CourseName', '')
                credits = float(grade.get('Credits', 0) or 0)
                grade_str = str(grade.get('Grade', '')).strip().upper()
                
                if credits > 0 and grade_str in grade_points:
                    total_points += grade_points[grade_str] * credits
                    total_credits += credits
                
                # 記錄關鍵課程（A以上）
                if grade_str in ['A+', 'A', 'A-'] and course_name:
                    key_courses.append(f"{course_name} {grade_str}")
            
            if total_credits > 0:
                gpa = total_points / total_credits
                grades_parts.append(f"GPA: {gpa:.2f}/4.3")
            
            if key_courses:
                grades_parts.append(f"關鍵課程成績：{', '.join(key_courses[:5])}")  # 最多顯示5個
        
        grades_text = "\n".join(grades_parts) if grades_parts else "（無成績資料）"
        
        # 取得所有公司和職缺（與 fill_preferences 頁面保持一致）
        # 先檢查是否有公司
        cursor.execute("SELECT COUNT(*) as count FROM internship_companies")
        company_count = cursor.fetchone().get('count', 0)
        
        if company_count == 0:
                return jsonify({
                    "success": False,
                "error": "目前系統中沒有任何公司資料，請聯繫管理員新增公司。"
                }), 400
        
        # 取得所有公司和職缺（不過濾狀態，與頁面顯示一致）
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
            WHERE ij.is_active = TRUE
            ORDER BY ic.company_name, ij.title
        """)
        companies_jobs = cursor.fetchall()
        
        if not companies_jobs:
            # 檢查是否有公司但沒有職缺
            cursor.execute("SELECT COUNT(*) as count FROM internship_jobs WHERE is_active = TRUE")
            job_count = cursor.fetchone().get('count', 0)
            
            if job_count == 0:
                return jsonify({
                    "success": False,
                    "error": "目前系統中沒有可用的職缺，請聯繫管理員新增職缺。"
                }), 400
            return jsonify({
                "success": False,
                "error": "目前沒有可選的公司和職缺組合。"
            }), 400
        
        # 整理公司和職缺資訊為結構化資料
        companies_info = {}
        company_name_to_id = {}
        job_by_id = {}
        job_by_company_title = {}
        job_title_index = {}
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
                company_name_to_id[item['company_name'].strip()] = company_id
            
            job_payload = {
                'job_id': item['job_id'],
                'job_title': item['job_title'],
                'job_description': item['job_description'] or '',
                'job_period': item['job_period'] or '',
                'job_work_time': item['job_work_time'] or '',
                'job_remark': item['job_remark'] or ''
            }
            combined_job = {**job_payload, 'company_id': company_id, 'company_name': item['company_name']}
            companies_info[company_id]['jobs'].append(job_payload)
            job_by_id[item['job_id']] = combined_job
            normalized_title = (item['job_title'] or '').strip().lower()
            job_by_company_title[(company_id, normalized_title)] = combined_job
            if normalized_title:
                job_title_index.setdefault(normalized_title, []).append(combined_job)
        
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

        prompt = f"""{SYSTEM_PROMPT}
你是一位專業的實習顧問，請根據學生的履歷重點和學業成績，推薦最適合的實習志願序（最多5個）。

【學生履歷重點（系統自動擷取）】
{resume_text}

【學業成績摘要（系統自動擷取）】
{grades_text}

【可選的公司和職缺資訊】
{companies_text}

【任務要求】
1. 分析並比對【學生履歷重點】、【學業成績摘要】與【可選的公司和職缺資訊】。
2. 根據學生的技能、證照、語言能力、成績表現，匹配最符合的公司與職缺。
3. 按適合度排序，推薦最多5個志願（由最適合至較適合）。
4. 每個推薦需包含：公司ID、職缺ID、推薦理由 (理由必須明確說明如何符合學生的履歷和成績)。

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

        print(
            "🔍 AI 推薦志願序 - "
            f"學生ID: {student_id}, "
            f"履歷長度: {len(resume_text)}, 成績摘要長度: {len(grades_text)}"
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

        def try_parse_json(raw_text: str):
            try:
                return json.loads(raw_text)
            except json.JSONDecodeError:
                return None

        recommendations_data = try_parse_json(ai_response_text)

        if recommendations_data is None:
            # 嘗試從文字中擷取 JSON 片段
            first_brace = ai_response_text.find('{')
            last_brace = ai_response_text.rfind('}')
            parsed = None
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                possible_json = ai_response_text[first_brace:last_brace+1]
                parsed = try_parse_json(possible_json)
            if parsed is None:
                print("❌ AI 回傳無法解析為 JSON，改用 fallback。原始回應：", ai_response_text)
                recommendations = []
            else:
                recommendations_data = parsed
                recommendations = recommendations_data.get('recommendations', [])
        else:
            recommendations = recommendations_data.get('recommendations', [])

        valid = []
        for rec in recommendations:
            cid = rec.get('company_id')
            jid = rec.get('job_id')
            rec_company_name = (rec.get('company_name') or '').strip()
            rec_job_title = (rec.get('job_title') or '').strip()

            matched_job = None

            # 嘗試以 job_id 優先匹配
            try:
                jid_int = int(str(jid)) if jid is not None and str(jid).strip().isdigit() else None
            except ValueError:
                jid_int = None

            try:
                cid_int = int(str(cid)) if cid is not None and str(cid).strip().isdigit() else None
            except ValueError:
                cid_int = None

            if jid_int and jid_int in job_by_id:
                job_info = job_by_id[jid_int]
                # 若有指定 company_id 但不符，則視為 mismatch
                if not cid_int or cid_int == job_info['company_id']:
                    matched_job = job_info
                else:
                    matched_job = None

            # 若未匹配成功，改以公司名稱 + 職缺名稱嘗試
            if not matched_job and rec_job_title:
                normalized_title = rec_job_title.lower()
                # 1) 精確匹配同公司
                if not cid_int and rec_company_name:
                    cid_int = company_name_to_id.get(rec_company_name)
                if cid_int:
                    key = (cid_int, normalized_title)
                    if key in job_by_company_title:
                        matched_job = job_by_company_title[key]
                # 2) 若仍未找到，嘗試唯一職缺名稱
                if not matched_job and normalized_title in job_title_index:
                    possible_jobs = job_title_index[normalized_title]
                    if len(possible_jobs) == 1:
                        matched_job = possible_jobs[0]
                # 3) 嘗試模糊比對 (包含關鍵字)
                if not matched_job:
                    for job in job_by_id.values():
                        job_title_lower = (job['job_title'] or '').lower()
                        if normalized_title and (normalized_title in job_title_lower or job_title_lower in normalized_title):
                            if cid_int and job['company_id'] != cid_int:
                                continue
                            matched_job = job
                            break

            if matched_job:
                valid.append({
                    'order': rec.get('order'),
                    'company_id': matched_job['company_id'],
                    'job_id': matched_job['job_id'],
                    'company_name': matched_job['company_name'],
                    'job_title': matched_job['job_title'],
                    'reason': rec.get('reason', '')
                })

        if not valid:
            print("⚠️ AI 推薦無法直接對應職缺，啟用後備推薦。原始結果：", recommendations)
            fallback_jobs = list(job_by_id.values())
            fallback_jobs.sort(key=lambda j: (j['company_name'], j['job_title']))
            fallback_limit = min(5, len(fallback_jobs))
            if fallback_limit == 0:
                return jsonify({"success": False, "error": "系統目前找不到可用職缺，請稍後再試。"}), 400
            for idx in range(fallback_limit):
                job = fallback_jobs[idx]
                valid.append({
                    'order': idx + 1,
                    'company_id': job['company_id'],
                    'job_id': job['job_id'],
                    'company_name': job['company_name'],
                    'job_title': job['job_title'],
                    'reason': "系統自動推薦：依照您目前的背景與熱門程度優先推薦此職缺。"
                })

        print(f"✅ AI 推薦成功 - 共 {len(valid)} 個推薦")
        return jsonify({"success": True, "recommendations": valid})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": f"AI 服務處理失敗: {str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()