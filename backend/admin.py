from flask import Blueprint, request, send_file, jsonify, render_template
from werkzeug.security import generate_password_hash
from config import get_db
import pandas as pd
import io

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

            role_map = {'ta': '科助', 'teacher': '老師', 'student': '學生', 'director': '主任', 'admin': '管理員'}
            user['role_display'] = role_map.get(user.get('role'), user.get('role'))

        return jsonify({"success": True, "users": users})
    except Exception as e:
        print(f"獲取用戶列表錯誤: {e}")
        return jsonify({"success": False, "message": "獲取用戶列表失敗"}), 500
    finally:
        cursor.close()
        conn.close()


@admin_bp.route('/api/search_users', methods=['GET'])
def search_users():
    username = (request.args.get('username') or '').strip()
    filename = (request.args.get('filename') or '').strip()
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        conditions = []
        params = []

        if username:
            conditions.append("u.username LIKE %s")
            params.append(f"%{username}%")
        if filename:
            conditions.append("EXISTS (SELECT 1 FROM resumes r WHERE r.user_id = u.id AND r.original_filename LIKE %s)")
            params.append(f"%{filename}%")

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        cursor.execute(f"""
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
            {where_clause}
            ORDER BY u.created_at DESC
        """, params)
        users = cursor.fetchall()
        for user in users:
            if user.get('created_at'):
                user['created_at'] = user['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "users": users})
    except Exception as e:
        print(f"搜尋用戶錯誤: {e}")
        return jsonify({"success": False, "message": "搜尋失敗"}), 500
    finally:
        cursor.close()
        conn.close()


# --------------------------------
# 學生班級分配
# --------------------------------
@admin_bp.route('/api/get_students_by_class', methods=['GET'])
def get_students_by_class():
    class_id = request.args.get('class_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        query = "SELECT u.id, u.username, u.name, u.email, " \
                "(SELECT COUNT(*) FROM resumes r WHERE r.user_id = u.id) AS resume_count, " \
                "(SELECT COUNT(*) FROM student_preferences sp WHERE sp.student_id = u.id) AS preference_count " \
                "FROM users u WHERE u.role='student' "
        params = []
        if class_id and class_id != "all":
            query += "AND u.class_id=%s "
            params.append(class_id)
        query += "ORDER BY u.username"
        cursor.execute(query, params)
        students = cursor.fetchall()
        return jsonify({"success": True, "students": students})
    except Exception as e:
        print("取得學生資料錯誤:", e)
        return jsonify({"success": False, "message": "取得學生資料失敗"}), 500
    finally:
        cursor.close()
        conn.close()


# --------------------------------
# 班級列表
# --------------------------------
@admin_bp.route("/api/get_classes")
def get_classes():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, name, department FROM classes ORDER BY department, name")
        classes = cursor.fetchall()
        return jsonify({"success": True, "classes": classes})
    except Exception as e:
        print(f"取得班級列表錯誤: {e}")
        return jsonify({"success": False, "message": "取得班級列表失敗"}), 500
    finally:
        cursor.close()
        conn.close()


# --------------------------------
# 公司統計
# --------------------------------
@admin_bp.route("/api/manage_companies_stats")
def manage_companies_stats():
    class_id = request.args.get("class_id")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        student_filter = ""
        params = []
        if class_id and class_id != "all":
            student_filter = "WHERE u.class_id=%s"
            params.append(class_id)

        # 各公司被選志願次數
        cursor.execute(f"""
            SELECT c.company_name, COUNT(sp.id) AS preference_count
            FROM internship_companies c
            LEFT JOIN student_preferences sp ON c.id=sp.company_id
            LEFT JOIN users u ON sp.student_id=u.id
            {student_filter}
            GROUP BY c.id
            ORDER BY preference_count DESC
            LIMIT 5
        """, params)
        top_companies = cursor.fetchall()

        # 履歷繳交率
        cursor.execute(f"SELECT COUNT(*) AS total FROM users u {student_filter}", params)
        total_students = cursor.fetchone()["total"]
        cursor.execute(f"""
            SELECT COUNT(DISTINCT r.user_id) AS uploaded
            FROM resumes r
            JOIN users u ON r.user_id = u.id
            {student_filter}
        """, params)
        uploaded = cursor.fetchone()["uploaded"]
        resume_stats = {"uploaded": uploaded, "not_uploaded": total_students - uploaded}

        # 志願序填寫率
        cursor.execute(f"""
            SELECT COUNT(DISTINCT sp.student_id) AS filled
            FROM student_preferences sp
            JOIN users u ON sp.student_id=u.id
            {student_filter}
        """, params)
        filled = cursor.fetchone()["filled"]
        preference_stats = {"filled": filled, "not_filled": total_students - filled}

        return jsonify({
            "success": True,
            "top_companies": top_companies,
            "resume_stats": resume_stats,
            "preference_stats": preference_stats
        })
    except Exception as e:
        print("❌ manage_companies_stats error:", e)
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# --------------------------------
# 匯出 Excel
# --------------------------------
@admin_bp.route("/api/export_companies_stats")
def export_companies_stats():
    class_id = request.args.get("class_id")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        student_filter = ""
        params = []
        if class_id and class_id != "all":
            student_filter = "WHERE u.class_id=%s"
            params.append(class_id)

        cursor.execute(f"""
            SELECT c.company_name, COUNT(sp.id) AS preference_count
            FROM internship_companies c
            LEFT JOIN student_preferences sp ON c.id=sp.company_id
            LEFT JOIN users u ON sp.student_id=u.id
            {student_filter}
            GROUP BY c.id
            ORDER BY preference_count DESC
        """, params)
        data = cursor.fetchall()

        df = pd.DataFrame(data)
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        return send_file(output,
                         download_name="companies_stats.xlsx",
                         as_attachment=True,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    finally:
        cursor.close()
        conn.close()


# --------------------------------
# 用戶管理頁面
# --------------------------------
@admin_bp.route('/user_management')
def user_management():
    try:
        return render_template('admin/user_management.html')
    except Exception as e:
        print(f"用戶管理頁面錯誤: {e}")
        return f"用戶管理頁面載入錯誤: {str(e)}", 500
