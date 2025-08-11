from flask import Flask, render_template, request, send_file, redirect, jsonify
import mysql.connector
import re
import os
import traceback
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS

# -------------------------
# Flask 與 CORS 設定
# -------------------------
app = Flask(__name__, template_folder='../frontend/templates', static_folder='../frontend/static')
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
# 指定上傳資料夾
UPLOAD_FOLDER = './uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/api/upload_resume', methods=['POST'])
def upload_resume_api():
    conn = None
    cursor = None
    try:
        file = request.files.get('resume')
        username = request.form.get('username') 
        original_filename = file.filename

        print("收到檔案:", file.filename if file else None)
        print("收到帳號:", username)

        if not file or not username:
            return jsonify({"success": False, "message": "缺少檔案或帳號資訊"}), 400

        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

        from werkzeug.utils import secure_filename
        safe_filename = secure_filename(file.filename)
        filename = f"{username}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{safe_filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

        file.save(filepath)
        filesize = round(os.path.getsize(filepath) / (1024 * 1024), 3)  # MB

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        result = cursor.fetchone()
        if not result:
            return jsonify({"success": False, "message": "找不到對應使用者"}), 404

        user_id = result[0]

        # 先查該使用者是否已有履歷
        cursor.execute("SELECT id FROM resumes WHERE user_id = %s", (user_id,))
        resume = cursor.fetchone()

        if resume:
            # 已有履歷，更新資料
           resume_id = resume[0]
           cursor.execute("""
               UPDATE resumes
               SET filename=%s, original_filename=%s, filepath=%s, filesize=%s, status=%s
            WHERE id=%s
            """, (filename, original_filename, filepath, filesize, 'uploaded', resume_id))
        else:
            # 沒有履歷，新增一筆
            cursor.execute("""
               INSERT INTO resumes (user_id, filename, original_filename, filepath, filesize, status)
               VALUES (%s, %s, %s, %s, %s, %s)
             """, (user_id, filename, original_filename, filepath, filesize, 'uploaded'))
            resume_id = cursor.lastrowid

        conn.commit()

        print("resume_id:", resume_id)

        return jsonify({
            "success": True,
            "message": "履歷上傳成功",
            "resume_id": resume_id
        }), 201

    except Exception as e:
        tb = traceback.format_exc()
        print("上傳履歷時發生錯誤：", tb)
        return jsonify({"success": False, "message": str(e), "trace": tb}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# -------------------------
# API - 取得履歷狀態
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
# API - 取得所有履歷清單
# -------------------------
@app.route('/api/get_all_resumes', methods=['GET'])
def get_all_resumes():
    username = request.args.get('username')
    if not username:
        return jsonify({"success": False, "message": "缺少 username 參數"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if not user:
            return jsonify({"success": False, "message": "找不到使用者"}), 404

        user_id = user['id']

        cursor.execute("""
            SELECT id, filename, filepath, filesize, status, '' AS comment, uploaded_at AS upload_time
            FROM resumes WHERE user_id = %s
        """, (user_id,))
        resumes = cursor.fetchall()

        # 將 datetime 轉成字串（前端會用 new Date() 解析）
        for r in resumes:
            if isinstance(r.get('upload_time'), datetime):
                r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"success": True, "resumes": resumes})

    finally:
        cursor.close()
        conn.close()


# -------------------------
# API - 留言提交
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

    try:
        # 先確認履歷存在
        cursor.execute("SELECT id FROM resumes WHERE id = %s", (resume_id,))
        if cursor.fetchone() is None:
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        cursor.execute("UPDATE resumes SET comment = %s WHERE id = %s", (comment, resume_id))
        conn.commit()

        return jsonify({"success": True, "message": "留言更新成功"})

    except Exception as e:
        print("留言更新錯誤:", e)
        return jsonify({"success": False, "message": "留言更新失敗"}), 500

    finally:
        cursor.close()
        conn.close()

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

    if not resume:
        return jsonify({"success": False, "message": "找不到該履歷"}), 404

    filepath = resume['filepath']
    original_filename = resume['original_filename']

    if not os.path.exists(filepath):
        return jsonify({"success": False, "message": "檔案不存在"}), 404

    return send_file(filepath, as_attachment=True, download_name=original_filename)

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

@app.route('/resume_list')
def resume_list():
    return render_template('resume_list.html')

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
