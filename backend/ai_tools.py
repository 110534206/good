import os
import google.generativeai as genai
# ⚠️ 修正：新增 render_template, redirect, url_for 以支援頁面路由
from flask import Blueprint, request, Response, jsonify, session, render_template, redirect, url_for
from config import get_db # 假設 config.py 存在
import json
import traceback
# 使用 pypdf 提高對 PDF 檔案錯誤的容錯性
from pypdf import PdfReader, errors as pypdf_errors 

# --- 初始化 AI Blueprint ---
ai_bp = Blueprint('ai_bp', __name__)

# --- 初始化 Google GenAI ---
api_key = os.getenv('GEMINI_API_KEY')

if not api_key:
    print("AI 模組警告：在環境變數中找不到 GEMINI_API_KEY。")
    model = None
else:
    genai.configure(api_key=api_key)
    # 使用 genai.Client() 並設置 model_name
    model = genai.GenerativeModel('gemini-2.5-flash')

# ==========================================================
# 🧠 系統提示詞（System Prompt for Job Recommendation）
# 專用於實習推薦功能
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
# 🧠 履歷修改系統提示詞（System Prompt for Resume Revision）
# 專用於 /api/revise-resume 功能
# ==========================================================
REVISE_PROMPT = """
你是一位專業的履歷撰寫師，專長是將用戶貼上的履歷草稿修改得更具專業性和吸引力。
請在所有回覆中遵守以下原則：
1. 僅回覆修改後的履歷文本，禁止加入任何開頭、結尾、解釋、或標題（如「修改後的履歷：」）。
2. 保持內容的真實性，不虛構技能或經驗。
3. 根據使用者選擇的「修改任務」和「語氣風格」進行優化。
4. 全程使用繁體中文，可搭配必要的英文專有名詞。
5. 使用清晰的段落和條列式清單（如 `-` 或 `*`）來呈現，但禁止使用 Markdown 標記（如 `**` 或 `##`）。
6. **核心原則：**
    - 調整為更專業、主動的動詞。
    - 強調可量化的成果 (e.g., "提升了 20% 的效率")。
    - 確保語意流暢且結構完整。
"""

# ----------------------------------------------------------
# Helper: 讀取 PDF 履歷文字
# ----------------------------------------------------------
def extract_pdf_text(pdf_path: str) -> str:
    if not pdf_path or not os.path.exists(pdf_path):
        print(f"❗ 找不到履歷檔案：{pdf_path}")
        return ""

    # 關鍵修正：先檢查檔案標頭是否為 PDF，以排除 DOCX/ZIP 誤傳
    try:
        with open(pdf_path, 'rb') as f:
            header = f.read(4) # 讀取前 4 個位元組
            if header != b'%PDF':
                # 判斷是否為 ZIP/DOCX 的標記 (PK\x03\x04)
                if header.startswith(b'PK\x03\x04'):
                    print(f"❌ 檔案格式錯誤: 檔案標頭顯示為 ZIP/DOCX 格式 (標記: {header})，非標準 PDF。")
                    return "ERROR_NOT_A_PDF_DOCX"
                else:
                    print(f"❌ 檔案格式錯誤: 檔案標頭非 PDF (標記: {header})。")
                    return "ERROR_NOT_A_PDF_OTHER"
    except Exception as e:
        print(f"❌ 讀取檔案標頭失敗: {e}")
        return "" # 讀取失敗，回傳空字串

    # 如果通過標頭檢查，則繼續使用 pypdf 解析
    try:
        reader = PdfReader(pdf_path) 
        
        if reader.is_encrypted:
            print(f"❌ PDF 解析失敗：檔案已加密，無法讀取 {pdf_path}")
            return ""
            
        pages_text = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            pages_text.append(page_text.strip())
            
        combined = "\n".join(filter(None, pages_text)).strip()
        if not combined:
            print(f"❗ PDF 解析結果為空：{pdf_path}")
        return combined
        
    except pypdf_errors.PdfReadError as exc: 
        print(f"❌ PDF 解析失敗 (檔案損壞/格式錯誤)：{exc}")
        return ""
    except Exception as exc:
        print(f"❌ PDF 解析失敗 (通用錯誤)：{exc}")
        traceback.print_exc()
        return ""

# ==========================================================
# 🎯 API 0: AI 履歷修改頁面路由 (解決 404 錯誤)
# ==========================================================
@ai_bp.route('/ai_edit_resume')
def ai_edit_resume_page():
    # 檢查使用者是否登入，如果未登入則導向登入頁
    if "username" not in session:
        # 假設您的登入路由註冊在 'auth_bp.login_page'
        return redirect(url_for("auth_bp.login_page"))
        
    # 如果已登入，渲染 HTML 模板
    return render_template('ai_edit_resume.html')


# ==========================================================
# 🎯 API 1: 實習職缺推薦 
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
        transportation_filter = data.get('transportationFilter', 'any')
        distance_filter = data.get('distanceFilter', 'any')
        salary_filter = data.get('salaryFilter', 'any')

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 取得學生最新「審核通過」的履歷檔案
        cursor.execute("""
            SELECT filepath, original_filename
            FROM resumes
            WHERE user_id = %s AND status = 'approved'
            ORDER BY created_at DESC
            LIMIT 1
        """, (student_id,))
        resume_record = cursor.fetchone()

        if not resume_record:
            print(f"❌ 找不到通過審核的履歷 - student_id: {student_id}")
            return jsonify({
                "success": False,
                "error": "尚未找到審核通過的履歷檔案，請先完成上傳與審核再使用 AI 推薦。"
            }), 400

        resume_path = resume_record.get('filepath')
        print(f"🧩 找到履歷: {resume_path}, 狀態: approved")

        # 防呆：檢查檔案存在與副檔名
        if not os.path.exists(resume_path):
            return jsonify({
                "success": False,
                "error": f"履歷檔案不存在: {resume_path}"
            }), 400

        if not resume_path.lower().endswith('.pdf'):
            return jsonify({
                "success": False,
                "error": "履歷檔案不是 PDF 格式，請重新上傳 PDF 檔案。"
            }), 400

        # 解析 PDF
        resume_text = extract_pdf_text(resume_path)
        if not resume_text:
            return jsonify({
                "success": False,
                "error": "無法讀取履歷檔案內容，請確認檔案為可解析的 PDF。"
            }), 400

        print(f"✅ 履歷文字長度: {len(resume_text)} 字元")

        # 避免過長，截斷
        resume_text = resume_text[:6000]

        # 取得公司與職缺資料
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

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": f"AI 服務處理失敗: {str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ==========================================================
# 🎯 API 2: AI 履歷修改 (新功能，支持串流)
# ==========================================================
@ai_bp.route('/api/revise-resume', methods=['POST'])
def revise_resume():
    if not api_key or not model:
        # 回傳 text/plain 錯誤訊息以供前端接收 
        return Response(
            "AI 服務連線失敗：後端 AI 服務未正確配置或無法啟動。",
            status=500,
            mimetype='text/plain' 
        )
    
    try:
        data = request.get_json()
        resume_text = data.get('resumeText', '').strip()
        edit_style = data.get('style', 'polish')
        tone_style = data.get('tone', 'professional')

        if not resume_text:
            return Response("請提供履歷內容。", status=400, mimetype='text/plain')

        # ------------------------------------------------------------------
        # 1. 根據前端選項設定 AI 指令
        # ------------------------------------------------------------------
        style_map = {
            'polish': '任務：進行履歷美化與專業潤飾，將描述轉為更具影響力的行動句。',
            'concise': '任務：將所有文字精簡，去除贅字，讓履歷更為簡潔有力，長度需至少縮短 30%。',
            'keyword_focus': '任務：分析內容，著重強調專業技能、專案成果、和可量化數據，使履歷更符合業界標準。'
        }
        
        tone_map = {
            'professional': '語氣設定：專業、正式、權威。',
            'friendly': '語氣設定：親切、隨和、注重團隊合作與溝通。',
            'cautious': '語氣設定：謹慎、嚴謹、注重細節與風險控管。',
            'academic': '語氣設定：學術、嚴謹、注重研究方法與理論基礎。'
        }

        # 組合完整的使用者指令
        user_instruction = f"""
請根據以下要求修改履歷：
- 修改任務: {style_map.get(edit_style, style_map['polish'])}
- 語氣風格: {tone_map.get(tone_style, tone_map['professional'])}

---
以下是原始履歷草稿：
{resume_text}
"""
        
        # ------------------------------------------------------------------
        # 2. 呼叫 Gemini API 進行串流生成 
        # ------------------------------------------------------------------
        print(f"🔍 AI 履歷修改請求 - 樣式: {edit_style}, 語氣: {tone_style}, 原始長度: {len(resume_text)}")

        # 使用 stream_generate_content 進行串流回覆 (已修正)
        response = model.stream_generate_content(  # <--- 這是正確的函數名稱
            contents=[user_instruction],
            config={"system_instruction": REVISE_PROMPT} # 使用專門的系統提示詞
        )
        
        # ------------------------------------------------------------------
        # 3. 定義串流 Generator
        # ------------------------------------------------------------------
        def stream_generator():
            for chunk in response:
                if chunk.text:
                    yield chunk.text

        # ------------------------------------------------------------------
        # 4. 回傳 Streaming Response
        # ------------------------------------------------------------------
        # 返回 text/plain 讓前端可以解析並顯示串流結果
        return Response(stream_generator(), mimetype='text/plain')

    except Exception as e:
        traceback.print_exc()
        # 處理任何可能發生的錯誤
        return Response(
            f"AI 服務處理失敗，發生內部錯誤: {str(e)}", 
            status=500, 
            mimetype='text/plain'
        )