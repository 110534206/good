from flask import Flask, request, jsonify, session
from flask_cors import CORS
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
from flask import render_template

app = Flask(__name__)
CORS(app, supports_credentials=True)
app.secret_key = 'your_secret_key'  # session 使用的金鑰

# 資料庫連線
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",
    database="user"  
)
cursor = db.cursor()
from flask import render_template
# ✅登入
@app.route("/login")
def login_page():
    return render_template("login.html")

# ✅ 老師註冊
@app.route("/api/register_teacher", methods=["POST"])
def register_teacher():
    data = request.get_json()
    account = data.get("account")
    password = data.get("password")
    hashed_password = generate_password_hash(password)

    cursor.execute("SELECT * FROM teacher WHERE account = %s", (account,))
    existing_user = cursor.fetchone()
    if existing_user:
        return jsonify({"success": False, "message": "帳號已存在"})

    cursor.execute("INSERT INTO teacher (account, password) VALUES (%s, %s)", (account, hashed_password))
    db.commit()
    return jsonify({"success": True, "message": "註冊成功"})

# ✅ 主任註冊
@app.route("/api/register_admin", methods=["POST"])
def register_admin():
    data = request.get_json()
    account = data.get("account")
    password = data.get("password")
    hashed_password = generate_password_hash(password)

    cursor.execute("SELECT * FROM administrative WHERE account = %s", (account,))
    existing_user = cursor.fetchone()
    if existing_user:
        return jsonify({"success": False, "message": "帳號已存在"})

    cursor.execute("INSERT INTO administrative (account, password) VALUES (%s, %s)", (account, hashed_password))
    db.commit()
    return jsonify({"success": True, "message": "註冊成功"})

# ✅ 學生註冊（假設你之前也有寫這個路由）
@app.route("/api/register_student", methods=["POST"])
def register_student():
    data = request.get_json()
    account = data.get("account")
    password = data.get("password")
    email = data.get("email")
    hashed_password = generate_password_hash(password)

    cursor.execute("SELECT * FROM student WHERE account = %s", (account,))
    existing_user = cursor.fetchone()
    if existing_user:
        return jsonify({"success": False, "message": "帳號已存在"})

    cursor.execute("INSERT INTO student (account, password, email) VALUES (%s, %s, %s)", (account, hashed_password, email))
    db.commit()
    return jsonify({"success": True, "message": "註冊成功"})

# ✅ 登入
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    roles = ['student', 'teacher', 'administrative']
    for role in roles:
        cursor.execute(f"SELECT password FROM {role} WHERE account = %s", (username,))
        result = cursor.fetchone()
        if result and check_password_hash(result[0], password):
            session["username"] = username
            session["role"] = role
            return jsonify({"success": True, "role": role})
    return jsonify({"success": False, "message": "帳號或密碼錯誤"})

# ✅ 登出
@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})
    
if __name__ == "__main__":
    app.run(debug=True)
