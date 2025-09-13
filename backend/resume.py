from flask import Blueprint, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename
from config import get_db
import os
import traceback
from datetime import datetime

resume_bp = Blueprint("resume_bp", __name__)

# -------------------------
# 上傳資料夾設定
# -------------------------
UPLOAD_FOLDER = "uploads/resumes"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# -------------------------
# API - 上傳履歷
# -------------------------
@resume_bp.route('/api/upload_resume', methods=['POST'])
def upload_resume_api():
    try:
        if 'resume' not in request.files:
            return jsonify({"success": False, "message": "未上傳檔案"}), 400

        file = request.files['resume']
        username = request.form.get('username')

        if not username:
            return jsonify({"success": False, "message": "缺少使用者帳號"}), 400
        if file.filename == '':
            return jsonify({"success": False, "message": "檔案名稱為空"}), 400

        # 安全檔名與時間戳
        original_filename = file.filename
        safe_filename = secure_filename(original_filename)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        stored_filename = f"{timestamp}_{safe_filename}"
        save_path = os.path.join(UPLOAD_FOLDER, stored_filename)

        # 儲存檔案
        file.save(save_path)

        conn = get_db()
        cursor = conn.cursor()

        # 找使用者
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if not user:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到使用者"}), 404

        user_id = user[0]
        filesize = os.path.getsize(save_path)

        # 新增履歷紀錄
        cursor.execute("""
            INSERT INTO resumes (user_id, original_filename, filepath, filesize, status, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
        """, (user_id, original_filename, save_path, filesize, 'uploaded'))

        resume_id = cursor.lastrowid
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "success": True,
            "resume_id": resume_id,
            "filename": original_filename,
            "filesize": filesize,
            "status": "uploaded",
            "message": "履歷上傳成功"
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"上傳失敗: {str(e)}"}), 500


# -------------------------
# API - 下載履歷
# -------------------------
@resume_bp.route('/api/download_resume/<int:resume_id>', methods=['GET'])
def download_resume(resume_id):
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT filepath, original_filename FROM resumes WHERE id = %s", (resume_id,))
        resume = cursor.fetchone()
        cursor.close()
        conn.close()

        if not resume:
            return jsonify({"success": False, "message": "找不到履歷"}), 404

        return send_file(resume["filepath"], as_attachment=True, download_name=resume["original_filename"])

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"下載失敗: {str(e)}"}), 500


# -------------------------
# API - 查詢使用者履歷列表
# -------------------------
@resume_bp.route('/api/list_resumes/<username>', methods=['GET'])
def list_resumes(username):
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if not user:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到使用者"}), 404

        user_id = user["id"]
        cursor.execute("""
            SELECT id, original_filename, status, created_at
            FROM resumes
            WHERE user_id = %s
            ORDER BY created_at DESC
        """, (user_id,))
        resumes = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({"success": True, "resumes": resumes})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500


# -------------------------
# API - 教師/主任審核履歷
# -------------------------
@resume_bp.route('/api/review_resume', methods=['POST'])
def review_resume():
    try:
        data = request.get_json()
        resume_id = data.get("resume_id")
        status = data.get("status")  # e.g., approved / rejected
        feedback = data.get("feedback", "")

        if not resume_id or not status:
            return jsonify({"success": False, "message": "缺少必要參數"}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE resumes
            SET status = %s, feedback = %s, reviewed_at = NOW()
            WHERE id = %s
        """, (status, feedback, resume_id))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"success": True, "message": "審核完成"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"審核失敗: {str(e)}"}), 500
    

# -------------------------
# # 頁面路由
# -------------------------

# 上傳履歷頁面
resume_bp.route('/upload_resume')
def upload_resume():
    return render_template('upload_resume.html')

# 審核履歷頁面
resume_bp.route('/review_resume')
def review_resume():
    return render_template('review_resume.html')

# AI 編輯履歷頁面
resume_bp.route('/ai_edit_resume')
def ai_edit_resume():
    return render_template('ai_edit_resume.html')    
