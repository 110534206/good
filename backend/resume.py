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
    """將數字分數轉換成操行等級 (優/甲/乙/丙/丁)"""
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
# 語文能力複選框處理輔助函式 (修正版)
# -------------------------
def generate_language_marks(level):
    """將語文能力（精通/中等/略懂）轉換為方格標記（■/□）"""
    # 初始化所有方框為未選中 (□)
    marks = {
        'Jing': '□',  # 精通
        'Zhong': '□', # 中等
        'Lue': '□'    # 略懂
    }
    
    # 建立等級與字典 key 的對應
    level_map = {
        '精通': 'Jing',
        '中等': 'Zhong',
        '略懂': 'Lue'
    }
    
    # 檢查傳入的等級並標記為選中 (■)
    level_key = level_map.get(level)
    if level_key in marks:
        marks[level_key] = '■'
        
    return marks

# -------------------------
# 權限與工具函式
# -------------------------
def get_user_by_username(cursor, username):
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    return cursor.fetchone()

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
# 實習履歷生成邏輯
# -------------------------
def save_structured_data(cursor, student_id, data):
    """儲存結構化資料到 Student_Info"""
    try:
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

        # 儲存課程
        cursor.execute("DELETE FROM Course_Grades WHERE StuID=%s", (student_id,))
        for c in data.get('courses', []):
            if c.get('name'):
                cursor.execute("""
                    INSERT INTO Course_Grades (StuID, CourseName, Credits, Grade)
                    VALUES (%s,%s,%s,%s)
                """, (student_id, c['name'], c.get('credits'), c.get('grade')))
        return True
    except Exception as e:
        print("❌ 儲存結構化資料錯誤:", e)
        traceback.print_exc()
        return False


def get_student_info_for_doc(cursor, student_id):
    data = {}
    cursor.execute("SELECT * FROM Student_Info WHERE StuID=%s", (student_id,))
    data['info'] = cursor.fetchone() or {}
    cursor.execute("SELECT CourseName, Credits, Grade FROM Course_Grades WHERE StuID=%s", (student_id,))
    data['grades'] = cursor.fetchall() or []
    return data


def generate_application_form_docx(student_data, output_path):
    """根據模板生成 Word 履歷檔"""
    try:
        base_dir = os.path.dirname(__file__)
        template_path = os.path.abspath(os.path.join(base_dir, "..", "frontend", "static", "examples", "實習履歷(空白).docx"))
        if not os.path.exists(template_path):
            print("❌ 找不到模板：", template_path)
            return False

        doc = DocxTemplate(template_path)
        info = student_data.get("info", {})
        grades = student_data.get("grades", [])

        # 出生日期處理
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

        # 插入照片
        photo_path = info.get("PhotoPath")
        image_obj = None
        try:
            if photo_path and os.path.exists(photo_path):
                abs_photo_path = os.path.abspath(photo_path)
                image_obj = InlineImage(doc, abs_photo_path, width=Inches(1.2))
        except Exception as e:
            print(f"⚠️ 圖片載入錯誤: {e}")

        # 操行等級
        conduct_score = info.get('ConductScore', '')
        conduct_marks = {k: '□' for k in ['C_You','C_Jia','C_Yi','C_Bing','C_Ding']}
        mapping = {'優': 'C_You', '甲': 'C_Jia', '乙': 'C_Yi', '丙': 'C_Bing', '丁': 'C_Ding'}
        if conduct_score in mapping:
            conduct_marks[mapping[conduct_score]] = '■'

        # 證照處理（四類分類）
        certs = student_data.get("certifications", [])
        labor_certs = []
        intl_certs = []
        local_certs = []
        other_certs = []

        for cert in certs:
            name = cert.strip()
            if not name:
                continue
            if "勞" in name or "技師" in name:
                labor_certs.append(name)
            elif any(x in name.upper() for x in ["TOEIC", "TOEFL", "IELTS", "JLPT"]):
                intl_certs.append(name)
            elif "乙級" in name or "丙級" in name:
                local_certs.append(name)
            else:
                other_certs.append(name)

        # 若無資料則填入「無」
        def fmt_cert_list(lst):
            return "、".join(lst) if lst else "無"

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
            'LaborCerts': fmt_cert_list(labor_certs),
            'IntlCerts': fmt_cert_list(intl_certs),
            'LocalCerts': fmt_cert_list(local_certs),
            'OtherCerts': fmt_cert_list(other_certs),
            'Image_1': image_obj
        }
        context.update(conduct_marks)

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
        if session.get('role') != 'student' or not session.get('user_id'):
            return jsonify({"success": False, "message": "只有學生可以提交"}), 403

        user_id = session['user_id']
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        data = request.form.to_dict()
        courses = json.loads(data.get('courses', '[]'))
        photo = request.files.get('photo')

        # 儲存照片
        photo_path = None
        if photo:
            filename = secure_filename(photo.filename)
            photo_dir = os.path.join(UPLOAD_FOLDER, "photos")
            os.makedirs(photo_dir, exist_ok=True)
            photo_path = os.path.join(photo_dir, f"{user_id}_{filename}")
            photo.save(photo_path)

        # 證照名稱（僅名稱，無發證單位）
        cert_names = request.form.getlist('certification_name[]')
        certifications = [n.strip() for n in cert_names if n.strip()]

        # 查學號
        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        student_id = cursor.fetchone()['username']

        structured_data = {
            "name": data.get("name"),
            "birth_date": data.get("birth_date"),
            "gender": data.get("gender"),
            "phone": data.get("phone"),
            "email": data.get("email"),
            "address": data.get("address"),
            "conduct_score": data.get("conduct_score"),
            "autobiography": data.get("autobiography"),
            "certifications": certifications,
            "courses": courses,
            "photo_path": photo_path
        }

        if not save_structured_data(cursor, student_id, structured_data):
            conn.close()
            return jsonify({"success": False, "message": "資料儲存失敗"}), 500

        student_data_for_doc = get_student_info_for_doc(cursor, student_id)
        student_data_for_doc["info"]["PhotoPath"] = photo_path
        student_data_for_doc["certifications"] = certifications

        filename = f"{student_id}_履歷_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        if not generate_application_form_docx(student_data_for_doc, save_path):
            conn.close()
            return jsonify({"success": False, "message": "文件生成失敗"}), 500

        semester_id = get_current_semester_id(cursor)
        cursor.execute("""
            INSERT INTO resumes (user_id, semester_id, original_filename, filepath, status, created_at)
            VALUES (%s,%s,%s,%s,%s,NOW())
        """, (user_id, semester_id, filename, save_path, 'generated'))
        conn.commit()
        return jsonify({"success": True, "message": "履歷生成成功"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conn' in locals(): conn.close()


# -------------------------
# API：下載履歷
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
# API：查詢學生履歷列表
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
