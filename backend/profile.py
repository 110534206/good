from flask import Blueprint, request, jsonify
from config import get_db

profile_bp = Blueprint("profile_bp", __name__)

# 取得使用者個人資料
@profile_bp.route("/api/profile", methods=["GET"])
def get_profile():
    username = request.args.get("username")
    if not username:
        return jsonify({"success": False, "message": "缺少參數"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT username, name, email, role FROM users WHERE username=%s", (username,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if user:
        return jsonify({"success": True, "data": user})
    return jsonify({"success": False, "message": "使用者不存在"}), 404

# 更新個人資料
@profile_bp.route("/api/profile", methods=["POST"])
def update_profile():
    data = request.get_json()
    username = data.get("username")
    name = data.get("name")
    email = data.get("email")

    if not username or not name or not email:
        return jsonify({"success": False, "message": "資料不完整"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET name=%s, email=%s WHERE username=%s",
        (name, email, username)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "個人資料已更新"})
