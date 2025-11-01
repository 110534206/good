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
    # 確保只有老師或主任身份可以進入
    if 'username' not in session or session.get('role') not in ['teacher', 'director']:
        return redirect(url_for('auth_bp.login_page'))
        
    # 最終導向指導老師主頁，讓用戶自行透過前端下拉選單切換班導身分
    return render_template('user_shared/teacher_home.html')

# -------------------------
# 班導首頁
# -------------------------
@users_bp.route("/class_teacher_home")
def class_teacher_home():
    # 確保只有老師或主任身份可以進入
    if "username" not in session or session.get("role") not in ["teacher", "director"]:
        return redirect(url_for("auth_bp.login_page"))

    # 🎯 關鍵邏輯：若沒有班導師狀態，導回其當前 active role 的主頁
    if not session.get("is_homeroom"):
        current_role = session.get("role")
        if current_role == 'director':
            return redirect(url_for("users_bp.director_home")) # 主任導回主任主頁
        else:
            return redirect(url_for("users_bp.teacher_home")) # 老師導回指導老師主頁

    return render_template("user_shared/class_teacher_home.html",
                           username=session.get("username"),
                           original_role=session.get("original_role"))

# -------------------------
# API - 取得個人資料
# -------------------------
@users_bp.route("/api/profile", methods=["GET"])
def get_profile():
    # 🎯 修正 1: 使用 user_id 進行查詢，user_id 在登入時被設定且不會變
    user_id = session.get("user_id") 
    if not user_id:
        return jsonify({"success": False, "message": "尚未登入"}), 401

    active_role = session["role"] # 當前活躍的角色 (例如: 'teacher')

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 查詢用戶基本資料 - 只使用 ID 查詢，確保取得原始 DB 資料
        cursor.execute("""
            SELECT u.id, u.username, u.email, u.role AS original_role, u.name,
                   c.department, c.name AS class_name, u.class_id, u.avatar_url
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            WHERE u.id = %s -- 修正: 只使用 user_id 篩選
        """, (user_id,)) # 傳遞 user_id
        user = cursor.fetchone()

        if not user:
            return jsonify({"success": False, "message": "使用者不存在"}), 404
            
        # 將 DB 中的原始 role 賦值給一個新變數
        original_role_from_db = user.pop("original_role")
        
        # 🎯 修正 2: 確保傳遞給前端的 user["role"] 是當前活躍的角色
        user["role"] = active_role 
        user["original_role"] = original_role_from_db
        
        # ... (學生屆數邏輯 - 保持不變)
        if original_role_from_db == "student" and user.get("username") and len(user["username"]) >= 3:
            user["admission_year"] = user["username"][:3]
        else:
            user["admission_year"] = ""
        
        # 🎯 修正 3: 班導狀態直接從 Session 取得，避免重複查詢
        is_homeroom = session.get("is_homeroom", False)
        classes = []
        if original_role_from_db in ("teacher", "director"): # 使用原始角色判斷是否需要查詢管理的班級
            # 查詢所有管理的班級 (無論是不是班導師)
            cursor.execute("""
                SELECT c.id, c.name, c.department, ct.role
                FROM classes c
                JOIN classes_teacher ct ON c.id = ct.class_id
                WHERE ct.teacher_id = %s
            """, (user["id"],))
            classes = cursor.fetchall()
            user["classes"] = classes # 傳遞所有班級資料
            # **(原程式碼中重複查詢 is_homeroom 的邏輯已被 session.get("is_homeroom") 取代)**

        user["is_homeroom"] = is_homeroom # 傳遞班導師狀態
        user["email"] = user["email"] or ""

        # 如果是老師/主任，且是班導師，且有多班級，拼成一個字串顯示在「管理班級」
        if active_role in ("teacher", "director") and is_homeroom and classes:
            class_names = [f"{c['department'].replace('管科', '')}{c['name']}" for c in classes]
            user["class_display_name"] = "、".join(class_names)
        elif original_role_from_db == "student":
            # 學生班級顯示
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
    
    # 🎯 確認：此處直接渲染主任主頁，不執行任何班導師跳轉邏輯。
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