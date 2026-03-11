from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from config import get_db
from flask import current_app
import json
import re
import random
import string
from datetime import datetime, timedelta

auth_bp = Blueprint("auth_bp", __name__)


# =========================================================
# 輔助函式：檢查是否為班導師
# =========================================================
def check_is_homeroom(user_id):
    """查詢用戶是否在 classes_teacher 中擔任班導師角色（role = 'classteacher'）"""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    is_homeroom = False
    try:
        cursor.execute("""
            SELECT COUNT(*) as count FROM classes_teacher 
            WHERE teacher_id = %s AND role = 'classteacher'
        """, (user_id,))
        result = cursor.fetchone()
        is_homeroom = result['count'] > 0 if result else False
    except Exception as e:
        current_app.logger.error(f"Error checking homeroom status for user {user_id}: {e}")
        # 如果發生錯誤，預設為 False
    finally:
        cursor.close()
        conn.close()
    return is_homeroom

# =========================================================
# 輔助函式：發送通知給所有科助
# =========================================================
def notify_all_ta(conn, title, message, link_url=None, category="general"):
    """發送通知給所有科助（role='ta'）"""
    cursor = None
    try:
        cursor = conn.cursor()
        # 查詢所有科助的 user_id
        cursor.execute("SELECT id FROM users WHERE role = 'ta'")
        ta_users = cursor.fetchall()
        
        # 為每個科助創建通知
        for ta_user in ta_users:
            ta_user_id = ta_user[0]
            cursor.execute("""
                INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
                VALUES (%s, %s, %s, %s, %s, 0, NOW())
            """, (ta_user_id, title, message, category, link_url))
        
        # 注意：不在此處 commit，由調用者負責 commit
        if cursor:
            cursor.close()
    except Exception as e:
        if cursor:
            cursor.close()
        print(f"❌ 發送科助通知錯誤: {e}")
        # 不影響主流程，只記錄錯誤

# =========================================================
# 輔助函式：發送通知給所有主任
# =========================================================
def notify_all_directors(conn, title, message, link_url=None, category="general"):
    """發送通知給所有主任（role='director'）"""
    cursor = None
    try:
        cursor = conn.cursor()
        # 查詢所有主任的 user_id
        cursor.execute("SELECT id FROM users WHERE role = 'director'")
        director_users = cursor.fetchall()
        
        # 為每個主任創建通知
        for director_user in director_users:
            director_user_id = director_user[0]
            cursor.execute("""
                INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
                VALUES (%s, %s, %s, %s, %s, 0, NOW())
            """, (director_user_id, title, message, category, link_url))
        
        # 注意：不在此處 commit，由調用者負責 commit
        if cursor:
            cursor.close()
    except Exception as e:
        if cursor:
            cursor.close()
        print(f"❌ 發送主任通知錯誤: {e}")
        # 不影響主流程，只記錄錯誤

# =========================================================
# API - 登入
# =========================================================
# 未來可在此加入雙重認證 (2FA)：密碼驗證通過後若用戶已啟用 2FA，
# 則回傳 need_2fa 並要求輸入 TOTP 驗證碼，驗證通過後再寫入 session 並導向。
@auth_bp.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"success": False, "message": "帳號或密碼不得為空"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    user = None

    try:
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()

        if not user:
            return jsonify({"success": False, "message": "帳號不存在"}), 404

        if not check_password_hash(user["password"], password):
            return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401
        
        # 🌟 廠商帳號審核檢查 (保留您原有的邏輯)
        if user["role"] == "vendor":
            vendor_status = user.get("status")
            if vendor_status == "pending":
                return jsonify({"success": False, "message": "廠商帳號待審核中"}), 403
            if vendor_status == "rejected":
                return jsonify({"success": False, "message": "廠商帳號已被拒絕"}), 403

        # ----------------------------------------
        # 🎯 核心：Session 設定與分流邏輯
        # ----------------------------------------
        
        # 1. 清除舊 Session 並設定基本資訊
        session.clear()
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['original_role'] = user['role'] # 儲存資料庫中的原始角色 (teacher/director)
        
        # 2. 判斷並儲存班導師狀態 (無論原始角色是什麼，is_homeroom 狀態固定)
        is_homeroom = check_is_homeroom(user['id'])
        session['is_homeroom'] = is_homeroom 

        original_role = user['role']
        # 未修改過帳密者不強制跳轉，改由各角色首頁顯示提示訊息

        if original_role == 'director':
            session['role'] = 'director'
            session['pending_roles'] = [
                {"id": "director", "name": "主任"},
                {"id": "teacher", "name": "指導老師"},
            ]
            return jsonify({"success": True, "redirect": url_for("auth_bp.login_confirm_page")})

        if original_role == 'teacher':
            session['role'] = 'teacher'
            return jsonify({"success": True, "redirect": url_for("users_bp.teacher_home")})

        if original_role == 'student':
            session['role'] = 'student'
            return jsonify({"success": True, "redirect": url_for("users_bp.student_home")})

        if original_role == 'admin':
            session['role'] = 'admin'
            return jsonify({"success": True, "redirect": url_for("users_bp.admin_home")})

        if original_role == 'ta':
            session['role'] = 'ta'
            return jsonify({"success": True, "redirect": url_for("users_bp.ta_home")})

        if original_role == 'vendor':
            session['role'] = 'vendor'
            return jsonify({"success": True, "redirect": url_for("users_bp.vendor_home")})

        return jsonify({"success": False, "message": "帳號角色未定義"}), 403

    except Exception as e:
        current_app.logger.error(f"Login error for {username}: {e}")
        return jsonify({"success": False, "message": "伺服器發生錯誤"}), 500
    finally:
        cursor.close()
        conn.close()


# =========================================================
# 🧩 API - 忘記密碼：傳送驗證碼
# =========================================================
@auth_bp.route("/api/forgot_password/send_code", methods=["POST"])
def forgot_password_send_code():
    conn = None
    cursor = None
    try:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        if not email or "@" not in email:
            return jsonify({"success": False, "message": "請輸入有效的註冊 Email"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE email = %s LIMIT 1", (email,))
        user = cursor.fetchone()
        if not user:
            return jsonify({"success": False, "message": "此 Email 尚未註冊"}), 404

        code = "".join(random.choices(string.digits, k=6))
        expires_at = datetime.now() + timedelta(minutes=10)

        try:
            cursor.execute(
                "DELETE FROM password_reset_codes WHERE email = %s", (email,)
            )
            cursor.execute(
                """INSERT INTO password_reset_codes (email, code, expires_at)
                   VALUES (%s, %s, %s)""",
                (email, code, expires_at),
            )
            conn.commit()
        except Exception as db_err:
            conn.rollback()
            current_app.logger.error(f"password_reset_codes 表可能尚未建立: {db_err}")
            return jsonify({
                "success": False,
                "message": "系統尚未啟用忘記密碼功能，請聯絡管理員並執行 password_reset_codes.sql"
            }), 503

        try:
            from email_service import send_password_reset_code_email
            send_password_reset_code_email(email, code)
        except Exception as send_err:
            print(f"⚠️ 驗證碼已產生但寄送失敗: {send_err}")
            return jsonify({"success": False, "message": "驗證碼寄送失敗，請稍後再試或聯絡管理員"}), 500

        return jsonify({"success": True, "message": "驗證碼已寄至您的 Email，請於 10 分鐘內完成驗證"})
    except Exception as e:
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# =========================================================
# 🧩 API - 忘記密碼：驗證碼驗證
# =========================================================
@auth_bp.route("/api/forgot_password/verify", methods=["POST"])
def forgot_password_verify():
    conn = None
    cursor = None
    try:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        code = (data.get("code") or "").strip()
        if not email or not code:
            return jsonify({"success": False, "message": "請輸入 Email 與驗證碼"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT id FROM password_reset_codes
               WHERE email = %s AND code = %s AND expires_at > NOW() LIMIT 1""",
            (email, code),
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({"success": False, "message": "驗證碼錯誤或已過期，請重新取得驗證碼"}), 400
        return jsonify({"success": True, "message": "驗證成功，請設定新密碼"})
    except Exception as e:
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# =========================================================
# 🧩 API - 忘記密碼：重設密碼
# =========================================================
@auth_bp.route("/api/forgot_password/reset", methods=["POST"])
def forgot_password_reset():
    conn = None
    cursor = None
    try:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        code = (data.get("code") or "").strip()
        new_password = data.get("new_password") or ""
        if not email or not code:
            return jsonify({"success": False, "message": "請輸入 Email 與驗證碼"}), 400
        if len(new_password) < 6:
            return jsonify({"success": False, "message": "新密碼需至少 6 個字元"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT id FROM password_reset_codes
               WHERE email = %s AND code = %s AND expires_at > NOW() LIMIT 1""",
            (email, code),
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({"success": False, "message": "驗證碼錯誤或已過期，請重新取得驗證碼"}), 400

        hashed = generate_password_hash(new_password)
        cursor.execute("UPDATE users SET password = %s WHERE email = %s", (hashed, email))
        cursor.execute("DELETE FROM password_reset_codes WHERE email = %s", (email,))
        conn.commit()
        return jsonify({"success": True, "message": "密碼已重設，請使用新密碼登入"})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# =========================================================
# 🧩 API - 確認角色 (處理 login-confirm 頁面的選擇)
# =========================================================
@auth_bp.route('/api/confirm-role', methods=['POST'])
def confirm_role():
    data = request.get_json()
    selected_role = data.get('role')

    if 'user_id' not in session or 'pending_roles' not in session:
        return jsonify({"success": False, "message": "狀態錯誤，請重新登入"}), 403

    pending_roles = session.get('pending_roles', [])
    valid_ids = [r.get('id') for r in pending_roles if isinstance(r, dict)]
    if selected_role not in valid_ids:
        return jsonify({"success": False, "message": "無效的角色選擇"}), 400

    session['role'] = selected_role
    session.pop('pending_roles', None)

    if selected_role == 'director':
        redirect_page = '/director_home'
    elif selected_role == 'teacher':
        redirect_page = '/teacher_home'
    else:
        return jsonify({"success": False, "message": "系統錯誤：未知的角色"}), 500

    return jsonify({"success": True, "redirect": redirect_page})

# =========================================================
# 🧩 API - 學生註冊 
# =========================================================
@auth_bp.route("/api/register_student", methods=["POST"])
def register_student():
    try:
        data = request.json
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()
        email = (data.get("email") or "").strip()
        role = "student"

        if not re.match(r"^[A-Za-z0-9]{6,20}$", username):
            return jsonify({"success": False, "message": "學號格式錯誤"}), 400
        if not re.match(r"^[A-Za-z0-9]{8,}$", password):
            return jsonify({"success": False, "message": "密碼需至少8碼"}), 400
        if not re.match(r"^[A-Za-z0-9._%+-]+@.*\.edu\.tw$", email):
            return jsonify({"success": False, "message": "請使用學校信箱"}), 400

        hashed_pw = generate_password_hash(password)

        conn = get_db()
        cursor = conn.cursor()

        # 僅在學生、廠商之間檢查重複，避免與老師/主任等帳號混淆
        cursor.execute(
            "SELECT id FROM users WHERE username = %s AND role IN ('student', 'vendor')",
            (username,)
        )
        if cursor.fetchone():
            return jsonify({"success": False, "message": "該帳號已被使用，學生與廠商不可使用相同帳號"}), 400
        cursor.execute(
            "SELECT id FROM users WHERE email = %s AND role IN ('student', 'vendor')",
            (email,)
        )
        if cursor.fetchone():
            return jsonify({"success": False, "message": "該 Email 已被使用，學生與廠商不可使用相同 Email"}), 400

        cursor.execute("""
            INSERT INTO users (username, password, email, role)
            VALUES (%s, %s, %s, %s)
        """, (username, hashed_pw, email, role))
        user_id = cursor.lastrowid
        conn.commit()

        # 建立帳號後自動發送 Email 通知給用戶
        try:
            from email_service import send_account_created_email
            send_account_created_email(email, username, username, "學生", initial_password=None)
        except Exception as send_err:
            print(f"⚠️ 學生註冊成功，但發送通知信失敗: {send_err}")

        # 註冊成功後直接建立 Session，導向學生主頁
        session.clear()
        session['user_id'] = user_id
        session['username'] = username
        session['original_role'] = role
        session['role'] = 'student'
        session['is_homeroom'] = check_is_homeroom(user_id)

        return jsonify({
            "success": True,
            "message": "註冊成功，正在為您導向學生主頁。",
            "redirect": url_for("users_bp.student_home")
        })
    except Exception as e:
        print("❌ 註冊錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# 🧩 API - 廠商註冊
# =========================================================
@auth_bp.route("/api/register_company", methods=["POST"])
def register_company():
    try:
        data = request.json
        # 前端 (register_vendor.html) 提交 username, password, email
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()
        email = (data.get("email") or "").strip()
        role = "vendor"

        # 1. 基本資料驗證
        if not username or not password or not email:
            return jsonify({"success": False, "message": "所有欄位皆為必填"}), 400
        
        if not re.match(r"^[A-Za-z0-9._%+-]{1,50}$", username): 
            return jsonify({"success": False, "message": "帳號格式錯誤"}), 400
        
        if len(password) < 6:
            return jsonify({"success": False, "message": "密碼需至少 6 個字元"}), 400
        
        if not re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email):
            return jsonify({"success": False, "message": "Email 格式錯誤"}), 400

        # 2. 密碼加密
        hashed_pw = generate_password_hash(password)

        conn = get_db()
        cursor = conn.cursor()

        # 3. 僅在學生、廠商之間檢查重複，避免與老師/主任等帳號混淆
        cursor.execute(
            "SELECT id FROM users WHERE username = %s AND role IN ('student', 'vendor')",
            (username,)
        )
        if cursor.fetchone():
            return jsonify({"success": False, "message": "該帳號已被使用，學生與廠商不可使用相同帳號"}), 400
        cursor.execute(
            "SELECT id FROM users WHERE email = %s AND role IN ('student', 'vendor')",
            (email,)
        )
        if cursor.fetchone():
            return jsonify({"success": False, "message": "該 Email 已被使用，學生與廠商不可使用相同 Email"}), 400
        
        # 3.1 取得預設主任 ID（廠商註冊後指導老師預設為主任，科助可於「實習投遞流程管理」修改）
        default_teacher_id = 0
        cursor.execute("SELECT id FROM users WHERE role = 'director' AND status = 'approved' LIMIT 1")
        director_row = cursor.fetchone()
        if director_row:
            default_teacher_id = director_row[0]
        
        # 4. 將廠商資料寫入 users 資料表，並將 status 設為 'active'，teacher_id 預設為主任
        cursor.execute("""
            INSERT INTO users (username, password, email, role, status, teacher_id)
            VALUES (%s, %s, %s, %s, 'active', %s)
        """, (username, hashed_pw, email, role, default_teacher_id))
        
        user_id = cursor.lastrowid
        name_for_email = data.get("name") or username

        # 4.1 建立帳號後自動發送 Email 通知給該廠商
        try:
            from email_service import send_account_created_email
            send_account_created_email(email, username, name_for_email, "廠商", initial_password=None)
        except Exception as send_err:
            print(f"⚠️ 廠商註冊成功，但發送通知信失敗: {send_err}")

        # 5. 發送通知給所有科助和主任
        title = "新廠商註冊通知"
        message = f"有新的廠商已完成註冊：\n帳號：{username}\nEmail：{email}\n請前往管理頁面留意後續合作。"
        link_url = "/admin/user_management"
        notify_all_ta(conn, title, message, link_url, category="company")
        notify_all_directors(conn, title, message, link_url, category="company")
        
        conn.commit()

        # 註冊完成後直接建立登入 Session
        session.clear()
        session['user_id'] = user_id
        session['username'] = username
        session['original_role'] = role
        session['role'] = 'vendor'
        session['is_homeroom'] = False

        return jsonify({
            "success": True,
            "message": "廠商帳號註冊成功，正在為您導向主頁。",
            "redirect": url_for("users_bp.vendor_home")
        })
    
    except Exception as e:
        conn.rollback()
        print("❌ 廠商註冊錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# 🧩 API - 身份切換 (Teacher <-> Class Teacher) (保留不變)
# =========================================================
@auth_bp.route('/api/switch-role', methods=['POST'])
def switch_role():
    data = request.get_json()
    target_role = data.get('role') 

    if 'user_id' not in session or session.get('role') not in ['teacher', 'director', 'class_teacher']:
        return jsonify({"success": False, "message": "未授權或登入過期"}), 403
    
    if target_role == 'class_teacher' and session.get("is_homeroom") != True:
        return jsonify({"success": False, "message": "您不具備班導師身份，無法切換"}), 403

    if target_role == 'class_teacher':
        session['role'] = 'class_teacher'
        redirect_url = url_for('users_bp.class_teacher_home')
    elif target_role == 'teacher':
        session['role'] = 'teacher' 
        redirect_url = url_for('users_bp.teacher_home')
    else:
        return jsonify({"success": False, "message": "無效的目標角色"}), 400

    return jsonify({"success": True, "redirect": redirect_url})

# -------------------------
# 訪客入口 (Login 頁面點擊進入)
# -------------------------
@auth_bp.route("/visitor")
def visitor_entry():
    # 步驟 1: 清除現有 Session (確保不是登入狀態)
    session.clear() 
    # 步驟 2: 設定訪客身份的 Session 標誌
    session['role'] = 'visitor'
    session['is_visitor'] = True
    session['user_id'] = 0 # 訪客ID設為0
    # 步驟 3: 導向 /visitor 頁面 (在 users_bp 中)
    return redirect(url_for("users_bp.visitor_page"))

# =========================================================
# 🧩 頁面路由
# =========================================================

@auth_bp.route("/login")
def login_page():
    return render_template("auth/login.html")

@auth_bp.route('/login-confirm')
def login_confirm_page():
    roles = session.get("pending_roles")  
    if not roles:
      return redirect(url_for("auth_bp.login_page"))
    return render_template("auth/login-confirm.html", roles_json=roles)


@auth_bp.route("/logout")
def logout_page():
    session.clear()
    return redirect(url_for("auth_bp.login_page"))

@auth_bp.route("/register_role_selection")
def register_role_selection_page():
    """
    註冊入口：提供學生或廠商角色選擇。(保留不變)
    """
    return render_template("auth/register_role_selection.html")

@auth_bp.route("/register_vendor")
def show_register_vendor_page():
    return render_template("auth/register_vendor.html") 


@auth_bp.route("/register_student")
def show_register_student_page():
    """
    學生註冊頁面。
    """
    return render_template("auth/register_student.html")