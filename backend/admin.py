from flask import Blueprint, request, send_file, session,jsonify, render_template
from werkzeug.security import generate_password_hash
from config import get_db
from datetime import datetime
import re
import traceback

admin_bp = Blueprint("admin_bp", __name__, url_prefix='/admin')

# --------------------------------
# 用戶管理
# --------------------------------
def _get_active_semester_year(cursor):
    """取得當前啟用學期學年：優先 is_active=1 的 code 前三碼（如 1132->113）；若無則用 code 最大的一筆；再無則用 id 最大的一筆。"""
    cursor.execute("SELECT code FROM semesters WHERE is_active = 1 LIMIT 1")
    row = cursor.fetchone()
    if not row or row.get('code') is None:
        cursor.execute("SELECT code FROM semesters WHERE code IS NOT NULL AND code != '' ORDER BY code DESC LIMIT 1")
        row = cursor.fetchone()
    if not row or row.get('code') is None:
        cursor.execute("SELECT code FROM semesters ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
    raw = row.get('code') if row else None
    if raw is None:
        return None
    # 支援 code 為 int（如 1132）或 str（如 '1132'）或 bytes
    if isinstance(raw, int):
        return raw // 10 if raw >= 100 else None  # 1132 -> 113
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='ignore')
    code = str(raw).strip()
    if len(code) >= 3:
        try:
            return int(code[:3])
        except (TypeError, ValueError):
            pass
    return None


def _post_process_users(users, active_semester_year=None):
    """共用：補齊 role_display（指導老師/班導）、admission_year、created_at、動態 grade_display。"""
    role_map = {'ta': '科助', 'teacher': '指導老師', 'student': '學生', 'director': '主任', 'admin': '管理員', 'vendor': '廠商'}
    grade_labels = {1: '一年級', 2: '二年級', 3: '三年級', 4: '四年級', 5: '五年級', 6: '六年級'}
    for user in users:
        if user.get('created_at'):
            user['created_at'] = user['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        is_homeroom = (user.get('is_homeroom_count') or 0) > 0
        user['is_homeroom'] = is_homeroom
        if user['role'] == 'teacher':
            user['role_display'] = '指導老師' + ('、班導' if is_homeroom else '')
        elif user['role'] == 'director':
            user['role_display'] = '主任、指導老師' + ('、班導' if is_homeroom else '')
        else:
            user['role_display'] = role_map.get(user['role'], user['role'])
        # 入學年度：優先使用資料庫 u.admission_year，其次學生 username 前三碼
        if user['role'] == 'student':
            db_ay = user.get('admission_year')  # 已從 SELECT 取得 u.admission_year
            if db_ay is not None and str(db_ay).strip() != '':
                try:
                    user['admission_year'] = int(db_ay)
                except (TypeError, ValueError):
                    user['admission_year'] = user['username'][:3] if user.get('username') and len(user.get('username', '')) >= 3 else ''
            elif user.get('username') and len(user['username']) >= 3:
                try:
                    user['admission_year'] = int(user['username'][:3])
                except (TypeError, ValueError):
                    user['admission_year'] = ''
            else:
                user['admission_year'] = ''
            # 年級動態計算：當前啟用學期學年(code 前三碼) - admission_year + 1
            user['grade_display'] = '-'
            ay_for_grade = user.get('admission_year')
            if ay_for_grade == '' or ay_for_grade is None:
                try:
                    if user.get('username') and len(str(user['username'])) >= 3:
                        ay_for_grade = int(str(user['username'])[:3])
                except (TypeError, ValueError):
                    pass
            if active_semester_year is not None and ay_for_grade != '' and ay_for_grade is not None:
                try:
                    ay = int(ay_for_grade)
                    grade_num = active_semester_year - ay + 1
                    if 1 <= grade_num <= 6:
                        user['grade_display'] = grade_labels.get(grade_num, f'{grade_num}年級')
                    elif grade_num > 0:
                        user['grade_display'] = f'{grade_num}年級'
                except (TypeError, ValueError):
                    pass
        else:
            user['admission_year'] = ''
            user['grade_display'] = '-'
        # 班級動態顯示：僅科系 + 班名（例：資管科 忠），不顯示屆
        if user['role'] == 'student':
            dept = (user.get('department') or '').strip()
            cname = (user.get('class_name') or '').strip()
            if dept or cname:
                user['class_display'] = ' '.join(p for p in [dept, cname] if p)
            else:
                user['class_display'] = '-'
        else:
            user['class_display'] = '-'
        # 帶班列表、指導學生所屬班級：由「110屆資管科忠」改為「資管科四忠」（年級數字）
        grade_chars = ('一', '二', '三', '四', '五', '六')
        def _format_class_with_grade(s):
            if not s or not isinstance(s, str):
                return s
            out = []
            for part in [p.strip() for p in s.split(',') if p.strip()]:
                m = re.match(r'^(\d+)屆(.+)$', part)
                if m and active_semester_year is not None:
                    try:
                        ay = int(m.group(1))
                        suffix = (m.group(2) or '').strip()
                        grade_num = active_semester_year - ay + 1
                        if 1 <= grade_num <= 6 and suffix:
                            gc = grade_chars[grade_num - 1]
                            if len(suffix) >= 1 and suffix[-1] in '忠孝仁愛義禮':
                                out.append(suffix[:-1] + gc + suffix[-1])
                            else:
                                out.append(suffix + gc)
                        else:
                            out.append(part)
                    except (ValueError, TypeError):
                        out.append(part)
                else:
                    out.append(part)
            return ', '.join(out) if out else s
        if user.get('teaching_classes'):
            user['teaching_classes'] = _format_class_with_grade(user['teaching_classes'])
        if user.get('guided_classes'):
            user['guided_classes'] = _format_class_with_grade(user['guided_classes'])
    return users

@admin_bp.route('/api/current_semester_year', methods=['GET'])
def get_current_semester_year():
    """取得目前系統學年（semesters 表 is_active=1 的 code 前三碼），供詳情動態年級計算。"""
    if 'user_id' not in session or session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        year = _get_active_semester_year(cursor)
        return jsonify({"success": True, "active_semester_year": year})
    finally:
        cursor.close()
        conn.close()


@admin_bp.route('/api/get_all_users', methods=['GET'])
def get_all_users():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = max(1, min(100, request.args.get('per_page', 20, type=int)))
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT COUNT(*) AS total FROM users u
        """)
        total = cursor.fetchone()['total']

        cursor.execute("""
            SELECT 
                u.id, u.username, u.name, u.email, u.role, u.class_id, u.status,
                u.admission_year,
                c.name AS class_name,
                c.department,
                c.admission_year AS class_admission_year,
                (
                    SELECT COUNT(*) FROM classes_teacher ct_h
                    WHERE ct_h.teacher_id = u.id AND ct_h.role = 'classteacher'
                ) AS is_homeroom_count,
                (
                    SELECT GROUP_CONCAT(CONCAT(c2.admission_year, '屆', c2.department, c2.name) SEPARATOR ', ')
                    FROM classes_teacher ct2
                    JOIN classes c2 ON ct2.class_id = c2.id
                    WHERE ct2.teacher_id = u.id
                ) AS teaching_classes,
                (
                    SELECT GROUP_CONCAT(DISTINCT CONCAT(c3.admission_year, '屆', c3.department, c3.name) ORDER BY c3.admission_year, c3.department, c3.name SEPARATOR ', ')
                    FROM teacher_student_relations tsr
                    JOIN users u2 ON u2.id = tsr.student_id AND u2.role = 'student'
                    JOIN classes c3 ON c3.id = u2.class_id
                    WHERE tsr.teacher_id = u.id
                ) AS guided_classes,
                u.created_at
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            ORDER BY u.created_at DESC
            LIMIT %s OFFSET %s
        """, (per_page, (page - 1) * per_page))
        users = cursor.fetchall()
        active_semester_year = _get_active_semester_year(cursor)
        _post_process_users(users, active_semester_year)

        return jsonify({
            "success": True,
            "users": users,
            "total": total,
            "page": page,
            "per_page": per_page,
            "active_semester_year": active_semester_year,
        })
    except Exception as e:
        print(f"取得所有用戶錯誤: {e}")
        return jsonify({"success": False, "message": "取得失敗"}), 500
    finally:
        cursor.close()
        conn.close()

@admin_bp.route('/api/search_users', methods=['GET'])
def search_users():
    username_or_name_or_email = (request.args.get('username') or '').strip()
    role = (request.args.get('role') or '').strip()
    filename = (request.args.get('filename') or '').strip()
    page = max(1, request.args.get('page', 1, type=int))
    per_page = max(1, min(100, request.args.get('per_page', 20, type=int)))
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        conditions = []
        params = []

        if username_or_name_or_email:
            conditions.append("(u.username LIKE %s OR u.name LIKE %s OR u.email LIKE %s)")
            search_term = f"%{username_or_name_or_email}%"
            params.extend([search_term, search_term, search_term])
        if role:
            if role == 'homeroom':
                conditions.append("u.role IN ('teacher', 'director')")
                conditions.append("EXISTS (SELECT 1 FROM classes_teacher ct WHERE ct.teacher_id = u.id AND ct.role = 'classteacher')")
            else:
                conditions.append("u.role = %s")
                params.append(role)
        if filename:
            conditions.append("EXISTS (SELECT 1 FROM resumes r WHERE r.user_id = u.id AND r.original_filename LIKE %s)")
            params.append(f"%{filename}%")

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        cursor.execute(f"SELECT COUNT(*) AS total FROM users u {where_clause}", params)
        total = cursor.fetchone()['total']

        cursor.execute(f"""
            SELECT 
                u.id, u.username, u.name, u.email, u.role, u.class_id, u.status,
                u.admission_year,
                c.name AS class_name,
                c.department,
                c.admission_year AS class_admission_year,
                (
                    SELECT COUNT(*) FROM classes_teacher ct_h
                    WHERE ct_h.teacher_id = u.id AND ct_h.role = 'classteacher'
                ) AS is_homeroom_count,
                (
                    SELECT GROUP_CONCAT(CONCAT(c2.admission_year, '屆', c2.department, c2.name) SEPARATOR ', ')
                    FROM classes_teacher ct2
                    JOIN classes c2 ON ct2.class_id = c2.id
                    WHERE ct2.teacher_id = u.id
                ) AS teaching_classes,
                (
                    SELECT GROUP_CONCAT(DISTINCT CONCAT(c3.admission_year, '屆', c3.department, c3.name) ORDER BY c3.admission_year, c3.department, c3.name SEPARATOR ', ')
                    FROM teacher_student_relations tsr
                    JOIN users u2 ON u2.id = tsr.student_id AND u2.role = 'student'
                    JOIN classes c3 ON c3.id = u2.class_id
                    WHERE tsr.teacher_id = u.id
                ) AS guided_classes,
                u.created_at
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            {where_clause}
            ORDER BY u.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [per_page, (page - 1) * per_page])
        users = cursor.fetchall()
        active_semester_year = _get_active_semester_year(cursor)
        _post_process_users(users, active_semester_year)

        return jsonify({
            "success": True,
            "users": users,
            "total": total,
            "page": page,
            "per_page": per_page,
            "active_semester_year": active_semester_year,
        })
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

        # 如果是學生角色，將學號轉為純數字（移除所有非數字字符）
        # 如果沒有傳遞 role，從資料庫獲取當前用戶的角色
        if username:
            if not role:
                cursor.execute("SELECT role FROM users WHERE id = %s", (user_id,))
                current_user = cursor.fetchone()
                if current_user:
                    role = current_user.get('role')
            
            if role == 'student':
                username = ''.join(filter(str.isdigit, str(username)))

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

        # 後台註冊的用戶，狀態設為 'approved'（已啟用）；user_changed=0 強制首次登入後至個人資料修改帳密
        query = """
            INSERT INTO users (username, name, email, role, class_id, password, status, user_changed)
            VALUES (%s, %s, %s, %s, %s, %s, 'approved', 0)
        """
        cursor.execute(query, (username, name, email, role, class_id, hashed))
        conn.commit()

        # 建立帳號後自動發送 Email 通知給用戶（含初始密碼）
        if email and email.strip():
            try:
                from email_service import send_account_created_email
                role_display_map = {
                    "student": "學生", "teacher": "教師", "director": "主任",
                    "ta": "科助", "admin": "管理員", "vendor": "廠商"
                }
                send_account_created_email(
                    email.strip(), username, name,
                    role_display_map.get(role, role),
                    initial_password=password
                )
            except Exception as send_err:
                print(f"⚠️ 帳號建立成功，但發送通知信失敗: {send_err}")

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
        role = data.get("role", "classteacher")  # DB 使用 classteacher / subjectteacher
        if role == "班導師":
            role = "classteacher"
        elif role == "任課老師":
            role = "subjectteacher"

        if not class_ids:
            return jsonify({"success": False, "message": "未提供班級資料"}), 400

        # 驗證最多只能帶2個班級
        if len(class_ids) > 2:
            return jsonify({"success": False, "message": "一位老師最多只能帶2個班級"}), 400

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
# 缺勤預設學期範圍設定 API
# --------------------------------
@admin_bp.route('/api/absence_default_range/list', methods=['GET'])
def get_absence_default_range_list():
    if 'user_id' not in session or session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id, admission_year, start_semester_code, end_semester_code, updated_at
            FROM absence_default_semester_range
            ORDER BY admission_year ASC
        """)
        data = cursor.fetchall()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        print("取得預設學期範圍錯誤:", e)
        return jsonify({"success": False}), 500
    finally:
        cursor.close()
        conn.close()

# 刪除（可選）
@admin_bp.route('/api/absence_default_range/save', methods=['POST'])
def save_absence_default_range():
    if 'user_id' not in session or session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json()
    admission_year = data.get("admission_year")
    start_code = data.get("start_semester_code")
    end_code = data.get("end_semester_code")

    if not all([admission_year, start_code, end_code]):
        return jsonify({"success": False, "message": "資料不完整"}), 400

    # 確保 admission_year 是整數
    try:
        admission_year = int(admission_year)
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "入學年度必須是數字"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 如果該屆已有設定 → 更新
        cursor.execute("""
            SELECT id FROM absence_default_semester_range 
            WHERE admission_year = %s
        """, (admission_year,))
        record = cursor.fetchone()

        if record:
            cursor.execute("""
                UPDATE absence_default_semester_range
                SET start_semester_code=%s, end_semester_code=%s
                WHERE admission_year=%s
            """, (start_code, end_code, admission_year))
        else:
            cursor.execute("""
                INSERT INTO absence_default_semester_range
                (admission_year, start_semester_code, end_semester_code)
                VALUES (%s, %s, %s)
            """, (admission_year, start_code, end_code))

        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        print("儲存預設範圍錯誤:", e)
        return jsonify({"success": False}), 500
    finally:
        cursor.close()
        conn.close()

# 刪除缺勤預設學期範圍
@admin_bp.route('/api/absence_default_range/delete/<int:range_id>', methods=['DELETE'])
def delete_absence_default_range(range_id):
    if 'user_id' not in session or session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            DELETE FROM absence_default_semester_range
            WHERE id = %s
        """, (range_id,))
        
        if cursor.rowcount == 0:
            return jsonify({"success": False, "message": "找不到該筆記錄"}), 404
        
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        print("刪除預設範圍錯誤:", e)
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# 取得學期清單（供下拉使用）
@admin_bp.route('/api/semesters/list', methods=['GET'])
def admin_get_semesters_list():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, code, start_date, end_date FROM semesters ORDER BY code ASC")
        semesters = cursor.fetchall() or []
        return jsonify({"success": True, "semesters": semesters})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
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
    
