from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from config import get_db
import json
import re

auth_bp = Blueprint("auth_bp", __name__)

# =========================================================
# 🧩 API - 登入
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

    try:
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()

        if not user:
            return jsonify({"success": False, "message": "帳號不存在"}), 404

        if not check_password_hash(user["password"], password):
            return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

        role = user["role"]
        user_id = user["id"]
        
        # 檢查是否為班導師 (這段邏輯必須保留，因為無論選哪個身份，班導資訊都要帶入 session)
        cursor.execute("""
            SELECT 1 FROM classes_teacher 
            WHERE teacher_id = %s AND role = '班導師'
        """, (user_id,))
        is_homeroom = bool(cursor.fetchone())

        # 🎯 設定 session 資訊 (先儲存基本資訊)
        session.clear() 
        session["user_id"] = user_id
        session["username"] = user["username"]
        session["name"] = user["name"]
        session["is_homeroom"] = is_homeroom 
        
        # =========================================================
        # 🌟 核心修正：判斷是否為主任，強制跳轉至身份選擇頁面
        # =========================================================
        if role == "director":
            # 主任帳號，強制跳轉到選擇頁面，讓他選擇「主任」或「指導老師」
            pending_roles = [
                {"id": "director", "name": "主任"},
                {"id": "teacher", "name": "指導老師"}
            ]
            # 將多角色選項儲存到 session
            session["pending_roles"] = pending_roles
            
            # 不設定 session["role"]，讓使用者在 /login-confirm 選擇後再設置
            return jsonify({"success": True, "redirect": "/login-confirm"})

        # =========================================================
        # 🧩 單一角色登入導向邏輯
        # =========================================================
        # 非主任的角色，直接設定 session['role']
        session["role"] = role

        # 根據角色決定導向頁面
        if role == "teacher":
            # 依據新邏輯：指導老師登入一律先到指導老師主頁
            redirect_page = "/teacher_home" 
        elif role == "student":
            redirect_page = "/student_home"
        elif role == "ta":
            redirect_page = "/ta_home"
        elif role == "admin":
            redirect_page = "/admin_home"
        elif role == "director": 
            # 正常情況下不會跑到這裡 (會被上面的 if 攔截)，但保留作為單一主任身份的預設
            redirect_page = "/director_home" 
        else:
            # 其他角色或未知角色
            return jsonify({"success": False, "message": "無效的角色"}), 403

        return jsonify({"success": True, "redirect": redirect_page})
        
    except Exception as e:
        print("❌ 登入錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
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

    # 1. 檢查 Session 狀態
    # 必須有 user_id，且必須處於 pending_roles 待選擇狀態
    if 'user_id' not in session or 'pending_roles' not in session:
        # 如果沒有 pending_roles，表示使用者可能直接訪問此API，或Session已過期
        return jsonify({"success": False, "message": "狀態錯誤，請重新登入"}), 403

    # 2. 驗證角色選擇 (主任只能選 director 或 teacher)
    valid_ids = [r['id'] for r in session.get('pending_roles')]
    if selected_role not in valid_ids:
        return jsonify({"success": False, "message": "無效的角色選擇"}), 400

    # 3. 設定最終角色並清除 pending 資訊
    # 這是設定 session['role'] 的唯一位置
    session['role'] = selected_role
    session.pop('pending_roles', None) # 清除待選角色清單

    # 4. 決定跳轉頁面
    if selected_role == 'director':
        # 主任身分：跳轉到主任主頁
        redirect_page = '/director_home'
    elif selected_role == 'teacher':
        # 指導老師身分：跳轉到指導老師主頁 (即使有班導身份，也由前端下拉選單切換)
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
# 🧩 API - 身份切換 (Teacher <-> Class Teacher)
# =========================================================
@auth_bp.route('/api/switch-role', methods=['POST'])
def switch_role():
    data = request.get_json()
    target_role = data.get('role') # 預期為 'teacher' 或 'class_teacher'

    # 1. 檢查基本權限
    if 'user_id' not in session or session.get('role') not in ['teacher', 'director', 'class_teacher']:
        return jsonify({"success": False, "message": "未授權或登入過期"}), 403
    
    # 2. 檢查班導身份
    if target_role == 'class_teacher' and session.get("is_homeroom") != True:
        return jsonify({"success": False, "message": "您不具備班導師身份，無法切換"}), 403

    # 3. 執行角色切換
    if target_role == 'class_teacher':
        session['role'] = 'class_teacher'
        redirect_url = url_for('users_bp.class_teacher_home')
    elif target_role == 'teacher':
        # 切換回指導老師或主任身份
        session['role'] = 'teacher' 
        redirect_url = url_for('users_bp.teacher_home')
    else:
        return jsonify({"success": False, "message": "無效的目標角色"}), 400

    return jsonify({"success": True, "redirect": redirect_url})

# -------------------------
# 訪客角色選擇頁面
# -------------------------
@auth_bp.route("/visitor_role_selection")
def visitor_role_selection_page():
    """
    訪客角色選擇頁面，不需登入
    """
    # 設定 session 為 guest
    session["role"] = "guest"
    session["username"] = "guest"

    # 這裡可以提供不同的訪客選項，例如 "一般訪客"、"查看課程"、"查詢公司"
    roles = [
        {"id": "general", "name": "一般訪客"},
        {"id": "view_courses", "name": "查看課程"},
        {"id": "view_companies", "name": "查詢公司"},
    ]

    return render_template("auth/visitor_role_selection.html", roles=roles)

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

@auth_bp.route("/register_student")
def show_register_student_page():
    return render_template("auth/register_student.html")
