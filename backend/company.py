from flask import Blueprint, request, jsonify, render_template, session, send_file, current_app
from config import get_db
from datetime import datetime
from werkzeug.utils import secure_filename
import os
import traceback
from docx import Document
from notification import create_notification
from semester import get_current_semester_code

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

        role = session.get('role')
        if role not in ['teacher', 'director', 'ta']:
           return jsonify({"success": False, "message": "無權限操作此功能"}), 403

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

        # 如果是科助，自動填入 advisor_user_id 和 reviewed_by_user_id，並設為已核准狀態
        if role == 'ta':
            advisor_user_id = user_id
            reviewed_by_user_id = user_id
            status = 'approved'
            reviewed_at = datetime.now()
        else:
            # 如果是老師或主任，預設上傳教師為指導老師
            if role in ['teacher', 'director']:
                advisor_user_id = user_id
            else:
                advisor_user_id = None
            reviewed_by_user_id = None
            status = 'pending'
            reviewed_at = None

        cursor.execute("""
            INSERT INTO internship_companies 
            (company_name, uploaded_by_user_id, advisor_user_id, reviewed_by_user_id, status, submitted_at, reviewed_at, company_doc_path, 
             description, location, contact_person, contact_title, contact_email, contact_phone)
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, '（詳見附檔）', '', '', '', '', '')
        """, (company_name, user_id, advisor_user_id, reviewed_by_user_id, status, reviewed_at, file_path))
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

        # 根據角色顯示不同的成功訊息
        if role == 'ta':
            message = f"公司 '{company_name}' ({job_index} 個職缺) 上傳成功，已自動核准。"
        else:
            message = f"公司 '{company_name}' ({job_index} 個職缺) 上傳成功，等待審核。"

        return jsonify({
            "success": True,
            "message": message,
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
@company_bp.route('/api/get_my_companies', methods=['GET'])
def get_my_companies():
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
                submitted_at AS upload_time
            FROM internship_companies
            WHERE uploaded_by_user_id = %s
            ORDER BY submitted_at DESC
        """, (user_id,))
        records = cursor.fetchall()

        # === 🕒 加上台灣時區轉換 ===
        from datetime import datetime, timezone, timedelta
        taiwan_tz = timezone(timedelta(hours=8))

        for r in records:
            if isinstance(r.get("upload_time"), datetime):
                # 將 UTC 轉為台灣時間
                r["upload_time"] = r["upload_time"].astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
            else:
                r["upload_time"] = "-"

            r["filename"] = os.path.basename(r["filepath"]) if r["filepath"] else None

        return jsonify({"success": True, "companies": records})

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
# 🗑️ 刪除公司上傳紀錄
# =========================================================
@company_bp.route('/api/delete_company/<int:company_id>', methods=['DELETE'])
def delete_company(company_id):
    conn = None
    cursor = None
    try:
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "請先登入"}), 403

        user_id = session['user_id']
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 先查資料，確認是否為本人上傳
        cursor.execute("""
            SELECT company_doc_path FROM internship_companies 
            WHERE id=%s AND uploaded_by_user_id=%s
        """, (company_id, user_id))
        record = cursor.fetchone()

        if not record:
            return jsonify({"success": False, "message": "找不到該公司資料或您無權限刪除"}), 404

        # 刪除檔案（如果存在）
        if record["company_doc_path"]:
            project_root = os.path.dirname(current_app.root_path)
            abs_path = os.path.join(project_root, record["company_doc_path"])
            if os.path.exists(abs_path):
                os.remove(abs_path)

        # 刪除相關職缺資料
        cursor.execute("DELETE FROM internship_jobs WHERE company_id=%s", (company_id,))

        # 刪除公司主資料
        cursor.execute("DELETE FROM internship_companies WHERE id=%s", (company_id,))
        conn.commit()

        return jsonify({"success": True, "message": "公司資料已刪除。"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"刪除失敗: {e}"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# API - 取得待審核公司清單
# =========================================================
@company_bp.route("/api/get_pending_companies", methods=["GET"])
def api_get_pending_companies():
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                ic.id,
                u.name AS upload_teacher_name,
                ic.company_name,
                ic.contact_person AS contact_name,
                ic.contact_email,
                ic.submitted_at,
                ic.status
            FROM internship_companies ic
            LEFT JOIN users u ON ic.uploaded_by_user_id = u.id
            WHERE ic.status = 'pending'
            ORDER BY ic.submitted_at DESC
        """)

        companies = cursor.fetchall()

        # === 🕒 台灣時區轉換 & 格式化 ===
        from datetime import timezone, timedelta, datetime
        taiwan_tz = timezone(timedelta(hours=8))

        for r in companies:
            dt = r.get("submitted_at")
            if isinstance(dt, datetime):
                r["submitted_at"] = dt.astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
            else:
                r["submitted_at"] = "-"

        return jsonify({
            "success": True,
            "companies": companies
        })

    except Exception:
        import traceback
        print("❌ 取得待審核公司清單錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# API - 取得已審核公司（歷史紀錄）
# =========================================================
@company_bp.route("/api/get_reviewed_companies", methods=["GET"])
def api_get_reviewed_companies():
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 取得當前學期代碼
        current_semester_code = get_current_semester_code(cursor)

        # 如果沒有設定當前學期，仍然可以顯示公司列表，但無法顯示開放狀態
        if current_semester_code:
            cursor.execute("""
                SELECT 
                    ic.id,
                    u.name AS upload_teacher_name,
                    COALESCE(advisor.name, 
                        CASE 
                            WHEN ic.advisor_user_id IS NULL AND u.role IN ('teacher', 'director') THEN u.name 
                            ELSE NULL 
                        END
                    ) AS advisor_teacher_name,
                    COALESCE(ic.advisor_user_id, 
                        CASE 
                            WHEN u.role IN ('teacher', 'director') THEN ic.uploaded_by_user_id 
                            ELSE NULL 
                        END
                    ) AS advisor_user_id,
                    ic.company_name, 
                    ic.status,
                    ic.submitted_at AS upload_time,
                    ic.reviewed_at,
                    COALESCE(co.is_open, FALSE) AS is_open_current_semester
                FROM internship_companies ic
                LEFT JOIN users u ON ic.uploaded_by_user_id = u.id
                LEFT JOIN users advisor ON ic.advisor_user_id = advisor.id
                LEFT JOIN company_openings co ON ic.id = co.company_id 
                    AND co.semester = %s
                WHERE ic.status = 'approved'
                ORDER BY 
                    CASE WHEN ic.reviewed_at IS NULL THEN 1 ELSE 0 END,
                    ic.reviewed_at DESC,
                    ic.submitted_at DESC
            """, (current_semester_code,))
        else:
            cursor.execute("""
                SELECT 
                    ic.id,
                    u.name AS upload_teacher_name,
                    COALESCE(advisor.name, 
                        CASE 
                            WHEN ic.advisor_user_id IS NULL AND u.role IN ('teacher', 'director') THEN u.name 
                            ELSE NULL 
                        END
                    ) AS advisor_teacher_name,
                    COALESCE(ic.advisor_user_id, 
                        CASE 
                            WHEN u.role IN ('teacher', 'director') THEN ic.uploaded_by_user_id 
                            ELSE NULL 
                        END
                    ) AS advisor_user_id,
                    ic.company_name, 
                    ic.status,
                    ic.submitted_at AS upload_time,
                    ic.reviewed_at,
                    FALSE AS is_open_current_semester
                FROM internship_companies ic
                LEFT JOIN users u ON ic.uploaded_by_user_id = u.id
                LEFT JOIN users advisor ON ic.advisor_user_id = advisor.id
                WHERE ic.status = 'approved'
                ORDER BY 
                    CASE WHEN ic.reviewed_at IS NULL THEN 1 ELSE 0 END,
                    ic.reviewed_at DESC,
                    ic.submitted_at DESC
            """)

        companies = cursor.fetchall()
        
        # 調試：記錄返回的公司狀態分布
        status_count = {}
        for company in companies:
            status = company.get('status', 'unknown')
            status_count[status] = status_count.get(status, 0) + 1
        print(f"📊 已審核公司查詢結果: 總數={len(companies)}, 狀態分布={status_count}")
        
        # 格式化時間
        from datetime import timezone, timedelta
        taiwan_tz = timezone(timedelta(hours=8))
        
        for company in companies:
            if company.get('upload_time') and isinstance(company['upload_time'], datetime):
                company['upload_time'] = company['upload_time'].astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
            else:
                company['upload_time'] = "-"
            
            if company.get('reviewed_at') and isinstance(company['reviewed_at'], datetime):
                company['reviewed_at'] = company['reviewed_at'].astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
            else:
                company['reviewed_at'] = "-"
            
            # 確保 is_open_current_semester 是布林值
            company['is_open_current_semester'] = bool(company.get('is_open_current_semester', False))
        
        return jsonify({"success": True, "companies": companies, "current_semester": current_semester_code})

    except Exception:
        print("❌ 取得已審核公司錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# 🔎 取得公司詳細資料 (包含職缺)
# =========================================================
@company_bp.route('/api/get_company_detail', methods=['GET'])
def get_company_detail():
    conn = None
    cursor = None
    try:
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "請先登入"}), 403

        company_id = request.args.get('company_id', type=int)
        if not company_id:
            return jsonify({"success": False, "message": "缺少 company_id"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 查詢公司主資料
        cursor.execute("""
            SELECT 
                ic.id, ic.company_name, ic.status, ic.description AS company_intro, 
                ic.location AS company_address, ic.contact_person AS contact_name, 
                ic.contact_title, ic.contact_email, ic.contact_phone, 
                ic.reject_reason, ic.submitted_at, ic.reviewed_at, 
                u.name AS upload_teacher_name
            FROM internship_companies ic
            JOIN users u ON ic.uploaded_by_user_id = u.id
            WHERE ic.id = %s
        """, (company_id,))
        company = cursor.fetchone()

        if not company:
            return jsonify({"success": False, "message": "找不到公司資料"}), 404

        # 查詢職缺資料
        cursor.execute("""
            SELECT 
                title AS internship_unit, 
                description AS internship_content, 
                period AS internship_period, 
                work_time AS internship_time, 
                slots AS internship_quota, 
                remark, salary
            FROM internship_jobs
            WHERE company_id = %s
            AND is_active = TRUE
        """, (company_id,))
        jobs = cursor.fetchall()
        company['internship_jobs'] = jobs

        return jsonify({"success": True, "company": company})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"載入詳細資料失敗: {e}"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# 📚 實習 QA - 取得所有問答
# =========================================================
@company_bp.route('/api/qa/list', methods=['GET'])
def qa_list():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, question, answer 
            FROM internship_qa
            ORDER BY sort_order ASC, id DESC
        """)
        data = cursor.fetchall()

        return jsonify({"success": True, "data": data})

    except Exception:
        import traceback
        print("❌ QA 列表錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# ➕ 實習 QA - 新增
# =========================================================
@company_bp.route('/api/qa/add', methods=['POST'])
def qa_add():
    data = request.json

    question = data.get("question", "").strip()
    answer   = data.get("answer", "").strip()
    sort     = data.get("sort_order", 0)

    if not question or not answer:
        return jsonify({"success": False, "message": "問題與答案不得為空"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO internship_qa (question, answer, sort_order)
            VALUES (%s, %s, %s)
        """, (question, answer, sort))

        conn.commit()
        return jsonify({"success": True, "message": "新增成功"})

    except Exception:
        import traceback
        print("❌ QA 新增錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# ✏️ 實習 QA - 更新
# =========================================================
@company_bp.route('/api/qa/update/<int:qa_id>', methods=['PUT'])
def qa_update(qa_id):
    data = request.json

    question = data.get("question", "").strip()
    answer   = data.get("answer", "").strip()
    sort     = data.get("sort_order")

    if not question or not answer:
        return jsonify({"success": False, "message": "問題與答案不得為空"}), 400

    try:
        sort = int(sort) if str(sort).isdigit() else 0

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE internship_qa
            SET question=%s, answer=%s, sort_order=%s
            WHERE id=%s
        """, (question, answer, sort, qa_id))

        conn.commit()

        if cursor.rowcount == 0:
            return jsonify({"success": False, "message": "找不到該 QA"}), 404

        return jsonify({"success": True, "message": "更新成功"})

    except Exception:
        import traceback
        print("❌ QA 更新錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# 🗑️ 實習 QA - 刪除
# =========================================================
@company_bp.route('/api/qa/delete/<int:qa_id>', methods=['DELETE'])
def qa_delete(qa_id):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM internship_qa WHERE id=%s", (qa_id,))
        conn.commit()

        return jsonify({"success": True, "message": "刪除成功"})

    except Exception:
        import traceback
        print("❌ QA 刪除錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# API - 審核公司
# =========================================================
@company_bp.route("/api/approve_company", methods=["POST"])
def api_approve_company():
    data = request.get_json()
    company_id = data.get("company_id")
    status = data.get("status")

    if not company_id or status not in ['approved', 'rejected']:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT company_name, status FROM internship_companies WHERE id = %s", (company_id,))
        company_row = cursor.fetchone()

        if not company_row:
            return jsonify({"success": False, "message": "查無此公司"}), 404

        company_name, current_status = company_row
        if current_status != 'pending':
            return jsonify({"success": False, "message": f"公司已被審核過（目前狀態為 {current_status}）"}), 400

        # 取得審核者的 user_id
        reviewer_id = session.get('user_id') if 'user_id' in session else None

        cursor.execute("""
            UPDATE internship_companies
            SET status = %s, reviewed_at = %s, reviewed_by_user_id = %s
            WHERE id = %s
        """, (status, datetime.now(), reviewer_id, company_id))
        conn.commit()

        action_text = '核准' if status == 'approved' else '拒絕'
        return jsonify({"success": True, "message": f"公司「{company_name}」已{action_text}"})

    except Exception:
        print("❌ 審核公司錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()

# =========================================================
# API - 設定公司本學期開放狀態
# =========================================================
@company_bp.route("/api/set_company_open_status", methods=["POST"])
def api_set_company_open_status():
    """設定公司在本學期是否開放"""
    data = request.get_json()
    company_id = data.get("company_id")
    is_open = data.get("is_open", False)

    if company_id is None:
        return jsonify({"success": False, "message": "缺少 company_id"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 取得當前學期代碼
        current_semester_code = get_current_semester_code(cursor)
        if not current_semester_code:
            return jsonify({"success": False, "message": "目前沒有設定當前學期"}), 400

        # 檢查公司是否存在且已審核通過
        cursor.execute("SELECT id, company_name, status FROM internship_companies WHERE id = %s", (company_id,))
        company = cursor.fetchone()
        
        if not company:
            return jsonify({"success": False, "message": "找不到該公司"}), 404
        
        if company['status'] != 'approved':
            return jsonify({"success": False, "message": "只有已審核通過的公司才能設定開放狀態"}), 400

        # 檢查是否已存在該公司該學期的記錄
        cursor.execute("""
            SELECT id FROM company_openings 
            WHERE company_id = %s AND semester = %s
        """, (company_id, current_semester_code))
        existing = cursor.fetchone()

        if existing:
            # 更新現有記錄
            cursor.execute("""
                UPDATE company_openings 
                SET is_open = %s, opened_at = %s
                WHERE company_id = %s AND semester = %s
            """, (is_open, datetime.now(), company_id, current_semester_code))
        else:
            # 建立新記錄
            cursor.execute("""
                INSERT INTO company_openings (company_id, semester, is_open, opened_at)
                VALUES (%s, %s, %s, %s)
            """, (company_id, current_semester_code, is_open, datetime.now()))

        conn.commit()
        
        status_text = '開放' if is_open else '關閉'
        return jsonify({
            "success": True, 
            "message": f"公司「{company['company_name']}」已{status_text}",
            "is_open": bool(is_open)
        })

    except Exception as e:
        print("❌ 設定公司開放狀態錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# 🖥️ 上傳公司頁面
# =========================================================
@company_bp.route('/upload_company', methods=['GET'])
def upload_company_form_page():
    return render_template('company/upload_company.html')

# =========================================================
# API - 取得所有指導老師
# =========================================================
@company_bp.route("/api/get_all_teachers", methods=["GET"])
def api_get_all_teachers():
    """取得所有指導老師（teacher 和 director 角色）"""
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT id, name
            FROM users
            WHERE role IN ('teacher', 'director')
            ORDER BY name ASC
        """)
        teachers = cursor.fetchall()
        
        return jsonify({"success": True, "teachers": teachers})
    except Exception:
        print("❌ 取得指導老師列表錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# API - 更新公司指導老師
# =========================================================
@company_bp.route("/api/update_company_advisor", methods=["POST"])
def api_update_company_advisor():
    """更新公司的指導老師"""
    data = request.get_json()
    company_id = data.get("company_id")
    advisor_user_id = data.get("advisor_user_id")  # 可以是 None
    
    if not company_id:
        return jsonify({"success": False, "message": "缺少 company_id"}), 400
    
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 檢查公司是否存在
        cursor.execute("SELECT id, company_name FROM internship_companies WHERE id = %s", (company_id,))
        company = cursor.fetchone()
        if not company:
            return jsonify({"success": False, "message": "找不到該公司"}), 404
        
        # 如果提供了 advisor_user_id，驗證該用戶是老師或主任
        if advisor_user_id:
            cursor.execute("SELECT id, name, role FROM users WHERE id = %s AND role IN ('teacher', 'director')", (advisor_user_id,))
            teacher = cursor.fetchone()
            if not teacher:
                return jsonify({"success": False, "message": "指定的用戶不是有效的指導老師"}), 400
        
        # 更新指導老師
        cursor.execute("""
            UPDATE internship_companies
            SET advisor_user_id = %s
            WHERE id = %s
        """, (advisor_user_id, company_id))
        conn.commit()
        
        # 取得更新後的指導老師名稱
        advisor_name = None
        if advisor_user_id:
            cursor.execute("SELECT name FROM users WHERE id = %s", (advisor_user_id,))
            advisor = cursor.fetchone()
            if advisor:
                advisor_name = advisor['name']
        
        return jsonify({
            "success": True,
            "message": f"公司「{company['company_name']}」的指導老師已更新",
            "advisor_name": advisor_name
        })
    except Exception:
        print("❌ 更新公司指導老師錯誤：", traceback.format_exc())
        conn.rollback()
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# 🖥️ 審核公司頁面
# =========================================================
@company_bp.route('/approve_company', methods=['GET'])
def approve_company_form_page():
    return render_template('company/approve_company.html')
