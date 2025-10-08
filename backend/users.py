from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from config import get_db
import os

users_bp = Blueprint("users_bp", __name__)

# -------------------------
# 老師首頁
# -------------------------
@users_bp.route('/teacher_home')
def teacher_home():
    if 'username' not in session or session.get('role') != 'teacher':
        return redirect(url_for('auth_bp.login_page'))
    return render_template('user_shared/teacher_home.html')

# -------------------------
# 老師首頁(班導)
# -------------------------
@users_bp.route('/class_teacher_home')
def class_teacher_home():
    if 'username' not in session or session.get('role') not in ['teacher', 'director']:
        return redirect(url_for('auth_bp.login_page'))

    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_bp.login_page'))

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT 1 FROM classes_teacher
            WHERE teacher_id = %s AND role = '班導師'
        """, (user_id,))
        is_homeroom = cursor.fetchone()

        if is_homeroom is None:
            # 沒有班導身分就不能看這頁
            return redirect(url_for('auth_bp.login_page'))
    finally:
        cursor.close()
        conn.close()

    return render_template('user_shared/class_teacher_home.html',
        username=session.get('username'),
        original_role=session.get('role')
    )

# -------------------------
# API - 取得個人資料
# -------------------------
@users_bp.route("/api/profile", methods=["GET"])
def get_profile():
    if "username" not in session or "role" not in session:
        return jsonify({"success": False, "message": "尚未登入"}), 401

    username = session["username"]
    role = session["role"]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT u.id, u.username, u.email, u.role, u.name,
                   c.department, c.name AS class_name, u.class_id, u.avatar_url
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            WHERE u.username = %s AND u.role = %s
        """, (username, role))
        user = cursor.fetchone()

        if not user:
            return jsonify({"success": False, "message": "使用者不存在"}), 404

        # 檢查是否為班導 / 主任
        is_homeroom = False
        classes = []
        if role in ("teacher", "director"):
            cursor.execute("""
                SELECT c.id, c.name, c.department
                FROM classes c
                JOIN classes_teacher ct ON c.id = ct.class_id
                WHERE ct.teacher_id = %s
            """, (user["id"],))
            classes = cursor.fetchall()
            user["classes"] = classes

            cursor.execute("""
                SELECT 1 FROM classes_teacher 
                WHERE teacher_id = %s AND role = '班導師'
            """, (user["id"],))
            is_homeroom = bool(cursor.fetchone())

        user["is_homeroom"] = is_homeroom
        user["email"] = user["email"] or ""

        # 如果有多班級，拼成一個字串顯示
        if classes:
            class_names = [f"{c['department'].replace('管科', '')}{c['name']}" for c in classes]
            user["class_display_name"] = "、".join(class_names)
        else:
            dep_short = user['department'].replace("管科", "") if user['department'] else ""
            user["class_display_name"] = f"{dep_short}{user['class_name'] or ''}"

        return jsonify({"success": True, "user": user})
    except Exception as e:
        print("❌ 取得個人資料錯誤:", e)
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
    try:
        cursor.execute("SELECT id FROM users WHERE username=%s AND role=%s", (username, role))
        if not cursor.fetchone():
            return jsonify({"success": False, "message": "找不到該使用者資料"}), 404

        cursor.execute("UPDATE users SET name=%s WHERE username=%s AND role=%s", (name, username, role))

        if role == "student":
            if not class_id:
                return jsonify({"success": False, "message": f"{role_display}需提供班級"}), 400
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

        conn.commit()
        return jsonify({"success": True, "message": "資料更新成功"})
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
        cursor.execute("SELECT password FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()

        if not user or not check_password_hash(user["password"], old_password):
            return jsonify({"success": False, "message": "舊密碼錯誤"}), 403

        hashed_pw = generate_password_hash(new_password)
        cursor.execute("UPDATE users SET password = %s WHERE id = %s", (hashed_pw, user_id))
        conn.commit()

        return jsonify({"success": True, "message": "密碼已更新"})
    except Exception as e:
        print("❌ 密碼變更錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# # 頁面路由
# -------------------------

# 使用者首頁（學生前台）
@users_bp.route('/student_home')
def student_home():
    return render_template('user_shared/student_home.html')

# 使用者首頁 (主任前台)
@users_bp.route('/director_home')
def director_home():
    # 檢查用戶是否已登入
    if 'username' not in session or 'user_id' not in session:
        return redirect(url_for("auth_bp.login_page"))
    
    user_id = session.get('user_id')
    
    # 檢查用戶是否具有主任權限
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM users WHERE id = %s AND role = 'director'", (user_id,))
        is_director = cursor.fetchone()
        
        if not is_director:
            return redirect(url_for("auth_bp.login_page"))
    finally:
        cursor.close()
        conn.close()

    # 取得待審核公司資料
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, company_name FROM internship_companies WHERE status = 'pending'")
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