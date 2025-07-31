from flask import Flask, request, jsonify
from werkzeug.security import check_password_hash
import mysql.connector
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/admin/*": {"origins": "http://127.0.0.1:5000"}})  # 允許前端的來源

# 資料庫連線
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="admin"  # 專屬後台資料庫
    )

# 後台登入（/admin/login 路徑） 
@app.route("/api/admin/login", methods=["POST"])  # 只允許 POST 請求
def admin_login():
    data = request.get_json()
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

    if not user or user["role"] not in ["teacher", "director"]:
        return jsonify({"success": False, "message": "無此後台使用者"}), 401

    if not check_password_hash(user["password"], password):
        return jsonify({"success": False, "message": "密碼錯誤"}), 403

    return jsonify({"success": True, "message": f"{user['role']} 登入成功", "role": user["role"]})

# 其他後台相關路由，例如查詢公告、新增公告等...
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5100, debug=True)
