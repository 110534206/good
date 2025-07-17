from flask import Flask, render_template, request, jsonify
import mysql.connector
import re
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS

app = Flask(__name__, template_folder='../frontend/templates', static_folder='../frontend/static')
CORS(app, supports_credentials=True)

# 建立資料庫連線
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",
    database="user"
)

# 註冊學生帳號（後端自動生成email）
@app.route("/api/register_student", methods=["POST"])
def register_student():
    username = request.form.get("username")
    raw_password = request.form.get("password")  # 原始身分證字號，先不要加密

    # 學號格式驗證
    if not re.match(r"^\d{9}$", username):
        return jsonify({"success": False, "message": "學號格式錯誤"}), 400

    # 身分證格式驗證（用原始密碼驗證）
    if not re.match(r"^[A-Z][1-2]\d{8}$", raw_password):
        return jsonify({"success": False, "message": "身分證字號格式錯誤"}), 400

    email = f"{username}@stu.ukn.edu.tw"  # 自動生成 email

    password = generate_password_hash(raw_password)  # 加密密碼

    role = "student"

    cursor = db.cursor()

    # 檢查帳號是否已存在
    cursor.execute("SELECT * FROM student WHERE username = %s", (username,))
    if cursor.fetchone():
        return jsonify({"success": False, "message": "學號已存在"}), 409

    # 寫入資料庫
    cursor.execute(
        "INSERT INTO student (username, password, email, role) VALUES (%s, %s, %s, %s)",
        (username, password, email, role)
    )
    db.commit()

    return jsonify({"success": True, "message": "學生註冊成功"})

# 其他路由、老師、行政註冊和登入部分不變，省略...

# 頁面路由（for flask 渲染 html）
@app.route("/login")
def login_page():
    return render_template("login.html")

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
    app.run(debug=True)
