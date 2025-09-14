from flask import Blueprint, request, jsonify, render_template
from werkzeug.security import generate_password_hash
from config import get_db

admin_bp = Blueprint("admin_bp", __name__, url_prefix="/admin")


@admin_bp.route('/api/get_all_users', methods=['GET'])
def get_all_users():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT 
                u.id, u.username, u.name, u.email, u.role, u.class_id,
                c.name AS class_name,
                c.department,
                (
                    SELECT GROUP_CONCAT(c2.name SEPARATOR ', ')
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

        return jsonify({"success": True, "users": users})
    except Exception as e:
        print(f"獲取用戶列表錯誤: {e}")
        return jsonify({"success": False, "message": "獲取用戶列表失敗"}), 500
    finally:
        cursor.close()
        conn.close()

@admin_bp.route('/api/assign_student_class', methods=['POST'])
def assign_student_class():
    data = request.get_json()
    user_id = data.get('user_id')
    class_id = data.get('class_id')

    if not user_id or not class_id:
        return jsonify({"success": False, "message": "缺少必要參數"}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT role FROM users WHERE id=%s", (user_id,))
        user = cursor.fetchone()
        if not user or user[0] != 'student':
            return jsonify({"success": False, "message": "該用戶不是學生"}), 400

        cursor.execute("SELECT id FROM classes WHERE id=%s", (class_id,))
        if not cursor.fetchone():
            return jsonify({"success": False, "message": "班級不存在"}), 404

        cursor.execute("UPDATE users SET class_id=%s WHERE id=%s", (class_id, user_id))
        conn.commit()
        return jsonify({"success": True, "message": "學生班級設定成功"})
    except Exception as e:
        print(f"設定學生班級錯誤: {e}")
        return jsonify({"success": False, "message": "設定失敗"}), 500
    finally:
        cursor.close()
        conn.close()

@admin_bp.route('/api/assign_class_teacher', methods=['POST'])
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
        cursor.execute("SELECT role FROM users WHERE id=%s", (teacher_id,))
        user = cursor.fetchone()
        if not user or user[0] not in ('teacher', 'director'):
            return jsonify({"success": False, "message": "該用戶不是教師或主任"}), 400

        cursor.execute("SELECT id FROM classes WHERE id=%s", (class_id,))
        if not cursor.fetchone():
            return jsonify({"success": False, "message": "班級不存在"}), 404

        cursor.execute("""
            SELECT id FROM classes_teacher 
            WHERE class_id=%s AND teacher_id=%s AND role=%s
        """, (class_id, teacher_id, role))
        if cursor.fetchone():
            return jsonify({"success": False, "message": f"該班級已有此教師擔任 {role}"}), 409

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

@admin_bp.route('/api/create_user', methods=['POST'])
def admin_create_user():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    role = data.get('role')
    name = data.get('name', '')
    email = data.get('email', '')
    class_id = data.get('class_id')

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
            cursor.execute("""
                INSERT INTO users (username, password, role, name, email, class_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (username, hashed_password, role, name, email, class_id))
        else:
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

@admin_bp.route('/api/update_user/<int:user_id>', methods=['PUT'])
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

@admin_bp.route('/api/delete_user/<int:user_id>', methods=['DELETE'])
def admin_delete_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, role FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            return jsonify({"success": False, "message": "用戶不存在"}), 404

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

@admin_bp.route('/api/teacher/classes/<int:user_id>', methods=['GET'])
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

@admin_bp.route('/api/get_all_classes', methods=['GET'])
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

  # 用戶管理頁面
@admin_bp.route('/user_management')
def user_management():
    try:
        return render_template('admin/user_management.html')
    except Exception as e:
        print(f"用戶管理頁面錯誤: {e}")
        return f"用戶管理頁面載入錯誤: {str(e)}", 500      
