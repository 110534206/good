from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from config import get_db
from flask import current_app
import json
import re

auth_bp = Blueprint("auth_bp", __name__)


# =========================================================
# 輔助函式：檢查是否為班導師
# =========================================================
def check_is_homeroom(user_id):
    """查詢用戶是否在 classes_teacher 中擔任 '班導師' 角色"""
    conn = get_db()
    cursor = conn.cursor()
    is_homeroom = False
    try:
        # 查詢 classes_teacher 表中是否有該 user_id 且 role 為 '班導師' 的記錄
        cursor.execute("""
            SELECT 1 FROM classes_teacher 
            WHERE teacher_id = %s AND role = '班導師'
        """, (user_id,))
        is_homeroom = bool(cursor.fetchone())
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
def notify_all_ta(conn, title, message, link_url=None):
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
                INSERT INTO notifications (user_id, title, message, link_url, is_read, created_at)
                VALUES (%s, %s, %s, %s, 0, NOW())
            """, (ta_user_id, title, message, link_url))
        
        # 注意：不在此處 commit，由調用者負責 commit
        if cursor:
            cursor.close()
    except Exception as e:
        if cursor:
            cursor.close()
        print(f"❌ 發送科助通知錯誤: {e}")
        # 不影響主流程，只記錄錯誤

# =========================================================
# API - 登入
# =========================================================
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
        
        if original_role == 'director':
            # 主任：導向選擇頁面 (login-confirm)
            session['pending_roles'] = [
                {"id": "director", "name": "主任"},
                {"id": "teacher", "name": "指導老師"},
            ]
            # 初始 active role 設為 director (在選擇前仍需一個預設值，但它會被 confirm-role 覆蓋)
            session['role'] = 'director' 
            return jsonify({"success": True, "redirect": url_for("auth_bp.login_confirm_page")})
            
        elif original_role == 'teacher':
            # 指導老師：直接導向指導老師主頁 (role 設為 teacher)
            session['role'] = 'teacher' 
            
            # 💡 備註：在您的需求中，老師的班導切換由「下拉選單」控制，
            # 因此這裡不需自動跳轉到 class_teacher_home。
            return jsonify({"success": True, "redirect": url_for("users_bp.teacher_home")})
            
        # ... 其他角色的處理 (例如 student, ta, admin,vendor 等)
        elif original_role == 'student':
            session['role'] = 'student'
            return jsonify({"success": True, "redirect": url_for("users_bp.student_home")})

        elif original_role == 'admin':
            session['role'] = 'admin'
            return jsonify({"success": True, "redirect": url_for("users_bp.admin_home")})
        
        elif original_role == 'ta':
            session['role'] = 'ta'
            return jsonify({"success": True, "redirect": url_for("users_bp.ta_home")})
        
        elif original_role == 'vendor':
            session['role'] = 'vendor'
            return jsonify({"success": True, "redirect": url_for("users_bp.vendor_home")})
        # Fallback 處理
        else:
            return jsonify({"success": False, "message": "帳號角色未定義"}), 403

    except Exception as e:
        current_app.logger.error(f"Login error for {username}: {e}")
        return jsonify({"success": False, "message": "伺服器發生錯誤"}), 500
    finally:
        cursor.close()
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
        username = data.get("username")
        password = data.get("password")
        email = data.get("email")
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

        cursor.execute("SELECT id FROM users WHERE username=%s AND role='student'", (username,))
        if cursor.fetchone():
            return jsonify({"success": False, "message": "該學生帳號已存在"}), 400

        cursor.execute("""
            INSERT INTO users (username, password, email, role)
            VALUES (%s, %s, %s, %s)
        """, (username, hashed_pw, email, role))
        conn.commit()

        return jsonify({"success": True, "message": "註冊成功"})
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
        username = data.get("username")
        password = data.get("password")
        email = data.get("email")
        role = "vendor" # 設定廠商的角色為 'vendor'

        # 1. 基本資料驗證
        if not username or not password or not email:
            return jsonify({"success": False, "message": "所有欄位皆為必填"}), 400
        
        # 帳號格式驗證 (與前端邏輯一致，確保不為空)
        # 由於帳號是從 Email 前綴自動生成，這裡只做基礎檢查
        if not re.match(r"^[A-Za-z0-9._%+-]{1,50}$", username): 
            return jsonify({"success": False, "message": "帳號格式錯誤"}), 400
        
        # 密碼長度驗證 (register_vendor.html 要求至少 6 個字元)
        if len(password) < 6:
            return jsonify({"success": False, "message": "密碼需至少 6 個字元"}), 400
        
        # Email 格式驗證 (廠商信箱無須限制 .edu.tw)
        if not re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email):
             return jsonify({"success": False, "message": "Email 格式錯誤"}), 400

        # 2. 密碼加密
        hashed_pw = generate_password_hash(password)

        conn = get_db()
        cursor = conn.cursor()

        # 3. 檢查帳號 (username) 是否已存在
        cursor.execute("SELECT id FROM users WHERE username=%s AND role=%s", (username, role))
        if cursor.fetchone():
            return jsonify({"success": False, "message": "該廠商帳號已存在"}), 400
        
        # 4. 將廠商資料寫入 users 資料表，並將 status 設為 'pending'
        cursor.execute("""
            INSERT INTO users (username, password, email, role, status)
            VALUES (%s, %s, %s, %s, 'pending')  -- <<< 新增 status 欄位
        """, (username, hashed_pw, email, role))
        
        user_id = cursor.lastrowid # 獲取剛插入的 users.id
        
        # 5. 發送通知給所有科助
        title = "新廠商申請通知"
        message = f"有新的廠商申請待審核：\n帳號：{username}\nEmail：{email}\n請前往審核頁面處理。"
        link_url = "/admin/user_management"  # 連結到用戶管理頁面，科助可以在此審核廠商
        
        notify_all_ta(conn, title, message, link_url)
        
        conn.commit()

        # 修正回覆訊息
        return jsonify({"success": True, "message": "廠商帳號註冊申請已送出，需等待科助審核通過後才能登入。"})
    
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