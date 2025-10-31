from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from config import get_db
import os
import re 

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
    # 訪客 (role='guest') 不應該有個人資料，但為了一致性，我們讓他們可以嘗試訪問
    if 'user_id' not in session and session.get('role') != 'guest':
        return jsonify({"success": False, "message": "未登入"}), 401
    
    # 如果是訪客，直接回傳基本資訊
    if session.get('role') == 'guest':
        return jsonify({"success": True, "user": {
            "id": None, 
            "username": "guest", 
            "name": "訪客", 
            "avatar_url": None, 
            "current_role": "guest",                      
            "original_role": "guest",            
            "is_homeroom": False    
        }})


    user_id = session.get('user_id')
    current_role = session.get('role')

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT role, avatar_url, name, username, email FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()

        if not user_data:
            return jsonify({"success": False, "message": "用戶不存在"}), 404
        
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
                (user_id)
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
# 學生訪客頁面
# -------------------------
@users_bp.route('/student_visitor')
def student_visitor_page():
    current_role = session.get('role')
    
    # 🌟 核心修正：明確檢查 current_role 是否在 ['student', 'visitor'] 列表中
    if current_role not in ['student', 'visitor']:
        # 如果不是學生也不是訪客，導向登入頁
        return redirect(url_for('auth_bp.login_page'))
    
    # 如果是 'student' 或 'visitor'，則渲染頁面
    return render_template('user_shared/student_visitor.html')

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