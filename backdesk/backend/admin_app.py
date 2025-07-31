from flask import Flask, request, jsonify
from flask_cors import CORS
import mysql.connector
from werkzeug.security import check_password_hash

app = Flask(__name__)
CORS(app, supports_credentials=True)

# 資料庫連線
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="admin"  # 專屬後台資料庫
    )

# 後台登入（教師、主任）
@app.route("/admin/login", methods=["POST"])
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

# 查詢公告
@app.route("/admin/announcements", methods=["GET"])
def get_announcements():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, title, content, created_at FROM announcements ORDER BY created_at DESC")
    result = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "announcements": result})

# 新增公告
@app.route("/admin/announcements", methods=["POST"])
def create_announcement():
    data = request.get_json()
    title = data.get("title")
    content = data.get("content")

    if not title or not content:
        return jsonify({"success": False, "message": "標題與內容不得為空"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO announcements (title, content, created_at) VALUES (%s, %s, NOW())", (title, content))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "公告新增成功"})

# 刪除公告
@app.route("/admin/announcements/<int:announcement_id>", methods=["DELETE"])
def delete_announcement(announcement_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM announcements WHERE id = %s", (announcement_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "公告刪除成功"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5100, debug=True)
