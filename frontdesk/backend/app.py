from flask import Flask, render_template, request, redirect, url_for, jsonify
import mysql.connector
import re
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, template_folder='../frontend/templates')

# ✅ 建立資料庫連線
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",
    database="ai_resume"
)
cursor = db.cursor()
# ✅ 設定靜態檔案路徑
@app.route('/dashboard')
def dashboard():
    return render_template('index.html')

# ✅ 驗證學校 email（學生用）
def is_valid_student_email(email):
    pattern = r'^[\w\.-]+@stu\.ukn\.edu\.tw$'
    return re.match(pattern, email)

# ✅ 驗證台灣身分證格式
def is_valid_taiwan_id(id_number):
    pattern = r'^[A-Z][1-2]\d{8}$'
    return re.match(pattern, id_number)

# ✅ 驗證學號格式（學生專用）
def is_valid_student_id(student_id):
    return re.fullmatch(r'\d{9}', student_id)

# ✅ 頁面導向：三個角色註冊頁
@app.route('/')
def index():
    return redirect(url_for('register_choice'))

@app.route('/register_choice')
def register_choice():
    return render_template('register_choice.html')

@app.route('/register_student')
def register_student_page():
    return render_template('register_student.html')

@app.route('/register_teacher')
def register_teacher_page():
    return render_template('register_teacher.html')

@app.route('/register_administrative')
def register_administrative_page():
    return render_template('register_administrative.html')

@app.route('/login')
def login_page():
    return render_template('login.html')

# ✅ 註冊 API（共用邏輯）
def register_user(role):
    username = request.form.get('username')
    password = request.form.get('password')
    email = request.form.get('email', '').lower()

    if not username or not password or not email:
        return "請填寫所有欄位"

    if role == 'student':
        if not is_valid_student_id(username):
            return "學號格式錯誤（9位數字）"
        if not is_valid_student_email(email):
            return "請使用學校信箱（例如 123456789@stu.ukn.edu.tw）"
        if not is_valid_taiwan_id(password):
            return "請輸入正確格式的身分證作為密碼"
    else:
        # 老師與主任可放寬限制，也可自行補上教師代碼驗證
        if not re.match(r'^[\w\.-]+@.+$', email):
            return "信箱格式錯誤"

    # 檢查帳號是否已存在
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    if cursor.fetchone():
        return "帳號已存在"

    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    if cursor.fetchone():
        return "信箱已註冊"

    hashed_password = generate_password_hash(password)

    cursor.execute(
        "INSERT INTO users (username, password, email, role) VALUES (%s, %s, %s, %s)",
        (username, hashed_password, email, role)
    )
    db.commit()

    return redirect(url_for('login_page'))

# ✅ 各角色註冊路由
@app.route('/api/register_student', methods=['POST'])
def api_register_student():
    return register_user(role='student')

@app.route('/api/register_teacher', methods=['POST'])
def api_register_teacher():
    return register_user(role='teacher')

@app.route('/api/register_administrative', methods=['POST'])
def api_register_Administrative():
    return register_user(role='administrative')

# ✅ 登入 API
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    role = data.get('role')

    cursor.execute("SELECT password FROM users WHERE username = %s AND role = %s", (username, role))
    result = cursor.fetchone()

    if result and check_password_hash(result[0], password):
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

if __name__ == '__main__':
    app.run(debug=True)
