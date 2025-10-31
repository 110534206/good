from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from config import get_db
import os
import re # 引入正則表達式

users_bp = Blueprint("users_bp", __name__)

# -------------------------
# 老師首頁 (指導老師)
# -------------------------
@users_bp.route('/teacher_home')
def teacher_home():
    # 允許 'teacher' (單一身份) 或 'director' (多身份選擇後) 進入
    if 'username' not in session or session.get('role') not in ['teacher', 'director']:
        return redirect(url_for('auth_bp.login_page'))
    return render_template('user_shared/teacher_home.html')

# -------------------------
# 班導首頁
# -------------------------
@users_bp.route("/class_teacher_home")
def class_teacher_home():
    if "username" not in session or session.get("role") not in ["teacher", "director"]:
        return redirect(url_for("auth_bp.login_page"))

    if not session.get("is_homeroom"):
        return redirect(url_for("users_bp.teacher_home"))

    return render_template("user_shared/class_teacher_home.html",
                           username=session.get("username"),
                           original_role=session.get("role"))

# -------------------------
# API - 取得個人資料
# -------------------------
@users_bp.route('/api/profile', methods=['GET'])
def get_user_profile():
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "未登入"}), 401

    user_id = session.get('user_id')
    current_role = session.get('role')

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 取得用戶在資料庫中的原始角色及基本資料
        # 這是判斷是否為「主任」的核心依據
        cursor.execute("SELECT role, avatar_url, name, username, email FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()

        if not user_data:
            return jsonify({"success": False, "message": "用戶不存在"}), 404
        
        # 準備回傳資料
        user_info = {
            "id": user_id,
            "username": user_data.get('username'),
            "name": user_data.get('name'),
            "avatar_url": user_data.get('avatar_url'),
            "current_role": current_role,                      
            "original_role": user_data.get('role'),            
            "is_homeroom": session.get('is_homeroom', False)    
        }
        
        return jsonify({"success": True, "user": user_info})

    except Exception as e:
        current_app.logger.error(f"Error fetching profile: {e}")
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 更新個人資料
# -------------------------
@users_bp.route("/api/saveProfile", methods=["POST"])
def save_profile():
    data = request.get_json()
    username = data.get("username")
    role_display = data.get("role")
    name = data.get("name")
    class_id = data.get("class_id")

    if not username or not role_display or not name:
        return jsonify({"success": False, "message": "缺少必要欄位"}), 400

    role_map = {
        "學生": "student",
        "教師": "teacher",
        "主任": "director",
        "科助": "ta",
        "管理員": "admin"
    }
    role = role_map.get(role_display)
    if not role:
        return jsonify({"success": False, "message": "身分錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor()
    user_id = None
    try:
        # 取得 user_id
        cursor.execute("SELECT id FROM users WHERE username=%s AND role=%s", (username, role))
        user_row = cursor.fetchone()
        if not user_row:
            return jsonify({"success": False, "message": "找不到該使用者資料"}), 404
        
        user_id = user_row[0] # 取得 user_id

        cursor.execute("UPDATE users SET name=%s WHERE username=%s AND role=%s", (name, username, role))

        if role == "student":
            if not class_id:
                # 學生身分不強制 class_id，如果沒有提供則不更新 class_id
                pass
            else:
                try:
                    class_id = int(class_id)
                except ValueError:
                    return jsonify({"success": False, "message": "班級格式錯誤"}), 400

                cursor.execute("SELECT id FROM classes WHERE id=%s", (class_id,))
                if not cursor.fetchone():
                    return jsonify({"success": False, "message": "班級不存在"}), 404

                cursor.execute("UPDATE users SET class_id=%s WHERE username=%s AND role=%s",
                            (class_id, username, role)
                )
        else:
            # 非學生身分一律清空 class_id（避免舊資料殘留）
            cursor.execute(
                "UPDATE users SET class_id=NULL WHERE username=%s AND role=%s",
                (username, role)
            )

        # 查詢是否為班導師 (用於回傳給前端判斷跳轉)
        is_homeroom = False
        if role in ("teacher", "director"):
            cursor.execute("""
                SELECT 1 FROM classes_teacher 
                WHERE teacher_id = %s AND role = '班導師'
            """, (user_id,))
            is_homeroom = bool(cursor.fetchone())

        conn.commit()
        
        # 回傳 role 和 is_homeroom
        return jsonify({
            "success": True, 
            "message": "資料更新成功",
            "role": role, 
            "is_homeroom": is_homeroom 
        })
    except Exception as e:
        print("❌ 更新資料錯誤:", e)
        return jsonify({"success": False, "message": "資料庫錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 上傳頭像
# -------------------------
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@users_bp.route('/api/upload_avatar', methods=['POST'])
def upload_avatar():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "未登入"}), 401

    if 'avatar' not in request.files:
        return jsonify({"success": False, "message": "沒有檔案"}), 400

    file = request.files['avatar']
    if file and allowed_file(file.filename):
        filename = secure_filename(f"{session['user_id']}.png")
        
        # 確保 avatars 資料夾存在
        avatars_folder = os.path.join(current_app.static_folder, "avatars")
        os.makedirs(avatars_folder, exist_ok=True)
        
        # 儲存到 static/avatars 資料夾
        filepath = os.path.join(avatars_folder, filename)
        file.save(filepath)

        avatar_url = url_for('static', filename=f"avatars/{filename}")
        
        # 將頭像URL保存到資料庫
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE users SET avatar_url = %s WHERE id = %s", (avatar_url, session['user_id']))
            conn.commit()
        except Exception as e:
            print("❌ 更新頭像URL錯誤:", e)
            return jsonify({"success": False, "message": "更新頭像URL失敗"}), 500
        finally:
            cursor.close()
            conn.close()
        
        return jsonify({"success": True, "avatar_url": avatar_url})
    else:
        return jsonify({"success": False, "message": "檔案格式錯誤"}), 400

# -------------------------
# API - 變更密碼
# -------------------------
@users_bp.route('/api/change_password', methods=['POST'])
def change_password():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "尚未登入"}), 401

    data = request.get_json()
    old_password = data.get("old_password")
    new_password = data.get("new_password")

    if not old_password or not new_password:
        return jsonify({"success": False, "message": "請填寫所有欄位"}), 400

    user_id = session["user_id"]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 查詢密碼和角色
        cursor.execute("SELECT password, role FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()

        if not user or not check_password_hash(user["password"], old_password):
            return jsonify({"success": False, "message": "舊密碼錯誤"}), 403

        # 查詢是否為班導師 (用於回傳給前端判斷跳轉)
        is_homeroom = False
        if user["role"] in ("teacher", "director"):
            # 必須使用新的 cursor(非 dictionary=True) 才能在 fetchone() 取得 (1,)
            check_cursor = conn.cursor() 
            check_cursor.execute("""
                SELECT 1 FROM classes_teacher 
                WHERE teacher_id = %s AND role = '班導師'
            """, (user_id,))
            is_homeroom = bool(check_cursor.fetchone())
            check_cursor.close()
            
        hashed_pw = generate_password_hash(new_password)
        cursor.execute("UPDATE users SET password = %s WHERE id = %s", (hashed_pw, user_id))
        conn.commit()

        # 回傳 role 和 is_homeroom
        return jsonify({
            "success": True, 
            "message": "密碼已更新",
            "role": user["role"], # 傳遞英文 role 碼，供前端跳轉判斷
            "is_homeroom": is_homeroom # 傳遞班導師狀態
        })
    except Exception as e:
        print("❌ 密碼變更錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 訪客 - 查詢實習廠商
# -------------------------
@users_bp.route('/vendor_visitor')
def vendor_visitor():
    # 設 session 為 guest
    session["role"] = "guest"
    session["username"] = "guest"

    # 取得所有已核准的公司（或依需求調整）
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, company_name, location, industry FROM internship_companies WHERE status = 'approved'")
        companies = cursor.fetchall()
    except Exception as e:
        print("❌ 取得公司資料錯誤:", e)
        companies = []
    finally:
        cursor.close()
        conn.close()

    return render_template("user_shared/vendor_visitor.html", companies=companies)


# -------------------------
# 訪客 - 查詢學生資訊 / 志願序
# -------------------------
@users_bp.route('/student_visitor')
def student_visitor():
    # 設 session 為 guest
    session["role"] = "guest"
    session["username"] = "guest"

    # 取得公開的學生志願序或基本資訊（依需求調整）
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT s.id, s.username, s.name, c.name AS class_name
            FROM users s
            LEFT JOIN classes c ON s.class_id = c.id
            WHERE s.role='student'
        """)
        students = cursor.fetchall()
    except Exception as e:
        print("❌ 取得學生資料錯誤:", e)
        students = []
    finally:
        cursor.close()
        conn.close()

    return render_template("user_shared/student_visitor.html", students=students)


# -------------------------
# # 頁面路由
# -------------------------

# 使用者首頁（學生前台）
@users_bp.route('/student_home')
def student_home():
    return render_template('user_shared/student_home.html')

# 使用者首頁 (主任前台)
@users_bp.route("/director_home")
def director_home():
    if "username" not in session or session.get("role") != "director":
        return redirect(url_for("auth_bp.login_page"))

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, company_name FROM internship_companies WHERE status='pending'")
    companies = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("user_shared/director_home.html", companies=companies)

# 科助
@users_bp.route('/ta_home')
def ta_home():
    return render_template('user_shared/ta_home.html')
    
# 實習廠商管理
@users_bp.route('/manage_companies')
def manage_companies():
    return render_template('user_shared/manage_companies.html')

# 志願序最終結果
@users_bp.route('/final_results')
def final_results():
    return render_template('user_shared/final_results.html')

# 管理員首頁（後台）
@users_bp.route('/admin_home')
def admin_home():
    return render_template('admin/admin_home.html')

# 個人頁面
@users_bp.route('/profile')
def profile():
    return render_template('user_shared/profile.html')

# 取得 session 資訊
@users_bp.route('/api/get-session')
def get_session():
    if "username" in session and "role" in session:
        return jsonify({
            "success": True,
            "username": session["username"],
            "role": session["role"]
        })
    return jsonify({"success": False}), 401