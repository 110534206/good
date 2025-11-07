from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from config import get_db
import os
import re 

users_bp = Blueprint("users_bp", __name__)

# -------------------------
# 指導老師首頁
# -------------------------
@users_bp.route('/teacher_home')
def teacher_home():
    # 允許 teacher、director、class_teacher 進入
    if 'username' not in session or session.get('role') not in ['teacher', 'director', 'class_teacher']:
        return redirect(url_for('auth_bp.login_page'))

    # 若目前是班導身分，切回指導老師身分
    if session.get('role') == 'class_teacher':
        # 不論原本是主任或老師，都暫時切回指導老師身份
        session['role'] = 'teacher'
        session['display_role'] = '指導老師'

    # 記得保留原始身份（供切回班導時使用）
    if 'original_role' not in session:
        # 若第一次登入，紀錄原始身份
        session['original_role'] = 'director' if session.get('role') == 'director' else 'teacher'

    return render_template('user_shared/teacher_home.html')


# -------------------------
# 班導首頁
# -------------------------
@users_bp.route("/class_teacher_home")
def class_teacher_home():
    # 確保只有老師或主任身份可以進入
    if "username" not in session or session.get("role") not in ["teacher", "director"]:
        return redirect(url_for("auth_bp.login_page"))

    # 若沒有班導師身分，導回原本主頁
    if not session.get("is_homeroom"):
        current_role = session.get("role")
        if current_role == 'director':
            return redirect(url_for("users_bp.director_home")) # 主任導回主任主頁
        else:
            return redirect(url_for("users_bp.teacher_home")) # 老師導回指導老師主頁

    # 新增：進入班導頁時，暫時設定為 "class_teacher"
    session["role"] = "class_teacher"

    return render_template("user_shared/class_teacher_home.html",
                           username=session.get("username"),
                           original_role=session.get("original_role"))

# -------------------------
# API - 取得個人資料
# -------------------------
@users_bp.route("/api/profile", methods=["GET"])
def get_profile():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "尚未登入"}), 401

    active_role = session.get("role", "")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT u.id, u.username, u.email, u.role AS original_role, u.name,
                   c.department, c.name AS class_name, u.class_id, u.avatar_url
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            WHERE u.id = %s
        """, (user_id,))
        user = cursor.fetchone()

        if not user:
            return jsonify({"success": False, "message": "使用者不存在"}), 404

       
        display_role = active_role
        if active_role == "class_teacher":
            display_role = "teacher"

        # 原始角色
        original_role_from_db = user.pop("original_role")

        # 🔹 確保回傳的 user["role"] 是當前活躍角色
        user["role"] = display_role
        # 🔹 額外提供前端顯示文字
        user["display_role"] = "班導師" if active_role == "class_teacher" else (
            "主任" if display_role == "director" else
            "指導老師" if display_role == "teacher" else
            "學生" if display_role == "student" else
            "科助" if display_role == "ta" else
            "管理員" if display_role == "admin" else
            "廠商" if display_role == "vendor" else display_role
        )

        if original_role_from_db == "student" and user.get("username") and len(user["username"]) >= 3:
            user["admission_year"] = user["username"][:3]
        else:
            user["admission_year"] = ""

        is_homeroom = session.get("is_homeroom", False)
        classes = []
        if original_role_from_db in ("teacher", "director"):
            cursor.execute("""
                SELECT c.id, c.name, c.department, ct.role
                FROM classes c
                JOIN classes_teacher ct ON c.id = ct.class_id
                WHERE ct.teacher_id = %s
            """, (user["id"],))
            classes = cursor.fetchall()
            user["classes"] = classes

        user["is_homeroom"] = is_homeroom
        user["email"] = user["email"] or ""

        if active_role in ("teacher", "director", "class_teacher") and is_homeroom and classes:
            class_names = [f"{c['department'].replace('管科', '')}{c['name']}" for c in classes]
            user["class_display_name"] = "、".join(class_names)
        elif original_role_from_db == "student":
            dep_short = user['department'].replace("管科", "") if user['department'] else ""
            user["class_display_name"] = f"{dep_short}{user['class_name'] or ''}"
        else:
            user["class_display_name"] = ""

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
    # 訪客禁止使用此功能
    if session.get('role') == 'guest':
        return jsonify({"success": False, "message": "訪客無權限操作此功能"}), 403

    data = request.get_json()
    username = data.get("username")
    role_display = data.get("role")
    name = data.get("name")
    class_id = data.get("class_id")

    if not username or not role_display or not name:
        return jsonify({"success": False, "message": "缺少必要欄位"}), 400

    role_map = {
        "學生": "student",
        "指導老師": "teacher",
        "主任": "director",
        "科助": "ta",
        "管理員": "admin",
        "廠商": "vendor"
    }
    role = role_map.get(role_display)
    if not role:
        return jsonify({"success": False, "message": "身分錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor()
    user_id = None
    try:
        user_id = session.get("user_id") # 使用 session 中的 user_id
        
        if not user_id:
             return jsonify({"success": False, "message": "請重新登入"}), 401

        cursor.execute("UPDATE users SET name=%s WHERE id=%s", (name, user_id))

        if role == "student":
            if not class_id:
                pass
            else:
                try:
                    class_id = int(class_id)
                except ValueError:
                    return jsonify({"success": False, "message": "班級格式錯誤"}), 400

                cursor.execute("SELECT id FROM classes WHERE id=%s", (class_id,))
                if not cursor.fetchone():
                    return jsonify({"success": False, "message": "班級不存在"}), 404

                cursor.execute("UPDATE users SET class_id=%s WHERE id=%s",
                            (class_id, user_id)
                )
        else:
            cursor.execute(
                "UPDATE users SET class_id=NULL WHERE id=%s",
                (user_id,)
            )

        is_homeroom = False
        if role in ("teacher", "director"):
            cursor.execute("""
                SELECT 1 FROM classes_teacher 
                WHERE teacher_id = %s AND role = '班導師'
            """, (user_id,))
            is_homeroom = bool(cursor.fetchone())

        conn.commit()
        
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
    if "user_id" not in session or session.get('role') == 'guest':
        return jsonify({"success": False, "message": "未授權或訪客無權限"}), 401

    if 'avatar' not in request.files:
        return jsonify({"success": False, "message": "沒有檔案"}), 400

    file = request.files['avatar']
    if file and allowed_file(file.filename):
        filename = secure_filename(f"{session['user_id']}.png")
        
        avatars_folder = os.path.join(current_app.static_folder, "avatars")
        os.makedirs(avatars_folder, exist_ok=True)
        
        filepath = os.path.join(avatars_folder, filename)
        file.save(filepath)

        avatar_url = url_for('static', filename=f"avatars/{filename}")
        
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
    if "user_id" not in session or session.get('role') == 'guest':
        return jsonify({"success": False, "message": "尚未登入或訪客無權限"}), 401

    data = request.get_json()
    old_password = data.get("old_password")
    new_password = data.get("new_password")

    if not old_password or not new_password:
        return jsonify({"success": False, "message": "請填寫所有欄位"}), 400

    user_id = session["user_id"]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT password, role FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()

        if not user or not check_password_hash(user["password"], old_password):
            return jsonify({"success": False, "message": "舊密碼錯誤"}), 403

        is_homeroom = False
        if user["role"] in ("teacher", "director"):
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

        return jsonify({
            "success": True, 
            "message": "密碼已更新",
            "role": user["role"], 
            "is_homeroom": is_homeroom 
        })
    except Exception as e:
        print("❌ 密碼變更錯誤:", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 訪客主頁
# -------------------------
@users_bp.route('/visitor_page')
def visitor_page():
    # 確保用戶是以訪客身份進入
    if session.get('role') != 'visitor' and session.get('is_visitor') != True:
         # 如果用戶不是訪客身份，導回登入頁面
         return redirect(url_for('auth_bp.login_page'))
         
    return render_template('user_shared/visitor.html')

# -------------------------
# 廠商首頁
# ------------------------
@users_bp.route('/vendor_home')
def vendor_home():
    """
    實習廠商登入後進入的主頁。
    """
    # 權限檢查：必須是已登入的用戶，且角色為 'vendor'
    if 'username' not in session or session.get('role') != 'vendor':
        return redirect(url_for('auth_bp.login_page'))
    return render_template('user_shared/vendor_home.html') 

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
    
    # 🎯 確認：此處直接渲染主任主頁，不執行任何班導師跳轉邏輯。
    return render_template("user_shared/director_home.html", companies=companies)

# 科助
@users_bp.route('/ta_home')
def ta_home():
    return render_template('user_shared/ta_home.html')
    

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