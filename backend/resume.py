from flask import Blueprint, request, jsonify, session,send_file, render_template
from werkzeug.utils import secure_filename
from config import get_db
import os
import traceback
from datetime import datetime

resume_bp = Blueprint("resume_bp", __name__)

# 上傳資料夾設定
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

        original_filename = file.filename
        safe_filename = secure_filename(original_filename)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        stored_filename = f"{timestamp}_{safe_filename}"
        save_path = os.path.join(UPLOAD_FOLDER, stored_filename)

        file.save(save_path)

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if not user:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到使用者"}), 404

        user_id = user[0]
        filesize = os.path.getsize(save_path)

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

        if not resume or not os.path.exists(resume['filepath']):
            return jsonify({"success": False, "message": "找不到履歷檔案"}), 404

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
            SELECT id, original_filename, status, comment, note, created_at
            FROM resumes
            WHERE user_id = %s
            ORDER BY created_at DESC
        """, (user_id,))
        resumes = cursor.fetchall()

        for r in resumes:
            if isinstance(r.get('created_at'), datetime):
                r['created_at'] = r['created_at'].strftime("%Y-%m-%d %H:%M:%S")

        cursor.close()
        conn.close()

        return jsonify({"success": True, "resumes": resumes})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500

# -------------------------
# API - 審核履歷 (僅限班導師 / 主任)
# -------------------------
@resume_bp.route('/api/review_resume', methods=['POST'])
def review_resume():
    try:
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "未登入"}), 403

        role = session.get('role')
        if role not in ["teacher", "director"]:
            return jsonify({"success": False, "message": "角色無權限"}), 403

        data = request.get_json()
        resume_id = data.get("resume_id")
        status = data.get("status")
        comment = (data.get("comment") or "").strip()

        if not resume_id or status not in ['approved', 'rejected']:
            return jsonify({"success": False, "message": "參數錯誤"}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM resumes WHERE id = %s", (resume_id,))
        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        if comment:
            cursor.execute(
                "UPDATE resumes SET status = %s, comment = %s WHERE id = %s",
                (status, comment, resume_id)
            )
        else:
            cursor.execute(
                "UPDATE resumes SET status = %s WHERE id = %s",
                (status, resume_id)
            )

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"success": True, "message": "履歷審核完成", "resume_id": resume_id, "status": status})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

# -------------------------
# API - 更新履歷欄位 (comment/note)
# -------------------------
@resume_bp.route('/api/update_resume_field', methods=['POST'])
def update_resume_field():
    try:
        data = request.get_json()

        resume_id = data.get('resume_id')
        field = data.get('field')
        value = (data.get('value') or '').strip()

        allowed_fields = {
            "comment": "comment",
            "note": "note"
        }

        try:
            resume_id = int(resume_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "resume_id 必須是數字"}), 400

        if field not in allowed_fields:
            return jsonify({"success": False, "message": "參數錯誤"}), 400

        conn = get_db()
        cursor = conn.cursor()
        sql = f"UPDATE resumes SET {allowed_fields[field]} = %s WHERE id = %s"
        cursor.execute(sql, (value, resume_id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"success": True, "field": field, "resume_id": resume_id})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

# -------------------------
# API - 查詢履歷狀態
# -------------------------
@resume_bp.route('/api/resume_status', methods=['GET'])
def resume_status():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT status FROM resumes WHERE id = %s", (resume_id,))
        resume = cursor.fetchone()
        cursor.close()
        conn.close()

        if not resume:
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        return jsonify({"success": True, "status": resume['status']})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

# -------------------------
# API - 查詢所有學生履歷
# -------------------------
@resume_bp.route('/api/get_student_resumes', methods=['GET'])
def get_student_resumes():
    try:
        username = request.args.get('username')
        if not username:
            return jsonify({"success": False, "message": "缺少 username"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT r.id, r.original_filename, r.status, r.comment, r.note, r.created_at AS upload_time
            FROM resumes r
            JOIN users u ON r.user_id = u.id
            WHERE u.username = %s
            ORDER BY r.created_at DESC
        """, (username,))
        resumes = cursor.fetchall()

        for r in resumes:
            if isinstance(r.get('upload_time'), datetime):
                r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")

        cursor.close()
        conn.close()
        return jsonify({"success": True, "resumes": resumes})
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

# -------------------------
# API - 取得班導 / 主任 履歷 (支援多班級 & 全系)
# -------------------------
@resume_bp.route('/api/get_class_resumes', methods=['GET'])
def get_class_resumes():
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "未授權"}), 403

    user_id = session['user_id']
    role = session.get('role')

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 在班導首頁中，老師與主任皆應只看到自己擔任「班導師」的班級之履歷
        if role in ("teacher", "director"):
            cursor.execute(
                """
                SELECT class_id
                FROM classes_teacher
                WHERE teacher_id = %s AND role = '班導師'
                """,
                (user_id,)
            )
            class_rows = cursor.fetchall()

            if not class_rows:
                return jsonify({"success": True, "resumes": []})

            class_ids = [row['class_id'] for row in class_rows]
            format_strings = ','.join(['%s'] * len(class_ids))

            query = f"""
                SELECT 
                    r.id,
                    r.original_filename AS original_filename,
                    r.filepath,
                    r.status,
                    r.created_at AS submitted_at,
                    u.id AS student_id,
                    u.username,
                    u.name,
                    u.class_id,
                    c.name AS className,
                    c.department AS department
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                JOIN classes c ON u.class_id = c.id
                WHERE u.class_id IN ({format_strings})
                ORDER BY r.created_at DESC
            """
            cursor.execute(query, class_ids)
            resumes = cursor.fetchall()

        else:
            return jsonify({"success": False, "message": "角色無權限"}), 403

        # 時間格式化
        for r in resumes:
            if isinstance(r.get('submitted_at'), datetime):
                r['submitted_at'] = r['submitted_at'].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"success": True, "resumes": resumes})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 刪除履歷
# -------------------------
@resume_bp.route('/api/delete_resume', methods=['DELETE'])
def delete_resume():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT filepath FROM resumes WHERE id = %s", (resume_id,))
        result = cursor.fetchone()
        if not result:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        filepath = result[0]
        if os.path.exists(filepath):
            os.remove(filepath)

        cursor.execute("DELETE FROM resumes WHERE id = %s", (resume_id,))
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"success": True, "message": "履歷已刪除"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

# -------------------------
# API - 標記履歷為 approved
# -------------------------
@resume_bp.route('/api/approve_resume', methods=['POST'])
def approve_resume():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM resumes WHERE id = %s", (resume_id,))
        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        cursor.execute("UPDATE resumes SET status = %s WHERE id = %s", ("approved", resume_id))
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"success": True, "message": "履歷已標記為完成"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

# -------------------------
# API - 標記履歷為 rejected
# -------------------------
@resume_bp.route('/api/reject_resume', methods=['POST'])
def reject_resume():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM resumes WHERE id = %s", (resume_id,))
        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        cursor.execute("UPDATE resumes SET status = 'rejected' WHERE id = %s", (resume_id,))
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"success": True, "message": "履歷已標記為拒絕"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

# -------------------------
# API - 留言更新 (note 欄位)
# -------------------------
@resume_bp.route('/api/submit_comment', methods=['POST'])
def submit_comment():
    try:
        data = request.get_json()
        resume_id = data.get('resume_id')
        comment = (data.get('comment') or '').strip()

        if not resume_id or not comment:
            return jsonify({"success": False, "message": "缺少必要參數"}), 400

        try:
            resume_id = int(resume_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "resume_id 必須是數字"}), 400

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM resumes WHERE id=%s", (resume_id,))
        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        cursor.execute("UPDATE resumes SET note=%s WHERE id=%s", (comment, resume_id))
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"success": True, "message": "留言更新成功"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500


# -------------------------
# # 頁面路由
# -------------------------

#上傳履歷頁面
@resume_bp.route('/upload_resume')
def upload_resume_page():
    return render_template('upload_resume.html')

#審核履歷頁面
@resume_bp.route('/review_resume')
def review_resume_page():
    return render_template('review_resume.html')

#ai 編輯履歷頁面
@resume_bp.route('/ai_edit_resume')
def ai_edit_resume_page():
    return render_template('ai_edit_resume.html')
