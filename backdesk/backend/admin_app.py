from flask import Flask, request, jsonify, render_template, redirect
from werkzeug.security import check_password_hash
import mysql.connector
from flask_cors import CORS

app = Flask(__name__,
            template_folder='../frontend/templates',
            static_folder='../frontend/static')

CORS(app, supports_credentials=True)

# 資料庫連線
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="admin"  # 專屬後台資料庫
    )

# 顯示後台登入頁面
@app.route("/admin/login", methods=["GET"])
def show_admin_login():
    return render_template("admin_login.html")

# 接收 JSON 格式的後台登入 POST 請求
@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "請求資料格式錯誤"}), 400

    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"success": False, "message": "帳號與密碼為必填"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user or user["role"] not in ["teacher", "administrative"]:
        return jsonify({"success": False, "message": "無此後台使用者"}), 401

    if not check_password_hash(user["password"], password):
        return jsonify({"success": False, "message": "密碼錯誤"}), 401

    # 登入成功，回傳成功訊息和使用者角色
    return jsonify({"success": True, "role": user["role"]})

# 登入成功後導向頁
@app.route("/admin/dashboard", methods=["GET"])
def admin_dashboard():
    return "<h2>登入成功，歡迎使用後台系統！</h2>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5100, debug=True)