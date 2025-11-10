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
        return '丁' # 若無效，預設為不及格 (丁)

    if 90 <= score <= 99:
        return '優'
    elif 80 <= score <= 89:
        return '甲'
    elif 70 <= score <= 79:
        return '乙'
    elif 60 <= score <= 69:
        return '丙'
    # 這裡的邏輯與您的規則 (未滿 60 分為丁等) 相符
    else: 
        return '丁'

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
    """將結構化資料寫入資料表"""
    try:
        cursor.execute("""
            INSERT INTO Student_Info (StuID, StuName, BirthDate, Gender, Phone, Email, Address, ConductScore, Autobiography, PhotoPath)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE 
                StuName=VALUES(StuName), BirthDate=VALUES(BirthDate), Gender=VALUES(Gender), Phone=VALUES(Phone),
                Email=VALUES(Email), Address=VALUES(Address), ConductScore=VALUES(ConductScore),
                Autobiography=VALUES(Autobiography), PhotoPath=VALUES(PhotoPath), UpdatedAt=NOW()
        """, (
            student_id, data.get('name'), data.get('birth_date'), data.get('gender'),
            data.get('phone'), data.get('email'), data.get('address'),
            data.get('conduct_score'), data.get('autobiography'), data.get('photo_path')
        ))

        cursor.execute("DELETE FROM Course_Grades WHERE StuID=%s", (student_id,))
        for c in data.get('courses', []):
            if c.get('name'):
                cursor.execute("""
                    INSERT INTO Course_Grades (StuID, CourseName, Credits, Grade)
                    VALUES (%s,%s,%s,%s)
                """, (student_id, c['name'], c.get('credits'), c.get('grade')))
        return True
    except Exception as e:
        print("❌ 寫入結構化資料錯誤:", e)
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
    """根據模板生成 Word 履歷檔（支援圖片）"""
    try:
        base_dir = os.path.dirname(__file__)
        template_path = os.path.abspath(os.path.join(base_dir, "..", "frontend", "static", "examples", "實習履歷(空白).docx"))
        if not os.path.exists(template_path):
            print("❌ 找不到模板：", template_path)
            return False

        doc = DocxTemplate(template_path)
        info = student_data.get("info", {})
        grades = student_data.get("grades", [])

        # 出生年月日處理
        def fmt_date(val):
            # 檢查是否有 strftime 方法（適用於 datetime.date 和 datetime.datetime）
            if hasattr(val, 'strftime'): 
                return val.strftime("%Y-%m-%d")
            if isinstance(val, str) and len(val) >= 10:
                # 處理來自表單的字串，例如 '2005-11-10'
                return val.split("T")[0]
            return ""

        bdate = fmt_date(info.get("BirthDate"))
        year, month, day = ("", "", "")
        if bdate:
            try:
                # 確保分拆時不會崩潰
                year, month, day = bdate.split("-")
            except:
                pass

        # ✅ 插入照片（簡化並強化錯誤處理）
        photo_path = info.get("PhotoPath")
        image_obj = None  # 預設為 None，讓 docxtpl 在找不到圖片時安全地忽略

        try:
            if photo_path and os.path.exists(photo_path):
                # 直接將絕對路徑傳給 InlineImage
                abs_photo_path = os.path.abspath(photo_path)
                image_obj = InlineImage(doc, abs_photo_path, width=Inches(1.2))
            else:
                print(f"⚠️ 找不到圖片檔案: {photo_path}")
        except Exception as e:
            # 捕獲所有圖片相關錯誤 (包括 UnrecognizedImageError)
            print(f"❌ 圖片載入/格式錯誤: {photo_path}, 錯誤: {e}")
            image_obj = None # 確保錯誤時傳入 None，避免渲染崩潰

        # 操行成績複選框處理 (新增)
        conduct_score = info.get('ConductScore', '')

        # 預設所有複選框變數為 '□' (空心方塊)
        conduct_marks = {
            'C_You': '□', 
            'C_Jia': '□', 
            'C_Yi': '□', 
            'C_Bing': '□', 
            'C_Ding': '□'
        }
        # 將選中的選項從 '□' 替換為 '■' (實心方塊)
        if conduct_score == '優': conduct_marks['C_You'] = '■'
        elif conduct_score == '甲': conduct_marks['C_Jia'] = '■'
        elif conduct_score == '乙': conduct_marks['C_Yi'] = '■'
        elif conduct_score == '丙': conduct_marks['C_Bing'] = '■'
        elif conduct_score == '丁': conduct_marks['C_Ding'] = '■'

        # Word 模板變數替換
        context = {
            'StuID': info.get('StuID', ''),
            'StuName': info.get('StuName', ''),
            'BirthYear': year, 'BirthMonth': month, 'BirthDay': day,
            'Gender': info.get('Gender', ''),
            'Phone': info.get('Phone', ''),
            'Email': info.get('Email', ''),
            'Address': info.get('Address', ''),
            'ConductScore': info.get('ConductScore', ''),
            'Autobiography': info.get('Autobiography', ''),
            'courses': [
                {'name': g.get('CourseName', ''), 'credits': g.get('Credits', ''), 'grade': g.get('Grade', '')}
                for g in grades
            ],
            'Image_1': image_obj
        }

        context.update(conduct_marks)

        doc.render(context)
        doc.save(output_path)
        print(f"✅ 履歷文件已生成: {output_path}")
        return True

    except Exception as e:
        print("❌ 生成 Word 檔錯誤：", e)
        traceback.print_exc()
        return False

# -------------------------
# API：提交並生成履歷
# -------------------------
@resume_bp.route('/api/submit_and_generate', methods=['POST'])
def submit_and_generate_api():
    try:
        if session.get('role') != 'student' or not session.get('user_id'):
            return jsonify({"success": False, "message": "只有學生可以提交申請"}), 403

        user_id = session['user_id']
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 接收 multipart 表單
        data = request.form.to_dict()
        courses = json.loads(data.get('courses', '[]'))
        photo = request.files.get('photo')

        photo_path = None
        if photo:
            filename = secure_filename(photo.filename)
            photo_dir = os.path.join(UPLOAD_FOLDER, "photos")
            os.makedirs(photo_dir, exist_ok=True)
            photo_path = os.path.join(photo_dir, f"{user_id}_{filename}")
            photo.save(photo_path)

        # 查出學號
        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        u = cursor.fetchone()
        student_id = u['username']

        structured_data = {
            "name": data.get("name"),
            "birth_date": data.get("birth_date"),
            "gender": data.get("gender"),
            "phone": data.get("phone"),
            "email": data.get("email"),
            "address": data.get("address"),
            "conduct_score": data.get("conduct_score"),
            "autobiography": data.get("autobiography"),
            "courses": courses,
            "photo_path": photo_path
        }

        if not save_structured_data(cursor, student_id, structured_data):
            conn.close()
            return jsonify({"success": False, "message": "資料儲存失敗"}), 500

        student_data_for_doc = get_student_info_for_doc(cursor, student_id)
        student_data_for_doc["info"]["PhotoPath"] = photo_path

        filename = f"{student_id}_履歷_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        save_path = os.path.join(UPLOAD_FOLDER, filename)

        if not generate_application_form_docx(student_data_for_doc, save_path):
            conn.close()
            return jsonify({"success": False, "message": "文件生成失敗"}), 500

        # 寫入 resumes 紀錄表
        semester_id = get_current_semester_id(cursor)
        cursor.execute("""
            INSERT INTO resumes (user_id, semester_id, original_filename, filepath, status, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
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
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT filepath, original_filename, user_id FROM resumes WHERE id=%s", (resume_id,))
        r = cursor.fetchone()
        if not r:
            return jsonify({"success": False, "message": "找不到履歷"}), 404
        if not can_access_target_resume(cursor, session['user_id'], session['role'], r['user_id']):
            return jsonify({"success": False, "message": "沒有權限下載"}), 403
        path = r['filepath']
        if not os.path.exists(path):
            return jsonify({"success": False, "message": "檔案不存在"}), 404
        return send_file(path, as_attachment=True, download_name=r['original_filename'])
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 查詢自己的履歷列表 (學生)
# -------------------------
@resume_bp.route('/api/get_my_resumes', methods=['GET'])
def get_my_resumes():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    user_id = session['user_id']

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT r.id, r.original_filename, r.status, r.comment, r.note, r.created_at AS upload_time
            FROM resumes r
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
        """, (user_id,))
        resumes = cursor.fetchall()

        for r in resumes:
            if isinstance(r.get('upload_time'), datetime):
                r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"success": True, "resumes": resumes})
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# -------------------------
# 頁面路由
# -------------------------
@resume_bp.route('/upload_resume')
def upload_resume_page():
    return render_template('resume/upload_resume.html')


#ai 編輯履歷頁面
@resume_bp.route('/ai_edit_resume')
def ai_edit_resume_page():
    return render_template('resume/ai_edit_resume.html')