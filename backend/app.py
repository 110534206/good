from flask import Flask, render_template, request, send_file, redirect,session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_cors import CORS
import mysql.connector
import traceback
import os
import re
from datetime import datetime
from flask import Blueprint
from flask import url_for

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

    print(f"登入嘗試: username={username}")  # 調試信息

    if not username or not password:
        return jsonify({"success": False, "message": "帳號或密碼不得為空"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 查詢所有匹配用戶名的用戶
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        users = cursor.fetchall()
        print(f"找到 {len(users)} 個用戶")  # 調試信息

        # 檢查密碼並收集所有匹配的角色
        matching_roles = []
        for user in users:
            print(f"檢查用戶: {user['username']}, role: {user['role']}")  # 調試信息
            if check_password_hash(user["password"], password):
                matching_roles.append(user["role"])
                session["username"] = username
                session["role"] = user["role"]

        if matching_roles:
            print(f"密碼驗證成功，角色: {matching_roles}")  # 調試信息
            return jsonify({
                "success": True, 
                "username": username,
                "roles": matching_roles  # 返回角色陣列，符合前端期望
            })
        else:
            print("密碼驗證失敗")  # 調試信息
            return jsonify({"success": False, "message": "帳號或密碼錯誤"}), 401

    except Exception as e:
        print(f"登入錯誤: {e}")  # 調試信息
        return jsonify({"success": False, "message": "登入失敗"}), 500
    finally:
        cursor.close()
        conn.close()       

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
            c.department
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id;
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
# API - 取得個人資料
# -------------------------
@app.route("/api/profile", methods=["GET"])
def get_profile():
    username = request.args.get("username")
    role = request.args.get("role")

    if not username or role not in ["student", "teacher", "director", "admin"]:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 先取得基本資料和學生班級
        cursor.execute("""
            SELECT u.username, u.email, u.role, u.name, c.department, c.name AS class_name
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            WHERE u.username = %s AND u.role = %s
        """, (username, role))
        
        user = cursor.fetchone()
        if not user:
            return jsonify({"success": False, "message": "使用者不存在"}), 404

        if role in ('teacher', 'director'):
            # 老師及主任要取得他們所帶班級
            cursor.execute("""
                SELECT c.id, c.name, c.department
                FROM classes c
                JOIN classes_teacher ct ON c.id = ct.class_id
                JOIN users u ON ct.teacher_id = u.id
                WHERE u.username = %s AND u.role = %s
            """, (username, role))
            classes = cursor.fetchall()
            user['classes'] = classes  # 新增帶班班級清單

        if not user.get("email"):
            user["email"] = ""

        return jsonify({"success": True, "user": user, "role": role})

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
        # 更新狀態和備註（如果有提供）
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
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": "更新失敗"}), 500
    finally:
        cursor.close()
        conn.close()

    return jsonify({"success": True, "message": f"履歷狀態已更新為 {status}"})

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
    comment = data.get('comment', '').strip()

    if not resume_id or not comment:
        return jsonify({"success": False, "message": "缺少必要參數"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE resumes SET comment = %s WHERE id = %s", (comment, resume_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "留言更新成功"})

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
# API - 提交志願
# -------------------------
@app.route('/submit_preferences', methods=['POST'])
def submit_preferences():
    student_id = request.form.get('student_id')
    preferences = []
    
    for i in range(1, 6):
        company_id = request.form.get(f'preference_{i}')
        if company_id:
            preferences.append((student_id, i, company_id, datetime.now()))

    try:
        conn = get_db()
        cursor = conn.cursor()

        # 刪除學生舊資料
        cursor.execute("DELETE FROM student_preferences WHERE student_id = %s", (student_id,))
        conn.commit()

        # 插入新志願
        cursor.executemany("""
            INSERT INTO student_preferences (student_id, preference_order, company_id, submitted_at)
            VALUES (%s, %s, %s, %s)
        """, preferences)

        conn.commit()
        cursor.close()
        conn.close()

        return render_template("fill_preferences.html", message="✅ 志願序已成功送出", companies=[])  # or redirect
    except Exception as e:
        print("志願儲存錯誤：", e)
        return render_template("fill_preferences.html", message="❌ 發生錯誤，請稍後再試", companies=[])

# -------------------------
# 公司資訊上傳 API
# -------------------------
@app.route("/api/upload_company", methods=["POST"])
def upload_company():
    try:
        data = request.json
        company_name = data.get("company_name")
        job_title = data.get("job_title")
        job_description = data.get("job_description")
        job_location = data.get("job_location")
        contact_person = data.get("contact_person")
        contact_email = data.get("contact_email")

        if not company_name or not job_title or not job_description:
            return jsonify({"success": False, "message": "❌ 公司名稱、職位名稱與工作內容為必填"})

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO internship_companies
            (company_name, job_title, job_description, job_location, contact_person, contact_email, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """, (company_name, job_title, job_description, job_location, contact_person, contact_email))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"success": True, "message": "✅ 公司資訊已成功上傳"})

    except Exception as e:
        print("❌ upload_company 錯誤：", e)
        return jsonify({"success": False, "message": "伺服器錯誤，請稍後再試"})

@app.route('/upload_company', methods=['GET'])
def upload_company_form():
    return render_template('upload_company.html')

# -------------------------
# API - 取得已審核通過的公司清單
# -------------------------
@app.route("/api/approved_companies", methods=["GET"])
def get_approved_companies():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT 
                id,
                company_name,
                description,
                location,
                contact_person,
                contact_email,
                contact_phone,
                uploaded_by_user_id,
                submitted_at,
                reviewed_by_user_id,
                reviewed_at
            FROM internship_companies
            WHERE status = 'approved'
        """)
        companies = cursor.fetchall()
        return jsonify({"success": True, "companies": companies})
    except Exception as e:
        print("取得公司清單錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

@app.route("/api/review_company", methods=["POST"])
def review_company():
    data = request.get_json()
    company_id = data.get("company_id")
    status = data.get("status")

    if not company_id or status not in ['approved', 'rejected']:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        reviewed_at = datetime.now()
        reviewed_by_user_id = 1  # 假設寫死為主任 ID

        cursor.execute("""
            UPDATE internship_companies 
            SET status = %s, reviewed_by_user_id = %s, reviewed_at = %s
            WHERE id = %s
        """, (status, reviewed_by_user_id, reviewed_at, company_id))

        conn.commit()
        return jsonify({"success": True, "message": f"公司已{status == 'approved' and '核准' or '拒絕'}"})
    except Exception as e:
        print("審核公司錯誤：", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 填寫志願
# -------------------------
@app.route('/fill_preferences', methods=['GET', 'POST'])
def fill_preferences():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    if request.method == 'POST':
        student_id = request.form.get('student_id')

        preferences = []
        for i in range(1, 6):  
            company_id = request.form.get(f'preference_{i}')
            if company_id:
                preferences.append((student_id, i, company_id, datetime.now()))

        try:
            # 先刪除舊的志願資料（避免重複）
            cursor.execute("DELETE FROM student_preferences WHERE student_id = %s", (student_id,))
            conn.commit()

            # 批次插入新的志願
            cursor.executemany("""
                INSERT INTO student_preferences (student_id, preference_order, company_id, submitted_at)
                VALUES (%s, %s, %s, %s)
            """, preferences)

            conn.commit()
            message = "志願已成功送出！"
        except Exception as e:
            print("寫入志願錯誤：", e)
            message = "送出失敗，請稍後再試"
        finally:
            cursor.close()
            conn.close()

        return render_template('fill_preferences.html', message=message, companies=[])

    # GET：撈出核准公司清單
    cursor.execute("""
        SELECT id, company_name 
        FROM internship_companies 
        WHERE status = 'approved'
    """)
    companies = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('fill_preferences.html', companies=companies)
       

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
    role = request.args.get('role')
    if role == "student":
        return redirect('/student_home')
    elif role == "teacher":
        return redirect('/teacher_home')
    elif role == "director":
        return redirect('/director_home')
    else:
        return redirect('/student_home') 

@app.route('/student_home')
def student_home():
    return render_template('student_home.html')

@app.route('/admin_home')
def admin_home():
    return render_template('admin_home.html')

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

@app.route("/register_student")
def register_student_page():
    return render_template("register_student.html")

@app.route("/login-confirm")
def login_confirm_page():
    return render_template("login-confirm.html")

# -------------------------
# 主程式入口
# -------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
