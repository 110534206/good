import os
import re
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

7. **絕對禁止**在輸出中包含任何解釋性文字、前綴說明（如「這是為您改寫的...」、「以下是...」）、後綴註解或評論。

8. **只輸出修改後的文本內容**，直接從修改後的文本開始，不要有任何說明或介紹。
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


        # 🌟 [新功能] 如果用戶沒有提供 resumeText，自動從資料庫讀取自傳
        if not user_resume_text or not user_resume_text.strip():

            # 檢查用戶是否已登入

            if 'user_id' not in session or session.get('role') != 'student':

                return jsonify({"error": "請先登入並提供履歷文本，或先上傳履歷。"}), 400
            

            user_id = session['user_id']

            conn = get_db()

            cursor = conn.cursor(dictionary=True)
            

            try:

                # 獲取學號

                cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))

                user_result = cursor.fetchone()

                if not user_result:

                    return jsonify({"error": "找不到使用者資訊。"}), 404
                

                student_id = user_result["username"]
                
                

                # 從資料庫讀取自傳

                cursor.execute("SELECT Autobiography FROM Student_Info WHERE StuID=%s", (student_id,))

                student_info = cursor.fetchone()
                

                if student_info and student_info.get('Autobiography'):

                    user_resume_text = str(student_info.get('Autobiography', '')).strip()

                    print(f"✅ 自動從資料庫讀取自傳內容，長度: {len(user_resume_text)}")

                else:

                    return jsonify({"error": "資料庫中沒有自傳內容，請先上傳履歷或手動輸入。"}), 400
                    

            except Exception as e:

                print(f"從資料庫讀取自傳失敗: {e}")

                return jsonify({"error": "無法從資料庫讀取自傳，請手動輸入。"}), 500

            finally:

                if cursor:

                    cursor.close()

                if conn:

                    conn.close()


        if not user_resume_text or not user_resume_text.strip():

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
        


        if edit_style == 'light_polish':

            print(f"偵測任務: 輕度潤飾, 語氣: {tone_style}")

            final_prompt = f"""[任務] 在最大程度保留原始結構與內容的前提下，對以下 [原始文本] 進行輕度潤飾。
[嚴格規則]
1. 不得大幅改寫內容
2. 不得新增原文沒有的經驗或成就
3. 保留原本段落順序
4. 僅修正文法、語句不順與過於口語的表達
5. 可微幅優化語氣，但不得改變原意
6. 不得重組全文
7. 修改比例不得超過原文字數的30%
8. {tone_prompt}
9. 只輸出修改後文本，不要任何說明
[原始文本] {user_resume_text}
[修改後的文本]"""

        elif edit_style == 'keyword_focus':


            # --- 選項 1: 關鍵字導向 (兩步驟) ---

            keyword_prompt = f"[任務] 從以下履歷文本中提取 5-7 個最核心的技能和成就關鍵字。[規則] 以逗號 (,) 分隔所有關鍵字，並在**一行中**輸出。[原始文本] {user_resume_text} [關鍵字列表]"

            keyword_response = model.generate_content(keyword_prompt)

            keywords = keyword_response.text.strip()

            print(f"偵測任務: 關鍵字導向 (關鍵字: {keywords}), 語氣: {tone_style}")


            final_prompt = f"[任務] 你是一位頂尖的人力資源專家。請根據 [核心關鍵字] 重寫 [原始文本]。[關鍵規則] 1. **必須**突出並強調 [核心關鍵字] 相關的技能和成就。 2. **{tone_prompt}** [規則] 1. 使用強動詞開頭的行動句。 2. 量化成果。 3. **絕對禁止**包含任何解釋性文字、前綴說明、後綴註解或評論。 4. **只輸出修改後的文本內容**，不要有任何「這是為您改寫的...」、「以下是...」等說明文字。 5. 直接從修改後的文本開始輸出，不要有任何前綴。[核心關鍵字] {keywords} [原始文本] {user_resume_text} [修改後的文本]"
        

        elif edit_style == 'concise':

            # --- 選項 2: 文案精簡 (一步驟) ---

            # 強化文案精簡任務，強制其以成就導向

            print(f"偵測任務: 文案精簡, 語氣: {tone_style}")

            final_prompt = f"[任務] 將以下 [原始文本] 改寫得**極度精簡、清楚明瞭且成就導向**。[規則] 1. **{tone_prompt}** 2. **每句話必須以行動動詞開頭**。 3. 刪除所有贅字、口語化和非成就型描述。 4. 保留並強化核心資訊。 5. **絕對禁止**包含任何解釋性文字、前綴說明、後綴註解或評論。 6. **只輸出修改後的文本內容**，不要有任何「這是為您改寫的...」、「以下是...」等說明文字。 7. 直接從修改後的文本開始輸出，不要有任何前綴。[原始文本] {user_resume_text} [修改後的文本]"


        else: # 'polish' (預設)

            # --- 選項 3: 履歷美化 (預設) (一步驟) ---

            print(f"偵測任務: 履歷美化, 語氣: {tone_style}")

            # 修正原始程式碼中 tone_prompt 的引用錯誤 ($ 改為 {})

            final_prompt = f"[任務] 專業地**美化並潤飾**以下 [原始文本]。[規則] 1. **{tone_prompt}** 2. 使用強動詞開頭的行動句。 3. 盡可能量化成果。 4. 修正文法。 5. **絕對禁止**包含任何解釋性文字、前綴說明、後綴註解或評論。 6. **只輸出修改後的文本內容**，不要有任何「這是為您改寫的...」、「以下是...」等說明文字。 7. 直接從修改後的文本開始輸出，不要有任何前綴。[原始文本] {user_resume_text} [修改後的文本]"


        # --- 統一的串流輸出 ---
        

        def generate_stream():

            try:

                response_stream = model.generate_content(final_prompt, stream=True)

                for chunk in response_stream:

                    if chunk.text:

                        yield chunk.text

            except Exception as e:

                print(f"串流處理中發生錯誤: {e}")
                
                error_str = str(e)
                
                # 檢查是否為配額限制錯誤（429）
                if "429" in error_str or "quota" in error_str.lower() or "Quota exceeded" in error_str:
                    # 嘗試提取重試時間
                    retry_seconds = None
                    if "retry in" in error_str.lower() or "retry_delay" in error_str.lower():
                        retry_match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
                        if retry_match:
                            retry_seconds = int(float(retry_match.group(1)))
                    
                    error_message = "⚠️ AI 服務目前使用量已達上限（免費層級每日限制 20 次）"
                    if retry_seconds:
                        error_message += f"，請在 {retry_seconds} 秒後再試"
                    else:
                        error_message += "，請稍後再試或明天再使用此功能"
                    
                    yield error_message
                else:
                    yield f"AI 服務處理失敗: {error_str}"


        headers = {

            'Content-Type': 'text/plain; charset=utf-8',

            'Transfer-Encoding': 'chunked',

            'Cache-Control': 'no-cache',

            'Connection': 'keep-alive'

        }

        return Response(generate_stream(), headers=headers)


    except Exception as e:

        print(f"Gemini API 呼叫失敗： {e}")
        
        error_str = str(e)
        
        # 檢查是否為配額限制錯誤（429）
        if "429" in error_str or "quota" in error_str.lower() or "Quota exceeded" in error_str:
            # 嘗試提取重試時間
            retry_seconds = None
            if "retry in" in error_str.lower() or "retry_delay" in error_str.lower():
                retry_match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
                if retry_match:
                    retry_seconds = int(float(retry_match.group(1)))
            
            error_message = "AI 服務目前使用量已達上限（每日限制 20 次）"
            if retry_seconds:
                error_message += f"，請在 {retry_seconds} 秒後再試"
            else:
                error_message += "，請稍後再試或明天再使用此功能"
            
            return jsonify({
                "error": error_message,
                "error_type": "quota_exceeded",
                "retry_after": retry_seconds
            }), 429

        return jsonify({"error": f"AI 服務處理失敗: {error_str}"}), 500



# ==========================================================

# AI 推薦志願序 API 端點

# ==========================================================

@ai_bp.route('/api/recommend-preferences', methods=['POST'])

def recommend_preferences():

    if not model:

        return jsonify({"success": False, "error": "AI 模型未正確初始化"}), 500
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
    
    user_id = session["user_id"]

    if not model:

        return jsonify({"success": False, "error": "AI 模型未正確初始化"}), 500


    conn = None

    cursor = None
    

    try:

        data = request.get_json()

        target_student_id = data.get('student_id')

        if target_student_id:
            user_id = target_student_id

        else:
            user_id = session["user_id"]

        conn = get_db()
        if not conn:
            return jsonify({"error": "無法連接資料庫"}), 500

        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        user_result = cursor.fetchone()
        if not user_result:
            return jsonify({"success": False, "error": "找不到使用者資訊。"}), 404  
        else:
            student_id = user_result["username"]
        # ==========================================================

        # 檢查是否有上傳履歷（不管審核狀態）

        # ==========================================================

        cursor.execute("SELECT id, status FROM resumes WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (user_id,))

        resume_record = cursor.fetchone()
        

        if not resume_record:

            return jsonify({

                "success": False,

                "error": "您尚未上傳履歷，請先完成履歷上傳後再使用推薦功能。"

            }), 400
        

        # ==========================================================

        # 自動從資料庫獲取學生的履歷和成績資料

        # ==========================================================
        

        # 1. 獲取學生基本資訊

        cursor.execute("SELECT * FROM Student_Info WHERE StuID=%s",(student_id,))

        student_info = cursor.fetchone() or {}
        

        # 2. 獲取課程成績（從 course_grades 資料表讀取）

        # 檢查是否有成績單圖片欄位

        try:

            cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'ProofImage'")

            has_proof_image = cursor.fetchone() is not None

            cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'transcript_path'")

            has_transcript_path = cursor.fetchone() is not None

        except:

            has_proof_image = False

            has_transcript_path = False
        

        # 查詢成績資料（可能包含成績單圖片路徑）

        if has_proof_image:

            cursor.execute("SELECT CourseName, Credits, Grade, ProofImage FROM course_grades WHERE StuID=%s", (student_id,))

        elif has_transcript_path:

            cursor.execute("SELECT CourseName, Credits, Grade, transcript_path FROM course_grades WHERE StuID=%s", (student_id,))

        else:

            cursor.execute("SELECT CourseName, Credits, Grade FROM course_grades WHERE StuID=%s", (student_id,))

        grades = cursor.fetchall() or []
        

        # 檢查是否有成績單圖片

        has_transcript_image = False

        for grade in grades:

            if grade.get('ProofImage') or grade.get('transcript_path'):

                has_transcript_image = True

                break
        

        # 3. 獲取證照（使用完整的 JOIN 查詢，獲取證照完整資訊）

        # 先檢查證照圖片路徑欄位的實際名稱

        try:

            cursor.execute("SHOW COLUMNS FROM student_certifications")

            cert_columns = {row["Field"] for row in cursor.fetchall()}

            has_cert_path = 'CertPath' in cert_columns

            has_cert_photo_path = 'CertPhotoPath' in cert_columns
            

            # 選擇正確的圖片路徑欄位名稱

            cert_path_field = 'CertPath' if has_cert_path else ('CertPhotoPath' if has_cert_photo_path else None)

        except:

            cert_path_field = None
        

        try:

            # 構建 SELECT 語句，根據實際欄位動態選擇

            cert_path_select = f"sc.{cert_path_field} AS CertPath" if cert_path_field else "NULL AS CertPath"
            

            # 嘗試使用完整的 JOIN 查詢（包含證照名稱、類別、取得日期等）

            cursor.execute(f"""
                SELECT

                    sc.CertName AS CertName,

                    sc.CertType AS CertType,

                    {cert_path_select},

                    sc.AcquisitionDate AS AcquisitionDate,

                    sc.cert_code AS cert_code,

                    CONCAT(COALESCE(cc.job_category, ''), COALESCE(cc.level, '')) AS cert_name_from_code,

                    cc.category AS cert_category,

                    ca.name AS authority_name

                FROM student_certifications sc

                LEFT JOIN certificate_codes cc 

                    ON sc.cert_code COLLATE utf8mb4_unicode_ci = cc.code COLLATE utf8mb4_unicode_ci

                LEFT JOIN cert_authorities ca 

                    ON cc.authority_id = ca.id

                WHERE sc.StuID = %s

                ORDER BY sc.AcquisitionDate DESC, sc.id ASC

            """, (student_id,))

            cert_rows = cursor.fetchall() or []
            

            # 轉換為統一格式

            certifications = []

            for row in cert_rows:

                # 優先使用 JOIN 結果的證照名稱，否則使用原始欄位

                cert_name = row.get('cert_name_from_code', '').strip() or row.get('CertName', '').strip()

                cert_type = row.get('cert_category', '').strip() or row.get('CertType', '').strip()

                cert_path = row.get('CertPath', '').strip() or ''

                acquisition_date = row.get('AcquisitionDate', '')
                

                if cert_name:

                    certifications.append({

                        'CertName': cert_name,

                        'CertType': cert_type if cert_type else '其他',

                        'CertPath': cert_path,

                        'AcquisitionDate': acquisition_date,

                        'AuthorityName': row.get('authority_name', '').strip()

                    })

        except Exception as e:

            # 如果 JOIN 查詢失敗，使用簡單查詢

            print(f"⚠️ 證照完整查詢失敗，使用簡單查詢: {e}")

            try:

                # 先檢查可用欄位

                if cert_path_field:

                    cursor.execute(f"SELECT CertName, CertType, {cert_path_field} AS CertPath, AcquisitionDate FROM student_certifications WHERE StuID=%s", (student_id,))

                else:

                    cursor.execute("SELECT CertName, CertType, AcquisitionDate FROM student_certifications WHERE StuID=%s", (student_id,))

                cert_rows = cursor.fetchall() or []

                certifications = []

                for row in cert_rows:

                    if row.get('CertName'):

                        certifications.append({

                            'CertName': row.get('CertName', '').strip(),

                            'CertType': row.get('CertType', '').strip() or '其他',

                            'CertPath': row.get('CertPath', '').strip() if cert_path_field else '',

                            'AcquisitionDate': row.get('AcquisitionDate', ''),

                            'AuthorityName': ''

                        })

            except Exception as e2:

                print(f"⚠️ 簡單查詢也失敗: {e2}")

                certifications = []
        

        # 4. 獲取語言能力

        cursor.execute("SELECT Language, Level FROM student_languageskills WHERE StuID=%s", (student_id,))

        languages = cursor.fetchall() or []
        

        # 5. 整理履歷重點文字（從資料庫讀取的完整履歷資料）

        resume_parts = []
        

        # 基本資訊區塊（從 Student_Info 資料表讀取）

        basic_info = []

        if student_info:

            # 檢查並加入所有可能的欄位

            if student_info.get('Major'):

                basic_info.append(f"主修領域：{student_info.get('Major')}")

            if student_info.get('Skills'):

                skills = str(student_info.get('Skills', '')).strip()

                if skills:

                    basic_info.append(f"技能專長：{skills}")
        

        if basic_info:

            resume_parts.append("【基本資訊（從資料庫 Student_Info 表讀取）】\n" + "\n".join(basic_info))
        

        # 證照區塊（從 student_certifications 資料表讀取，包含取得日期等完整資訊）

        if certifications:

            cert_list = []

            for c in certifications:

                cert_name = c.get('CertName', '').strip()

                cert_type = c.get('CertType', '').strip()

                acquisition_date = c.get('AcquisitionDate', '')

                authority_name = c.get('AuthorityName', '').strip()
                

                if cert_name:

                    cert_info = f"  - {cert_name}"

                    if cert_type:

                        cert_info += f" ({cert_type})"

                    if authority_name:

                        cert_info += f" - 發證單位：{authority_name}"

                    if acquisition_date:

                        cert_info += f" - 取得日期：{acquisition_date}"

                    cert_list.append(cert_info)

            if cert_list:

                resume_parts.append("【證照資格（從資料庫 student_certifications 表讀取，包含證照名稱、類別、發證單位、取得日期等完整資訊）】\n" + "\n".join(cert_list))
        

        # 語言能力區塊（從 student_languageskills 資料表讀取）

        if languages:

            lang_list = []

            for l in languages:

                lang = l.get('Language', '').strip()

                level = l.get('Level', '').strip()

                if lang:

                    if level:

                        lang_list.append(f"  - {lang}：{level}")

                    else:

                        lang_list.append(f"  - {lang}")

            if lang_list:

                resume_parts.append("【語言能力（從資料庫 student_languageskills 表讀取）】\n" + "\n".join(lang_list))
        

        # 自傳區塊（從 Student_Info 資料表的 Autobiography 欄位讀取 - AI 分析重點）

        if student_info and student_info.get('Autobiography'):

            autobiography = str(student_info.get('Autobiography', '')).strip()

            if autobiography:

                # 保留完整自傳內容（從資料庫讀取，不截斷以確保完整性）

                # 如果太長，最多保留2000字以確保分析品質

                if len(autobiography) > 2000:

                    autobiography = autobiography[:2000] + "..."

                resume_parts.append("【自傳內容（從資料庫 Student_Info.Autobiography 欄位讀取 - AI 分析重點，請優先引用此內容）】\n" + autobiography)
        

        # 加入證照圖片說明（如果有的話）

        cert_images_count = sum(1 for c in certifications if c.get('CertPath'))

        if cert_images_count > 0:

            resume_parts.append(f"【證照圖片說明】\n學生已上傳 {cert_images_count} 張證照圖片至資料庫系統中，證照資料已完整記錄。")
        

        resume_text = "\n\n".join(resume_parts) if resume_parts else ""
        

        # 6. 整理學業成績摘要（從 course_grades 資料表讀取的完整課程資訊）

        grades_parts = []
        
        # 計算專業核心科目平均成績（從 course_grades 表的 Grade 欄位）
        core_course_total_score = 0.0  # 專業核心科目總分
        core_course_count = 0  # 專業核心科目數量

        # 計算 GPA（如果有成績資料）

        if grades:

            grade_points = {'A+': 4.3, 'A': 4.0, 'A-': 3.7, 'B+': 3.3, 'B': 3.0, 'B-': 2.7, 

                           'C+': 2.3, 'C': 2.0, 'C-': 1.7, 'D': 1.0, 'F': 0.0}

            total_points = 0

            total_credits = 0
            

            excellent_courses = []  # A以上

            good_courses = []  # B+和B

            all_courses_list = []  # 所有課程（用於完整分析）

            for grade in grades:

                course_name = grade.get('CourseName', '').strip()

                if not course_name:
                    continue
                    
                raw_credits = str(grade.get('Credits', '0'))
                if '/' in raw_credits:
                    # 如果看到 '3/3'，就只取第一個 '3'
                    raw_credits = raw_credits.split('/')[0]

                try:
                    credits = float(raw_credits or 0)
                except ValueError:
                    credits = 0.0 # 萬一還是轉失敗，給個預設值

                grade_str = str(grade.get('Grade', '')).strip().upper()
                
                # 計算專業核心科目平均成績（處理數字成績）
                grade_value = grade.get('Grade', '').strip()
                if grade_value:
                    try:
                        # 嘗試將成績轉換為數字（處理數字成績如 90, 88）
                        numeric_grade = float(grade_value)
                        if 0 <= numeric_grade <= 100:  # 確保是有效的成績範圍
                            core_course_total_score += numeric_grade
                            core_course_count += 1
                    except (ValueError, TypeError):
                        # 如果不是數字成績，可能是等級成績（如 A, B+），跳過數字計算
                        pass
                

                if credits > 0 and grade_str in grade_points:

                    total_points += grade_points[grade_str] * credits

                    total_credits += credits
                

                # 分類課程

                if grade_str in ['A+', 'A', 'A-']:

                    excellent_courses.append(f"{course_name} ({grade_str})")

                elif grade_str in ['B+', 'B']:

                    good_courses.append(f"{course_name} ({grade_str})")
                

                # 記錄所有課程（用於 AI 分析）

                if grade_str in grade_points:

                    all_courses_list.append(f"{course_name}: {grade_str}")
            

            if total_credits > 0:

                gpa = total_points / total_credits

                grades_parts.append(f"GPA: {gpa:.2f}/4.3")
            

            if excellent_courses:

                grades_parts.append(f"優秀課程成績（A以上）：{', '.join(excellent_courses[:8])}")  # 最多顯示8個
            

            if good_courses:

                grades_parts.append(f"良好課程成績（B以上）：{', '.join(good_courses[:5])}")  # 最多顯示5個
            

            # 加入所有課程列表（供 AI 深度分析使用 - 對應履歷中的「已修習專業核心科目」表格）

            if all_courses_list:

                grades_parts.append(f"\n完整課程列表（從資料庫 course_grades 資料表讀取，對應履歷中的「已修習專業核心科目」）：\n" + "\n".join(all_courses_list[:50]))  # 增加到最多50門課程，確保包含所有專業核心科目
        

        # 加入專業核心科目平均成績（從 course_grades 資料表的 Grade 欄位讀取）

        if core_course_count > 0:

            core_course_avg_score = core_course_total_score / core_course_count

            grades_parts.append(f"專業核心科目平均成績（從資料庫 course_grades 資料表的 Grade 欄位讀取）：{core_course_avg_score:.2f} 分（共 {core_course_count} 門專業核心科目）")
        

        # 說明成績單圖片狀態（從資料庫讀取）

        if has_transcript_image:

            grades_parts.append("成績單圖片：已上傳至資料庫（可於系統中查看）")
        

        grades_text = "\n".join(grades_parts) if grades_parts else ""
        

        # 取得所有公司和職缺（與 fill_preferences 頁面保持一致）

        # 先檢查是否有公司

        cursor.execute("SELECT COUNT(*) as count FROM internship_companies")

        company_count = cursor.fetchone().get('count', 0)
        

        if company_count == 0:

                return jsonify({

                    "success": False,

                "error": "目前系統中沒有任何公司資料，請聯繫管理員新增公司。"

                }), 400
        

        # 取得本學期開放的公司和職缺（只顯示已審核通過且在當前學期開放的公司）

        from semester import get_current_semester_code

        current_semester_code = get_current_semester_code(cursor)
        

        if current_semester_code:

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

                INNER JOIN company_openings co ON ic.id = co.company_id

                JOIN internship_jobs ij ON ic.id = ij.company_id

                WHERE ic.status = 'approved'

                  AND co.semester = %s

                  AND co.is_open = TRUE

                  AND ij.is_active = TRUE

                ORDER BY ic.company_name, ij.title

            """, (current_semester_code,))

        else:

            # 如果沒有設定當前學期，返回空列表

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

                WHERE 1=0

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


        # 根據是否有履歷資料來構建不同的 prompt

        has_resume_data = bool(resume_text.strip())

        has_grades_data = bool(grades_text.strip())
        

        if has_resume_data or has_grades_data:

            # 檢查是否有自傳內容

            has_autobiography = '【自傳內容' in resume_text or '自傳內容' in resume_text
            

            # 有履歷或成績資料時的 prompt

            resume_section = f"""

【學生履歷重點（系統已自動從資料庫中讀取的完整履歷資料）】

資料來源說明：

- 所有資料都是從資料庫中直接查詢取得，代表學生的真實履歷記錄

- 基本資訊和自傳：來自 Student_Info 資料表（包含 Autobiography 自傳欄位）

- 證照：來自 student_certifications 資料表

- 語言能力：來自 student_languageskills 資料表


{resume_text if has_resume_data else "（履歷資料較少，主要參考成績資料）"}
"""
            

            if has_autobiography:

                resume_section += "\n⚠️ **特別提醒**：上述履歷中包含【自傳內容】區塊（從資料庫 Student_Info.Autobiography 欄位讀取），這是學生在系統中填寫的真實自傳內容。請優先分析自傳中的興趣、經驗、動機和目標，並在推薦理由中明確引用自傳的具體內容。\n"
            

            grades_section = f"""

【學業成績摘要（系統已自動從資料庫 course_grades 資料表讀取）】

資料來源說明：

- 所有成績資料都是從資料庫 course_grades 資料表中直接查詢取得

- 包含課程名稱、學分數、成績等級等完整資訊


{grades_text if has_grades_data else "（成績資料較少，主要參考履歷資料）"}
"""
            

            task_requirements = """

【任務要求】

1. **嚴格要求**：你已經獲得上述【學生履歷重點】和【學業成績摘要】的完整資料。這些資料都是系統自動從資料庫中直接查詢取得的真實記錄：

   - **履歷資料來源**（必須引用）：

     * Student_Info 資料表：基本資訊、自傳內容（Autobiography 欄位）- **這是推薦理由的核心依據**

     * student_certifications 資料表：證照資格、取得日期、發證單位 - **必須在推薦理由中明確引用證照資訊**

     * student_languageskills 資料表：語言能力

   - **成績單資料來源**（必須引用）：

     * course_grades 資料表：完整課程成績、GPA計算、課程表現 - **必須在推薦理由中明確引用成績單中的課程表現**

   這些都是學生在系統中填寫和儲存的真實履歷資料，你必須基於這些**資料庫中的實際資料**進行分析。
   

   **重要**：每個推薦理由都必須綜合引用以下三類資料：

   - **履歷（特別是自傳內容）**：學生的興趣、經驗、技能、動機

   - **成績單**：相關課程成績、GPA表現、學習能力

   - **證照**：證照資格、取得日期、相關技能認證
   

   推薦理由必須明確指出這三類資料如何共同支持該職缺的適合度。

2. **履歷為本的分析原則（嚴格遵守）**：

   - 所有推薦理由必須**直接引用**履歷、成績單和證照中的具體資料內容

   - **不要使用介紹性語句**：不要說「根據履歷...」、「履歷顯示...」、「從履歷中可以看到...」等，直接引用資料內容

   - **絕對禁止推測或假設**：不能使用「可見」、「可能」、「應該」、「推測」、「或許」等推測性詞彙

   - **只能引用履歷中明確提到的內容**：如果履歷中沒有提到「專案」、「個人研究」、「課程專案」等，絕對不能說「從其課程專案或個人研究中可見」

   - 如果履歷中沒有相關內容，就只引用履歷中實際存在的內容，不要推測或補充

   - 推薦理由必須基於履歷中的實際資料，不能使用假設或推測

3. **自傳分析優先原則**：如果履歷中包含【自傳內容】區塊，你必須優先分析自傳內容，深入理解學生的興趣、經驗、動機和職涯目標，並在推薦理由中優先引用自傳中的具體描述。

4. **絕對禁止**：在任何推薦理由中，絕對不要提到「未提供履歷資料」、「未提供成績資料」、「由於未提供...」或類似字眼。這些資料已經提供給你了。

3. **深度分析要求**（按優先順序）：

   - **自傳內容深度分析（最高優先級）**：

     * 仔細閱讀【學生履歷重點】中的「【自傳內容】」區塊，深入分析自傳中提到的：

       - 學生的興趣領域和專業方向

       - 過往相關經驗或專案

       - 學習動機和職涯目標

       - 個人特質和能力描述

       - 對特定技術或領域的熱忱

     * 將自傳內容與職缺描述進行匹配，找出：

       - 自傳中提到的技能、技術與職缺要求的關聯

       - 自傳中表達的興趣與職缺領域的匹配

       - 自傳中描述的能力與職缺需求的對應

     * **如果自傳有相關內容，必須優先引用自傳中的具體描述**
   

   - **技能匹配分析**：仔細比對職缺描述中提到的技能要求（如程式語言、工具、技術等），與學生履歷中的「技能」、「主修」、「證照」進行匹配。
   

   - **成績單分析（必須明確引用）**：

     * 分析【學業成績摘要】中的「完整課程列表」，找出與職缺要求相關的課程（例如：職缺要求 Java，就尋找學生修過的 Java 相關課程）

     * 優先引用「優秀課程成績（A以上）」和「良好課程成績（B以上）」中與職缺相關的課程

     * 明確指出學生在哪些具體課程中表現優秀，獲得的成績等級，以及這些課程成績如何證明學生的相關能力

     * 引用 GPA 數據說明學生的整體學習表現

     * 必須在推薦理由中明確引用課程名稱和成績，例如：「根據成績單，學生在[課程名稱]課程獲得[成績]，展現了[相關能力]，這與職缺要求的[具體需求]相關...」

     * **重要**：只能說成績單中明確列出的課程和成績，不能推測「從課程專案中可見」等履歷中沒有的內容
   

   - **證照匹配（必須明確引用）**：

     * 仔細比對學生擁有的證照（從【學生履歷重點】中的【證照資格】區塊）與職缺描述中的證照要求

     * 明確指出學生擁有哪些證照，證照的取得日期，以及這些證照如何符合職缺需求

     * 必須在推薦理由中明確引用證照的全名、類別、發證單位等資訊

     * 例如：「根據履歷，學生擁有[證照全名]（[證照類別]），取得日期為[日期]，發證單位為[單位]，這與職缺要求的[具體需求]高度匹配...」
   

   - **語言能力匹配**：如果職缺有語言要求，明確指出學生的語言能力等級。
   

   - **綜合評估**：結合 GPA、專業核心科目平均成績、整體表現，說明學生的學習態度和能力。

4. **推薦理由撰寫規範**（必須嚴格遵守）：

   - **每個推薦理由必須基於履歷資料，並包含以下結構**：
     

     a) **直接引用履歷資料**（不要說「根據履歷」，直接引用內容）：

        * 直接說：「學生主修[主修領域]，具備[具體技能]技能...」

        * 直接說：「學生在自傳中提及[自傳中的具體內容，如：對網頁開發有高度興趣/曾參與...專案/希望學習...技術]...」

        * **不要使用「根據履歷」、「履歷顯示」、「從履歷中可以看到」等介紹性語句，直接引用資料內容**
     

     b) **優先引用自傳內容**（如果有自傳資料，必須優先引用，直接引用不要介紹）：

        * 直接說：「學生在自傳中提到[自傳中的具體內容，如：對網頁開發有高度興趣/曾參與...專案/希望學習...技術]...」

        * 直接說：「學生表達[具體的興趣、經驗或目標]，與此職缺的[職缺特色]高度相關...」

        * **直接引用自傳內容，不要說「根據學生自傳」或「自傳內容顯示」**
     

     c) **直接引用三類資料**（每個推薦理由都必須包含，不要介紹直接引用）：

        * **履歷資料（特別是自傳）**：直接說「學生提到[具體內容]...」或「學生具備[具體技能名稱]技能...」

        * **成績單資料**：直接說「學生在[具體課程名稱]課程獲得[成績]，展現了[相關能力]，這與職缺要求的[具體需求]相關...」或「學生的 GPA 為[具體數值]，在[相關課程]方面表現優秀...」

        * **證照資料**：直接說「學生擁有[證照全名]（[證照類別]），取得日期為[日期]，發證單位為[單位]，這證明其[相關能力]，與職缺要求高度匹配...」
        

        **重要**：不要使用「根據履歷」、「根據成績單」、「根據證照資料」等介紹性語句，直接引用資料內容。
     

     d) **具體比對職缺要求**（直接引用資料，不要介紹）：

        * 直接說：「此職缺明確要求[職缺要求]，而學生在自傳中提到[相關經驗/興趣]，並在[課程/證照/技能]方面表現優異...」

        * 直接說：「職缺描述提到需要[技能/知識]，學生在自傳中表達對此領域的[興趣/經驗]，且通過[課程/證照]已具備此能力...」

        * 直接說：「學生的[具體背景]與職缺要求的[具體需求]高度匹配...」
     

     e) **明確說明學生為什麼適合此職缺**（必須明確展示適合度，直接說明不要介紹）：

        * **開頭明確說明適合度**：直接說：「學生非常適合此職缺，因為...」或「學生的背景與此職缺高度匹配，主要原因包括...」

        * 直接說：「學生在自傳中展現的[具體特質/興趣/目標]與此職缺的要求高度吻合，[具體說明匹配點]...」

        * 直接說：「此職缺將能讓學生實現自傳中提到的[職涯目標/學習期望]，並進一步深化[相關技能]，因此非常適合...」

        * 直接說：「綜合學生的[自傳內容]、[課程成績]、[證照資格]等資料，學生非常適合此職缺，因為[具體說明理由]...」

        * **結尾強調適合度**：每個推薦理由的結尾都應該明確說明「因此，學生非常適合此職缺」或「綜上所述，學生的[具體背景]使其成為此職缺的理想人選」
   

   - **重要規則（嚴格遵守）**：

     * **所有推薦理由必須直接引用履歷、成績單和證照中的資料內容，不要使用「根據履歷」、「從履歷中可以看到」、「履歷顯示」等介紹性語句**

     * **絕對禁止推測性描述**：不能使用「可見」、「可能」、「應該」、「推測」、「或許」、「從...中可見」、「從...中可以看出」等推測性詞彙

     * **只能引用履歷中明確提到的內容**：

       - 如果履歷中沒有提到「專案」，不能說「從其專案中可見」

       - 如果履歷中沒有提到「個人研究」，不能說「從其個人研究中可見」

       - 如果履歷中沒有提到「課程專案」，不能說「從其課程專案中可見」

       - 直接引用履歷中實際存在的內容，例如：直接說「學生具備 Java 技能」、「學生在自傳中提到對網頁開發有興趣」，不要說「履歷顯示」或「根據履歷」

     * **如果有自傳內容，必須優先引用自傳中的具體描述，並將自傳內容作為推薦理由的核心依據**

     * **每個推薦理由必須綜合引用履歷、成績單和證照三類資料**：

       - 至少引用1項履歷資料（優先引用自傳內容）

       - 至少引用1項成績單資料（具體課程名稱和成績）

       - 至少引用1項證照資料（證照名稱、類別、取得日期等）

     * **直接引用資料內容，不要使用「根據履歷」、「根據成績單」、「根據證照資料」等介紹性語句**

     * 例如：直接說「學生在自傳中提到...」而不是「根據履歷中的自傳內容，學生提到...」

     * 必須具體指出履歷、成績單和證照中的具體內容，不能使用模糊的表述

     * 絕對不要以「未提供資料」或「無法確認」作為理由

     * 如果某些資料較少，就深入分析現有的資料，特別是自傳內容，找出與職缺的匹配點，但不要推測履歷中沒有的內容

     * **每個推薦理由都必須明確展示學生的適合度**：

       - 開頭明確說明「學生非常適合此職缺」或「學生的背景與此職缺高度匹配」

       - 中間詳細說明為什麼適合（引用履歷、成績單、證照資料）

       - 結尾強調適合度，明確說明「因此，學生非常適合此職缺」

     * 推薦理由必須讓讀者清楚地理解「學生為什麼適合這個職缺」

     * **如果履歷中沒有明確提到某項內容，就只說履歷中有的，不要推測或假設**

5. 按適合度排序，推薦最多5個志願（由最適合至較適合）。
"""

        else:

            # 如果完全沒有履歷和成績資料（這種情況應該很少見）

            resume_section = """

【學生履歷重點】

（系統中暫無履歷資料）
"""

            grades_section = """

【學業成績摘要】

（系統中暫無成績資料）
"""

            task_requirements = """

【任務要求】

1. 由於系統中暫時缺少學生的履歷和成績資料，請基於職缺的要求和一般學生的背景進行推薦。

2. 按職缺的熱門程度和一般適合度排序，推薦最多5個志願。

3. 推薦理由可以說明該職缺的一般要求，但不要提及「未提供資料」等字眼，而是說明職缺的特色和發展機會。
"""


        prompt = f"""

### 任務目標

你是專業實習顧問。請根據以下【真實資料庫數據】，為學生推薦最匹配的 5 個實習志願。


### 學生背景資料

1. 【自傳與技能】：{resume_text}

2. 【成績單摘要】：{grades_text}


### 可選公司與職缺

{companies_text}


### 輸出規範 (JSON)

每個 "reason" 必須簡潔地包含以下三點（禁止廢話）：

- 引用自傳中的 [興趣/經驗]。

- 引用成績單中的 [具體科目成績]。

- 引用 [證照名稱]。


請直接輸出 JSON，格式如下：

{{

  "recommendations": [

    {{

      "order": 1,

      "company_id": ID,

      "job_id": ID,

      "company_name": "名稱",

      "job_title": "職稱",

      "reason": "直接引述資料的推薦理由"

    }}

  ]

}}
"""


        print(

            f"🔍 AI 推薦志願序 - "
            f"學生ID: {student_id}, "
            f"履歷長度: {len(resume_text)}, 成績摘要長度: {len(grades_text)}"

        )

        # ========== [DEBUG 開始] 強制印出發送給 API 的原始資料 ==========

        print("\n" + "🔥" * 40
)
        print(f"【DEBUG 資訊：正在為學號 {student_id} 產生推薦】")

        print(f"1. 抓取到的自傳文字 (來自 Autobiography 欄位):")

        print(f"   >>> {resume_text if resume_text else '❌ 沒抓到資料 (空值)'}")

        print("-" * 40)

        print(f"2. 抓取到的成績資料:")

        print(f"   >>> {grades_text if grades_text else '❌ 沒抓到成績'}")

        print("-" * 40)

        print(f"3. 準備匹配的職缺清單 (前 200 字):")

        print(f"   >>> {companies_text[:200]}...")

        print("🔥" * 40 + "\n"
)
        # ========== [DEBUG 結束] ======================================

        response = model.generate_content(

            prompt,

            generation_config={

                "response_mime_type": "application/json",

                "temperature": 0.2  # 調低隨機性，讓推薦更嚴謹

            }

        )

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
        
        error_str = str(e)
        
        # 檢查是否為配額限制錯誤（429）
        if "429" in error_str or "quota" in error_str.lower() or "Quota exceeded" in error_str:
            # 嘗試提取重試時間
            retry_seconds = None
            if "retry in" in error_str.lower() or "retry_delay" in error_str.lower():
                retry_match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
                if retry_match:
                    retry_seconds = int(float(retry_match.group(1)))
            
            error_message = "AI 服務目前使用量已達上限（免費層級每日限制 20 次）"
            if retry_seconds:
                error_message += f"，請在 {retry_seconds} 秒後再試"
            else:
                error_message += "，請稍後再試或明天再使用此功能"
            
            return jsonify({
                "success": False, 
                "error": error_message,
                "error_type": "quota_exceeded",
                "retry_after": retry_seconds
            }), 429
        
        return jsonify({"success": False, "error": f"AI 服務處理失敗: {str(e)}"}), 500

    finally:

        if cursor:

            cursor.close()

        if conn:

            conn.close()



# ==========================================================

# API：更新自傳內容

# ==========================================================

@ai_bp.route('/api/update_autobiography', methods=['POST'])

def update_autobiography():
    """

    將 AI 美化後的自傳更新至資料庫
    """

    # 權限檢查

    if 'user_id' not in session or session.get('role') != 'student':

        return jsonify({"success": False, "message": "只有學生可以使用此功能。"}), 403
    

    user_id = session['user_id']

    conn = None

    cursor = None
    

    try:

        data = request.get_json()

        autobiography = data.get('autobiography', '').strip()
        

        if not autobiography:

            return jsonify({"success": False, "message": "自傳內容不能為空。"}), 400
        

        conn = get_db()

        cursor = conn.cursor(dictionary=True)
        

        # 獲取學號

        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))

        user_result = cursor.fetchone()

        if not user_result:

            return jsonify({"success": False, "message": "找不到使用者資訊。"}), 404
        

        student_id = user_result["username"]
        

        # 更新自傳（使用 ON DUPLICATE KEY UPDATE 確保如果記錄不存在則創建）

        cursor.execute("""

            INSERT INTO Student_Info (StuID, Autobiography, UpdatedAt)

            VALUES (%s, %s, NOW())

            ON DUPLICATE KEY UPDATE

                Autobiography = VALUES(Autobiography),

                UpdatedAt = NOW()

        """, (student_id, autobiography))
        

        conn.commit()
        

        print(f"✅ 自傳已更新 - 學生ID: {student_id}, 長度: {len(autobiography)}")

        return jsonify({"success": True, "message": "自傳已成功更新。"})
        

    except Exception as e:

        traceback.print_exc()

        if conn:

            conn.rollback()

        return jsonify({"success": False, "message": f"更新失敗: {str(e)}"}), 500

    finally:

        if cursor:

            cursor.close()

        if conn:

            conn.close()