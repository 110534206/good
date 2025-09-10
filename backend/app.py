from flask import Flask, render_template, request, send_file, redirect,session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_cors import CORS
import mysql.connector
import traceback
import os
import json
import re
from datetime import datetime
from flask import Blueprint
from collections import defaultdict
from flask import url_for
from werkzeug.security import generate_password_hash

# -------------------------
# Flask 與 CORS 設定
# -------------------------
app = Flask(
    __name__,
    static_folder='../frontend/static',
    template_folder='../frontend/templates'
)

# -------------------------
# 添加管理員模板目錄
# -------------------------
from jinja2 import ChoiceLoader, FileSystemLoader
app.jinja_loader = ChoiceLoader([
    app.jinja_loader,
    FileSystemLoader('../admin_frontend/templates')
])
app.secret_key = "your_secret_key"  
app.config['UPLOAD_FOLDER'] = './uploads'
CORS(app, supports_credentials=True)

# -------------------------
# 資料庫連線
# -------------------------
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="user"
    )

# -------------------------
# 共用註冊函數
# -------------------------
def register_user(username, raw_password, role, email=""):
    if not username or not raw_password or not role:
       return {"success": False, "message": "必要欄位缺失"}, 400 

    hashed_password = generate_password_hash(raw_password)

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE username = %s AND role = %s", (username, role))
    if cursor.fetchone():
        cursor.close()
        conn.close()
        return {"success": False, "message": "此帳號已註冊為該身分"}, 409

    cursor.execute(
        "INSERT INTO users (username, password, role, email) VALUES (%s, %s, %s, %s)",
        (username, hashed_password, role, email)
    )
    conn.commit()
    user_id = cursor.lastrowid
    print("使用者ID:", user_id)
    cursor.close()
    conn.close()

    role_display = {
        "student": "學生",
    }.get(role, role)
    
    return {"success": True, "message": f"{role_display}註冊成功"}, 201

# -------------------------
# 首頁路由（使用者前台）
# -------------------------
@app.route("/")
def index():
    if "username" in session and session.get("role") == "student":
        return redirect('/student_home')
    return redirect(url_for("login_page"))

# -------------------------
# 管理員首頁（後台）
# -------------------------
@app.route("/admin")
def admin_index():
    if "username" in session and session.get("role") == "admin":
        try:
            return render_template("admin_home.html", username=session["username"])
        except Exception as e:
            print(f"管理員主頁錯誤: {e}")
            # 如果找不到 admin_home.html，重定向到管理員首頁
            return redirect("/admin_home")
    return redirect(url_for("login_page"))

# -------------------------
# API - 註冊學生帳號 (POST)
# -------------------------
@app.route("/api/register_student", methods=["POST"])
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
# API - 登入
# -------------------------
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"success": False, "message": "帳號或密碼不得為空"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 找出所有同帳號的角色 (可能有 teacher + director)
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        users = cursor.fetchall()

        if not users:
            return jsonify({"success": False, "message": "帳號不存在"}), 404

        matching_roles = []
        matched_user = None  

        # 驗證密碼
        for user in users:
            if check_password_hash(user["password"], password):
                matching_roles.append(user["role"])
                matched_user = user  

        if not matched_user:
            return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

        # 設定 session
        session["username"] = matched_user["username"]
        session["user_id"] = matched_user["id"]

        # 如果角色超過 1 個 → 跳 login-confirm 頁面
        if len(matching_roles) > 1:
         session["pending_roles"] = matching_roles 
         return jsonify({
        "success": True,
        "username": matched_user["username"],
        "roles": matching_roles,
        "redirect": "/login-confirm"
    })

        # 只有一個角色 → 直接判斷導向
        single_role = matching_roles[0]
        session["role"] = single_role
        session["original_role"] = single_role  

        redirect_page = "/"

        if single_role == "student":
            redirect_page = "/student_home"

        elif single_role == "teacher":
            # 檢查是否為班導
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
@app.route('/api/confirm-role', methods=['POST'])
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
            # 檢查是否為班導
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
            # 其他角色
            redirect_page = f"/{role}_home"

        # 設定 session 角色
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
# API - 班導首頁
# -------------------------
@app.route('/class_teacher_home')
def class_teacher_home():
    if 'username' not in session or session.get('role') not in ['teacher', 'director']:
        return redirect(url_for('login_page'))

    user_id = session.get('user_id')

    conn = get_db()
    cursor = conn.cursor()
    try:
        # 確認是不是班導
        cursor.execute("""
            SELECT 1 FROM classes_teacher
            WHERE teacher_id = %s AND role = '班導師'
        """, (user_id,))
        is_homeroom = cursor.fetchone()

        if not is_homeroom:
            if session.get('original_role') == 'teacher':
                return redirect('/teacher_home')
            elif session.get('original_role') == 'director':
                return redirect('/director_home')
    finally:
        cursor.close()
        conn.close()

    # 傳入 original_role 到模板
    return render_template(
        'class_teacher_home.html',
        username=session.get('username'),
        original_role=session.get('original_role', 'teacher')  # fallback 預設為 teacher
    )

# -------------------------
# API - 註冊頁面    
# -------------------------
@app.route("/register_student", methods=["GET"])
def show_register_student_page():
    return render_template("register_student.html")

# -------------------------
# API - 管理員用戶管理
# -------------------------
@app.route('/api/admin/get_all_users', methods=['GET'])
def get_all_users():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT 
            u.id, u.username, u.name, u.email, u.role, u.class_id,
            c.name AS class_name,
            c.department,
            u.created_at
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            ORDER BY u.created_at DESC
        """)
        users = cursor.fetchall()

        for user in users:
            if user.get('created_at'):
                user['created_at'] = user['created_at'].strftime("%Y-%m-%d %H:%M:%S")

            # 老師或主任不顯示 class_id 及班級名稱，因為他們的班級由 classes_teacher 管理
            if user['role'] in ('teacher', 'director'):
                user['class_id'] = None
                user['class_name'] = None
                user['department'] = None

        return jsonify({"success": True, "users": users})
    except Exception as e:
        print(f"獲取用戶列表錯誤: {e}")
        return jsonify({"success": False, "message": "獲取用戶列表失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# 管理員指定學生班級
@app.route('/api/admin/assign_student_class', methods=['POST'])
def assign_student_class():
    data = request.get_json()
    user_id = data.get('user_id')
    class_id = data.get('class_id')

    if not user_id or not class_id:
        return jsonify({"success": False, "message": "缺少必要參數"}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        # 確認使用者是學生
        cursor.execute("SELECT role FROM users WHERE id=%s", (user_id,))
        user = cursor.fetchone()
        if not user or user[0] != 'student':
            return jsonify({"success": False, "message": "該用戶不是學生"}), 400

        # 確認班級存在
        cursor.execute("SELECT id FROM classes WHERE id=%s", (class_id,))
        if not cursor.fetchone():
            return jsonify({"success": False, "message": "班級不存在"}), 404

        # 更新學生班級
        cursor.execute("UPDATE users SET class_id=%s WHERE id=%s", (class_id, user_id))
        conn.commit()
        return jsonify({"success": True, "message": "學生班級設定成功"})
    except Exception as e:
        print(f"設定學生班級錯誤: {e}")
        return jsonify({"success": False, "message": "設定失敗"}), 500
    finally:
        cursor.close()
        conn.close()


# 管理員指定老師在班級的角色 (班導師 / 授課老師)
@app.route('/api/admin/assign_class_teacher', methods=['POST'])
def assign_class_teacher():
    data = request.get_json()
    class_id = data.get('class_id')
    teacher_id = data.get('teacher_id')
    role = data.get('role', '班導師')  # 預設是班導師

    if not class_id or not teacher_id:
        return jsonify({"success": False, "message": "缺少必要參數"}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        # 檢查是否為教師或主任
        cursor.execute("SELECT role FROM users WHERE id=%s", (teacher_id,))
        user = cursor.fetchone()
        if not user or user[0] not in ('teacher', 'director'):
            return jsonify({"success": False, "message": "該用戶不是教師或主任"}), 400

        # 確認班級存在
        cursor.execute("SELECT id FROM classes WHERE id=%s", (class_id,))
        if not cursor.fetchone():
            return jsonify({"success": False, "message": "班級不存在"}), 404

        # 檢查是否已存在相同關聯（避免重複）
        cursor.execute("""
            SELECT id FROM classes_teacher 
            WHERE class_id=%s AND teacher_id=%s AND role=%s
        """, (class_id, teacher_id, role))
        if cursor.fetchone():
            return jsonify({"success": False, "message": f"該班級已有此教師擔任 {role}"}), 409

        # 插入班級與教師的角色關聯
        cursor.execute("""
            INSERT INTO classes_teacher (class_id, teacher_id, role, created_at) 
            VALUES (%s, %s, %s, NOW())
        """, (class_id, teacher_id, role))

        conn.commit()
        return jsonify({"success": True, "message": f"{role} 設定成功"})

    except Exception as e:
        print(f"設定班導錯誤: {e}")
        return jsonify({"success": False, "message": "設定失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# 新增用戶
@app.route('/api/admin/create_user', methods=['POST'])
def admin_create_user():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    role = data.get('role')
    name = data.get('name', '')
    email = data.get('email', '')
    class_id = data.get('class_id')  # 學生才會用到

    if not username or not password or not role:
        return jsonify({"success": False, "message": "用戶名、密碼和角色為必填欄位"}), 400

    valid_roles = ['student', 'teacher', 'director', 'admin']
    if role not in valid_roles:
        return jsonify({"success": False, "message": "無效的角色"}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE username = %s AND role = %s", (username, role))
        if cursor.fetchone():
            return jsonify({"success": False, "message": "該帳號已存在此角色"}), 409

        hashed_password = generate_password_hash(password)

        if role == "student":
            # 學生可指定班級
            cursor.execute("""
                INSERT INTO users (username, password, role, name, email, class_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (username, hashed_password, role, name, email, class_id))
        else:
            # 老師、主任、管理員不指定班級
            cursor.execute("""
                INSERT INTO users (username, password, role, name, email)
                VALUES (%s, %s, %s, %s, %s)
            """, (username, hashed_password, role, name, email))

        conn.commit()
        return jsonify({"success": True, "message": "用戶新增成功"})
    except Exception as e:
        print(f"新增用戶錯誤: {e}")
        return jsonify({"success": False, "message": "新增用戶失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# 更新用戶
@app.route('/api/admin/update_user/<int:user_id>', methods=['PUT'])
def admin_update_user(user_id):
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    role = data.get('role')
    name = data.get('name', '')
    email = data.get('email', '')
    class_id = data.get('class_id')

    if not username or not role:
        return jsonify({"success": False, "message": "用戶名和角色為必填欄位"}), 400

    valid_roles = ['student', 'teacher', 'director', 'admin']
    if role not in valid_roles:
        return jsonify({"success": False, "message": "無效的角色"}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            return jsonify({"success": False, "message": "用戶不存在"}), 404

        cursor.execute("SELECT id FROM users WHERE username = %s AND id != %s", (username, user_id))
        if cursor.fetchone():
            return jsonify({"success": False, "message": "用戶名已被其他用戶使用"}), 409

        hashed_password = generate_password_hash(password) if password else None

        if role == "student":
            # 學生可指定班級
            if hashed_password:
                cursor.execute("""
                    UPDATE users SET username=%s, password=%s, role=%s, name=%s, email=%s, class_id=%s
                    WHERE id=%s
                """, (username, hashed_password, role, name, email, class_id, user_id))
            else:
                cursor.execute("""
                    UPDATE users SET username=%s, role=%s, name=%s, email=%s, class_id=%s
                    WHERE id=%s
                """, (username, role, name, email, class_id, user_id))
        else:
            # 老師、主任、管理員不更新班級欄位（因為沒用）
            if hashed_password:
                cursor.execute("""
                    UPDATE users SET username=%s, password=%s, role=%s, name=%s, email=%s
                    WHERE id=%s
                """, (username, hashed_password, role, name, email, user_id))
            else:
                cursor.execute("""
                    UPDATE users SET username=%s, role=%s, name=%s, email=%s
                    WHERE id=%s
                """, (username, role, name, email, user_id))

        conn.commit()
        return jsonify({"success": True, "message": "用戶更新成功"})
    except Exception as e:
        print(f"更新用戶錯誤: {e}")
        return jsonify({"success": False, "message": "更新用戶失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# 刪除用戶
@app.route('/api/admin/delete_user/<int:user_id>', methods=['DELETE'])
def admin_delete_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        # 檢查用戶是否存在
        cursor.execute("SELECT id, role FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            return jsonify({"success": False, "message": "用戶不存在"}), 404

        # 如果是老師或主任，刪除時同時刪除 classes_teacher 的關聯
        if user[1] in ('teacher', 'director'):
            cursor.execute("DELETE FROM classes_teacher WHERE teacher_id = %s", (user_id,))

        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        return jsonify({"success": True, "message": "用戶刪除成功"})
    except Exception as e:
        print(f"刪除用戶錯誤: {e}")
        return jsonify({"success": False, "message": "刪除用戶失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# 取得教師所帶班級
@app.route('/api/teacher/classes/<int:user_id>', methods=['GET'])
def get_classes_by_teacher(user_id):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT c.id, c.name, c.department
            FROM classes c
            JOIN classes_teacher ct ON c.id = ct.class_id
            WHERE ct.teacher_id = %s
        """, (user_id,))
        classes = cursor.fetchall()
        return jsonify({"success": True, "classes": classes})
    except Exception as e:
        print("獲取教師班級錯誤:", e)
        return jsonify({"success": False, "message": "獲取資料失敗"}), 500
    finally:
        cursor.close()
        conn.close()


# 取得所有班級
@app.route('/api/admin/get_all_classes', methods=['GET'])
def get_all_classes():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
           SELECT c.id, c.name, c.department, GROUP_CONCAT(u.name) AS teacher_names
           FROM classes c
           LEFT JOIN classes_teacher ct ON c.id = ct.class_id
           LEFT JOIN users u ON ct.teacher_id = u.id
           GROUP BY c.id, c.name, c.department
        """)
        classes = cursor.fetchall()
        return jsonify({"success": True, "classes": classes})
    except Exception as e:
        print(f"獲取班級列表錯誤: {e}")
        return jsonify({"success": False, "message": "獲取班級列表失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 個人資料
# -------------------------
@app.route("/api/profile", methods=["GET"])
def get_profile():
    # 檢查登入狀態
    if "username" not in session or "role" not in session:
        return jsonify({"success": False, "message": "尚未登入"}), 401

    username = session["username"]
    role = session["role"]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 基本資料
        cursor.execute("""
            SELECT u.id, u.username, u.email, u.role, u.name, 
                   c.department, c.name AS class_name, u.class_id
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            WHERE u.username = %s AND u.role = %s
        """, (username, role))
        user = cursor.fetchone()

        if not user:
            return jsonify({"success": False, "message": "使用者不存在"}), 404

        # 教師/主任 → 查所帶班級 & 是否為班導
        is_homeroom = False
        if role in ("teacher", "director"):
            # 查詢所帶的所有班級
            cursor.execute("""
                SELECT c.id, c.name, c.department
                FROM classes c
                JOIN classes_teacher ct ON c.id = ct.class_id
                WHERE ct.teacher_id = %s
            """, (user["id"],))
            user["classes"] = cursor.fetchall()

            # 是否為班導
            cursor.execute("""
                SELECT 1 FROM classes_teacher 
                WHERE teacher_id = %s AND role = '班導師'
            """, (user["id"],))
            is_homeroom = bool(cursor.fetchone())

        user["is_homeroom"] = is_homeroom

        # 避免前端收到 None
        if not user.get("email"):
            user["email"] = ""

        return jsonify({"success": True, "user": user})

    except Exception as e:
        print("取得個人資料錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 更新個人資料
# -------------------------
@app.route("/api/saveProfile", methods=["POST"])
def save_profile():
    data = request.get_json()
    username = data.get("number")
    role_display = data.get("role")
    name = data.get("name")
    class_id = data.get("class_id")

    if not username or not role_display or not name:
        return jsonify({"success": False, "message": "缺少必要欄位"}), 400

    role_map = {
        "學生": "student",
        "教師": "teacher",
        "主任": "director",
        "管理員": "admin"
    }
    role = role_map.get(role_display)
    if not role:
        return jsonify({"success": False, "message": "身分錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor()

    try:
        # 先檢查使用者是否存在
        cursor.execute("SELECT id FROM users WHERE username=%s AND role=%s", (username, role))
        if not cursor.fetchone():
            return jsonify({"success": False, "message": "找不到該使用者資料"}), 404

        # 更新姓名
        cursor.execute("UPDATE users SET name=%s WHERE username=%s AND role=%s", (name, username, role))

        # 只有學生需更新班級，且班級存在才更新
        if role == "student":
            if not class_id:
                return jsonify({"success": False, "message": "學生需提供班級"}), 400
            # 檢查班級是否存在
            cursor.execute("SELECT id FROM classes WHERE id=%s", (class_id,))
            if not cursor.fetchone():
                return jsonify({"success": False, "message": "班級不存在"}), 404
            cursor.execute("UPDATE users SET class_id=%s WHERE username=%s AND role=%s", (class_id, username, role))

        conn.commit()
        return jsonify({"success": True, "message": "資料更新成功"})

    except Exception as e:
        print("更新資料錯誤:", e)
        return jsonify({"success": False, "message": "資料庫錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 上傳履歷
# -------------------------
@app.route('/api/upload_resume', methods=['POST'])
def upload_resume_api():
    # 檢查是否有檔案
    if 'resume' not in request.files:
        return jsonify({"success": False, "message": "未上傳檔案"}), 400

    file = request.files['resume']
    username = request.form.get('username')

    # 檢查使用者帳號
    if not username:
        return jsonify({"success": False, "message": "缺少使用者帳號"}), 400

    # 檢查檔名
    if file.filename == '':
        return jsonify({"success": False, "message": "檔案名稱為空"}), 400

    # 取得原始檔名(用於前端顯示與DB紀錄)
    original_filename = file.filename

    # 產生安全的檔名並加上時間戳
    safe_filename = secure_filename(original_filename)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    stored_filename = f"{timestamp}_{safe_filename}"

    # 儲存檔案
    upload_folder = app.config['UPLOAD_FOLDER']
    save_path = os.path.join(upload_folder, stored_filename)
    file.save(save_path)

    # 查詢使用者 ID
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
    user = cursor.fetchone()
    if not user:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "找不到使用者"}), 404

    user_id = user[0]
    filesize = os.path.getsize(save_path)  # 取得檔案大小 (bytes)

    # 寫入資料庫
    cursor.execute("""
        INSERT INTO resumes (user_id, original_filename, filepath, filesize, status, created_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
    """, (user_id, original_filename, save_path, filesize, 'uploaded'))

    resume_id = cursor.lastrowid

    conn.commit()
    cursor.close()
    conn.close()

    # 回傳資訊給前端
    return jsonify({
        "success": True,
        "resume_id": resume_id,
        "filename": original_filename,  # 前端用的原始檔名
        "filesize": filesize,
        "status": "uploaded",
        "message": "履歷上傳成功"
    })

# -------------------------
# API - 審核履歷
# -------------------------
@app.route('/api/review_resume', methods=['POST'])
def review_resume_api():
    data = request.get_json()
    resume_id = data.get('resume_id')
    status = data.get('status')
    comment = data.get('comment', '').strip()
    
    if not resume_id or status not in ['approved', 'rejected']:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor()

    # 確認履歷存在
    cursor.execute("SELECT id FROM resumes WHERE id = %s", (resume_id,))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "找不到該履歷"}), 404

    try:
        # 更新狀態與審核意見
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
        return jsonify({"success": True, "message": "履歷審核完成", "resume_id": resume_id, "status": status})

    except Exception as e:
        print("審核履歷錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()                    

# -------------------------
# API - 更新履歷
# -------------------------
@app.route('/api/update_resume_field', methods=['POST'])
def update_resume_field():
    data = request.get_json()

    resume_id = data.get('resume_id')
    field = data.get('field')
    value = (data.get('value') or '').strip()

    allowed_fields = {
        "comment": "comment",
        "note": "note"
    }

    # 驗證 resume_id 與 field
    try:
        resume_id = int(resume_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "resume_id 必須是數字"}), 400

    if field not in allowed_fields:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        sql = f"UPDATE resumes SET {allowed_fields[field]} = %s WHERE id = %s"
        cursor.execute(sql, (value, resume_id))
        conn.commit()
        return jsonify({"success": True, "field": field, "resume_id": resume_id}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 查詢履歷狀態
# -------------------------
@app.route('/api/resume_status', methods=['GET'])
def resume_status():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT status FROM resumes WHERE id = %s", (resume_id,))
    resume = cursor.fetchone()
    cursor.close()
    conn.close()

    if not resume:
        return jsonify({"success": False, "message": "找不到該履歷"}), 404

    return jsonify({"success": True, "status": resume['status']})

# -------------------------
# API - 所有履歷清單
# -------------------------
@app.route('/api/get_all_resumes', methods=['GET'])
def get_all_resumes():
    username = request.args.get('username')
    if not username:
        return jsonify({"success": False, "message": "缺少 username 參數"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
    user = cursor.fetchone()
    if not user:
        return jsonify({"success": False, "message": "找不到使用者"}), 404

    user_id = user['id']
    cursor.execute("""
      SELECT id, original_filename, filepath, filesize, status, comment, note, created_at AS upload_time
      FROM resumes WHERE user_id = %s ORDER BY created_at DESC
     """, (user_id,))
    resumes = cursor.fetchall()
    for r in resumes:
        if isinstance(r.get('upload_time'), datetime):
            r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")

    cursor.close()
    conn.close()
    return jsonify({"success": True, "resumes": resumes})

# -------------------------
# API - 留言更新
# -------------------------
@app.route('/api/submit_comment', methods=['POST'])
def submit_comment():
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
    try:
        # 檢查履歷是否存在
        cursor.execute("SELECT id FROM resumes WHERE id=%s", (resume_id,))
        if not cursor.fetchone():
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        # 更新留言 (note 欄位)
        cursor.execute("UPDATE resumes SET note=%s WHERE id=%s", (comment, resume_id))
        conn.commit()

        return jsonify({"success": True, "message": "留言更新成功"})
    except Exception as e:
        conn.rollback()
        print("更新留言錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 下載履歷
# -------------------------
@app.route('/api/download_resume', methods=['GET'])
def download_resume():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT filepath, original_filename FROM resumes WHERE id = %s", (resume_id,))
    resume = cursor.fetchone()
    cursor.close()
    conn.close()

    if not resume or not os.path.exists(resume['filepath']):
        return jsonify({"success": False, "message": "找不到檔案"}), 404

    return send_file(resume['filepath'], as_attachment=True, download_name=resume['original_filename'])

# -------------------------
# API - 刪除履歷
# -------------------------
@app.route('/api/delete_resume', methods=['DELETE'])
def delete_resume():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT filepath FROM resumes WHERE id = %s", (resume_id,))
    result = cursor.fetchone()
    if not result:
        return jsonify({"success": False, "message": "找不到該履歷"}), 404

    filepath = result[0]
    if os.path.exists(filepath):
        os.remove(filepath)

    cursor.execute("DELETE FROM resumes WHERE id = %s", (resume_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "履歷已刪除"})

# -------------------------
# API - 審核履歷
# -------------------------
@app.route('/api/approve_resume', methods=['POST'])
def approve_resume():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    conn = get_db()
    cursor = conn.cursor()

    # 檢查履歷是否存在
    cursor.execute("SELECT id FROM resumes WHERE id = %s", (resume_id,))
    if not cursor.fetchone():
        return jsonify({"success": False, "message": "找不到該履歷"}), 404

    # 更新狀態為 'approved'
    cursor.execute("UPDATE resumes SET status = %s WHERE id = %s", ("approved", resume_id))
    conn.commit()

    cursor.close()
    conn.close()

    return jsonify({"success": True, "message": "履歷已標記為完成"})

# -------------------------
# API - 取得所有學生履歷
# -------------------------
@app.route('/api/get_all_students_resumes', methods=['GET'])
def get_all_students_resumes():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT r.id, r.original_filename, r.status, r.comment, r.note, 
            r.created_at AS upload_time,
            u.username, u.name, c.department, c.name AS className
            FROM resumes r
            JOIN users u ON r.user_id = u.id
            JOIN classes c ON u.class_id = c.id
            ORDER BY r.created_at DESC
        """)
        resumes = cursor.fetchall()
        for r in resumes:
            if isinstance(r.get('upload_time'), datetime):
                r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"success": True, "resumes": resumes})
    except Exception as e:
        print("取得所有學生履歷錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 接受履歷
# -------------------------
@app.route('/api/reject_resume', methods=['POST'])
def reject_resume():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE resumes SET status = 'rejected' WHERE id = %s", (resume_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "履歷已標記為拒絕"})

# -------------------------
# 上傳公司
# -------------------------
@app.route('/upload_company', methods=['GET', 'POST'])
def upload_company_form():
    if request.method == 'POST':
        try:
            data = request.form
            company_name = data.get("company_name")
            company_description = data.get("description")
            company_location = data.get("location")
            contact_person = data.get("contact_person")
            contact_email = data.get("contact_email")
            contact_phone = data.get("contact_phone")

            # 公司名稱必填
            if not company_name:
                return render_template('upload_company.html', error="公司名稱為必填")

            # 從 session 拿上傳者 id
            uploaded_by_user_id = session.get("user_id")
            if not uploaded_by_user_id:
                return render_template('upload_company.html', error="請先登入")

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO internship_companies
                (company_name, description, location, contact_person, contact_email, contact_phone, 
                 uploaded_by_user_id, status, submitted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', NOW())
            """, (
                company_name,
                company_description,
                company_location,
                contact_person,
                contact_email,
                contact_phone,
                uploaded_by_user_id
            ))
            conn.commit()
            cursor.close()
            conn.close()

            # 上傳成功訊息，告知狀態是待審核
            success_msg = "公司資訊已成功上傳，狀態：待審核"
            return render_template('upload_company.html', success=success_msg)

        except Exception as e:
            print("❌ 錯誤：", e)
            return render_template('upload_company.html', error="伺服器錯誤，請稍後再試")
    else:
        return render_template('upload_company.html')

# -------------------------
# API - 審核公司
# -------------------------
@app.route("/api/approve_company", methods=["POST"])
def api_approve_company():
    data = request.get_json()
    company_id = data.get("company_id")
    status = data.get("status")

    if not company_id or status not in ['approved', 'rejected']:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()

        reviewed_at = datetime.now()

        # 取得公司資訊
        cursor.execute("SELECT company_name, status FROM internship_companies WHERE id = %s", (company_id,))
        company_row = cursor.fetchone()

        if not company_row:
            return jsonify({"success": False, "message": "查無此公司"}), 404

        company_name, current_status = company_row

        # 防止重複審核
        if current_status != 'pending':
            return jsonify({"success": False, "message": f"公司已被審核過（目前狀態為 {current_status}）"}), 400

        # 更新公司狀態與審核時間
        cursor.execute("""
            UPDATE internship_companies
            SET status = %s, reviewed_at = %s
            WHERE id = %s
        """, (status, reviewed_at, company_id))

        conn.commit()

        action_text = '核准' if status == 'approved' else '拒絕'
        return jsonify({"success": True, "message": f"公司已{action_text}"}), 200

    except Exception as e:
        print("審核公司錯誤：", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 志願填寫
# -------------------------
@app.route('/fill_preferences', methods=['GET', 'POST'])
def fill_preferences():
    # 1. 登入檢查
    if 'user_id' not in session or session.get('role') != 'student':
        return redirect(url_for('login'))

    student_id = session['user_id']

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    message = None

    if request.method == 'POST':
        preferences = []
        for i in range(1, 6):
            company_id = request.form.get(f'preference_{i}')
            if company_id:
                preferences.append((student_id, i, company_id, datetime.now()))

        try:
            # 刪除舊志願
            cursor.execute("DELETE FROM student_preferences WHERE student_id = %s", (student_id,))
            conn.commit()

            # 新增志願
            if preferences:
                cursor.executemany("""
                    INSERT INTO student_preferences (student_id, preference_order, company_id, submitted_at)
                    VALUES (%s, %s, %s, %s)
                """, preferences)
                conn.commit()
                message = "✅ 志願序已成功送出"
            else:
                message = "⚠️ 未選擇任何志願，公司清單已重置"
        except Exception as e:
            print("寫入志願錯誤：", e)
            message = "❌ 發生錯誤，請稍後再試"

    # 不管是 GET 還是 POST，都要載入公司列表及該學生已填的志願（以便前端顯示）
    cursor.execute("""
        SELECT id, company_name FROM internship_companies WHERE status = 'approved'
    """)
    companies = cursor.fetchall()

    cursor.execute("""
        SELECT preference_order, company_id FROM student_preferences WHERE student_id = %s ORDER BY preference_order
    """, (student_id,))
    prefs = cursor.fetchall()

    cursor.close()
    conn.close()

    # 把 prefs 轉成純 company_id 的 list，index 對應志願順序 -1
    submitted_preferences = [None] * 5  # 預設 5 個志願空位
    for pref in prefs:
        order = pref['preference_order']
        company_id = pref['company_id']
        if 1 <= order <= 5:
            submitted_preferences[order - 1] = company_id

    return render_template('fill_preferences.html', 
                           companies=companies, 
                           submitted_preferences=submitted_preferences,
                           message=message)

# -------------------------
# API - 選擇角色
# -------------------------
@app.route('/api/select_role', methods=['POST'])
def select_role():
    data = request.json
    username = data.get("username")
    role = data.get("role")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM users WHERE username=%s AND role=%s", (username, role))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if user:
        session["user_id"] = user["id"]  
        session["role"] = role
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "無此角色"}), 404

# -------------------------
# 班導查看志願序
# -------------------------
@app.route('/review_preferences')
def review_preferences():
    if 'username' not in session or session.get('role') not in ['teacher', 'director']:
        return redirect(url_for('login_page'))

    user_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 確認是否為班導
        cursor.execute("""
            SELECT c.id AS class_id
            FROM classes c
            JOIN classes_teacher ct ON c.id = ct.class_id
            WHERE ct.teacher_id = %s AND ct.role = '班導師'
        """, (user_id,))
        class_info = cursor.fetchone()
        if not class_info:
            return "你不是班導，無法查看志願序", 403

        class_id = class_info['class_id']

        # 查詢班上學生及其志願
        cursor.execute("""
            SELECT 
                u.id AS student_id,
                u.name AS student_name,
                sp.preference_order,
                ic.company_name,
                sp.submitted_at
            FROM users u
            JOIN student_preferences sp ON u.id = sp.student_id
            JOIN internship_companies ic ON sp.company_id = ic.id
            WHERE u.class_id = %s
            ORDER BY u.name, sp.preference_order
        """, (class_id,))
        results = cursor.fetchall()

        # 整理資料結構給前端使用
        student_data = defaultdict(list)
        for row in results:
            student_data[row['student_name']].append({
                'order': row['preference_order'],
                'company': row['company_name'],
                'submitted_at': row['submitted_at']
            })

        return render_template('review_preferences.html', student_data=student_data)

    except Exception as e:
        print("取得志願資料錯誤：", e)
        return "伺服器錯誤", 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 頁面路由
# -------------------------
@app.route('/profile')
def profile_page():
    return render_template('profile.html')

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/logout")
def logout_page():
    return render_template("login.html")

@app.route('/index')
def index_page():
    role = session.get("role")
    user_id = session.get("user_id")

    if not role:
        return redirect(url_for("login_page"))

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
            return redirect('/class_teacher_home')
        else:
            # 老師導向 teacher_home，主任導向 director_home
            if role == "teacher":
                return redirect('/teacher_home')
            else:
                return redirect('/director_home')

    elif role == "student":
        return redirect('/student_home')

    elif role == "admin":
        return redirect('/admin_home')

    return redirect(url_for("login_page"))


@app.route('/student_home')
def student_home():
    return render_template('student_home.html')

@app.route('/admin_home')
def admin_home():
    return render_template('admin_home.html')

@app.route('/api/get-session')
def get_session():
    if "username" in session and "role" in session:
        return jsonify({
            "success": True,
            "username": session["username"],
            "role": session["role"]
        })
    return jsonify({"success": False}), 401

@app.route('/user_management')
def user_management():
    try:
        return render_template('user_management.html')
    except Exception as e:
        print(f"用戶管理頁面錯誤: {e}")
        return f"用戶管理頁面載入錯誤: {str(e)}", 500
    

@app.route('/teacher_home')
def teacher_home():
    return render_template('teacher_home.html')

@app.route('/director_home')
def director_home():
    if session.get("role") != "director":
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, company_name FROM internship_companies WHERE status = 'pending'")
    companies = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("director_home.html", companies=companies)

@app.route('/upload_resume')
def upload_resume():
    return render_template('upload_resume.html')

@app.route('/review_resume')
def review_resume():
    return render_template('review_resume.html')

@app.route('/ai_edit_resume')
def ai_edit_resume():
    return render_template('ai_edit_resume.html')

@app.route('/approve_company')
def approve_company():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM internship_companies WHERE status = 'pending'")
    companies = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('approve_company.html', companies=companies)

@app.route('/notifications')
def notifications():
    return render_template('notifications.html')

@app.route("/api/announcements", methods=["GET"])
def get_announcements():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        now = datetime.now()
        cursor.execute("""
            SELECT 
                id, title, content, created_by, created_at,
                target_roles, status, visible_from, visible_until,
                is_important, view_count
            FROM announcements
            WHERE status = 'published'
              AND (visible_from IS NULL OR visible_from <= %s)
              AND (visible_until IS NULL OR visible_until >= %s)
            ORDER BY is_important DESC, created_at DESC
        """, (now, now))
        rows = cursor.fetchall()

        for row in rows:
            row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            row["visible_from"] = row["visible_from"].strftime("%Y-%m-%d %H:%M:%S") if row["visible_from"] else None
            row["visible_until"] = row["visible_until"].strftime("%Y-%m-%d %H:%M:%S") if row["visible_until"] else None
            row["source"] = row.pop("created_by") or "平台"

            if row["target_roles"]:
                try:
                    row["target_roles"] = json.loads(row["target_roles"])
                except Exception:
                    row["target_roles"] = []
            else:
                row["target_roles"] = []

        return jsonify({"success": True, "announcements": rows})

    except Exception as e:
        print("❌ 取得公告失敗：", e)
        return jsonify({"success": False, "message": "取得公告失敗"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.route("/register_student")
def register_student_page():
    return render_template("register_student.html")

@app.route('/login-confirm')
def login_confirm_page():
    roles = session.get("pending_roles")  # 登入時先把多角色放這
    if not roles:
        return redirect(url_for("login_page"))

    return render_template("login-confirm.html", roles_json=json.dumps(roles))

# 管理員密碼
hashed_password = generate_password_hash("1234", method="scrypt")
print(hashed_password)

# -------------------------
# 主程式入口
# -------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
