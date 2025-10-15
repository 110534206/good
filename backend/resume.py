from flask import Blueprint, request, jsonify, session, send_file, render_template
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
# Helper / 權限管理
# -------------------------
def get_user_by_username(cursor, username):
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    return cursor.fetchone()

def get_user_by_id(cursor, user_id):
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    return cursor.fetchone()

def get_director_department(cursor, user_id):
    """
    取得主任所屬 department（透過 classes_teacher -> classes.department）
    若管理多個班級，只回傳第一個有 department 的值（可擴充回傳 list）
    """
    cursor.execute("""
        SELECT DISTINCT c.department
        FROM classes c
        JOIN classes_teacher ct ON ct.class_id = c.id
        WHERE ct.teacher_id = %s
        LIMIT 1
    """, (user_id,))
    r = cursor.fetchone()
    return r['department'] if r and r.get('department') else None

def teacher_manages_class(cursor, teacher_id, class_id):
    cursor.execute("""
        SELECT 1 FROM classes_teacher
        WHERE teacher_id = %s AND class_id = %s
        LIMIT 1
    """, (teacher_id, class_id))
    return cursor.fetchone() is not None

def can_access_target_resume(cursor, session_user_id, session_role, target_user_id):
    """
    判斷 session 的使用者（session_user_id, session_role）是否可存取 target_user_id 的履歷
    - admin: 全部
    - ta: 只讀（此函式只處理存取權，呼叫端需再判斷是否為可寫操作）
    - student: 只能存取自己的履歷
    - teacher: 只能存取自己帶的班級學生
    - director: 只能存取自己科系的學生（由 classes_teacher -> classes.department 判斷）
    """
    # admin 可以
    if session_role == "admin":
        return True

    # student 只能自己
    if session_role == "student":
        return session_user_id == target_user_id

    # ta 可以讀所有（呼叫端若為寫動作需拒絕）
    if session_role == "ta":
        return True

    # teacher / director 需要查 student 的班級與科系
    cursor.execute("SELECT class_id FROM users WHERE id = %s", (target_user_id,))
    u = cursor.fetchone()
    if not u:
        return False
    target_class_id = u.get('class_id')

    if session_role == "teacher":
        return teacher_manages_class(cursor, session_user_id, target_class_id)

    if session_role == "director":
        # 取得 director 的 department（若沒有設定，則無法存取）
        director_dept = get_director_department(cursor, session_user_id)
        if not director_dept:
            return False
        # 取得 target student's department
        cursor.execute("SELECT c.department FROM classes c WHERE c.id = %s", (target_class_id,))
        cd = cursor.fetchone()
        if not cd:
            return False
        return cd.get('department') == director_dept

    # 預設拒絕
    return False

def require_login():
    return 'user_id' in session and 'role' in session

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
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if not user:
            cursor.close()
            conn.close()
            # 刪掉已存檔案
            if os.path.exists(save_path):
                os.remove(save_path)
            return jsonify({"success": False, "message": "找不到使用者"}), 404

        user_id = user['id']
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
        # 檢查登入（所有角色皆須登入）
        if not require_login():
            return jsonify({"success": False, "message": "未授權"}), 403

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 取得 resume 與 owner
        cursor.execute("""
            SELECT r.filepath, r.original_filename, r.user_id
            FROM resumes r
            WHERE r.id = %s
        """, (resume_id,))
        resume = cursor.fetchone()
        if not resume:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到履歷"}), 404

        # 權限檢查（TA 和其他讀取角色會透過 can_access_target_resume）
        if not can_access_target_resume(cursor, session['user_id'], session['role'], resume['user_id']):
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "沒有權限下載該履歷"}), 403

        filepath = resume['filepath']
        cursor.close()
        conn.close()

        if not filepath or not os.path.exists(filepath):
            return jsonify({"success": False, "message": "檔案不存在"}), 404

        return send_file(filepath, as_attachment=True, download_name=resume["original_filename"])

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"下載失敗: {str(e)}"}), 500

# -------------------------
# API - 查詢使用者履歷列表（含權限檢查）
# -------------------------
@resume_bp.route('/api/list_resumes/<username>', methods=['GET'])
def list_resumes(username):
    try:
        if not require_login():
            return jsonify({"success": False, "message": "未授權"}), 403

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        user = get_user_by_username(cursor, username)
        if not user:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到使用者"}), 404

        target_user_id = user['id']

        # 權限檢查：讀取型的權限（TA 可讀）
        if not can_access_target_resume(cursor, session['user_id'], session['role'], target_user_id):
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "沒有權限查看該使用者的履歷"}), 403

        cursor.execute("""
            SELECT id, original_filename, status, comment, note, created_at
            FROM resumes
            WHERE user_id = %s
            ORDER BY created_at DESC
        """, (target_user_id,))
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
# API - 審核履歷（合併 approve/reject 的邏輯）
# -------------------------
@resume_bp.route('/api/review_resume/<int:resume_id>', methods=['POST'])
def review_resume(resume_id):
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "未授權"}), 403

    user_id = session['user_id']
    role = session.get('role')
    data = request.get_json() or {}
    status = data.get("status")
    comment = data.get("comment", "")
    note = data.get("note", "")

    if status not in ["approved", "rejected"]:
        return jsonify({"success": False, "message": "無效的狀態"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 查履歷對應學生與班級
        cursor.execute("""
            SELECT r.id, r.user_id, u.class_id, c.department
            FROM resumes r
            JOIN users u ON r.user_id = u.id
            LEFT JOIN classes c ON u.class_id = c.id
            WHERE r.id = %s
        """, (resume_id,))
        resume = cursor.fetchone()

        if not resume:
            return jsonify({"success": False, "message": "找不到履歷"}), 404

        target_user_id = resume['user_id']

        # 權限 ： teacher / director / admin 可審核； ta 不能審核
        if role == "teacher":
            if not teacher_manages_class(cursor, user_id, resume['class_id']):
                return jsonify({"success": False, "message": "沒有權限審核這份履歷"}), 403

        elif role == "director":
            director_dept = get_director_department(cursor, user_id)
            if not director_dept or director_dept != resume.get('department'):
                return jsonify({"success": False, "message": "主任無權限審核其他科系的履歷"}), 403

        elif role == "admin":
            pass  # admin 可以

        else:
            # ta, student, 其他角色不可審核
            return jsonify({"success": False, "message": "角色無權限審核"}), 403

        # 更新履歷狀態與備註
        cursor.execute("""
            UPDATE resumes
            SET status = %s, comment = %s, note = %s, updated_at = NOW()
            WHERE id = %s
        """, (status, comment, note, resume_id))
        conn.commit()

        return jsonify({"success": True, "message": "履歷審核成功"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 查詢自己的履歷列表 (學生)
# -------------------------
@resume_bp.route('/api/get_my_resumes', methods=['GET'])
def get_my_resumes():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    user_id = session['user_id']

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT r.id, r.original_filename, r.status, r.comment, r.note, r.created_at AS upload_time
            FROM resumes r
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
        """, (user_id,))
        resumes = cursor.fetchall()

        for r in resumes:
            if isinstance(r.get('upload_time'), datetime):
                r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"success": True, "resumes": resumes})
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 更新履歷欄位 (comment/note)（需有寫入權限）
# -------------------------
@resume_bp.route('/api/update_resume_field', methods=['POST'])
def update_resume_field():
    try:
        if not require_login():
            return jsonify({"success": False, "message": "未授權"}), 403

        data = request.get_json() or {}
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
        cursor = conn.cursor(dictionary=True)

        # 先找出 resume 的 owner
        cursor.execute("SELECT user_id FROM resumes WHERE id = %s", (resume_id,))
        r = cursor.fetchone()
        if not r:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        owner_id = r['user_id']

        # 檢查寫入權限：只有 teacher（帶班）、director（同科系）、admin 可以改 comment/note
        role = session.get('role')
        user_id = session['user_id']

        if role == "teacher":
            if not teacher_manages_class(cursor, user_id, get_user_by_id(cursor, owner_id)['class_id']):
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "沒有權限修改該履歷"}), 403

        elif role == "director":
            director_dept = get_director_department(cursor, user_id)
            cursor.execute("SELECT c.department FROM classes c JOIN users u ON u.class_id = c.id WHERE u.id = %s", (owner_id,))
            target_dept_row = cursor.fetchone()
            if not director_dept or not target_dept_row or director_dept != target_dept_row.get('department'):
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "沒有權限修改該履歷"}), 403

        elif role == "admin":
            pass  # admin 可以

        else:
            # ta, student, 其他不可修改
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "角色無權限修改"}), 403

        # 更新欄位
        sql = f"UPDATE resumes SET {allowed_fields[field]} = %s, updated_at = NOW() WHERE id = %s"
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
# API - 查詢所有學生履歷（根據 username，含讀取權限檢查）
# -------------------------
@resume_bp.route('/api/get_student_resumes', methods=['GET'])
def get_student_resumes():
    if not require_login():
        return jsonify({"success": False, "message": "未授權"}), 403

    username = request.args.get('username')
    if not username:
        return jsonify({"success": False, "message": "缺少 username"}), 400

    user_id = session['user_id']
    role = session['role']

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT u.id AS student_id, u.class_id, c.department
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            WHERE u.username = %s
        """, (username,))
        student = cursor.fetchone()
        if not student:
            return jsonify({"success": False, "message": "找不到學生"}), 404

        # 權限判斷（讀取）
        if role == "teacher":
            if not teacher_manages_class(cursor, user_id, student['class_id']):
                return jsonify({"success": False, "message": "沒有權限查看該學生履歷"}), 403

        elif role == "director":
            director_dept = get_director_department(cursor, user_id)
            if not director_dept or director_dept != student.get('department'):
                return jsonify({"success": False, "message": "沒有權限查看該學生履歷"}), 403

        elif role == "ta":
            pass  # TA 可讀全部（如需限制可在此修改）

        elif role == "admin":
            pass

        else:
            return jsonify({"success": False, "message": "角色無權限"}), 403

        # 取得該學生履歷
        cursor.execute("""
            SELECT r.id, r.original_filename, r.status, r.comment, r.note, r.created_at AS upload_time
            FROM resumes r
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
        """, (student['student_id'],))
        resumes = cursor.fetchall()

        for r in resumes:
            if isinstance(r.get('upload_time'), datetime):
                r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"success": True, "resumes": resumes})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 取得班導 / 主任 履歷 (支援多班級 & 全系)（讀取）
# -------------------------
@resume_bp.route("/api/get_class_resumes", methods=["GET"])
def get_class_resumes():
    # 驗證登入
    if not require_login():
        return jsonify({"success": False, "message": "未授權"}), 403

    user_id = session['user_id']
    role = session['role']

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        resumes = [] # 初始化結果列表
        sql_query = ""
        sql_params = tuple()
        
        print(f"🔍 [DEBUG] get_class_resumes called - user_id: {user_id}, role: {role}")
        
        # ------------------------------------------------------------------
        # 1. 班導 / 教師 (role == "teacher") 
        # ------------------------------------------------------------------
        if role == "teacher":
            # 這是標準的 SQL 邏輯：只看自己帶的班級（透過 classes_teacher 關聯）
            sql_query = """
                SELECT 
                    r.id,
                    u.name AS student_name,
                    u.username AS student_number,
                    c.name AS class_name,
                    c.department,
                    r.original_filename,
                    r.filepath,
                    r.status,
                    r.comment,
                    r.note,
                    r.created_at
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                JOIN classes_teacher ct ON ct.class_id = c.id
                WHERE ct.teacher_id = %s
                ORDER BY c.name, u.name
            """
            sql_params = (user_id,)
            
            # 先執行標準查詢
            cursor.execute(sql_query, sql_params)
            resumes = cursor.fetchall()

            # 如果班導沒有在 classes_teacher 表中找到對應記錄，則返回空結果
            if not resumes:
                print(f"⚠️ [DEBUG] Teacher user {user_id} has no classes assigned in classes_teacher. Returning empty result.")
                resumes = []  # 返回空結果，不應該看到任何履歷


        # ------------------------------------------------------------------
        # 2. 主任 (role == "director")
        # ------------------------------------------------------------------
        elif role == "director":
            # 1. 嘗試查詢主任管理的班級所屬的 department
            cursor.execute("""
                SELECT DISTINCT c.department
                FROM classes c
                JOIN classes_teacher ct ON ct.class_id = c.id
                WHERE ct.teacher_id = %s
                LIMIT 1
            """, (user_id,))
            dept = cursor.fetchone()
            
            department = dept.get("department") if dept else None

            # ------------------------------------------------------------------
            # 【上次修改處】: 如果找不到科系，則改為查詢所有履歷 (像 admin 一樣)
            # ------------------------------------------------------------------
            if not department:
                print(f"⚠️ [DEBUG] Director user {user_id} department not found. Falling back to view ALL resumes.")
                sql_query = """
                    SELECT 
                        r.id,
                        u.name AS student_name,
                        u.username AS student_number,
                        c.name AS class_name,
                        c.department,
                        r.original_filename,
                        r.filepath,
                        r.status,
                        r.comment,
                        r.note,
                        r.created_at
                    FROM resumes r
                    JOIN users u ON r.user_id = u.id
                    LEFT JOIN classes c ON u.class_id = c.id
                    ORDER BY c.name, u.name
                """
                sql_params = tuple()
            else:
                # 找到科系，執行原本的科系查詢邏輯
                sql_query = """
                    SELECT 
                        r.id,
                        u.name AS student_name,
                        u.username AS student_number,
                        c.name AS class_name,
                        c.department,
                        r.original_filename,
                        r.filepath,
                        r.status,
                        r.comment,
                        r.note,
                        r.created_at
                    FROM resumes r
                    JOIN users u ON r.user_id = u.id
                    JOIN classes c ON u.class_id = c.id
                    WHERE c.department = %s
                    ORDER BY c.name, u.name
                """
                sql_params = (department,)
            # ------------------------------------------------------------------
            # 【上次修改處結束】
            # ------------------------------------------------------------------

            # 執行 SQL 查詢 (主任邏輯在上面已完成查詢或準備好查詢字串)
            if sql_query:
                cursor.execute(sql_query, sql_params)
                resumes = cursor.fetchall()
            
        # ------------------------------------------------------------------
        # 3. TA 或 Admin (role == "ta" or "admin")
        # ------------------------------------------------------------------
        elif role in ["ta", "admin"]:
            sql_query = """
                SELECT 
                    r.id,
                    u.name AS student_name,
                    u.username AS student_number,
                    c.name AS class_name,
                    c.department,
                    r.original_filename,
                    r.filepath,
                    r.status,
                    r.comment,
                    r.note,
                    r.created_at
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                ORDER BY c.name, u.name
            """
            cursor.execute(sql_query, tuple())
            resumes = cursor.fetchall()

        else:
            return jsonify({"success": False, "message": "無效的角色或權限"}), 403

        # 格式化日期時間並統一字段名稱
        for r in resumes:
            if isinstance(r.get('created_at'), datetime):
                r['created_at'] = r['created_at'].strftime("%Y-%m-%d %H:%M:%S")
            # 統一字段名稱，確保前端能正確訪問
            if 'student_name' in r:
                r['name'] = r['student_name']
            if 'student_number' in r:
                r['username'] = r['student_number']
            if 'class_name' in r:
                r['className'] = r['class_name']
            if 'created_at' in r:
                r['upload_time'] = r['created_at']

        print(f"✅ [DEBUG] Returning {len(resumes)} resumes for role {role}")
        return jsonify({"success": True, "resumes": resumes})

    except Exception:
        print("❌ 取得班級履歷資料錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 刪除履歷（需寫入權限）
# -------------------------
@resume_bp.route('/api/delete_resume', methods=['DELETE'])
def delete_resume():
    if not require_login():
        return jsonify({"success": False, "message": "未授權"}), 403

    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT filepath, user_id FROM resumes WHERE id = %s", (resume_id,))
        result = cursor.fetchone()
        if not result:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        owner_id = result['user_id']
        role = session['role']
        user_id = session['user_id']

        # 權限： teacher 要帶該班級； director 要同科系； admin 可以
        if role == "teacher":
            # 取得 owner 的 class_id
            cursor.execute("SELECT class_id FROM users WHERE id = %s", (owner_id,))
            owner = cursor.fetchone()
            if not owner or not teacher_manages_class(cursor, user_id, owner.get('class_id')):
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "沒有權限刪除該履歷"}), 403

        elif role == "director":
            director_dept = get_director_department(cursor, user_id)
            cursor.execute("SELECT c.department FROM classes c JOIN users u ON u.class_id = c.id WHERE u.id = %s", (owner_id,))
            target_dept_row = cursor.fetchone()
            if not director_dept or not target_dept_row or director_dept != target_dept_row.get('department'):
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "沒有權限刪除該履歷"}), 403

        elif role == "admin":
            pass

        else:
            # student, ta, others 無刪除權限
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "角色無權限刪除"}), 403

        # 刪除檔案與資料
        filepath = result['filepath']
        if filepath and os.path.exists(filepath):
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
# API - submit_comment（寫入 note，整合 update_resume_field）
# -------------------------
@resume_bp.route('/api/submit_comment', methods=['POST'])
def submit_comment():
    try:
        # 直接呼叫 update_resume_field 的邏輯會比較乾淨，但為保持原 API 也支援，我用相同的權限檢查
        data = request.get_json() or {}
        resume_id = data.get('resume_id')
        comment = (data.get('comment') or '').strip()

        if not resume_id or not comment:
            return jsonify({"success": False, "message": "缺少必要參數"}), 400

        try:
            resume_id = int(resume_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "resume_id 必須是數字"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, user_id FROM resumes WHERE id=%s", (resume_id,))
        r = cursor.fetchone()
        if not r:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        owner_id = r['user_id']

        # 權限檢查（寫入）
        role = session.get('role')
        user_id = session.get('user_id')
        if role == "teacher":
            cursor.execute("SELECT class_id FROM users WHERE id = %s", (owner_id,))
            owner = cursor.fetchone()
            if not owner or not teacher_manages_class(cursor, user_id, owner.get('class_id')):
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "沒有權限更新留言"}), 403
        elif role == "director":
            director_dept = get_director_department(cursor, user_id)
            cursor.execute("SELECT c.department FROM classes c JOIN users u ON u.class_id = c.id WHERE u.id = %s", (owner_id,))
            target_dept_row = cursor.fetchone()
            if not director_dept or not target_dept_row or director_dept != target_dept_row.get('department'):
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "沒有權限更新留言"}), 403
        elif role == "admin":
            pass
        else:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "角色無權限更新留言"}), 403

        cursor.execute("UPDATE resumes SET note=%s, updated_at=NOW() WHERE id=%s", (comment, resume_id))
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
    return render_template('resume/upload_resume.html')

#審核履歷頁面
@resume_bp.route('/review_resume')
def review_resume_page():
    return render_template('resume/review_resume.html')

#ai 編輯履歷頁面
@resume_bp.route('/ai_edit_resume')
def ai_edit_resume_page():
    return render_template('resume/ai_edit_resume.html')
