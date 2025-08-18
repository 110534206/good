from flask import Flask, render_template, request, send_file, redirect, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_cors import CORS
import mysql.connector
import traceback
import os
import re
from datetime import datetime

# -------------------------
# Flask 與 CORS 設定
# -------------------------
app = Flask(__name__, template_folder='../frontend/templates', static_folder='../frontend/static')
app.config['UPLOAD_FOLDER'] = './uploads'
CORS(app, supports_credentials=True)

# -------------------------
# 資料庫連線
# -------------------------
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="user"
    )

# -------------------------
# 共用註冊函數
# -------------------------
def register_user(username, raw_password, role, email=""):
    if not username or not raw_password or not role:
       return {"success": False, "message": "必要欄位缺失"}, 400 

    hashed_password = generate_password_hash(raw_password)

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE username = %s AND role = %s", (username, role))
    if cursor.fetchone():
        cursor.close()
        conn.close()
        return {"success": False, "message": "此帳號已註冊為該身分"}, 409

    cursor.execute(
        "INSERT INTO users (username, password, role, email) VALUES (%s, %s, %s, %s)",
        (username, hashed_password, role, email)
    )
    conn.commit()
    user_id = cursor.lastrowid
    print("使用者ID:", user_id)
    cursor.close()
    conn.close()

    role_display = {
        "student": "學生",
        "teacher": "教師",
        "administrative": "行政人員"
    }.get(role, role)
    
    return {"success": True, "message": f"{role_display}註冊成功"}, 201

# -------------------------
# API - 註冊
# -------------------------
@app.route("/api/register_student", methods=["POST"])
def register_student():
    username = request.form.get("username")
    raw_password = request.form.get("password")
    # 學號格式檢查
    if not re.match(r"^\d{9}$", username):
        return jsonify({"success": False, "message": "學號格式錯誤"}), 400
    # 身分證字號格式檢查
    if not re.match(r"^[A-Z][1-2]\d{8}$", raw_password):
        return jsonify({"success": False, "message": "身分證字號格式錯誤"}), 400

    email = f"{username}@stu.ukn.edu.tw"
    result, status_code = register_user(username, raw_password, "student", email)
    return jsonify(result), status_code

@app.route("/api/register_teacher", methods=["POST"])
def register_teacher():
    username = request.form.get("username")
    password = request.form.get("password")
    result, status = register_user(username, password, role="teacher")
    return jsonify(result), status

@app.route("/api/register_administrative", methods=["POST"])
def register_administrative():
    username = request.form.get("username")
    password = request.form.get("password")
    result, status = register_user(username, password, role="administrative")
    return jsonify(result), status

# -------------------------
# API - 登入
# -------------------------
@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(force=True)
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({"success": False, "message": "帳號或密碼不得為空"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        roles = []
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        users = cursor.fetchall()

        for user in users:
            if check_password_hash(user['password'], password):
                roles.append(user['role'])

        if roles:
            redirect_url = "/profile" if len(roles) > 1 else f"/{roles[0]}_home"
            return jsonify({
                "success": True,
                "username": username,
                "roles": roles,
                "redirect_url": redirect_url
            })
        else:
            return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 取得個人資料
# -------------------------
@app.route("/api/profile", methods=["GET"])
def get_profile():
    username = request.args.get("username")
    role = request.args.get("role")

    if not username or role not in ["student", "teacher", "administrative"]:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
    SELECT username, email, role, name, department, className AS classname
    FROM users WHERE username = %s AND role = %s
    """, (username, role))    
    user = cursor.fetchone()

    cursor.close()
    conn.close()

    if not user:
        return jsonify({"success": False, "message": "使用者不存在"}), 404

    if not user.get("email"):
        user["email"] = ""

    return jsonify({"success": True, "user": user, "role": role})

# -------------------------
# API - 更新個人資料
# -------------------------
@app.route("/api/saveProfile", methods=["POST"])
def save_profile():
    data = request.get_json()
    username = data.get("number")  # 前端使用 number 作為學號
    role_display = data.get("role")
    name = data.get("name")
    department = data.get("department")
    class_name = data.get("classname")

    if not username or not role_display or not name or not department or not class_name:
        return jsonify({"success": False, "message": "缺少必要欄位"}), 400

    role_map = {
        "學生": "student",
        "教師": "teacher",
        "行政人員": "administrative"
    }
    role = role_map.get(role_display)
    if not role:
        return jsonify({"success": False, "message": "身分錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE users SET name=%s, department=%s, className=%s WHERE username=%s AND role=%s
        """, (name, department, class_name, username, role))

        if cursor.rowcount == 0:
            cursor.execute("SELECT 1 FROM users WHERE username=%s AND role=%s", (username, role))
            if cursor.fetchone() is None:
                return jsonify({"success": False, "message": "找不到該使用者資料"}), 404
            else:
                return jsonify({"success": True, "message": "資料已儲存成功"}), 200

        conn.commit()
        return jsonify({"success": True, "message": "資料更新成功"})

    except Exception as e:
        print("更新資料錯誤:", e)
        return jsonify({"success": False, "message": "資料庫錯誤"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 上傳履歷
# -------------------------
@app.route('/api/upload_resume', methods=['POST'])
def upload_resume_api():
    # 檢查是否有檔案
    if 'resume' not in request.files:
        return jsonify({"success": False, "message": "未上傳檔案"}), 400

    file = request.files['resume']
    username = request.form.get('username')

    # 檢查使用者帳號
    if not username:
        return jsonify({"success": False, "message": "缺少使用者帳號"}), 400

    # 檢查檔名
    if file.filename == '':
        return jsonify({"success": False, "message": "檔案名稱為空"}), 400

    # 取得原始檔名(用於前端顯示與DB紀錄)
    original_filename = file.filename

    # 產生安全的檔名並加上時間戳
    safe_filename = secure_filename(original_filename)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    stored_filename = f"{timestamp}_{safe_filename}"

    # 儲存檔案
    upload_folder = app.config['UPLOAD_FOLDER']
    save_path = os.path.join(upload_folder, stored_filename)
    file.save(save_path)

    # 查詢使用者 ID
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
    user = cursor.fetchone()
    if not user:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "找不到使用者"}), 404

    user_id = user[0]
    filesize = os.path.getsize(save_path)  # 取得檔案大小 (bytes)

    # 寫入資料庫
    cursor.execute("""
        INSERT INTO resumes (user_id, original_filename, filepath, filesize, status, created_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
    """, (user_id, original_filename, save_path, filesize, 'uploaded'))

    resume_id = cursor.lastrowid

    conn.commit()
    cursor.close()
    conn.close()

    # 回傳資訊給前端
    return jsonify({
        "success": True,
        "resume_id": resume_id,
        "filename": original_filename,  # 前端用的原始檔名
        "filesize": filesize,
        "status": "uploaded",
        "message": "履歷上傳成功"
    })

# -------------------------
# API - 審核履歷
# -------------------------
@app.route('/api/review_resume', methods=['POST'])
def review_resume_api():
    data = request.get_json()
    resume_id = data.get('resume_id')
    status = data.get('status')
    comment = data.get('comment', '').strip()
    
    if not resume_id or status not in ['approved', 'rejected']:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor()

    # 確認履歷存在
    cursor.execute("SELECT id FROM resumes WHERE id = %s", (resume_id,))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "找不到該履歷"}), 404

    try:
        # 更新狀態和備註（如果有提供）
        if comment:
            cursor.execute(
                "UPDATE resumes SET status = %s, comment = %s WHERE id = %s",
                (status, comment, resume_id)
            )
        else:
            cursor.execute(
                "UPDATE resumes SET status = %s WHERE id = %s",
                (status, resume_id)
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": "更新失敗"}), 500
    finally:
        cursor.close()
        conn.close()

    return jsonify({"success": True, "message": f"履歷狀態已更新為 {status}"})

# -------------------------
# API - 更新履歷
# -------------------------
@app.route('/api/update_resume_field', methods=['POST'])
def update_resume_field():
    data = request.get_json()

    resume_id = data.get('resume_id')
    field = data.get('field')
    value = (data.get('value') or '').strip()

    allowed_fields = {
        "comment": "comment",
        "note": "note"
    }

    # 驗證 resume_id 與 field
    try:
        resume_id = int(resume_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "resume_id 必須是數字"}), 400

    if field not in allowed_fields:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        sql = f"UPDATE resumes SET {allowed_fields[field]} = %s WHERE id = %s"
        cursor.execute(sql, (value, resume_id))
        conn.commit()
        return jsonify({"success": True, "field": field, "resume_id": resume_id}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 查詢履歷狀態
# -------------------------
@app.route('/api/resume_status', methods=['GET'])
def resume_status():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT status FROM resumes WHERE id = %s", (resume_id,))
    resume = cursor.fetchone()
    cursor.close()
    conn.close()

    if not resume:
        return jsonify({"success": False, "message": "找不到該履歷"}), 404

    return jsonify({"success": True, "status": resume['status']})

# -------------------------
# API - 所有履歷清單
# -------------------------
@app.route('/api/get_all_resumes', methods=['GET'])
def get_all_resumes():
    username = request.args.get('username')
    if not username:
        return jsonify({"success": False, "message": "缺少 username 參數"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
    user = cursor.fetchone()
    if not user:
        return jsonify({"success": False, "message": "找不到使用者"}), 404

    user_id = user['id']
    cursor.execute("""
      SELECT id, original_filename, filepath, filesize, status, comment, note, created_at AS upload_time
      FROM resumes WHERE user_id = %s ORDER BY created_at DESC
     """, (user_id,))
    resumes = cursor.fetchall()
    for r in resumes:
        if isinstance(r.get('upload_time'), datetime):
            r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")

    cursor.close()
    conn.close()
    return jsonify({"success": True, "resumes": resumes})

# -------------------------
# API - 留言更新
# -------------------------
@app.route('/api/submit_comment', methods=['POST'])
def submit_comment():
    data = request.get_json()
    resume_id = data.get('resume_id')
    comment = data.get('comment', '').strip()

    if not resume_id or not comment:
        return jsonify({"success": False, "message": "缺少必要參數"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE resumes SET comment = %s WHERE id = %s", (comment, resume_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "留言更新成功"})

# -------------------------
# API - 下載履歷
# -------------------------
@app.route('/api/download_resume', methods=['GET'])
def download_resume():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT filepath, original_filename FROM resumes WHERE id = %s", (resume_id,))
    resume = cursor.fetchone()
    cursor.close()
    conn.close()

    if not resume or not os.path.exists(resume['filepath']):
        return jsonify({"success": False, "message": "找不到檔案"}), 404

    return send_file(resume['filepath'], as_attachment=True, download_name=resume['original_filename'])

# -------------------------
# API - 刪除履歷
# -------------------------
@app.route('/api/delete_resume', methods=['DELETE'])
def delete_resume():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT filepath FROM resumes WHERE id = %s", (resume_id,))
    result = cursor.fetchone()
    if not result:
        return jsonify({"success": False, "message": "找不到該履歷"}), 404

    filepath = result[0]
    if os.path.exists(filepath):
        os.remove(filepath)

    cursor.execute("DELETE FROM resumes WHERE id = %s", (resume_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "履歷已刪除"})

# -------------------------
# API - 審核履歷
# -------------------------
@app.route('/api/approve_resume', methods=['POST'])
def approve_resume():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    conn = get_db()
    cursor = conn.cursor()

    # 檢查履歷是否存在
    cursor.execute("SELECT id FROM resumes WHERE id = %s", (resume_id,))
    if not cursor.fetchone():
        return jsonify({"success": False, "message": "找不到該履歷"}), 404

    # 更新狀態為 'approved'
    cursor.execute("UPDATE resumes SET status = %s WHERE id = %s", ("approved", resume_id))
    conn.commit()

    cursor.close()
    conn.close()

    return jsonify({"success": True, "message": "履歷已標記為完成"})

# -------------------------
# API - 取得所有學生履歷
# -------------------------
@app.route('/api/get_all_students_resumes', methods=['GET'])
def get_all_students_resumes():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
      SELECT r.*, u.username
      FROM resumes r
      JOIN users u ON r.user_id = u.id
      WHERE u.role = 'student'
      ORDER BY r.created_at DESC
    """)
    resumes = cursor.fetchall()
    for r in resumes:
        r['upload_time'] = r['created_at'].strftime("%Y-%m-%d %H:%M:%S")
    cursor.close()
    conn.close()
    return jsonify({"success": True, "resumes": resumes})


# -------------------------
# # API - 接受履歷
# -------------------------
@app.route('/api/reject_resume', methods=['POST'])
def reject_resume():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"})

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE resumes SET status = 'rejected' WHERE id = %s", (resume_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True})

# -------------------------
# 頁面路由
# -------------------------
@app.route('/profile')
def profile_page():
    return render_template('profile.html')

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route('/index')
def index_page():
    role = request.args.get('role')
    if role == "student":
        return redirect('/student_home')
    elif role == "teacher":
        return redirect('/teacher_home')
    elif role == "administrative":
        return redirect('/administrative_home')
    else:
        return render_template("index.html")

@app.route('/visitor_home')
def visitor_home():
    return render_template('visitor_home.html') 

@app.route('/student_home')
def student_home():
    return render_template('student_home.html')

@app.route('/teacher_home')
def teacher_home():
    return render_template('teacher_home.html')

@app.route('/administrative_home')
def administrative_home():
    return render_template('administrative_home.html')

@app.route('/upload_resume')
def upload_resume():
    return render_template('upload_resume.html')

@app.route('/review_resume')
def review_resume():
    return render_template('review_resume.html')

@app.route('/ai_edit_resume')
def ai_edit_resume():
    return render_template('ai_edit_resume.html')

@app.route('/fill_preferences')
def fill_preferences():
    return render_template('fill_preferences.html')

@app.route('/notifications')
def notifications():
    return render_template('notifications.html')

@app.route("/register_choice")
def register_choice():
    return render_template("register_choice.html")

@app.route("/register_student")
def register_student_page():
    return render_template("register_student.html")

@app.route("/register_teacher")
def register_teacher_page():
    return render_template("register_teacher.html")

@app.route("/register_administrative")
def register_administrative_page():
    return render_template("register_administrative.html")

@app.route("/login-confirm")
def login_confirm_page():
    return render_template("login-confirm.html")

# -------------------------
# 主程式入口
# -------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
