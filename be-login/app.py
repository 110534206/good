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
def login():
    return render_template('login.html')

@app.route('/register')
def register():
    return render_template('register.html')

@app.route('/api/register', methods=['POST'])
def api_register():
    username = request.form['username']
    password = request.form['password']
    
    # 寫入資料庫
    cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, password))
    db.commit()
    return redirect(url_for('login'))

@app.route('/api/login', methods=['POST'])
def api_login():
    username = request.form['username']
    password = request.form['password']
    cursor.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
    user = cursor.fetchone()
    if user:
        return f"登入成功，歡迎 {username}！"
    else:
        return "帳號或密碼錯誤"

if __name__ == '__main__':
    app.run(debug=True)
