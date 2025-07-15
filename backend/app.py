from flask import Flask, render_template, request, redirect, url_for
import mysql.connector
import re
from werkzeug.security import generate_password_hash

app = Flask(__name__)

# 建立資料庫連線
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",
    database="ai_resume",
     connection_timeout=10
)

cursor = db.cursor()

# 驗證 email 格式 + 限定網域
def is_valid_email(email):
    pattern = r'^[\w\.-]+@stu\.ukn\.edu\.tw$'
    return re.match(pattern, email)

# 驗證台灣身分證格式
def is_valid_taiwan_id(id_number):
    pattern = r'^[A-Z][1-2]\d{8}$'
    return re.match(pattern, id_number)

# 驗證學號格式（9 位數字）
def is_valid_student_id(student_id):
    return re.fullmatch(r'\d{9}', student_id)

@app.route('/')
def index():
    return redirect(url_for('register_student_page'))

@app.route('/register_student')
def register_student_page():
    return render_template('register_student.html')

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/api/register_student', methods=['POST'])
def api_register_student():
    username = request.form['username']
    password = request.form['password']
    email = request.form['email'].lower()
    role = 'student'

    if not is_valid_student_id(username):
        return "學號格式錯誤，需為 9 位數字"

    if not is_valid_email(email):
        return "請使用學校信箱（例如 123456789@stu.ukn.edu.tw）"

    if not is_valid_taiwan_id(password):
        return "身分證格式錯誤，請重新輸入（例如 A123456789）"

    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    if cursor.fetchone():
        return "帳號已存在，請使用其他帳號"

    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    if cursor.fetchone():
        return "信箱已註冊，請使用其他信箱"

    hashed_password = generate_password_hash(password)

    cursor.execute(
        "INSERT INTO users (username, password, email, role) VALUES (%s, %s, %s, %s)",
        (username, hashed_password, email, role)
    )
    db.commit()

    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
