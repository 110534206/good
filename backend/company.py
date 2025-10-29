from flask import Blueprint, request, jsonify, render_template, session, send_file, current_app
from config import get_db
from datetime import datetime
from werkzeug.utils import secure_filename
import os
import traceback
from docx import Document

company_bp = Blueprint("company_bp", __name__)

# =========================================================
# 📁 上傳設定
# =========================================================
UPLOAD_FOLDER = "uploads/company_docs"
ALLOWED_EXTENSIONS = {"docx", "doc"}

def ensure_upload_folder():
    project_root = os.path.dirname(current_app.root_path)
    upload_path = os.path.join(project_root, UPLOAD_FOLDER)
    os.makedirs(upload_path, exist_ok=True)
    return upload_path

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# =========================================================
# 📥 下載公司上傳範本
# =========================================================
@company_bp.route('/download_company_template', methods=['GET'])
def download_company_template():
    try:
        template_file_name = "114學年實習單位基本資料表.docx"
        backend_dir = current_app.root_path
        project_root = os.path.dirname(backend_dir)
        file_path = os.path.join(project_root, 'frontend', 'static', 'examples', template_file_name)

        if not os.path.exists(file_path):
            return jsonify({"success": False, "message": "找不到範本檔案"}), 404

        return send_file(
            file_path,
            as_attachment=True,
            download_name=template_file_name,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "下載範本失敗"}), 500


# =========================================================
# 📤 上傳公司資料（線上填表 + Word 附檔）
# =========================================================
@company_bp.route('/api/upload_company', methods=['POST'])
def upload_company():
    conn = None
    cursor = None
    file_path = None

    try:
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "請先登入"}), 403

        user_id = session['user_id']
        company_name = request.form.get("company_name", "").strip()
        upload_dir = ensure_upload_folder()

        if not company_name:
            return jsonify({"success": False, "message": "公司名稱為必填欄位"}), 400

        # 處理 Word 檔案
        file = request.files.get("company_doc")
        if file and file.filename and allowed_file(file.filename):
            safe_name = secure_filename(f"{company_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
            save_path = os.path.join(upload_dir, safe_name)
            file.save(save_path)
            file_path = os.path.join(UPLOAD_FOLDER, safe_name)
        else:
            return jsonify({"success": False, "message": "請上傳有效的 Word 檔案 (.doc 或 .docx)"}), 400

        # 解析職缺資料
        jobs_data = []
        job_index = 0
        while True:
            job_title = request.form.get(f"job[{job_index}][title]", "").strip()
            slots_str = request.form.get(f"job[{job_index}][slots]", "0").strip()
            if not job_title:
                break
            try:
                slots = int(slots_str)
                if slots <= 0:
                    raise ValueError
            except ValueError:
                return jsonify({"success": False, "message": f"職缺 #{job_index+1} 名額必須是正整數"}), 400
            jobs_data.append({"title": job_title, "slots": slots})
            job_index += 1

        if not jobs_data:
            return jsonify({"success": False, "message": "請至少新增一個職缺"}), 400

        # 寫入資料庫
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO internship_companies 
            (company_name, uploaded_by_user_id, status, submitted_at, company_doc_path, 
             description, location, contact_person, contact_title, contact_email, contact_phone)
            VALUES (%s, %s, 'pending', NOW(), %s, '（詳見附檔）', '', '', '', '', '')
        """, (company_name, user_id, file_path))
        company_id = cursor.lastrowid

        # 插入職缺
        job_records = []
        for j in jobs_data:
            job_records.append((
                company_id,
                j["title"],
                j["slots"],
                "（詳見附檔）",
                "",
                "",
                "",
                True
            ))
        cursor.executemany("""
            INSERT INTO internship_jobs 
            (company_id, title, slots, description, period, work_time, remark, is_active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, job_records)

        conn.commit()

        return jsonify({
            "success": True,
            "message": f"公司 '{company_name}' ({job_index} 個職缺) 上傳成功，等待審核。",
            "company_id": company_id
        })

    except Exception as e:
        traceback.print_exc()
        # 如果發生錯誤，刪除剛剛儲存的檔案
        if file_path:
            project_root = os.path.dirname(current_app.root_path)
            abs_path = os.path.join(project_root, file_path)
            if os.path.exists(abs_path):
                os.remove(abs_path)
        return jsonify({"success": False, "message": f"伺服器錯誤: {e}"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# 📜 查詢使用者上傳紀錄
# =========================================================
@company_bp.route('/api/get_my_company_uploads', methods=['GET'])
def get_my_company_uploads():
    conn = None
    cursor = None
    try:
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "請先登入"}), 403

        user_id = session['user_id']
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                id,
                company_name,
                status,
                company_doc_path AS filepath,
                DATE_FORMAT(submitted_at, '%%Y-%%m-%%d %%H:%%i') AS upload_time
            FROM internship_companies
            WHERE uploaded_by_user_id = %s
            ORDER BY submitted_at DESC
        """, (user_id,))
        records = cursor.fetchall()

        for r in records:
            r["filename"] = os.path.basename(r["filepath"]) if r["filepath"] else None

        return jsonify({"success": True, "records": records})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "載入上傳紀錄失敗"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# 📂 下載上傳的公司檔案
# =========================================================
@company_bp.route('/api/download_company_file/<int:file_id>', methods=['GET'])
def download_company_file(file_id):
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT company_doc_path FROM internship_companies WHERE id=%s", (file_id,))
        record = cursor.fetchone()
        if not record or not record["company_doc_path"]:
            return jsonify({"success": False, "message": "找不到檔案"}), 404

        project_root = os.path.dirname(current_app.root_path)
        abs_path = os.path.join(project_root, record["company_doc_path"])
        if not os.path.exists(abs_path):
            return jsonify({"success": False, "message": "檔案不存在"}), 404

        filename = os.path.basename(abs_path)
        return send_file(abs_path, as_attachment=True, download_name=filename)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "下載失敗"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# 🖥️ 上傳公司頁面
# =========================================================
@company_bp.route('/upload_company', methods=['GET'])
def upload_company_form_page():
    return render_template('company/upload_company.html')
