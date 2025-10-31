from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from config import get_db
import json
import re

auth_bp = Blueprint("auth_bp", __name__)

# =========================================================
# 🧩 API - 登入 (保留不變)
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
        
        # 檢查是否為班導師 (這段邏輯必須保留)
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
        
        # 🌟 判斷是否為主任，強制跳轉至身份選擇頁面
        if role == "director":
            # 主任帳號，強制跳轉到選擇頁面，讓他選擇「主任」或「指導老師」
            pending_roles = [
                {"id": "director", "name": "主任"},
                {"id": "teacher", "name": "指導老師"}
            ]
            session["pending_roles"] = pending_roles
            return jsonify({"success": True, "redirect": "/login-confirm"})

        # 🧩 單一角色登入導向邏輯
        session["role"] = role

        # 根據角色決定導向頁面
        if role == "teacher":
            redirect_page = "/teacher_home" 
        elif role == "student":
            redirect_page = "/student_home"
        elif role == "ta":
            redirect_page = "/ta_home"
        elif role == "admin":
            redirect_page = "/admin_home"
        elif role == "director": 
            redirect_page = "/director_home" 
        else:
            return jsonify({"success": False, "message": "無效的角色"}), 403

        return jsonify({"success": True, "redirect": redirect_page})
        
    except Exception as e:
        print("❌ 登入錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# 🧩 API - 確認角色 (處理 login-confirm 頁面的選擇) (保留不變)
# =========================================================
@auth_bp.route('/api/confirm-role', methods=['POST'])
def confirm_role():
    data = request.get_json()
    selected_role = data.get('role')

    if 'user_id' not in session or 'pending_roles' not in session:
        return jsonify({"success": False, "message": "狀態錯誤，請重新登入"}), 403

    valid_ids = [r['id'] for r in session.get('pending_roles')]
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
# 🧩 API - 學生註冊 (保留不變)
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
    """
    訪客入口：設定訪客 Session 標誌，並導向最終頁面。
    """
    # 步驟 1: 清除現有 Session (確保不是登入狀態)
    session.clear() 

    # 步驟 2: 設定訪客身份的 Session 標誌
    session['role'] = 'visitor'
    session['is_visitor'] = True
    session['user_id'] = 0 # 訪客ID設為0

    # 步驟 3: 導向 /student_visitor 頁面 (在 users_bp 中)
    return redirect(url_for("users_bp.student_visitor_page"))

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

# 學生註冊頁面 (保留不變)
@auth_bp.route("/register_student")
def show_register_student_page():
    """
    學生註冊頁面。
    """
    return render_template("auth/register_student.html")