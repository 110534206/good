from flask import Flask, render_template, request, jsonify
import mysql.connector
import re
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS

app = Flask(__name__, template_folder='../frontend/templates', static_folder='../frontend/static')
CORS(app, supports_credentials=True)

db = mysql.connector.connect(
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

    cursor = db.cursor()

    cursor.execute("SELECT * FROM student WHERE username = %s", (username,))
    if cursor.fetchone():
        return jsonify({"success": False, "message": "學號已存在"}), 409

    cursor.execute(
        "INSERT INTO student (username, password, email, role) VALUES (%s, %s, %s, %s)",
        (username, password, email, role)
    )
    db.commit()

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

    cursor = db.cursor()

    # 確認帳號是否已存在
    cursor.execute("SELECT * FROM teacher WHERE username = %s", (username,))
    if cursor.fetchone():
        return jsonify({"success": False, "message": "帳號已存在"}), 409

    # 寫入資料庫
    cursor.execute(
        "INSERT INTO teacher (username, password, role) VALUES (%s, %s, %s)",
        (username, hashed_password, role)
    )
    db.commit()

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

    cursor = db.cursor()

    # 確認帳號是否已存在
    cursor.execute("SELECT * FROM administrative WHERE username = %s", (username,))
    if cursor.fetchone():
        return jsonify({"success": False, "message": "帳號已存在"}), 409

    # 寫入資料庫
    cursor.execute(
        "INSERT INTO administrative (username, password, role) VALUES (%s, %s, %s)",
        (username, hashed_password, role)
    )
    db.commit()

    return jsonify({"success": True, "message": "行政人員註冊成功"})

# 登入API
@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(force=True)
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({"success": False, "message": "帳號或密碼不得為空"}), 400

    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM student WHERE username = %s", (username,))
    user = cursor.fetchone()

    if user is None:
        cursor.execute("SELECT * FROM teacher WHERE username = %s", (username,))
        user = cursor.fetchone()
        if user is None:
            cursor.execute("SELECT * FROM administrative WHERE username = %s", (username,))
            user = cursor.fetchone()

    if user and check_password_hash(user['password'], password):
        return jsonify({
            "success": True,
            "username": user['username'],
            "role": user['role']
        })
    else:
        return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401
# 頭像路由
@app.route('/profile')
def profile_page():
    return render_template('profile.html') 


# 頁面路由
@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route('/index')
def index_page():
    return render_template('index.html')

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

if __name__ == '__main__':
    app.run(host='0.0.0.0' , port=5000, debug=True)