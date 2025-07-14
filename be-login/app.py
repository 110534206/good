from flask import Flask, render_template, request, redirect, url_for, jsonify
import mysql.connector

app = Flask(__name__)

# 資料庫連線設定
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",           # 預設空白
    database="ai_resume"   # 你的資料庫名稱
)
cursor = db.cursor()

@app.route('/')
def home():
    return render_template('login.html')

@app.route('/register')
def register():
    return render_template('register.html')

@app.route('/api/register', methods=['POST'])
def api_register():
    username = request.form['username']
    password = request.form['password']
    cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, password))
    db.commit()
    return redirect(url_for('home'))

@app.route('/api/login', methods=['POST'])
def api_login():
    username = request.form['username']
    password = request.form['password']
    cursor.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
    user = cursor.fetchone()
    if user:
        return "登入成功！"
    else:
        return "帳號或密碼錯誤"

if __name__ == '__main__':
    app.run(debug=True)
