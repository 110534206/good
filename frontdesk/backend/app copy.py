from flask import Flask, render_template, request, redirect, jsonify
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

# 共用註冊函數
def register_user(username, raw_password, role, email=""):
    if not username or not raw_password or not role:
        return {"success": False, "message": "必要欄位缺失"}, 400

    hashed_password = generate_password_hash(raw_password)

    conn = get_db()
    cursor = conn.cursor()

    # 允許相同 username 不同角色，但不允許同 username+role 重複
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
    cursor.close()
    conn.close()

 # 顯示中文角色名稱
    role_display = {
        "student": "學生",
        "teacher": "教師",
        "administrative": "行政人員"
    }.get(role, role)
    
    return {"success": True, "message": f"{role_display}註冊成功"}, 201

# 學生註冊 API
@app.route("/api/register_student", methods=["POST"])
def register_student():
    username = request.form.get("username")
    raw_password = request.form.get("password")
# 學號格式檢查，必須是9位數字
    if not re.match(r"^\d{9}$", username):
        return jsonify({"success": False, "message": "學號格式錯誤"}), 400
# 身分證字號格式檢查，第一碼大寫英文字母，第二碼1或2，後面8碼數字

    if not re.match(r"^[A-Z][1-2]\d{8}$", raw_password):
        return jsonify({"success": False, "message": "身分證字號格式錯誤"}), 400

    email = f"{username}@stu.ukn.edu.tw"
    result, status_code = register_user(username, raw_password, "student", email)
    return jsonify(result), status_code

# 教師註冊 API
@app.route("/api/register_teacher", methods=["POST"])
def register_teacher():
    username = request.form.get("username")
    raw_password = request.form.get("password")

# 帳號與密碼皆為必填欄位
    if not username or not raw_password:
        return jsonify({"success": False, "message": "帳號或密碼不得為空"}), 400

    result, status_code = register_user(username, raw_password, "teacher", "")
    return jsonify(result), status_code

# 行政人員註冊 API
@app.route("/api/register_administrative", methods=["POST"])
def register_administrative():
    username = request.form.get("username")
    raw_password = request.form.get("password")

    # 帳號與密碼皆為必填欄位
    if not username or not raw_password:
        return jsonify({"success": False, "message": "帳號或密碼不得為空"}), 400

    result, status_code = register_user(username, raw_password, "administrative", "")
    return jsonify(result), status_code

# 登入 API
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

        # 查詢三種角色
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        users = cursor.fetchall()

        for user in users:
            if check_password_hash(user['password'], password):
                roles.append(user['role'])

        if roles:
            # 多角色導向 /profile，單角色直接導向對應首頁
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

# 取得使用者資料 API
@app.route("/api/profile", methods=["GET"])
def get_profile():
    username = request.args.get("username")
    role = request.args.get("role")

    if not username or role not in ["student", "teacher", "administrative"]:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT username, email, role FROM users WHERE username = %s AND role = %s", (username, role))
    user = cursor.fetchone()

    cursor.close()
    conn.close()

    if not user:
        return jsonify({"success": False, "message": "使用者不存在"}), 404

    if not user.get("email"):
        user["email"] = ""

    return jsonify({"success": True, "user": user, "role": role})

# 確認角色 API
@app.route('/api/confirm_role', methods=['POST'])
def confirm_role():
    data = request.get_json()
    username = data.get("username")
    role = data.get("role")

    if role not in ['student', 'teacher', 'administrative']:
        return jsonify({"success": False, "message": "無效角色"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE username = %s AND role = %s", (username, role))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user:
        return jsonify({"success": False, "message": "帳號不存在於該角色"}), 404

    return jsonify({"success": True, "redirect_url": f"/{role}_home"})

# 頁面路由
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
    app.run(host='0.0.0.0', port=5000, debug=True)