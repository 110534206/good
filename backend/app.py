from flask import Flask, render_template, request, redirect, url_for
import mysql.connector

app = Flask(__name__)

# 資料庫連線
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",  # 預設為空
    database="ai_resume"
)
cursor = db.cursor()

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
    email = request.form['email']
    role = 'student'

    # 檢查帳號是否存在
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    if cursor.fetchone():
        return "帳號已存在，請使用其他帳號"

    # 寫入資料庫
    cursor.execute(
        "INSERT INTO users (username, password, email, role) VALUES (%s, %s, %s, %s)",
        (username, password, email, role)
    )
    db.commit()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
