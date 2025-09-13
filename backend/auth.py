from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from config import get_db
import json
import re

auth_bp = Blueprint("auth_bp", __name__)

# -------------------------
# API - 登入
# -------------------------
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
        users = cursor.fetchall()

        if not users:
            return jsonify({"success": False, "message": "帳號不存在"}), 404

        matching_roles = []
        matched_user = None  

        for user in users:
            if check_password_hash(user["password"], password):
                matching_roles.append(user["role"])
                matched_user = user  

        if not matched_user:
            return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

        session["username"] = matched_user["username"]
        session["user_id"] = matched_user["id"]

        if len(matching_roles) > 1:
            session["pending_roles"] = matching_roles 
            return jsonify({
                "success": True,
                "username": matched_user["username"],
                "roles": matching_roles,
                "redirect": "/login-confirm"
            })

        single_role = matching_roles[0]
        session["role"] = single_role
        session["original_role"] = single_role  

        redirect_page = "/"

        if single_role == "student":
            redirect_page = "/student_home"
        elif single_role == "teacher":
            cursor.execute("""
                SELECT 1 FROM classes_teacher 
                WHERE teacher_id = %s AND role = '班導師'
            """, (matched_user["id"],))
            is_homeroom = cursor.fetchone()

            if is_homeroom:
                redirect_page = "/class_teacher_home"
            else:
                redirect_page = "/teacher_home"
        elif single_role == "director":
            redirect_page = "/director_home"
        elif single_role == "admin":
            redirect_page = "/admin_home"

        return jsonify({
            "success": True,
            "username": matched_user["username"],
            "roles": matching_roles,
            "redirect": redirect_page
        })

    except Exception as e:
        print(f"登入錯誤: {e}")
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 確認角色 (多角色登入後)
# -------------------------
@auth_bp.route('/api/confirm-role', methods=['POST'])
def api_confirm_role():
    if "username" not in session or "user_id" not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    data = request.get_json()
    role = data.get("role")

    if role not in ['teacher', 'director', 'student', 'admin']:
        return jsonify({"success": False, "message": "角色錯誤"}), 400

    user_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor()

    try:
        redirect_page = "/"
        if role == "teacher" or role == "director":
            cursor.execute("""
                SELECT 1 FROM classes_teacher
                WHERE teacher_id = %s AND role = '班導師'
            """, (user_id,))
            is_homeroom = cursor.fetchone()

            if is_homeroom:
                redirect_page = "/class_teacher_home"
            else:
                redirect_page = f"/{role}_home"
        else:
            redirect_page = f"/{role}_home"

        session["role"] = role
        session["original_role"] = role 

        return jsonify({"success": True, "redirect": redirect_page})

    except Exception as e:
        print("確認角色錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 註冊學生帳號 (POST)
# -------------------------
@auth_bp.route("/api/register_student", methods=["POST"])
def register_student():
    try:
        data = request.json
        username = data.get("username")
        password = data.get("password")
        email = data.get("email")
        role = "student"

        # 格式驗證
        if not re.match(r"^[A-Za-z0-9]{6,20}$", username):
            return jsonify({"success": False, "message": "學號格式錯誤，需為6~20字元英數字"}), 400
        if not re.match(r"^[A-Za-z0-9]{8,}$", password):
            return jsonify({"success": False, "message": "密碼需至少8碼英數字"}), 400
        if not re.match(r"^[A-Za-z0-9._%+-]+@.*\.edu\.tw$", email):
            return jsonify({"success": False, "message": "必須使用學校信箱"}), 400

        hashed_password = generate_password_hash(password)

        conn = get_db()
        cursor = conn.cursor()

        # 檢查是否已有相同帳號 (在 users 表)
        cursor.execute("SELECT * FROM users WHERE username = %s AND role = %s", (username, role))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "帳號已存在"}), 400

        # 新增學生帳號 (存進 users)
        cursor.execute(
            "INSERT INTO users (username, password, email, role) VALUES (%s, %s, %s, %s)",
            (username, hashed_password, email, role)
        )
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"success": True, "message": "註冊成功"})

    except Exception as e:
        print("Error in register_student:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500   

# -------------------------
# API - 首頁 (多角色登入後)
# -------------------------
@auth_bp.route('/index')
def index_page():
    role = session.get("role")
    user_id = session.get("user_id")

    if not role:
        return redirect(url_for("auth_bp.login_page"))

    # 老師和主任都要檢查是否為班導
    if role in ["teacher", "director"]:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM classes_teacher 
            WHERE teacher_id = %s AND role = '班導師'
        """, (user_id,))
        is_homeroom = cursor.fetchone()
        cursor.close()
        conn.close()

        if is_homeroom:
            return redirect(url_for('users_bp.class_teacher_home'))
        else:
            if role == "teacher":
                return redirect(url_for('users_bp.teacher_home'))
            else:
                return redirect(url_for('users_bp.director_home'))

    elif role == "student":
        return redirect(url_for('users_bp.student_home')) 

    elif role == "admin":
        return redirect(url_for('admin_bp.admin_home')) 

    return redirect(url_for("auth_bp.login_page")) 


# -------------------------
# 頁面路由
# -------------------------
  
#登入
@auth_bp.route("/login")
def login_page():
    return render_template("auth/login.html")

#登出
@auth_bp.route("/logout")
def logout_page():
    session.clear()  # 清除所有登入資訊
    return redirect(url_for("auth_bp.login_page"))

# 多角色登入後確認角色頁面
@auth_bp.route('/login-confirm')
def login_confirm_page():
    roles = session.get("pending_roles")  # 登入時先把多角色放這
    if not roles:
        return redirect(url_for("auth_bp.login_page"))

    return render_template("auth/login-confirm.html", roles_json=json.dumps(roles))

# 學生註冊
@auth_bp.route("/register_student")
def show_register_student_page():
    return render_template("auth/register_student.html")


