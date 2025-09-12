from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from config import get_db
import json

auth_bp = Blueprint("auth_bp", __name__)

# -------------------------
# API - 登入
# -------------------------
@auth_bp.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()  # 改成 get_json()
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
#頁面管理  
# -------------------------
  
#登入
@auth_bp.route("/login")
def login_page():
    return render_template("login.html")

# 多角色登入後確認角色頁面
@auth_bp.route('/login-confirm')
def login_confirm_page():
    roles = session.get("pending_roles")  # 登入時先把多角色放這
    if not roles:
        return redirect(url_for("auth_bp.login_page"))

    return render_template("login-confirm.html", roles_json=json.dumps(roles))

# 學生註冊
@auth_bp.route("/register_student")
def show_register_student_page():
    return render_template("register_student.html")