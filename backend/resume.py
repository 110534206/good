from flask import Blueprint, request, jsonify, session, send_file, render_template, redirect
from werkzeug.utils import secure_filename
from config import get_db
from semester import get_current_semester_id
from docxtpl import DocxTemplate, InlineImage
from docx.shared import Inches
import os
import traceback
import json
from datetime import datetime
from notification import create_notification


resume_bp = Blueprint("resume_bp", __name__)

# 上傳資料夾設定
UPLOAD_FOLDER = "uploads/resumes"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 缺勤佐證圖片資料夾設定
ABSENCE_PROOF_FOLDER = "uploads/absence_proofs"
os.makedirs(ABSENCE_PROOF_FOLDER, exist_ok=True)

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
# Helper / 權限管理
# -------------------------
def get_user_by_username(cursor, username):
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    return cursor.fetchone()

def get_user_by_id(cursor, user_id):
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    return cursor.fetchone()

def get_director_department(cursor, user_id):
    """
    取得主任所屬 department（透過 classes_teacher -> classes.department）
    若管理多個班級，只回傳第一個有 department 的值（可擴充回傳 list）
    """
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
    # admin 可以
    if session_role == "admin":
        return True

    # student 只能自己
    if session_role == "student":
        return session_user_id == target_user_id

    # ta 可以讀所有
    if session_role == "ta":
        return True

    # 取得 target student's class_id
    cursor.execute("SELECT class_id FROM users WHERE id = %s", (target_user_id,))
    u = cursor.fetchone()
    if not u:
        return False
    target_class_id = u.get('class_id')

    if session_role == "teacher":
        return teacher_manages_class(cursor, session_user_id, target_class_id)

    if session_role == "class_teacher":
        return teacher_manages_class(cursor, session_user_id, target_class_id)

    if session_role == "director":
        director_dept = get_director_department(cursor, session_user_id)
        if not director_dept:
            return False
        cursor.execute("SELECT department FROM classes WHERE id = %s", (target_class_id,))
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
def save_structured_data(cursor, student_id, data, semester_id=None):
    try:
        # 1) 儲存 Student_Info (基本資料)
        cursor.execute("""
            INSERT INTO Student_Info (StuID, StuName, BirthDate, Gender, Phone, Email, Address, ConductScore, Autobiography, PhotoPath, UpdatedAt)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
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

        # 2) 儲存課程 (先刪除同學同學期的課程，再插入)
        if semester_id is None:
            # 若沒有 semester_id，仍刪除所有該 StuID 的課程（保守處理）
            cursor.execute("DELETE FROM course_grades WHERE StuID=%s", (student_id,))
        else:
            cursor.execute("DELETE FROM course_grades WHERE StuID=%s AND IFNULL(SemesterID, '')=%s", (student_id, semester_id))

        seen_course_names = set()
        unique_courses = []

        for c in data.get('courses', []):
            course_name = (c.get('name') or '').strip()
            if course_name and course_name not in seen_course_names:
                unique_courses.append(c)
                seen_course_names.add(course_name)
            elif course_name:
                # 重複課程，跳過
                print(f"⚠️ 偵測到重複課程名稱並已跳過: {course_name}")

        for c in unique_courses:
            # 支援 semester_id 儲存
            if semester_id is not None:
                cursor.execute("""
                    REPLACE INTO course_grades (StuID, CourseName, Credits, Grade, SemesterID)
                    VALUES (%s,%s,%s,%s,%s)
                """, (student_id, c.get('name'), c.get('credits'), c.get('grade'), semester_id))
            else:
                cursor.execute("""
                    INSERT INTO course_grades (StuID, CourseName, Credits, Grade)
                    VALUES (%s,%s,%s,%s)
                """, (student_id, c.get('name'), c.get('credits'), c.get('grade')))

        # 3) 儲存證照（整合：文本 + 圖片皆放 student_certifications）
        # 為簡潔處理：刪除該學生既有證照（提交履歷時，視為更新整份證照清單）
        cursor.execute("DELETE FROM student_certifications WHERE StuID=%s", (student_id,))

        # 3a) 插入文本證照 (structured_certifications)
        for cert in data.get('structured_certifications', []):
            name = cert.get('name', '').strip()
            ctype = cert.get('type', 'other')
            if name:
                cursor.execute("""
                    INSERT INTO student_certifications
                    (StuID, CertName, CertType, CertPhotoPath, CreatedAt)
                    VALUES (%s, %s, %s, %s, NOW())
                """, (student_id, name, ctype, None))

        # 3b) 插入上傳的證照圖片
        cert_photo_paths = data.get('cert_photo_paths') or []
        cert_names = data.get('cert_names') or []
        # 兩個陣列可能長度不同，取最大
        max_len = max(len(cert_photo_paths), len(cert_names))
        for i in range(max_len):
            path = cert_photo_paths[i] if i < len(cert_photo_paths) else None
            name = cert_names[i] if i < len(cert_names) else ''
            if not path and not name:
                continue
            cursor.execute("""
                INSERT INTO student_certifications
                (StuID, CertName, CertType, CertPhotoPath, CreatedAt)
                VALUES (%s, %s, %s, %s, NOW())
            """, (student_id, name or None, 'photo', path or None))

        # 4) 儲存語文能力（Student_LanguageSkills）
        cursor.execute("DELETE FROM Student_LanguageSkills WHERE StuID=%s", (student_id,))
        for lang_skill in data.get('structured_languages', []):
            if lang_skill.get('language') and lang_skill.get('level'):
                cursor.execute("""
                    INSERT INTO Student_LanguageSkills (StuID, Language, Level, CreatedAt)
                    VALUES (%s, %s, %s, NOW())
                """, (student_id, lang_skill['language'], lang_skill['level']))

        return True
    except Exception as e:
        print("❌ 儲存結構化資料錯誤:", e)
        traceback.print_exc()
        return False

# -------------------------
# 取回學生資料 (for 生成履歷)
# -------------------------
def get_student_info_for_doc(cursor, student_id, semester_id=None):
    data = {}
    cursor.execute("SELECT * FROM Student_Info WHERE StuID=%s", (student_id,))
    data['info'] = cursor.fetchone() or {}

    if semester_id is not None:
        cursor.execute("""
            SELECT CourseName, Credits, Grade, IFNULL(transcript_path, '') AS transcript_path, SemesterID
            FROM course_grades
            WHERE StuID=%s AND SemesterID=%s
        """, (student_id, semester_id))
    else:
        cursor.execute("""
            SELECT CourseName, Credits, Grade, IFNULL(transcript_path, '') AS transcript_path, SemesterID
            FROM course_grades
            WHERE StuID=%s
        """, (student_id,))

    grades_rows = cursor.fetchall() or []
    # Extract transcript_path: prefer the one with SemesterID == semester_id, else latest non-empty
    data['grades'] = grades_rows
    data['transcript_path'] = ''

    # Try to find a transcript_path from grades_rows
    for row in grades_rows:
        tp = row.get('transcript_path')
        if tp:
            data['transcript_path'] = tp
            break

    # 證照
    cursor.execute("SELECT CertName, CertType, CertPhotoPath, AcquisitionDate, IssuingBody FROM student_certifications WHERE StuID=%s", (student_id,))
    data['certifications'] = cursor.fetchall() or []

    # 語文能力
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

        # 格式化出生日期
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

        # 照片
        image_obj = None
        photo_path = info.get("PhotoPath")
        if photo_path and os.path.exists(photo_path):
            try:
                image_obj = InlineImage(doc, os.path.abspath(photo_path), width=Inches(1.2))
            except Exception as e:
                print(f"⚠️ 圖片載入錯誤: {e}")

        # 處理課程資料（保留原邏輯）
        MAX_COURSES = 30
        padded_grades = grades[:MAX_COURSES]
        padded_grades += [{'CourseName': '', 'Credits': ''}] * (MAX_COURSES - len(padded_grades))

        context_courses = {}
        NUM_ROWS = 10
        NUM_COLS = 3
        for i in range(NUM_ROWS):
            for j in range(NUM_COLS):
                index = i * NUM_COLS + j
                if index < MAX_COURSES:
                    course = padded_grades[index]
                    row_num = i + 1
                    col_num = j + 1
                    context_courses[f'CourseName_{row_num}_{col_num}'] = course.get('CourseName', '')
                    context_courses[f'Credits_{row_num}_{col_num}'] = course.get('Credits', '')

        # 插入成績單圖片：嘗試從 student_data['transcript_path']（由 get_student_info_for_doc 提供）
        transcript_obj = None
        transcript_path = student_data.get("transcript_path") or info.get("TranscriptPath") or ''
        if transcript_path and os.path.exists(transcript_path):
            try:
                transcript_obj = InlineImage(doc, os.path.abspath(transcript_path), width=Inches(6.0))
            except Exception as e:
                print(f"⚠️ 成績單圖片載入錯誤: {e}")

        # 缺勤佐證圖片
        absence_proof_obj = None
        absence_proof_path = student_data.get("Absence_Proof_Path")
        image_size = Inches(6.0)
        if absence_proof_path and os.path.exists(absence_proof_path):
            try:
                absence_proof_obj = InlineImage(doc, os.path.abspath(absence_proof_path), width=image_size)
            except Exception as e:
                print(f"⚠️ 缺勤佐證圖片載入錯誤: {e}")

        # 操行等級
        conduct_score = info.get('ConductScore', '')
        conduct_marks = {k: '□' for k in ['C_You', 'C_Jia', 'C_Yi', 'C_Bing', 'C_Ding']}
        mapping = {'優': 'C_You', '甲': 'C_Jia', '乙': 'C_Yi', '丙': 'C_Bing', '丁': 'C_Ding'}
        if conduct_score in mapping:
            conduct_marks[mapping[conduct_score]] = '■'

        # 證照分類 (文本 + 圖片資料已合併於 student_certifications)
        labor_certs, intl_certs, local_certs, other_certs = [], [], [], []
        cert_photo_paths = []
        cert_names = []

        for cert in certs:
            name = cert.get('CertName') or ''
            ctype = cert.get('CertType') or ''
            photo = cert.get('CertPhotoPath') or ''
            if photo:
                cert_photo_paths.append(photo)
                cert_names.append(name)
            else:
                # 沒照片的文本證照，分類放到列表
                if ctype == 'labor':
                    labor_certs.append(name)
                elif ctype == 'intl':
                    intl_certs.append(name)
                elif ctype == 'local':
                    local_certs.append(name)
                else:
                    other_certs.append(name)

        def pad_list(lst, length=5):
            lst = lst[:length]
            lst += [''] * (length - len(lst))
            return lst

        # 建 context
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
            'transcript_path': transcript_obj,
            'Absence_Proof_Image': absence_proof_obj if absence_proof_obj else "（查無佐證圖片）"
        }

        # 加入缺勤統計
        absence_fields = ['曠課', '遲到', '事假', '病假', '生理假', '公假', '喪假', '總計']
        for t in absence_fields:
            key = f"absence_{t}_units"
            context[key] = student_data.get(key, "0 節")

        # 加入操行等級勾選
        context.update(conduct_marks)

        # 加入課程資料
        context.update(context_courses)

        # 加入證照文字清單
        for i, val in enumerate(pad_list(labor_certs), 1):
            context[f'LaborCerts_{i}'] = val
        for i, val in enumerate(pad_list(intl_certs), 1):
            context[f'IntlCerts_{i}'] = val
        for i, val in enumerate(pad_list(local_certs), 1):
            context[f'LocalCerts_{i}'] = val
        for i, val in enumerate(pad_list(other_certs), 1):
            context[f'OtherCerts_{i}'] = val

        # 證照圖片（最多 8 張）
        MAX_CERTS = 8
        cert_photo_objs = []
        image_size = Inches(3.0)
        for i, path in enumerate(cert_photo_paths[:MAX_CERTS]):
            try:
                if path and os.path.exists(path):
                    cert_photo_objs.append(InlineImage(doc, os.path.abspath(path), width=image_size))
                else:
                    cert_photo_objs.append('')
            except Exception as e:
                print(f"⚠️ 證照圖片載入錯誤: {e}")
                cert_photo_objs.append('')

        for i in range(MAX_CERTS):
            image_key = f'CertPhotoImages_{i+1}'
            name_key = f'CertPhotoName_{i+1}'
            context[image_key] = cert_photo_objs[i] if i < len(cert_photo_objs) else ''
            context[name_key] = cert_names[i] if i < len(cert_names) else ''

        # 語文能力
        lang_context = {}
        lang_codes = ['En', 'Jp', 'Tw', 'Hk']
        level_codes = ['Jing', 'Zhong', 'Lue']
        for code in lang_codes:
            for level_code in level_codes:
                lang_context[f'{code}_{level_code}'] = '□'

        lang_code_map = {'英語': 'En', '日語': 'Jp', '台語': 'Tw', '客語': 'Hk'}
        level_code_map = {'精通': 'Jing', '中等': 'Zhong', '略懂': 'Lue'}

        for lang_skill in student_data.get('languages', []):
            lang = lang_skill.get('Language')
            level = lang_skill.get('Level')
            lang_code = lang_code_map.get(lang)
            level_code = level_code_map.get(level)
            if lang_code and level_code:
                key = f'{lang_code}_{level_code}'
                if key in lang_context:
                    lang_context[key] = '■'

        context.update(lang_context)

        # 渲染與儲存
        doc.render(context)
        doc.save(output_path)
        print(f"✅ 履歷文件已生成: {output_path}")
        return True

    except Exception as e:
        print("❌ 生成 Word 檔錯誤:", e)
        traceback.print_exc()
        return False

# -------------------------
# API: 根據證照代碼查詢名稱和類別
# -------------------------
@resume_bp.route('/api/get_certificate_info', methods=['GET'])
def get_certificate_info():
    conn = None
    cursor = None
    try:
        cert_code = request.args.get('code')
        if not cert_code:
            return jsonify({"success": False, "message": "缺少證照代碼 (code) 參數"}), 400

        cert_code = cert_code.strip().upper()

        conn = get_db()
        cursor = conn.cursor(dictionary=True) 

        # ❗ 查詢所有匹配的記錄
        sql_query = "SELECT name, category FROM certificate_codes WHERE code = %s"
        cursor.execute(sql_query, (cert_code,))
        
        # ❗ 使用 fetchall() 獲取所有結果
        results = cursor.fetchall()

        if results:
            # 找到資料，返回一個結果列表
            return jsonify({
                "success": True,
                # ❗ 返回的 info 是一個包含多個 {name, category} 的列表
                "info": results,
                "count": len(results)
            })
        else:
            # 查無此代碼
            return jsonify({
                "success": False,
                "message": f"查無代碼: {cert_code}"
            })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# -------------------------
# API：提交並生成履歷
# -------------------------
@resume_bp.route('/api/submit_and_generate', methods=['POST'])
def submit_and_generate_api():
    context = {}
    conn = None
    cursor = None

    try:
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
        cert_names = request.form.getlist('cert_names[]')

        ALLOWED_IMAGE_MIMES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']

        # 儲存照片
        photo_path = None
        if photo and photo.filename:
            if photo.mimetype not in ALLOWED_IMAGE_MIMES:
                return jsonify({"success": False, "message": f"照片檔案格式錯誤 ({photo.mimetype})"}), 400
            filename = secure_filename(photo.filename)
            photo_dir = os.path.join(UPLOAD_FOLDER, "photos")
            os.makedirs(photo_dir, exist_ok=True)
            ext = os.path.splitext(filename)[1]
            new_filename = f"{user_id}_photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            photo_path = os.path.join(photo_dir, new_filename)
            photo.save(photo_path)

        # 儲存成績單檔案（先儲存檔案，再 update 到 course_grades 的 transcript_path）
        transcript_path = None
        if transcript_file and transcript_file.filename:
            if transcript_file.mimetype not in ALLOWED_IMAGE_MIMES:
                return jsonify({"success": False, "message": f"成績單檔案格式錯誤 ({transcript_file.mimetype})"}), 400
            filename = secure_filename(transcript_file.filename)
            transcript_dir = os.path.join(UPLOAD_FOLDER, "transcripts")
            os.makedirs(transcript_dir, exist_ok=True)
            ext = os.path.splitext(filename)[1]
            new_filename = f"{user_id}_transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            transcript_path = os.path.join(transcript_dir, new_filename)
            transcript_file.save(transcript_path)

        # 儲存多張證照
        cert_photo_paths = []
        if cert_files:
            cert_dir = os.path.join(UPLOAD_FOLDER, "cert_photos")
            os.makedirs(cert_dir, exist_ok=True)

        for idx, file in enumerate(cert_files, start=1):
            if file and file.filename:
                if file.mimetype not in ALLOWED_IMAGE_MIMES:
                    print(f"⚠️ 證照檔案格式錯誤已跳過: {file.filename} ({file.mimetype})")
                    continue
                ext = os.path.splitext(secure_filename(file.filename))[1]
                new_filename = f"{user_id}_cert_{idx}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
                file_path = os.path.join(cert_dir, new_filename)
                file.save(file_path)
                cert_photo_paths.append(file_path)

        # 處理單張證照圖片（certificate_image + certificate_description）
        certificate_image_file = request.files.get('certificate_image')
        certificate_description = request.form.get('certificate_description', '')
        image_path_for_template = None
        if certificate_image_file and certificate_image_file.filename != '' and 'user_id' in session:
            try:
                cert_folder = os.path.join(UPLOAD_FOLDER, 'certificates')
                os.makedirs(cert_folder, exist_ok=True)
                filename = secure_filename(certificate_image_file.filename)
                file_extension = os.path.splitext(filename)[1] or '.png'
                unique_filename = f"{session['user_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}{file_extension}"
                image_save_path = os.path.join(cert_folder, unique_filename)
                certificate_image_file.save(image_save_path)
                image_path_for_template = image_save_path
            except Exception as e:
                print(f"❌ 儲存單一證照圖片失敗: {e}")
                traceback.print_exc()
                image_path_for_template = None

        if image_path_for_template or certificate_description:
            if cert_photo_paths is None:
                cert_photo_paths = []
            if cert_names is None:
                cert_names = []
            cert_photo_paths.insert(0, image_path_for_template or "")
            cert_names.insert(0, certificate_description or "")

        # 組合缺勤統計（與你現有邏輯相同）
        absence_stats = {}
        cursor.execute("""
            SELECT 
                absence_type, 
                SUM(duration_units) AS total_units 
            FROM absence_records
            WHERE user_id = %s
            GROUP BY absence_type
        """, (user_id,))
        results = cursor.fetchall()
        all_types = ["曠課", "遲到", "事假", "病假", "生理假", "公假", "喪假"]
        db_stats = {t: 0 for t in all_types}
        for row in results:
            typ = row.get('absence_type')
            if typ in db_stats:
                try:
                    db_stats[typ] = int(row.get('total_units') or 0)
                except Exception:
                    db_stats[typ] = 0
        for t in all_types:
            absence_stats[f"absence_{t}_units"] = f"{db_stats.get(t,0)} 節"

        incoming_stats_json = request.form.get("absence_stats_json", None)
        if incoming_stats_json:
            try:
                incoming = json.loads(incoming_stats_json)
                for t in all_types:
                    val = incoming.get(t)
                    if val is not None:
                        try:
                            val_int = int(val)
                        except Exception:
                            try:
                                val_int = int(str(val).replace("節","").strip())
                            except Exception:
                                val_int = db_stats.get(t, 0)
                        absence_stats[f"absence_{t}_units"] = f"{val_int} 節"
            except Exception as e:
                print("⚠️ 無法解析 absence_stats_json，忽略前端傳入值:", e)

        total = 0
        for t in all_types:
            v = absence_stats.get(f"absence_{t}_units", "0 節")
            try:
                total += int(str(v).replace("節","").strip())
            except Exception:
                pass
        absence_stats["absence_總計_units"] = f"{total} 節"
        context.update(absence_stats)

        # 處理並儲存缺勤佐證圖片（與你原邏輯一致）
        absence_image_path = None
        try:
            uploaded_proof = request.files.get('proof_image') or request.files.get('absence_proof')
            if uploaded_proof and uploaded_proof.filename:
                if uploaded_proof.mimetype in ALLOWED_IMAGE_MIMES:
                    os.makedirs(ABSENCE_PROOF_FOLDER, exist_ok=True)
                    ext = os.path.splitext(secure_filename(uploaded_proof.filename))[1] or ".png"
                    fname = f"{user_id}_absence_proof_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
                    savep = os.path.join(ABSENCE_PROOF_FOLDER, fname)
                    uploaded_proof.save(savep)
                    absence_image_path = savep
                else:
                    print(f"⚠️ 上傳的缺勤佐證圖片格式不支援: {uploaded_proof.mimetype}")
        except Exception as e:
            print("⚠️ 儲存上傳的缺勤佐證圖片失敗:", e)
            traceback.print_exc()

        if not absence_image_path:
            try:
                ar_json = request.form.get("absence_records_json", None)
                if ar_json:
                    try:
                        ar_list = json.loads(ar_json)
                        for rec in reversed(ar_list):
                            img = rec.get("image_filename") or rec.get("image_path")
                            if img:
                                absence_image_path = img
                                break
                    except Exception as e:
                        print("⚠️ 解析 absence_records_json 失敗:", e)
            except Exception as e:
                print("⚠️ 嘗試讀取 absence_records_json 失敗:", e)

        if not absence_image_path:
            try:
                cursor.execute("""
                    SELECT image_path
                    FROM absence_records
                    WHERE user_id = %s AND image_path IS NOT NULL AND image_path != ''
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (user_id,))
                row = cursor.fetchone()
                if row:
                    absence_image_path = row.get('image_path')
            except Exception as e:
                print(f"Error fetching latest absence proof path from DB: {e}")

        context['Absence_Proof_Path'] = absence_image_path

        # 查學生學號 (username)
        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        result = cursor.fetchone()
        if not result:
            return jsonify({"success": False, "message": "找不到使用者"}), 404
        student_id = result['username']

        # 確保 courses 中的 grade 欄位存在
        for c in courses:
            c['grade'] = c.get('grade', '')

        # 解析文本證照資料（非圖片）
        structured_certifications = []
        cert_names_text = request.form.getlist('cert_name[]')
        cert_types = request.form.getlist('cert_type[]')

        for n, t in zip(cert_names_text, cert_types):
           if n.strip():
                structured_certifications.append({
                "name": n.strip(),
                "type": t.strip() if t else "other"
        })

        # 解析語言能力資料
        structured_languages = []
        lang_names = request.form.getlist('language[]')
        lang_levels = request.form.getlist('language_level[]')

        for lang, lvl in zip(lang_names, lang_levels):
             if lang.strip() and lvl.strip():
                structured_languages.append({
                "language": lang.strip(),
                "level": lvl.strip()
        })

        # 建立結構化資料（傳入 save_structured_data）
        semester_id = get_current_semester_id(cursor)
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
            "cert_photo_paths": cert_photo_paths,
            "cert_names": cert_names
        }

        # 將表單數據和結構化數據也加入 context (以便套版)
        context.update(data)
        context.update(structured_data)

        # 儲存結構化資料（包含 language / Certs / course_grades）
        if not save_structured_data(cursor, student_id, structured_data, semester_id=semester_id):
            conn.rollback()
            return jsonify({"success": False, "message": "資料儲存失敗"}), 500

        # 將 transcript_path 更新到 course_grades（以 semester_id 為主）
        if transcript_path:
            try:
                # 嘗試 update 同學該學期的 course_grades（若沒有，插入一筆佔位紀錄）
                cursor.execute("""
                    UPDATE course_grades
                    SET transcript_path = %s
                    WHERE StuID = %s AND SemesterID = %s
                """, (transcript_path, student_id, semester_id))
                if cursor.rowcount == 0:
                    # 沒有更新到任何列，插入一筆僅含 transcript_path 的占位（可視情況調整）
                    cursor.execute("""
                        INSERT INTO course_grades (StuID, CourseName, Credits, Grade, SemesterID, transcript_path)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (student_id, '', 0, '', semester_id, transcript_path))
            except Exception as e:
                print("⚠️ 更新 course_grades.transcript_path 失敗:", e)

        # 生成 Word 文件
        student_data_for_doc = get_student_info_for_doc(cursor, student_id, semester_id=semester_id)
        # PhotoPath & ConductScoreNumeric
        student_data_for_doc["info"]["PhotoPath"] = photo_path
        student_data_for_doc["info"]["ConductScoreNumeric"] = data.get("conduct_score_numeric")
        # 傳遞證照圖片與名稱清單（generate 會自行從 certs 讀）
        student_data_for_doc["cert_photo_paths"] = cert_photo_paths
        student_data_for_doc["cert_names"] = cert_names
        # 合併 context
        student_data_for_doc.update(context)

        filename = f"{student_id}_履歷_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        save_path = os.path.join(UPLOAD_FOLDER, filename)

        if not generate_application_form_docx(student_data_for_doc, save_path):
            conn.rollback()
            return jsonify({"success": False, "message": "文件生成失敗"}), 500

        # 寫入 resumes
        cursor.execute("""
            INSERT INTO resumes
            (user_id, filepath, original_filename, status, semester_id, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
        """, (
            user_id,
            save_path,
            filename,
            'submitted',
            semester_id
        ))

        conn.commit()
        return jsonify({
            "success": True,
            "message": "履歷已成功提交並生成文件",
            "file_path": save_path,
            "filename": filename
        })

    except Exception as e:
        print("❌ submit_and_generate_api 發生錯誤:", e)
        traceback.print_exc()
        if conn:
            conn.rollback()
        return jsonify({"success": False, "message": f"系統錯誤: {str(e)}"}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

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
        # 取得履歷資料
        cursor.execute("""
            SELECT filepath, original_filename, user_id 
            FROM resumes 
            WHERE id = %s
        """, (resume_id,))
        resume = cursor.fetchone()

        if not resume:
            return jsonify({"success": False, "message": "找不到履歷"}), 404

        # 權限檢查
        session_user_id = session['user_id']
        session_role = session['role']

        if not can_access_target_resume(cursor, session_user_id, session_role, resume['user_id']):
            return jsonify({"success": False, "message": "無權限"}), 403

        # 統一路徑格式
        file_path = os.path.normpath(resume['filepath'])

        if not os.path.exists(file_path):
            print(f"[DEBUG] File not found: {file_path}")  # 方便除錯
            return jsonify({"success": False, "message": "檔案不存在"}), 404

        # 安全下載
        return send_file(file_path, as_attachment=True, download_name=resume['original_filename'])

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
#  缺勤統計查詢
# -------------------------
@resume_bp.route('/api/get_absence_stats', methods=['GET'])
def get_absence_stats():
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 查詢並計算各類別缺勤總節數
        cursor.execute("""
            SELECT 
                absence_type, 
                SUM(duration_units) AS total_units 
            FROM absence_records
            WHERE user_id = %s
            GROUP BY absence_type
        """, (user_id,))
        
        results = cursor.fetchall()
        
        # 將結果轉換為前端需要的字典格式 (例如: {"曠課": 5, "事假": 10, ...})
        stats = {}
        for row in results:
            # 確保 total_units 轉換為整數
            stats[row['absence_type']] = int(row['total_units'])

        return jsonify({"success": True, "stats": stats})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
#  缺勤紀錄提交
# -------------------------
@resume_bp.route('/api/submit_absence_record', methods=['POST'])
def submit_absence_record():
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    user_id = session['user_id']
    
    # 取得前端傳來的所有欄位
    absence_date = request.form.get('absence_date')
    absence_type = request.form.get('absence_type')
    duration_units = request.form.get('duration_units')
    reason = request.form.get('reason')

    if not all([absence_date, absence_type, duration_units, reason]):
        return jsonify({"success": False, "message": "日期、類型、節數、事由皆為必填欄位"}), 400

    try:
        duration_units = int(duration_units)
        if duration_units <= 0:
            return jsonify({"success": False, "message": "節數必須為正整數"}), 400
    except ValueError:
        return jsonify({"success": False, "message": "節數格式錯誤"}), 400

    image_path = None
    # 處理佐證圖片上傳
    if 'proof_image' in request.files:
        proof_image = request.files['proof_image']
        if proof_image and proof_image.filename:
            # 確保檔名安全，並加上 user_id 和時間戳以避免重複
            filename = secure_filename(f"{user_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{proof_image.filename}")
            save_path = os.path.join(ABSENCE_PROOF_FOLDER, filename)
            proof_image.save(save_path)
            image_path = save_path # 儲存到資料庫的路徑

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 插入缺勤紀錄到 absence_records 表格
        cursor.execute("""
            INSERT INTO absence_records 
            (user_id, absence_date, absence_type, duration_units, reason, image_path)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, absence_date, absence_type, duration_units, reason, image_path))
        
        conn.commit()

        return jsonify({"success": True, "message": "缺勤紀錄提交成功！"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"資料庫操作失敗: {str(e)}"}), 500

    finally:
        cursor.close()
        conn.close()

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
# API - 取得班導 / 主任 履歷 (支援多班級 & 全系)（讀取）
# -------------------------
@resume_bp.route("/api/get_class_resumes", methods=["GET"])
def get_class_resumes():
    # 驗證登入
    if not require_login():
        return jsonify({"success": False, "message": "未授權"}), 403

    user_id = session['user_id']
    role = session['role']
    mode = request.args.get('mode', '').strip().lower()

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        resumes = []  # 初始化結果列表
        sql_query = ""
        sql_params = tuple()

        print(f"🔍 [DEBUG] get_class_resumes called - user_id: {user_id}, role: {role}")

        # ------------------------------------------------------------------
        # 1. 班導 / 教師 (role == "teacher" or "class_teacher")
        # ------------------------------------------------------------------
        if role in ["teacher", "class_teacher"]:
            sql_query = """
                SELECT 
                    r.id,
                    u.name AS student_name,
                    u.username AS student_number,
                    c.name AS class_name,
                    c.department,
                    r.original_filename,
                    r.filepath,
                    r.status,
                    r.comment,
                    r.note,
                    r.created_at
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                JOIN classes_teacher ct ON ct.class_id = c.id
                WHERE ct.teacher_id = %s
                ORDER BY c.name, u.name
            """
            sql_params = (user_id,)

            cursor.execute(sql_query, sql_params)
            resumes = cursor.fetchall()

            if not resumes:
                print(f"⚠️ [DEBUG] Teacher/class_teacher user {user_id} has no assigned classes.")
                resumes = []

        # ------------------------------------------------------------------
        # 2. 主任 (role == "director")
        # ------------------------------------------------------------------
        elif role == "director":
            # director 根據 mode 控制可見範圍：
            # - mode=director → 同科系全部
            # - 其他/預設 → 僅自己帶的班級（班導模式）
            if mode == "director":
                # 取得主任所屬科系（使用 helper）
                department = get_director_department(cursor, user_id)

                if not department:
                    # 沒有設定科系 → 不顯示任何資料，以免越權
                    resumes = []
                    sql_query = ""
                    sql_params = tuple()
                else:
                    sql_query = """
                        SELECT 
                            r.id,
                            u.name AS student_name,
                            u.username AS student_number,
                            c.name AS class_name,
                            c.department,
                            r.original_filename,
                            r.filepath,
                            r.status,
                            r.comment,
                            r.note,
                            r.created_at
                        FROM resumes r
                        JOIN users u ON r.user_id = u.id
                        JOIN classes c ON u.class_id = c.id
                        WHERE c.department = %s
                        ORDER BY c.name, u.name
                    """
                    sql_params = (department,)
            else:
                # homeroom/預設：僅看自己帶的班級
                sql_query = """
                    SELECT 
                        r.id,
                        u.name AS student_name,
                        u.username AS student_number,
                        c.name AS class_name,
                        c.department,
                        r.original_filename,
                        r.filepath,
                        r.status,
                        r.comment,
                        r.note,
                        r.created_at
                    FROM resumes r
                    JOIN users u ON r.user_id = u.id
                    LEFT JOIN classes c ON u.class_id = c.id
                    JOIN classes_teacher ct ON ct.class_id = c.id
                    WHERE ct.teacher_id = %s
                    ORDER BY c.name, u.name
                """
                sql_params = (user_id,)

            # 執行 SQL 查詢 (主任邏輯在上面已完成查詢或準備好查詢字串)
            if sql_query:
                cursor.execute(sql_query, sql_params)
                resumes = cursor.fetchall()

        # ------------------------------------------------------------------
        # 3. TA 或 Admin (role == "ta" or "admin")
        # ------------------------------------------------------------------
        elif role in ["ta", "admin"]:
            sql_query = """
                SELECT 
                    r.id,
                    u.name AS student_name,
                    u.username AS student_number,
                    c.name AS class_name,
                    c.department,
                    r.original_filename,
                    r.filepath,
                    r.status,
                    r.comment,
                    r.note,
                    r.created_at
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                ORDER BY c.name, u.name
            """
            cursor.execute(sql_query, tuple())
            resumes = cursor.fetchall()

        # ------------------------------------------------------------------
        # 4. Vendor (role == "vendor")
        # ------------------------------------------------------------------
        elif role == "vendor":
            # Vendor 可以看到選擇了他們上傳的公司的學生履歷
            # 或者被錄取到他們公司的學生履歷
            sql_query = """
                SELECT DISTINCT
                    r.id,
                    u.name AS student_name,
                    u.username AS student_number,
                    c.name AS class_name,
                    c.department,
                    r.original_filename,
                    r.filepath,
                    r.status,
                    r.comment,
                    r.note,
                    r.created_at
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                WHERE EXISTS (
                    -- 學生選擇了該 vendor 上傳的公司
                    SELECT 1 FROM student_preferences sp
                    JOIN internship_companies ic ON sp.company_id = ic.id
                    WHERE sp.student_id = u.id
                    AND ic.uploaded_by_user_id = %s
                ) OR EXISTS (
                    -- 學生被錄取到該 vendor 的公司
                    SELECT 1 FROM internship_experiences ie
                    JOIN internship_companies ic ON ie.company_id = ic.id
                    WHERE ie.user_id = u.id
                    AND ic.uploaded_by_user_id = %s
                )
                ORDER BY c.name, u.name
            """
            cursor.execute(sql_query, (user_id, user_id))
            resumes = cursor.fetchall()

        else:
            return jsonify({"success": False, "message": "無效的角色或權限"}), 403

        # 格式化日期時間並統一字段名稱
        for r in resumes:
            if isinstance(r.get('created_at'), datetime):
                r['created_at'] = r['created_at'].strftime("%Y-%m-%d %H:%M:%S")
            # 統一字段名稱，確保前端能正確訪問
            if 'student_name' in r:
                r['name'] = r['student_name']
            if 'student_number' in r:
                r['username'] = r['student_number']
            if 'class_name' in r:
                r['className'] = r['class_name']
            if 'created_at' in r:
                r['upload_time'] = r['created_at']

        print(f"✅ [DEBUG] Returning {len(resumes)} resumes for role {role}")
        return jsonify({"success": True, "resumes": resumes})

    except Exception:
        print("❌ 取得班級履歷資料錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# 審核履歷 (退件/完成)
# -------------------------
@resume_bp.route('/api/review_resume/<int:resume_id>', methods=['POST'])
def review_resume(resume_id):
    user_id = session.get('user_id')
    user_role = session.get('role')

    # 1. 權限檢查
    ALLOWED_ROLES = ['teacher', 'admin', 'class_teacher']
    if not user_id or user_role not in ALLOWED_ROLES:
        return jsonify({"success": False, "message": "未授權或無權限"}), 403

    data = request.get_json()
    status = data.get('status')
    comment = data.get('comment') # 老師留言

    if status not in ['approved', 'rejected']:
        return jsonify({"success": False, "message": "無效的狀態碼"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 2. 查詢履歷並取得學生Email和姓名
        cursor.execute("""
            SELECT 
                r.user_id, r.original_filename, r.status AS old_status, r.comment,
                u.email AS student_email, u.name AS student_name
            FROM resumes r
            JOIN users u ON r.user_id = u.id
            WHERE r.id=%s
        """, (resume_id,))
        resume_data = cursor.fetchone()

        if not resume_data:
            return jsonify({"success": False, "message": "找不到履歷"}), 404

        student_user_id = resume_data['user_id']
        student_email = resume_data['student_email'] 
        student_name = resume_data['student_name']  
        old_status = resume_data['old_status']


        # 3. 更新履歷狀態 (使用您確認的 reviewed_by 和 reviewed_at)
        cursor.execute("""
            UPDATE resumes SET 
                status=%s, 
                comment=%s, 
                reviewed_by=%s,    
                reviewed_at=NOW()  
            WHERE id=%s
        """, (status, comment, user_id, resume_id))
        
        # 4. 取得審核者姓名
        cursor.execute("SELECT name FROM users WHERE id = %s", (user_id,))
        reviewer = cursor.fetchone()
        reviewer_name = reviewer['name'] if reviewer else "審核老師"

        # 5. 處理 Email 寄送與通知 (僅在狀態改變時處理)
        from email_service import send_resume_rejection_email, send_resume_approval_email
        if old_status != status:
            # =============== 退件 ===============
            if status == 'rejected':
                email_success, email_message, log_id = send_resume_rejection_email(
                    student_email, student_name, reviewer_name, comment or "無"
                )
                print(f"📧 履歷退件 Email: {email_success}, {email_message}, Log ID: {log_id}")

                # 🎯 建立退件通知（改成 create_notification）
                notification_content = (
                    f"您的履歷已被 {reviewer_name} 老師退件。\n\n"
                    f"退件原因：{comment if comment else '請查看老師留言'}\n\n"
                    f"請根據老師的建議修改後重新上傳。"
                )

                create_notification(
                    user_id=student_user_id,
                    title="履歷退件通知",
                    message=notification_content
                )

            # =============== 通過 ===============
            elif status == 'approved':
                email_success, email_message, log_id = send_resume_approval_email(
                    student_email, student_name, reviewer_name
                )
                print(f"📧 履歷通過 Email: {email_success}, {email_message}, Log ID: {log_id}")

                # 🎯 建立通過通知（改成 create_notification）
                notification_content = (
                    f"恭喜您！您的履歷已由 {reviewer_name} 老師審核通過。\n"
                    f"您可以繼續後續的實習申請流程。"
                )

                create_notification(
                    user_id=student_user_id,
                    title="履歷審核通過通知",
                    message=notification_content
                )

        conn.commit()

        return jsonify({"success": True, "message": "履歷審核狀態更新成功"})

    except Exception as e:
        conn.rollback()
        traceback.print_exc() 
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}，請檢查後台日誌"}), 500

    finally:
        cursor.close()
        conn.close()
        
# -------------------------
# API - 更新履歷欄位（comment, note）（含權限檢查）
# -------------------------
@resume_bp.route('/api/update_resume_field', methods=['POST'])
def update_resume_field():
    try:
        if not require_login():
            return jsonify({"success": False, "message": "未授權"}), 403

        data = request.get_json() or {}
        resume_id = data.get('resume_id')
        field = data.get('field')
        value = (data.get('value') or '').strip()

        allowed_fields = {
            "comment": "comment",
            "note": "note"
        }

        try:
            resume_id = int(resume_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "resume_id 必須是數字"}), 400

        if field not in allowed_fields:
            return jsonify({"success": False, "message": "參數錯誤"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 先找出 resume 的 owner
        cursor.execute("SELECT user_id FROM resumes WHERE id = %s", (resume_id,))
        r = cursor.fetchone()
        if not r:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        owner_id = r['user_id']

        # 取得使用者角色與 id
        role = session.get('role')
        user_id = session['user_id']

        if role == "class_teacher":
            if not teacher_manages_class(cursor, user_id, get_user_by_id(cursor, owner_id)['class_id']):
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "沒有權限修改該履歷"}), 403

        elif role == "director":
            director_dept = get_director_department(cursor, user_id)
            cursor.execute("SELECT c.department FROM classes c JOIN users u ON u.class_id = c.id WHERE u.id = %s", (owner_id,))
            target_dept_row = cursor.fetchone()
            if not director_dept or not target_dept_row or director_dept != target_dept_row.get('department'):
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "沒有權限修改該履歷"}), 403

        elif role == "admin":
            pass  # admin 可以

        elif role == "student":
            # 學生只能修改自己的履歷，且只能修改 note 欄位
            if user_id != owner_id:
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "學生只能修改自己的履歷"}), 403
            if field != "note":
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "學生只能修改備註欄位"}), 403

        else:
            # ta 或其他角色不可修改
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "角色無權限修改"}), 403

        # 更新欄位
        sql = f"UPDATE resumes SET {allowed_fields[field]} = %s, updated_at = NOW() WHERE id = %s"
        cursor.execute(sql, (value, resume_id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"success": True, "field": field, "resume_id": resume_id})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500


# -------------------------
# API - 查詢履歷狀態
# -------------------------
@resume_bp.route('/api/resume_status', methods=['GET'])
def resume_status():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT status FROM resumes WHERE id = %s", (resume_id,))
        resume = cursor.fetchone()
        cursor.close()
        conn.close()

        if not resume:
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        return jsonify({"success": True, "status": resume['status']})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500


# -------------------------
# API - 查詢所有學生履歷（根據 username，含讀取權限檢查）
# -------------------------
@resume_bp.route('/api/get_student_resumes', methods=['GET'])
def get_student_resumes():
    if not require_login():
        return jsonify({"success": False, "message": "未授權"}), 403

    username = request.args.get('username')
    if not username:
        return jsonify({"success": False, "message": "缺少 username"}), 400

    user_id = session['user_id']
    role = session['role']

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT u.id AS student_id, u.class_id, c.department
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            WHERE u.username = %s
        """, (username,))
        student = cursor.fetchone()
        if not student:
            return jsonify({"success": False, "message": "找不到學生"}), 404

        # 權限判斷（讀取）
        if role == "teacher":
            if not teacher_manages_class(cursor, user_id, student['class_id']):
                return jsonify({"success": False, "message": "沒有權限查看該學生履歷"}), 403

        elif role == "director":
            director_dept = get_director_department(cursor, user_id)
            if not director_dept or director_dept != student.get('department'):
                return jsonify({"success": False, "message": "沒有權限查看該學生履歷"}), 403

        elif role == "ta":
            pass  # TA 可讀全部（如需限制可在此修改）

        elif role == "admin":
            pass

        else:
            return jsonify({"success": False, "message": "角色無權限"}), 403

        # 取得該學生履歷
        cursor.execute("""
            SELECT r.id, r.original_filename, r.status, r.comment, r.note, r.created_at AS upload_time
            FROM resumes r
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
        """, (student['student_id'],))
        resumes = cursor.fetchall()

        for r in resumes:
            if isinstance(r.get('upload_time'), datetime):
                r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"success": True, "resumes": resumes})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# 頁面路由
# -------------------------
@resume_bp.route('/upload_resume')
def upload_resume_page():
    return render_template('resume/upload_resume.html')

@resume_bp.route('/review_resume')
def review_resume_page():
    # 檢查登入狀態
    if not require_login():
        return redirect('/login')
    
    # 根據角色返回對應的模板
    role = session.get('role', '')
    
    # 老師、班導、主任、TA、管理員、廠商使用審核頁面
    if role in ['teacher', 'class_teacher', 'director', 'ta', 'admin', 'vendor']:
        return render_template('user_shared/review_resumes.html')
    
    # 其他角色使用一般查看頁面
    return render_template('resume/review_resume.html')

@resume_bp.route('/ai_edit_resume')
def ai_edit_resume_page():
    return render_template('resume/ai_edit_resume.html')