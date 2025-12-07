from flask import Blueprint, request, send_file, session,jsonify, render_template
from werkzeug.security import generate_password_hash
from config import get_db
from datetime import datetime
import traceback

admin_bp = Blueprint("admin_bp", __name__, url_prefix='/admin')

# --------------------------------
# 用戶管理
# --------------------------------
@admin_bp.route('/api/get_all_users', methods=['GET'])
def get_all_users():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT 
                u.id, u.username, u.name, u.email, u.role, u.class_id, u.status,
                c.name AS class_name,
                c.department,
                (
                    SELECT GROUP_CONCAT(CONCAT(c2.admission_year, '屆', c2.department, c2.name) SEPARATOR ', ')
                    FROM classes_teacher ct2
                    JOIN classes c2 ON ct2.class_id = c2.id
                    WHERE ct2.teacher_id = u.id
                ) AS teaching_classes,
                u.created_at
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            ORDER BY u.created_at DESC
        """)
        users = cursor.fetchall()

        for user in users:
            if user.get('created_at'):
                user['created_at'] = user['created_at'].strftime("%Y-%m-%d %H:%M:%S")

            role_map = {'ta': '科助', 'teacher': '教師', 'student': '學生', 'director': '主任', 'admin': '管理員', 'vendor': '廠商'}
            # 使用 role_map 賦值給 role_display，這是前端需要顯示的中文名稱
            user['role_display'] = role_map.get(user['role'], user['role']) #

            # 【新增邏輯】提取學生的入學屆數
            if user['role'] == 'student' and user.get('username') and len(user['username']) >= 3:
                user['admission_year'] = user['username'][:3]
            else:
                user['admission_year'] = ''
            # 【新增邏輯結束】

        return jsonify({"success": True, "users": users})
    except Exception as e:
        print(f"取得所有用戶錯誤: {e}")
        return jsonify({"success": False, "message": "取得失敗"}), 500
    finally:
        cursor.close()
        conn.close()

@admin_bp.route('/api/search_users', methods=['GET'])
def search_users():
    # 修正：將前端傳送的參數名稱 'username' 變更為更具描述性的名稱
    username_or_name_or_email = (request.args.get('username') or '').strip()
    # 新增：取得角色篩選參數
    role = (request.args.get('role') or '').strip() 
    filename = (request.args.get('filename') or '').strip()
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        conditions = []
        params = []

        if username_or_name_or_email:
            # 修正：支援同時搜尋帳號、姓名或 Email (與前端提示一致)
            conditions.append("(u.username LIKE %s OR u.name LIKE %s OR u.email LIKE %s)")
            search_term = f"%{username_or_name_or_email}%"
            params.extend([search_term, search_term, search_term])
        
        # 修正：加入角色篩選條件
        if role:
            conditions.append("u.role = %s")
            params.append(role)

        if filename:
            conditions.append("EXISTS (SELECT 1 FROM resumes r WHERE r.user_id = u.id AND r.original_filename LIKE %s)")
            params.append(f"%{filename}%")

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        cursor.execute(f"""
            SELECT 
            u.id, u.username, u.name, u.email, u.role, u.class_id, u.status,
            c.name AS class_name,
            c.department,
            (
                SELECT GROUP_CONCAT(CONCAT(c2.admission_year, '屆', c2.department, c2.name) SEPARATOR ', ')
                FROM classes_teacher ct2
                JOIN classes c2 ON ct2.class_id = c2.id
                WHERE ct2.teacher_id = u.id
            ) AS teaching_classes,
            u.created_at
        FROM users u
        LEFT JOIN classes c ON u.class_id = c.id
        {where_clause}
        ORDER BY u.created_at DESC
    """, params)
        users = cursor.fetchall()
        
        # 補齊 post-processing 邏輯，確保前端能正確顯示角色名稱和學生屆數
        role_map = {'ta': '科助', 'teacher': '老師', 'student': '學生', 'director': '主任', 'admin': '管理員', 'vendor': '廠商'}
        
        for user in users:
            if user.get('created_at'):
                user['created_at'] = user['created_at'].strftime("%Y-%m-%d %H:%M:%S")

            user['role_display'] = role_map.get(user['role'], user['role'])

            # 提取學生的入學屆數
            if user['role'] == 'student' and user.get('username') and len(user['username']) >= 3:
                user['admission_year'] = user['username'][:3]
            else:
                user['admission_year'] = ''
            
        return jsonify({"success": True, "users": users})
    except Exception as e:
        print(f"搜尋用戶錯誤: {e}")
        return jsonify({"success": False, "message": "搜尋失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# --------------------------------
# 更新用戶資料
# --------------------------------
@admin_bp.route('/api/update_user/<int:user_id>', methods=['PUT'])
def update_user(user_id):
    # 權限檢查：允許 admin 和 ta 更新用戶
    if 'user_id' not in session or session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json()
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        username = data.get("username")
        name = data.get("name")
        email = data.get("email")
        role = data.get("role")
        class_id = data.get("class_id")
        password = data.get("password")
        status = data.get("status")  # 新增：支援更新廠商狀態

        update_fields = []
        params = []

        if username:
            update_fields.append("username=%s")
            params.append(username)
        if name:
            update_fields.append("name=%s")
            params.append(name)
        if email:
            update_fields.append("email=%s")
            params.append(email)
        if role:
            update_fields.append("role=%s")
            params.append(role)
        # 注意：class_id 可能為 None (例如：老師/主任)
        if class_id is not None:
            update_fields.append("class_id=%s")
            params.append(class_id)
        if password:
            hashed = generate_password_hash(password)
            update_fields.append("password=%s")
            params.append(hashed)
        if status:
            update_fields.append("status=%s")
            params.append(status)

        if not update_fields:
            return jsonify({"success": False, "message": "沒有提供要更新的欄位"}), 400

        params.append(user_id)
        query = f"UPDATE users SET {', '.join(update_fields)} WHERE id=%s"
        cursor.execute(query, params)
        conn.commit()
        return jsonify({"success": True, "message": "使用者更新成功"})
    except Exception as e:
        print(f"更新使用者錯誤: {e}")
        return jsonify({"success": False, "message": "更新失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# --------------------------------
# # 刪除用戶
# --------------------------------
@admin_bp.route('/api/update_user/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM users WHERE id=%s", (user_id,))
        conn.commit()
        return jsonify({"success": True, "message": "刪除成功"})
    except Exception as e:
        conn.rollback()
        print("刪除使用者錯誤：", e)
        return jsonify({"success": False, "message": "刪除失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# --------------------------------
# 單一班級統計 (新增部分)
# --------------------------------
@admin_bp.route('/api/get_class_stats/<int:class_id>', methods=['GET'])
def get_class_stats(class_id):
    """取得單一班級的實習進度統計資料"""
    # 這裡假設只有科助或管理員可以查看
    if 'user_id' not in session or session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 1. 查詢班級名稱
        cursor.execute("SELECT name FROM classes WHERE id = %s", (class_id,))
        class_info = cursor.fetchone()
        if not class_info:
            return jsonify({"success": False, "message": "找不到該班級資料"}), 404

        # 2. 查詢班級統計數據：
        # total_students: 總學生數 (users.role = 'student')
        # students_with_resume: 已上傳履歷人數 (與 resumes 表 LEFT JOIN)
        # students_with_preference: 已填寫志願人數 (與 student_preferences 表 LEFT JOIN)
        cursor.execute("""
            SELECT
                COUNT(u.id) AS total_students,
                SUM(CASE WHEN r.user_id IS NOT NULL THEN 1 ELSE 0 END) AS students_with_resume,
                SUM(CASE WHEN sp.student_id IS NOT NULL THEN 1 ELSE 0 END) AS students_with_preference
            FROM users u
            LEFT JOIN (SELECT DISTINCT user_id FROM resumes) r ON r.user_id = u.id
            LEFT JOIN (SELECT DISTINCT student_id FROM student_preferences) sp ON sp.student_id = u.id
            WHERE u.class_id = %s AND u.role = 'student'
        """, (class_id,))
        stats = cursor.fetchone()

        # 組合結果
        result = {
            "class_name": class_info['name'],
            "total_students": stats['total_students'] if stats else 0,
            "students_with_resume": stats['students_with_resume'] if stats else 0,
            "students_with_preference": stats['students_with_preference'] if stats else 0
        }
        
        return jsonify({"success": True, "stats": result})
            
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# --------------------------------
# 建立新用戶
# --------------------------------
@admin_bp.route('/api/create_user', methods=['POST'])
def create_user():
    data = request.get_json()
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        username = data.get("username")
        name = data.get("name") # 新增用戶時，姓名也是必填
        email = (data.get("email") or "").strip() 
        role = data.get("role")
        class_id = data.get("class_id")
        password = data.get("password")

        # 🧩 驗證必要欄位
        if not all([username, name, role, password]): # 確保姓名為必填
            return jsonify({"success": False, "message": "請填寫完整資料 (帳號、密碼、姓名、角色)"}), 400

        # 🧩 老師與主任可以不填 email，其他角色必須有
        if role not in ["teacher", "director","ta"] and not email:
            return jsonify({"success": False, "message": "學生需填寫 email"}), 400

        hashed = generate_password_hash(password)

        # 後台註冊的用戶，狀態設為 'approved'（已啟用）
        query = """
            INSERT INTO users (username, name, email, role, class_id, password, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'approved')
        """
        cursor.execute(query, (username, name, email, role, class_id, hashed))
        conn.commit()

        return jsonify({"success": True, "message": "使用者建立成功"})
    except Exception as e:
        print(f"建立使用者錯誤: {e}")
        return jsonify({"success": False, "message": "建立失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API - 分配導師班級（主任 / 老師 都能被指派）
# =========================================================
@admin_bp.route('/api/assign_teacher_class/<int:teacher_id>', methods=['POST'])
def assign_teacher_class(teacher_id):
    """管理員分配班導（可以是主任或老師）"""
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor()
    try:
        data = request.get_json()
        class_ids = data.get("class_ids", [])
        role = data.get("role", "班導師")  # 預設為班導師

        if not class_ids:
            return jsonify({"success": False, "message": "未提供班級資料"}), 400

        # 確認該老師存在，角色為 teacher 或 director
        cursor.execute("""
            SELECT id, role FROM users WHERE id = %s AND role IN ('teacher', 'director')
        """, (teacher_id,))
        user = cursor.fetchone()
        if not user:
            return jsonify({"success": False, "message": "找不到該老師或角色不符合"}), 404

        # 清除舊資料（避免重複）
        cursor.execute("DELETE FROM classes_teacher WHERE teacher_id = %s", (teacher_id,))

        # 新增指派
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for class_id in class_ids:
            cursor.execute("""
                INSERT INTO classes_teacher (teacher_id, class_id, role, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
            """, (teacher_id, class_id, role, now, now))

        conn.commit()
        return jsonify({"success": True, "message": "班級指派成功"})
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API - 查詢某位班導目前帶的班級
# =========================================================
@admin_bp.route('/api/get_teacher_classes/<int:teacher_id>', methods=['GET'])
def get_teacher_classes(teacher_id):
    """取得某位老師/主任目前所屬班級"""
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT 
                c.id AS class_id,
                c.name AS class_name,
                c.department,
                ct.role AS teacher_role,
                u.name AS teacher_name,
                u.role AS user_role
            FROM classes_teacher ct
            JOIN classes c ON ct.class_id = c.id
            JOIN users u ON ct.teacher_id = u.id
            WHERE ct.teacher_id = %s
        """, (teacher_id,))
        data = cursor.fetchall()

        return jsonify({"success": True, "data": data})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# --------------------------------
# API - 取得所有班級列表
# --------------------------------
@admin_bp.route('/api/get_classes', methods=['GET'])
def get_classes():
    """取得所有班級列表，用於用戶管理頁面"""
    if 'user_id' not in session or session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, name, department, admission_year FROM classes "
            "ORDER BY department ASC, admission_year DESC, name ASC"
        )
        classes = cursor.fetchall()
        return jsonify({"success": True, "classes": classes})
    except Exception as e:
        print(f"取得班級列表錯誤: {e}")
        return jsonify({"success": False, "message": "取得班級列表失敗"}), 500
    finally:
        cursor.close()
        conn.close()
    
# --------------------------------
# 用戶管理頁面
# --------------------------------
@admin_bp.route('/user_management')
def user_management():
    # 權限檢查：允許 admin 和 ta 訪問用戶管理頁面
    if 'user_id' not in session or session.get('role') not in ['admin', 'ta']:
        from flask import redirect, url_for
        return redirect(url_for('auth_bp.login_page'))
    try:
        return render_template('admin/user_management.html')
    except Exception as e:
        print(f"用戶管理頁面錯誤: {e}")
        return f"用戶管理頁面載入錯誤: {str(e)}", 500

@admin_bp.route('/absence_default_range')
def absence_default_range_page():
    """缺勤預設學期範圍設定頁面（科助/管理員）"""
    if 'user_id' not in session or session.get('role') not in ['admin', 'ta']:
        from flask import redirect, url_for
        return redirect(url_for('auth_bp.login_page'))
    try:
        return render_template('admin/absence_default_range.html')
    except Exception as e:
        print(f"缺勤預設學期範圍設定頁面錯誤: {e}")
        return f"頁面載入錯誤: {str(e)}", 500
    
