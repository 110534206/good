from flask import Blueprint, request, jsonify
from config import get_db

resume_bp = Blueprint("resume_bp", __name__)

# 取得某學生的履歷
@resume_bp.route("/api/resumes/<string:username>", methods=["GET"])
def get_resume(username):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM resumes WHERE username=%s", (username,))
    resume = cursor.fetchone()
    cursor.close()
    conn.close()
    if resume:
        return jsonify({"success": True, "data": resume})
    return jsonify({"success": False, "message": "履歷不存在"}), 404

# 上傳或更新履歷
@resume_bp.route("/api/resumes", methods=["POST"])
def upload_resume():
    data = request.get_json()
    username = data.get("username")
    content = data.get("content")

    if not username or not content:
        return jsonify({"success": False, "message": "資料不完整"}), 400

    conn = get_db()
    cursor = conn.cursor()
    # 檢查是否已有履歷
    cursor.execute("SELECT * FROM resumes WHERE username=%s", (username,))
    if cursor.fetchone():
        cursor.execute(
            "UPDATE resumes SET content=%s, updated_at=NOW() WHERE username=%s",
            (content, username)
        )
    else:
        cursor.execute(
            "INSERT INTO resumes (username, content, created_at, updated_at) VALUES (%s, %s, NOW(), NOW())",
            (username, content)
        )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "履歷已儲存"})
