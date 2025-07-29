from flask import Flask, render_template, request,redirect, jsonify
import mysql.connector
import re
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS

app = Flask(__name__, template_folder='../frontend/templates', static_folder='../frontend/static')
CORS(app, supports_credentials=True)

# MySQL 資料庫連線設定
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="user"
    )

# 學生註冊API
@app.route("/api/register_student", methods=["POST"])
def register_student():
    username = request.form.get("username")
    raw_password = request.form.get("password")

    if not re.match(r"^\d{9}$", username):
        return jsonify({"success": False, "message": "學號格式錯誤"}), 400

    if not re.match(r"^[A-Z][1-2]\d{8}$", raw_password):
        return jsonify({"success": False, "message": "身分證字號格式錯誤"}), 400

    email = f"{username}@stu.ukn.edu.tw"
    password = generate_password_hash(raw_password)
    role = "student"

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM student WHERE username = %s", (username,))
    if cursor.fetchone():
        return jsonify({"success": False, "message": "學號已存在"}), 409

    cursor.execute(
        "INSERT INTO student (username, password, email, role) VALUES (%s, %s, %s, %s)",
        (username, password, email, role)
    )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"success": True, "message": "學生註冊成功"})

# 教師註冊 API
@app.route("/api/register_teacher", methods=["POST"])
def register_teacher():
    username = request.form.get("username")
    raw_password = request.form.get("password")

    if not username or not raw_password:
        return jsonify({"success": False, "message": "帳號與密碼皆為必填"}), 400

    # 可以視需求添加格式驗證，例如教職員代碼格式檢查
    hashed_password = generate_password_hash(raw_password)
    role = "teacher"

    conn = get_db()
    cursor = conn.cursor()

    # 確認帳號是否已存在
    cursor.execute("SELECT * FROM teacher WHERE username = %s", (username,))
    if cursor.fetchone():
        return jsonify({"success": False, "message": "帳號已存在"}), 409

    # 寫入資料庫
    cursor.execute(
        "INSERT INTO teacher (username, password, role) VALUES (%s, %s, %s)",
        (username, hashed_password, role)
    )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"success": True, "message": "教師註冊成功"})

# 行政人員註冊 API
@app.route("/api/register_administrative", methods=["POST"])
def register_administrative():
    username = request.form.get("username")
    raw_password = request.form.get("password")

    if not username or not raw_password:
        return jsonify({"success": False, "message": "帳號與密碼皆為必填"}), 400

    # 可以視需求添加格式驗證，例如教職員代碼格式檢查
    hashed_password = generate_password_hash(raw_password)
    role = "administrative"

    conn = get_db()
    cursor = conn.cursor()

    # 確認帳號是否已存在
    cursor.execute("SELECT * FROM administrative WHERE username = %s", (username,))
    if cursor.fetchone():
        return jsonify({"success": False, "message": "帳號已存在"}), 409

    # 寫入資料庫
    cursor.execute(
        "INSERT INTO administrative (username, password, role) VALUES (%s, %s, %s)",
        (username, hashed_password, role)
    )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"success": True, "message": "行政人員註冊成功"})

# 登入API
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

        # 搜尋學生
        cursor.execute("SELECT * FROM student WHERE username = %s", (username,))
        student = cursor.fetchone()
        if student and check_password_hash(student['password'], password):
            roles.append("student")

        # 搜尋教師
        cursor.execute("SELECT * FROM teacher WHERE username = %s", (username,))
        teacher = cursor.fetchone()
        if teacher and check_password_hash(teacher['password'], password):
            roles.append("teacher")

        # 搜尋行政人員
        cursor.execute("SELECT * FROM administrative WHERE username = %s", (username,))
        admin = cursor.fetchone()
        if admin and check_password_hash(admin['password'], password):
            roles.append("administrative")

        if roles:
            role = roles[0]  # 預設使用第一個角色導向
            redirect_url = f"/{role}_home"
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

# 取得使用者資料API
@app.route("/api/profile", methods=["GET"])
def get_profile():
    username = request.args.get("username")
    role = request.args.get("role")

    if not username or role not in ["student", "teacher", "administrative"]:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    conn = get_db() 
    cursor = conn.cursor(dictionary=True)
    cursor.execute(f"SELECT username, email FROM {role} WHERE username = %s", (username,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user:
        return jsonify({"success": False, "message": "使用者不存在"}), 404

    return jsonify({"success": True, "user": user})

@app.route('/api/save_profile', methods=['POST'])
def save_profile():
    data = request.json
    # TODO: 寫入資料庫或其他處理
    # 假設處理成功
    return jsonify({"message": "儲存成功"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

# 確認角色API
@app.route('/api/confirm_role', methods=['POST'])
def confirm_role():
    data = request.get_json()
    username = data.get("username")
    role = data.get("role")

    if role not in ['student', 'teacher', 'administrative']:
        return jsonify({"success": False, "message": "無效角色"}), 400

    conn = get_db()  # ✅ 正確取得連線
    cursor = conn.cursor(dictionary=True)
    cursor.execute(f"SELECT * FROM {role} WHERE username = %s", (username,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()  # ✅ 用 conn

    if not user:
        return jsonify({"success": False, "message": "帳號不存在於該角色"}), 404

    return jsonify({"success": True, "redirect_url": f"/{role}_home"})

# 頭像路由
@app.route('/profile')
def profile_page():
    return render_template('profile.html') 

# 頁面路由 登入-使用者主頁-註冊選擇
@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route('/index')
def index_page():
    role = request.args.get('role')  # 或從 session/localStorage 傳來
    if role == "student":
        return redirect('/student_home')
    elif role == "teacher":
        return redirect('/teacher_home')
    elif role == "administrative":
        return redirect('/administrative_home')
    else:
        return render_template("index.html")  # 作為預設頁（可選）

@app.route('/student_home')
def student_home():
    return render_template('student_home.html')

@app.route('/teacher_home')
def teacher_home():
    return render_template('teacher_home.html')

@app.route('/administrative_home')
def administrative_home():
    return render_template('administrative_home.html')

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

if __name__ == '__main__':
    app.run(host='0.0.0.0' , port=5000, debug=True)