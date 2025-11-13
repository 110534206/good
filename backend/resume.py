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

def score_to_grade(score):
    # 若已經是等第，直接回傳
    if str(score).strip() in ['優', '甲', '乙', '丙', '丁']:
        return str(score).strip()

    # 若是分數才做數字轉換
    try:
        score = int(str(score).strip())
    except (ValueError, TypeError):
        return '丁'

    if score >= 90:
        return '優'
    elif score >= 80:
        return '甲'
    elif score >= 70:
        return '乙'
    elif score >= 60:
        return '丙'
    else:
        return '丁'

# -------------------------
# 語文能力複選框處理輔助函式 (未使用，但保留)
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
    # 假設這是儲存學生基本資料、課程、證照(文本)和語言能力的函式
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

        # 儲存課程 (資料來源已在 submit_and_generate_api 中處理，確保包含 Grade 欄位)
        cursor.execute("DELETE FROM Course_Grades WHERE StuID=%s", (student_id,))
        # 先去除重複的課程名稱，再一次性插入
        seen_course_names = set()
        unique_courses = []

        for c in data.get('courses', []):
          course_name = (c.get('name') or '').strip()
    
        # 確保課程名稱不為空，且不重複
          if course_name and course_name not in seen_course_names:
             unique_courses.append(c)
             seen_course_names.add(course_name)
          elif course_name:
        # 可選的除錯訊息
            print(f"⚠️ 偵測到重複課程名稱並已跳過: {course_name}")

        # 透過去重複後的清單進行插入
        for c in unique_courses:
          cursor.execute("""
        INSERT INTO Course_Grades (StuID, CourseName, Credits, Grade)
        VALUES (%s,%s,%s,%s)
    """, (student_id, c['name'], c.get('credits'), c.get('grade')))


        # 儲存證照 (此處處理的是文本證照)
        cursor.execute("DELETE FROM Student_Certifications WHERE StuID=%s", (student_id,))
        for cert in data.get('structured_certifications', []):
             # 由於前端只上傳圖片名稱，這裡假設所有結構化證照都屬於 'other' 類，但您可能需要調整
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
        traceback.print_exc()
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
        # 假設模板檔案的路徑
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
        # 處理專業核心科目資料
        # -------------------------
        MAX_COURSES = 30
        
        padded_grades = grades[:MAX_COURSES]
        padded_grades += [{'CourseName': '', 'Credits': ''}] * (MAX_COURSES - len(padded_grades))
        
        context_courses = {}
        # 處理專業核心科目資料 (四欄 x 十行 = 30 筆)
        # -------------------------
        MAX_COURSES = 30 # 總格子數設為 40 (4欄 x 10行)
        
        padded_grades = grades[:MAX_COURSES]
        # 填充空白，確保總數為 MAX_COURSES
        padded_grades += [{'CourseName': '', 'Credits': ''}] * (MAX_COURSES - len(padded_grades))
        
        # 將列表轉換為四欄格式 (4欄 X 10行)，並生成 context 變數
        context_courses = {}
        NUM_ROWS = 10 # 10 行
        NUM_COLS = 3  # 4 欄 (Word 表格中的科目+學分組算作 1 欄)

        for i in range(NUM_ROWS): # i 為行索引 (0 to 9)
            for j in range(NUM_COLS): # j 為欄索引 (0 to 3)
                index = i * NUM_COLS + j
                if index < MAX_COURSES:
                    course = padded_grades[index]
                    row_num = i + 1 # 模板變數從 1 開始 (1 to 10)
                    col_num = j + 1 # 模板變數從 1 開始 (1 to 4)
                    
                    # 假設 Word 模板變數為 CourseName_行號_欄號 和 Credits_行號_欄號
                    context_courses[f'CourseName_{row_num}_{col_num}'] = course.get('CourseName', '')
                    context_courses[f'Credits_{row_num}_{col_num}'] = course.get('Credits', '')

        # -------------------------
        # 插入成績單圖片
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
            'ConductScoreNumeric': info.get('ConductScoreNumeric', ''),
            'ConductScore': conduct_score,
            'Autobiography': info.get('Autobiography', ''),
            'Image_1': image_obj,
            'transcript_path': transcript_obj
        }

        # 加入操行等級勾選
        context.update(conduct_marks)

        # 加入課程資料
        context.update(context_courses)

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
        image_size = Inches(3.0) 
        
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
            # 使用傳入的名稱清單
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
            lang = lang_skill.get('Language')   # e.g., '英語'
            level = lang_skill.get('Level')     # e.g., '精通'
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
        #  儲存成績單檔案
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
        # 上傳多張證照圖片 (含 MIME 檢查)
        # ---------------------
        cert_photo_paths = []
        # 注意：這裡使用 getlist('cert_photos[]')，因為您 HTML 中 name="cert_photos[]"
        cert_files = request.files.getlist('cert_photos[]') 

        if cert_files:
          cert_dir = os.path.join(UPLOAD_FOLDER, "cert_photos")
          os.makedirs(cert_dir, exist_ok=True)

        for idx, file in enumerate(cert_files, start=1):
          if file and file.filename:
            # 【新增檢查】
            if file.mimetype not in ALLOWED_IMAGE_MIMES:
                print(f"⚠️ 證照檔案格式錯誤已跳過: {file.filename} ({file.mimetype})")
                continue
                
            ext = os.path.splitext(secure_filename(file.filename))[1]
            new_filename = f"{user_id}_cert_{idx}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            file_path = os.path.join(cert_dir, new_filename)
            file.save(file_path)
            cert_photo_paths.append(file_path)

        # ---------------------
        # 證照結構化 (文本證照列表)
        # ---------------------
        structured_certifications = []
        for cert_type, field_prefix in [
            ('labor', 'labor_cert[]'),
            ('intl', 'international_cert[]'),
            ('local', 'domestic_cert[]'),
            ('other', 'other_cert[]')
        ]:
            for name in request.form.getlist(field_prefix):
                if name.strip():
                    structured_certifications.append({'name': name.strip(), 'type': cert_type})

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
       # 處理「單一」證照圖片上傳（與多圖邏輯合併）
       # ---------------------
        # 這裡的邏輯是將單一證照圖片/名稱插入到現有的多圖列表中
        certificate_image_file = request.files.get('certificate_image')
        certificate_description = request.form.get('certificate_description', '')
        image_path_for_template = None

        if certificate_image_file and certificate_image_file.filename != '' and 'user_id' in session:
            try:
                # 確保圖片儲存子資料夾存在
                cert_folder = os.path.join(UPLOAD_FOLDER, 'certificates')
                os.makedirs(cert_folder, exist_ok=True)
                # 創建一個安全且獨特的檔案名稱
                filename = secure_filename(certificate_image_file.filename)
                file_extension = os.path.splitext(filename)[1] or '.png'
                unique_filename = f"{session['user_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}{file_extension}"
                image_save_path = os.path.join(cert_folder, unique_filename)
                # 儲存檔案
                certificate_image_file.save(image_save_path)
                image_path_for_template = image_save_path
            except Exception as e:
                print(f"❌ 儲存單一證照圖片失敗: {e}")
                traceback.print_exc()
                image_path_for_template = None

        # 將單一證照圖片/名稱插入在最前面
        if image_path_for_template or certificate_description:
            # 確保清單存在
            if cert_photo_paths is None:
                cert_photo_paths = []
            if cert_names is None:
                cert_names = []

            # 插入在最前面，確保 Word 模板會把它放在第一個位置
            cert_photo_paths.insert(0, image_path_for_template or "")
            cert_names.insert(0, certificate_description or "")


        # ---------------------
        # 查學生學號
        # ---------------------
        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        result = cursor.fetchone()
        if not result:
            return jsonify({"success": False, "message": "找不到使用者"}), 404
        student_id = result['username']

        # ---------------------
        # 【重要】處理課程資料：確保 Grade 欄位存在，以便寫入 Course_Grades 表
        # ---------------------
        # 確保每個課程物件都有 Grade 欄位，如果沒有則預設為空字串 ""
        for c in courses:
            c['grade'] = c.get('grade', '') 

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
            "conduct_score": score_to_grade(data.get("conduct_score")),
            "autobiography": data.get("autobiography"),
            "courses": courses,
            "photo_path": photo_path,
            "structured_certifications": structured_certifications,
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
        student_data_for_doc["info"]["ConductScoreNumeric"] = data.get("conduct_score_numeric")
        
        # 【重要修正】傳遞證照圖片路徑與名稱清單
        student_data_for_doc["cert_photo_paths"] = cert_photo_paths
        student_data_for_doc["cert_names"] = cert_names # 傳遞名稱清單
        
        filename = f"{student_id}_履歷_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        save_path = os.path.join(UPLOAD_FOLDER, filename)

        if not generate_application_form_docx(student_data_for_doc, save_path):
            conn.rollback()
            return jsonify({"success": False, "message": "文件生成失敗"}), 500

        semester_id = get_current_semester_id(cursor)
       
        # 【修正】新增 cert_photos 欄位
        cursor.execute("""
            INSERT INTO resumes 
                (user_id, filepath, original_filename, status, semester_id, created_at, cert_photos)
            VALUES (%s, %s, %s, %s, %s, NOW(), %s)
        """, (
            user_id,
            save_path,
            filename,
            'submitted',
            semester_id,
            json.dumps(cert_photo_paths, ensure_ascii=False)
        ))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "履歷已成功提交並生成文件",
            "file_path": save_path,
            "filename": filename
        })

    except Exception as e:
        print("❌ submit_and_generate_api 發生錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"系統錯誤: {str(e)}"}), 500

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
    try:
        cursor.execute("SELECT transcript_path, user_id FROM resumes WHERE id=%s", (resume_id,))
        result = cursor.fetchone()
        
        if not result or not result["transcript_path"]:
            return jsonify({"success": False, "message": "找不到成績單"}), 404
            
        # 權限檢查 (可以根據您的 can_access_target_resume 邏輯來決定是否需要加入)
        # 這裡假設下載成績單也需要權限檢查，如同下載履歷
        if not can_access_target_resume(cursor, session.get('user_id'), session.get('role'), result['user_id']):
            return jsonify({"success": False, "message": "無權限"}), 403

        path = result["transcript_path"]
        if not os.path.exists(path):
            return jsonify({"success": False, "message": "檔案不存在"}), 404

        # 嘗試推斷檔名，如果找不到則使用預設名
        download_name = os.path.basename(path)
        if not download_name or not os.path.splitext(download_name)[1]:
            download_name = f"transcript_{resume_id}.jpg" # 預設檔名
            
        return send_file(path, as_attachment=True, download_name=download_name)
    finally:
        cursor.close()
        db.close()

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
# API：取得標準核心科目
# -------------------------
@resume_bp.route('/api/get_standard_courses', methods=['GET'])
def get_standard_courses():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT course_name AS name, credits 
            FROM standard_courses 
            WHERE is_active = 1 
            ORDER BY order_index
        """)
        courses = cursor.fetchall()
        return jsonify({"success": True, "courses": courses})
    except Exception as e:
        print("❌ 取得標準核心科目錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": "取得標準核心科目失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 儲存學生個人模板
# -------------------------
@resume_bp.route('/api/save_personal_template', methods=['POST'])
def save_personal_template():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        data = request.get_json()
        courses_json = json.dumps(data.get('courses', []), ensure_ascii=False)
        cursor.execute("""
            INSERT INTO templates (template_type, content, display_name, is_active, uploaded_by, uploaded_at)
    VALUES (%s, %s, %s, %s, %s, NOW())
    ON DUPLICATE KEY UPDATE content=VALUES(content), display_name=VALUES(display_name), updated_at=NOW()
""", ('student_custom', courses_json, data.get('display_name', '我的模板'), 1, session['user_id']))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        print("❌ 儲存模板錯誤:", e)
        return jsonify({"success": False, "message": "儲存失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 載入學生個人模板
# -------------------------
@resume_bp.route('/api/load_personal_template', methods=['GET'])
def load_personal_template():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 1️⃣ 抓標準課程
        cursor.execute("""
            SELECT course_name AS name, credits 
            FROM standard_courses 
            WHERE is_active = 1 
            ORDER BY order_index
        """)
        standard_courses = cursor.fetchall()
        standard_count = len(standard_courses)
        
        # 【新增】建立標準課程的 (name, credits) 集合，用於內容比對
        standard_set = {(c['name'], c['credits']) for c in standard_courses}

        # 2️⃣ 嘗試抓學生個人模板
        cursor.execute("""
            SELECT content FROM templates
            WHERE uploaded_by=%s AND template_type='student_custom'
            ORDER BY uploaded_at DESC LIMIT 1
        """, (session['user_id'],))
        row = cursor.fetchone()

        if not row:
            # 沒模板 → 回傳標準課程
            return jsonify({
                "success": True,
                "courses": standard_courses,
                "needs_update": False,
                "source": "standard"
            })

        # 3️⃣ 解析模板內容
        try:
            student_courses = json.loads(row['content'])
        except Exception:
            student_courses = []
        
        student_count = len(student_courses)
        
        # 【新增】建立學生課程的 (name, credits) 集合，用於內容比對
        student_set = {(c.get('name'), c.get('credits')) for c in student_courses}

        # 4️⃣ 檢查是否有新增或內容變更
        # needs_update = student_count < standard_count
        # 【修改】若標準課程數量增加 OR 兩個課程內容集合不相等，則視為需要更新
        needs_update = (student_count < standard_count) or (student_set != standard_set)

        # 回傳資料
        return jsonify({
            "success": True,
            "courses": student_courses,
            "needs_update": needs_update,
            "source": "student" if not needs_update else "student_outdated"
        })
    except Exception as e:
        print("❌ 載入模板錯誤:", e)
        return jsonify({"success": False, "message": "載入模板失敗"}), 500
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