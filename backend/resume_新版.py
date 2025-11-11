from flask import Blueprint, request, jsonify, session, send_file, render_template
from werkzeug.utils import secure_filename
from config import get_db
from semester import get_current_semester_id
from email_service import send_resume_rejection_email, send_resume_approval_email
from docxtpl import DocxTemplate, InlineImage
from docx.shared import Inches
import os
import traceback
import json
from datetime import datetime

resume_bp = Blueprint("resume_bp", __name__)

# 上傳資料夾設定
UPLOAD_FOLDER = "uploads/resumes"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# -------------------------
# 分數轉等級輔助函式
# -------------------------
def score_to_grade(score):
    try:
        score = int(score)
    except (ValueError, TypeError):
        return '丁'
    if 90 <= score <= 99:
        return '優'
    elif 80 <= score <= 89:
        return '甲'
    elif 70 <= score <= 79:
        return '乙'
    elif 60 <= score <= 69:
        return '丙'
    else:
        return '丁'

# -------------------------
# 語文能力複選框處理輔助函式
# -------------------------
def generate_language_marks(level):
    marks = {'Jing': '□', 'Zhong': '□', 'Lue': '□'}
    level_map = {'精通': 'Jing', '中等': 'Zhong', '略懂': 'Lue'}
    level_key = level_map.get(level)
    if level_key in marks:
        marks[level_key] = '■'
    return marks

# -------------------------
# 權限與工具函式
# -------------------------
def get_user_by_id(cursor, user_id):
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    return cursor.fetchone()

def get_director_department(cursor, user_id):
    cursor.execute("""
        SELECT DISTINCT c.department
        FROM classes c
        JOIN classes_teacher ct ON ct.class_id = c.id
        WHERE ct.teacher_id = %s
        LIMIT 1
    """, (user_id,))
    r = cursor.fetchone()
    return r['department'] if r and r.get('department') else None

def teacher_manages_class(cursor, teacher_id, class_id):
    cursor.execute("""
        SELECT 1 FROM classes_teacher
        WHERE teacher_id = %s AND class_id = %s
        LIMIT 1
    """, (teacher_id, class_id))
    return cursor.fetchone() is not None

def can_access_target_resume(cursor, session_user_id, session_role, target_user_id):
    if session_role == "admin":
        return True
    if session_role == "student":
        return session_user_id == target_user_id
    if session_role == "ta":
        return True

    cursor.execute("SELECT class_id FROM users WHERE id = %s", (target_user_id,))
    u = cursor.fetchone()
    if not u:
        return False
    target_class_id = u.get('class_id')

    if session_role == "class_teacher":
        return teacher_manages_class(cursor, session_user_id, target_class_id)
    if session_role == "director":
        director_dept = get_director_department(cursor, session_user_id)
        if not director_dept:
            return False
        cursor.execute("SELECT c.department FROM classes c WHERE c.id = %s", (target_class_id,))
        cd = cursor.fetchone()
        if not cd:
            return False
        return cd.get('department') == director_dept
    return False

def require_login():
    return 'user_id' in session and 'role' in session

# -------------------------
# 儲存結構化資料
# -------------------------
def save_structured_data(cursor, student_id, data):
    try:
        # 儲存 Student_Info (基本資料)
        cursor.execute("""
            INSERT INTO Student_Info (StuID, StuName, BirthDate, Gender, Phone, Email, Address, ConductScore, Autobiography, PhotoPath)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE 
                StuName=VALUES(StuName), BirthDate=VALUES(BirthDate), Gender=VALUES(Gender),
                Phone=VALUES(Phone), Email=VALUES(Email), Address=VALUES(Address),
                ConductScore=VALUES(ConductScore), Autobiography=VALUES(Autobiography),
                PhotoPath=VALUES(PhotoPath), UpdatedAt=NOW()
        """, (
            student_id, data.get('name'), data.get('birth_date'), data.get('gender'),
            data.get('phone'), data.get('email'), data.get('address'),
            data.get('conduct_score'), data.get('autobiography'), data.get('photo_path')
        ))
        # 儲存 Course_Grades (課程成績)
        cursor.execute("DELETE FROM Course_Grades WHERE StuID=%s", (student_id,))
        
        for c in data.get('courses', []):
            if c.get('name'):
                cursor.execute("""
                    INSERT INTO Course_Grades (StuID, CourseName, Credits, Grade)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        Credits = VALUES(Credits),
                        Grade = VALUES(Grade)
                """, (student_id, c['name'], c.get('credits'), c.get('grade')))

        cursor.execute("DELETE FROM Student_Certifications WHERE StuID=%s", (student_id,))
        for cert in data.get('structured_certifications', []):
            if cert.get('name'):
                cursor.execute("""
                    INSERT INTO Student_Certifications (StuID, CertName, CertType)
                    VALUES (%s, %s, %s)
                """, (student_id, cert['name'], cert.get('type', 'other'))) 

        # 儲存語文能力
        cursor.execute("DELETE FROM Student_LanguageSkills WHERE StuID=%s", (student_id,))
        for lang_skill in data.get('structured_languages', []):
            if lang_skill.get('language') and lang_skill.get('level'):
                cursor.execute("""
                    INSERT INTO Student_LanguageSkills (StuID, Language, Level)
                    VALUES (%s, %s, %s)
                """, (student_id, lang_skill['language'], lang_skill['level']))

        return True
    except Exception as e:
        print("❌ 儲存結構化資料錯誤:", e)
        return False

# -------------------------
# 取回學生資料 (for 生成履歷)
# -------------------------
def get_student_info_for_doc(cursor, student_id):
    data = {}
    cursor.execute("SELECT * FROM Student_Info WHERE StuID=%s", (student_id,))
    data['info'] = cursor.fetchone() or {}
    cursor.execute("SELECT CourseName, Credits, Grade FROM Course_Grades WHERE StuID=%s", (student_id,))
    data['grades'] = cursor.fetchall() or []
    cursor.execute("SELECT CertName, CertType FROM Student_Certifications WHERE StuID=%s", (student_id,))
    data['certifications'] = cursor.fetchall() or []
    
    # 讀取語文能力資料 
    cursor.execute("SELECT Language, Level FROM Student_LanguageSkills WHERE StuID=%s", (student_id,))
    data['languages'] = cursor.fetchall() or [] 
    
    return data

# -------------------------
# Word 生成邏輯
# -------------------------
def generate_application_form_docx(student_data, output_path):
    try:
        base_dir = os.path.dirname(__file__)
        template_path = os.path.abspath(os.path.join(base_dir, "..", "frontend", "static", "examples", "實習履歷(空白).docx"))
        if not os.path.exists(template_path):
            print("❌ 找不到模板：", template_path)
            return False

        doc = DocxTemplate(template_path)
        info = student_data.get("info", {})
        grades = student_data.get("grades", [])
        certs = student_data.get("certifications", [])

        # -------------------------
        # 出生日期格式化
        # -------------------------
        def fmt_date(val):
            if hasattr(val, 'strftime'):
                return val.strftime("%Y-%m-%d")
            if isinstance(val, str) and len(val) >= 10:
                return val.split("T")[0]
            return ""

        bdate = fmt_date(info.get("BirthDate"))
        year, month, day = ("", "", "")
        if bdate:
            try:
                year, month, day = bdate.split("-")
            except:
                pass

        # -------------------------
        # 插入照片
        # -------------------------
        image_obj = None
        photo_path = info.get("PhotoPath")
        if photo_path and os.path.exists(photo_path):
            try:
                abs_photo_path = os.path.abspath(photo_path)
                image_obj = InlineImage(doc, abs_photo_path, width=Inches(1.2))
            except Exception as e:
                print(f"⚠️ 圖片載入錯誤: {e}")

        # -------------------------
        # 【修正】插入成績單圖片 (transcript_path)
        # -------------------------
        transcript_obj = None
        transcript_path = info.get("TranscriptPath")
        
        if transcript_path and os.path.exists(transcript_path):
            try:
                abs_transcript_path = os.path.abspath(transcript_path)
                # 設定圖片寬度，這裡使用 Inches(6)
                transcript_obj = InlineImage(doc, abs_transcript_path, width=Inches(6))
            except Exception as e:
                print(f"⚠️ 成績單圖片載入錯誤 (請確保它是圖片檔案): {e}")


        # -------------------------
        # 操行等級（優甲乙丙丁）
        # -------------------------
        conduct_score = info.get('ConductScore', '')
        conduct_marks = {k: '□' for k in ['C_You', 'C_Jia', 'C_Yi', 'C_Bing', 'C_Ding']}
        mapping = {'優': 'C_You', '甲': 'C_Jia', '乙': 'C_Yi', '丙': 'C_Bing', '丁': 'C_Ding'}
        if conduct_score in mapping:
            conduct_marks[mapping[conduct_score]] = '■'

        # -------------------------
        # 證照分類 (文本證照列表)
        # -------------------------
        labor_certs, intl_certs, local_certs, other_certs = [], [], [], []
        for cert in certs:
            name = cert.get('CertName', '')
            ctype = cert.get('CertType', '')
            if not name:
                continue
            if ctype == 'labor':
                labor_certs.append(name)
            elif ctype == 'intl':
                intl_certs.append(name)
            elif ctype == 'local':
                local_certs.append(name)
            else:
                other_certs.append(name)

        # 新增輔助函式：將列表擴展到固定長度
        def pad_list(lst, length=5):
            lst = lst[:length]
            lst += [''] * (length - len(lst))
            return lst
        
        # -------------------------
        # 建立 context (模板變數)
        # -------------------------
        context = {
            'StuID': info.get('StuID', ''),
            'StuName': info.get('StuName', ''),
            'BirthYear': year, 'BirthMonth': month, 'BirthDay': day,
            'Gender': info.get('Gender', ''),
            'Phone': info.get('Phone', ''),
            'Email': info.get('Email', ''),
            'Address': info.get('Address', ''),
            'ConductScore': conduct_score,
            'Autobiography': info.get('Autobiography', ''),
            'courses': [{'name': g.get('CourseName', ''), 'credits': g.get('Credits', ''), 'grade': g.get('Grade', '')} for g in grades],
            'Image_1': image_obj,
            # 【修正】將 key 從 'transcript_placeholder' 改為 'transcript_path'
            'transcript_path': transcript_obj
        }

        # 加入操行等級勾選
        context.update(conduct_marks)

        # 加入證照資料 (文本證照)
        for i, val in enumerate(pad_list(labor_certs), 1):
            context[f'LaborCerts_{i}'] = val
        for i, val in enumerate(pad_list(intl_certs), 1):
            context[f'IntlCerts_{i}'] = val
        for i, val in enumerate(pad_list(local_certs), 1):
            context[f'LocalCerts_{i}'] = val  
        for i, val in enumerate(pad_list(other_certs), 1):
            context[f'OtherCerts_{i}'] = val

       # -------------------------
        # 證照圖片與名稱 (最多8個)
        # -------------------------
        MAX_CERTS = 8
        cert_photo_paths = student_data.get("cert_photo_paths", []) # 上傳的圖片路徑清單
        cert_names = student_data.get("cert_names", [])             # 上傳的名稱清單
        
        cert_photo_objs = []
        image_size = Inches(1.5) 
        
        # 準備圖片物件
        for i, path in enumerate(cert_photo_paths[:MAX_CERTS]):
            try:
                if os.path.exists(path):
                    obj = InlineImage(doc, os.path.abspath(path), width=image_size)
                    cert_photo_objs.append(obj)
                else:
                    cert_photo_objs.append('')
            except Exception as e:
                print(f"⚠️ 證照圖片載入錯誤: {e}")
                cert_photo_objs.append('')

        # 將圖片物件和名稱放入 context
        for i in range(MAX_CERTS):
            # 圖片變數 (CertPhotoImages_1 to 8)
            image_key = f'CertPhotoImages_{i+1}'
            context[image_key] = cert_photo_objs[i] if i < len(cert_photo_objs) else ''
            
            # 名稱變數 (CertPhotoName_1 to 8)
            # 使用前端傳入的名稱清單
            name_key = f'CertPhotoName_{i+1}'
            context[name_key] = cert_names[i] if i < len(cert_names) else ''
            
        # -------------------------
        # 語文能力處理
        # -------------------------
        lang_context = {}

        # 1️⃣ 初始化所有欄位為 '□'
        lang_codes = ['En', 'Jp', 'Tw', 'Hk']
        level_codes = ['Jing', 'Zhong', 'Lue']
        for code in lang_codes:
            for level_code in level_codes:
                lang_context[f'{code}_{level_code}'] = '□'  # e.g., En_Jing, Jp_Zhong

        # 2️⃣ 建立對應表
        lang_code_map = {'英語': 'En', '日語': 'Jp', '台語': 'Tw', '客語': 'Hk'}
        level_code_map = {'精通': 'Jing', '中等': 'Zhong', '略懂': 'Lue'}

        # 3️⃣ 根據資料庫數據設定 '■'
        for lang_skill in student_data.get('languages', []):
            lang = lang_skill.get('Language')  # e.g., '英語'
            level = lang_skill.get('Level')    # e.g., '精通'
            lang_code = lang_code_map.get(lang)
            level_code = level_code_map.get(level)
            if lang_code and level_code:
                key = f'{lang_code}_{level_code}'
                if key in lang_context:
                    lang_context[key] = '■'

        context.update(lang_context)

        # -------------------------
        # 套入模板並輸出
        # -------------------------
        doc.render(context)
        doc.save(output_path)
        print(f"✅ 履歷文件已生成: {output_path}")
        return True

    except Exception as e:
        print("❌ 生成 Word 檔錯誤:", e)
        traceback.print_exc()
        return False

# -------------------------
# API：提交並生成履歷
# -------------------------
@resume_bp.route('/api/submit_and_generate', methods=['POST'])
def submit_and_generate_api():
    try:
        # 權限檢查：僅限學生
        if session.get('role') != 'student' or not session.get('user_id'):
            return jsonify({"success": False, "message": "只有學生可以提交"}), 403

        user_id = session['user_id']
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        data = request.form.to_dict()
        courses = json.loads(data.get('courses', '[]'))
        photo = request.files.get('photo')
        transcript_file = request.files.get('transcript_file')
        cert_files = request.files.getlist('cert_photos[]')

        # 【新增】接收證照名稱清單
        cert_names = request.form.getlist('cert_names[]')

        # 1. 圖片檔案類型白名單
        ALLOWED_IMAGE_MIMES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'] 

        # ---------------------
        # 儲存照片
        # ---------------------
        photo_path = None
        if photo and photo.filename:
            # 【新增檢查】
            if photo.mimetype not in ALLOWED_IMAGE_MIMES:
                 return jsonify({"success": False, "message": f"照片檔案格式錯誤 ({photo.mimetype})，請上傳 JPG/PNG/GIF 圖片"}), 400
                  
            filename = secure_filename(photo.filename)
            photo_dir = os.path.join(UPLOAD_FOLDER, "photos")
            os.makedirs(photo_dir, exist_ok=True)
            ext = os.path.splitext(filename)[1]
            new_filename = f"{user_id}_photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            photo_path = os.path.join(photo_dir, new_filename)
            photo.save(photo_path)

        # ---------------------
        # 儲存成績單檔案
        # ---------------------
        transcript_path = None
        if transcript_file and transcript_file.filename:
            # 【新增檢查】
            if transcript_file.mimetype not in ALLOWED_IMAGE_MIMES:
                 return jsonify({"success": False, "message": f"成績單檔案格式錯誤 ({transcript_file.mimetype})，請上傳 JPG/PNG/GIF 圖片"}), 400
                  
            filename = secure_filename(transcript_file.filename)
            transcript_dir = os.path.join(UPLOAD_FOLDER, "transcripts")
            os.makedirs(transcript_dir, exist_ok=True)
            ext = os.path.splitext(filename)[1]
            new_filename = f"{user_id}_transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            transcript_path = os.path.join(transcript_dir, new_filename)
            transcript_file.save(transcript_path)

        # ---------------------
        # 【修正/新增】上傳多張證照圖片與名稱結構化 (處理 cert_names 與 cert_files 的配對)
        # ---------------------
        cert_photo_paths = [] 
        # 【修正】新增結構化證照清單的定義：用於儲存到 Student_Certifications (文本)
        structured_certifications = [] 
        
        cert_dir = os.path.join(UPLOAD_FOLDER, "cert_photos")
        os.makedirs(cert_dir, exist_ok=True)

        # 假設 cert_files (檔案) 和 cert_names (名稱) 數量和順序一致
        # 使用 zip() 迭代，只處理「有效圖片 + 有效名稱」的配對
        cert_iterator = zip(cert_files, cert_names) if cert_files and cert_names else zip([], [])

        for idx, (file, cert_name) in enumerate(cert_iterator, start=1):
            cert_name = cert_name.strip()
            
            # 僅處理有檔案名稱且符合 MIME 類型，且有證照名稱的項目
            if file and file.filename and file.mimetype in ALLOWED_IMAGE_MIMES and cert_name:
                
                ext = os.path.splitext(secure_filename(file.filename))[1]
                # 使用當前的 idx 避免檔名重複
                new_filename = f"{user_id}_cert_{idx}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
                file_path = os.path.join(cert_dir, new_filename)
                
                # 儲存檔案
                file.save(file_path)
                
                # 儲存圖片路徑 (用於 docx 生成與 resumes 資料表)
                cert_photo_paths.append(file_path) 
                
                # 儲存結構化證照資料 (用於 Student_Certifications 資料表)
                # 假設類型為 'other'，這將在 save_structured_data 中使用
                structured_certifications.append({'name': cert_name, 'type': 'other'})
                
            elif file and file.filename and file.mimetype not in ALLOWED_IMAGE_MIMES:
                # 保留原有的錯誤訊息
                print(f"⚠️ 證照檔案格式錯誤已跳過: {file.filename} ({file.mimetype})")
                continue # 跳過不處理
            # 若只有名稱但沒圖片，或只有圖片但沒名稱，則跳過。只處理「有效圖片 + 有效名稱」的配對。

        # ---------------------
        # 語文能力結構化
        # ---------------------
        structured_languages = []
        languages_map = {"en": "英語", "jp": "日語", "tw": "台語", "hk": "客語"}

        for code, lang_name in languages_map.items():
            field_name = f"lang_{code}_level"
            level = data.get(field_name)
            if level:
                structured_languages.append({'language': lang_name, 'level': level})

        # ---------------------
        # 查學生學號
        # ---------------------
        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        result = cursor.fetchone()
        if not result:
            return jsonify({"success": False, "message": "找不到使用者"}), 404
        student_id = result['username']

        # ---------------------
        # 建立結構化資料
        # ---------------------
        structured_data = {
            "name": data.get("name"),
            "birth_date": data.get("birth_date"),
            "gender": data.get("gender"),
            "phone": data.get("phone"),
            "email": data.get("email"),
            "address": data.get("address"),
            "conduct_score": score_to_grade(data.get("conduct_score")), # 儲存轉換後的等級
            "autobiography": data.get("autobiography"),
            "courses": courses,
            "photo_path": photo_path,
            "structured_certifications": structured_certifications, # 【修正】已定義
            "structured_languages": structured_languages,
        }

        # ---------------------
        # 儲存結構化資料至資料庫
        # ---------------------
        if not save_structured_data(cursor, student_id, structured_data):
            conn.rollback()
            return jsonify({"success": False, "message": "資料儲存失敗"}), 500

        # ---------------------
        # 生成履歷 Word 檔案
        # ---------------------
        student_data_for_doc = get_student_info_for_doc(cursor, student_id)
        student_data_for_doc["info"]["PhotoPath"] = photo_path 
        student_data_for_doc["info"]["TranscriptPath"] = transcript_path 
        # 【新增】傳遞成功上傳圖片的證照名稱與路徑
        student_data_for_doc["cert_photo_paths"] = cert_photo_paths
        student_data_for_doc["cert_names"] = [c['name'] for c in structured_certifications] # 從結構化清單中提取名稱

        filename = f"{student_id}_履歷_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        save_path = os.path.join(UPLOAD_FOLDER, filename)

        if not generate_application_form_docx(student_data_for_doc, save_path):
            conn.rollback()
            return jsonify({"success": False, "message": "文件生成失敗"}), 500

        semester_id = get_current_semester_id(cursor)
        # 【修正】新增 cert_photos 欄位
        cursor.execute("""
            INSERT INTO resumes (user_id, semester_id, original_filename, filepath, status, transcript_path, cert_photos, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """, (user_id, semester_id, filename, save_path, 'generated', transcript_path, json.dumps(cert_photo_paths))) 

        conn.commit()
        return jsonify({"success": True, "message": "履歷生成成功"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conn' in locals(): conn.close()
# -------------------------
# 下載履歷
# -------------------------
@resume_bp.route('/api/download_resume/<int:resume_id>', methods=['GET'])
def download_resume(resume_id):
    if not require_login():
        return jsonify({"success": False, "message": "未授權"}), 403
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT filepath, original_filename, user_id FROM resumes WHERE id=%s", (resume_id,))
        r = cursor.fetchone()
        if not r:
            return jsonify({"success": False, "message": "找不到履歷"}), 404
        if not can_access_target_resume(cursor, session['user_id'], session['role'], r['user_id']):
            return jsonify({"success": False, "message": "無權限"}), 403
        if not os.path.exists(r['filepath']):
            return jsonify({"success": False, "message": "檔案不存在"}), 404
        return send_file(r['filepath'], as_attachment=True, download_name=r['original_filename'])
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 下載成績單
# -------------------------
@resume_bp.route("/api/download_transcript/<int:resume_id>")
def download_transcript(resume_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT transcript_path FROM resumes WHERE id=%s", (resume_id,))
    result = cursor.fetchone()
    if not result or not result["transcript_path"]:
        return jsonify({"success": False, "message": "找不到成績單"}), 404

    path = result["transcript_path"]
    if not os.path.exists(path):
        return jsonify({"success": False, "message": "檔案不存在"}), 404

    # 嘗試推斷檔名，如果找不到則使用預設名
    download_name = os.path.basename(path)
    if not download_name or not os.path.splitext(download_name)[1]:
        download_name = f"transcript_{resume_id}.jpg" # 預設檔名
        
    return send_file(path, as_attachment=True, download_name=download_name)

# -------------------------
# 查詢學生履歷列表
# -------------------------
@resume_bp.route('/api/get_my_resumes', methods=['GET'])
def get_my_resumes():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT r.id, r.original_filename, r.status, r.comment, r.note, r.created_at AS upload_time
            FROM resumes r
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
        """, (session['user_id'],))
        resumes = cursor.fetchall()
        for r in resumes:
            if isinstance(r.get('upload_time'), datetime):
                r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "resumes": resumes})
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 頁面路由
# -------------------------
@resume_bp.route('/upload_resume')
def upload_resume_page():
    return render_template('resume/upload_resume.html')

@resume_bp.route('/ai_edit_resume')
def ai_edit_resume_page():
    return render_template('resume/ai_edit_resume.html')