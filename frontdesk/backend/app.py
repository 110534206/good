from flask import Flask, render_template, request, redirect, url_for, jsonify
import mysql.connector
import re
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, template_folder='../frontend/templates', static_folder='../frontend/static')

# 資料庫連線
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",
    database="ai_resume"
)
cursor = db.cursor(buffered=True)

# 驗證函式
def is_valid_student_email(email):
    pattern = r'^[\w\.-]+@stu\.ukn\.edu\.tw$'
    return re.match(pattern, email)

def is_valid_taiwan_id(id_number):
    pattern = r'^[A-Z][1-2]\d{8}$'
    return re.match(pattern, id_number)

def is_valid_student_id(student_id):
    return re.fullmatch(r'\d{9}', student_id)

# 網頁路由
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

@app.route('/profile')
def profile():
    return render_template('profile.html')

# 共用註冊邏輯
def register_user(role):
   if request.is_json:
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    email = data.get('email', '').lower() if role == 'student' else None
   else:
    username = request.form.get('username')
    password = request.form.get('password')
    email = request.form.get('email', '').lower() if role == 'student' else None

    if not username or not password:
        return jsonify({"success": False, "message": "請填寫所有必填欄位"}), 400

    if role == 'student':
        table_name = 'students'
        id_field = 'student_id'
        if not email:
            return jsonify({"success": False, "message": "請填寫學校信箱"}), 400
        if not is_valid_student_email(email):
            return jsonify({"success": False, "message": "請使用學校信箱（例如 123456789@stu.ukn.edu.tw）"}), 400
        if not is_valid_student_id(username):
            return jsonify({"success": False, "message": "學號格式錯誤（9位數字）"}), 400
        if not is_valid_taiwan_id(password):
            return jsonify({"success": False, "message": "請輸入正確格式的身分證作為密碼"}), 400
    elif role == 'teacher':
        table_name = 'teachers'
        id_field = 'teacher_id'
    elif role == 'administrative':
        table_name = 'administratives'
        id_field = 'admin_id'
    else:
        return jsonify({"success": False, "message": "無效的角色"}), 400

    try:
        # 檢查帳號是否已存在
        cursor.execute(f"SELECT * FROM {table_name} WHERE {id_field} = %s", (username,))
        if cursor.fetchone():
            return jsonify({"success": False, "message": "帳號已存在"}), 400

        # 學生 email 不能重複
        if role == 'student':
            cursor.execute(f"SELECT * FROM {table_name} WHERE email = %s", (email,))
            if cursor.fetchone():
                return jsonify({"success": False, "message": "信箱已註冊"}), 400

        hashed_password = generate_password_hash(password)

        # 寫入資料庫
        if role == 'student':
            cursor.execute(
                f"INSERT INTO {table_name} ({id_field}, password, email) VALUES (%s, %s, %s)",
                (username, hashed_password, email)
            )
        else:
            cursor.execute(
                f"INSERT INTO {table_name} ({id_field}, password) VALUES (%s, %s)",
                (username, hashed_password)
            )

        db.commit()
        return jsonify({"success": True, "message": "註冊成功"}), 200

    except Exception as e:
        return jsonify({"success": False, "message": f"內部錯誤：{str(e)}"}), 500

# 註冊 API 路由
@app.route('/api/register_student', methods=['POST'])
def api_register_student():
    return register_user('student')

@app.route('/api/register_teacher', methods=['POST'])
def api_register_teacher():
    return register_user('teacher')

@app.route('/api/register_administrative', methods=['POST'])
def api_register_administrative():
    return register_user('administrative')

# 登入驗證 API
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    role = data.get('role')

    if not username or not password or not role:
        return jsonify({"success": False, "message": "請提供帳號、密碼與角色"}), 400

    if role == 'student':
        table_name = 'students'
        id_field = 'student_id'
    elif role == 'teacher':
        table_name = 'teachers'
        id_field = 'teacher_id'
    elif role == 'administrative':
        table_name = 'administratives'
        id_field = 'admin_id'
    else:
        return jsonify({"success": False, "message": "無效的角色"}), 400

    cursor.execute(f"SELECT password FROM {table_name} WHERE {id_field} = %s", (username,))
    result = cursor.fetchone()

    if result and check_password_hash(result[0], password):
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

if __name__ == '__main__':
    app.run(debug=True)
