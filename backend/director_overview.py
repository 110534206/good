from flask import Blueprint, request, jsonify, session
from config import get_db
from datetime import datetime
import traceback

director_overview_bp = Blueprint("director_overview_bp", __name__, url_prefix="/director")

# =========================================================
# Helper: 取得主任所屬科系
# =========================================================
def get_director_department(cursor, user_id):
    """取得主任所屬 department（透過 classes_teacher -> classes.department）"""
    cursor.execute("""
        SELECT DISTINCT c.department
        FROM classes c
        JOIN classes_teacher ct ON ct.class_id = c.id
        WHERE ct.teacher_id = %s
        LIMIT 1
    """, (user_id,))
    r = cursor.fetchone()
    return r['department'] if r and r.get('department') else None

# =========================================================
# API: 主任查看全系所有班級的履歷進度
# =========================================================
@director_overview_bp.route("/api/get_all_classes_resumes", methods=["GET"])
def get_all_classes_resumes():
    """主任查看全系所有班級的履歷進度"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    user_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取主任所屬科系
        department = get_director_department(cursor, user_id)
        if not department:
            return jsonify({"success": False, "message": "無法取得主任所屬科系"}), 403
        
        # 查詢所有班級的履歷統計
        cursor.execute("""
            SELECT 
                c.id AS class_id,
                c.name AS class_name,
                c.department,
                COUNT(DISTINCT u.id) AS total_students,
                COUNT(DISTINCT r.id) AS total_resumes,
                COUNT(DISTINCT CASE WHEN r.status = 'approved' THEN r.id END) AS approved_resumes,
                COUNT(DISTINCT CASE WHEN r.status = 'rejected' THEN r.id END) AS rejected_resumes,
                COUNT(DISTINCT CASE WHEN r.status = 'uploaded' THEN r.id END) AS pending_resumes,
                COUNT(DISTINCT r.user_id) AS students_with_resume,
                ROUND(COUNT(DISTINCT r.user_id) * 100.0 / NULLIF(COUNT(DISTINCT u.id), 0), 2) AS resume_completion_rate
            FROM classes c
            LEFT JOIN users u ON u.class_id = c.id AND u.role = 'student'
            LEFT JOIN resumes r ON r.user_id = u.id
            WHERE c.department = %s
            GROUP BY c.id, c.name, c.department
            ORDER BY c.name
        """, (department,))
        
        classes_stats = cursor.fetchall()
        
        # 格式化完成率（如果是 None，設為 0）
        for stat in classes_stats:
            if stat['resume_completion_rate'] is None:
                stat['resume_completion_rate'] = 0.0
        
        return jsonify({
            "success": True,
            "department": department,
            "classes": classes_stats
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 主任查看全系所有班級的志願序進度
# =========================================================
@director_overview_bp.route("/api/get_all_classes_preferences", methods=["GET"])
def get_all_classes_preferences():
    """主任查看全系所有班級的志願序進度"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    user_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取主任所屬科系
        department = get_director_department(cursor, user_id)
        if not department:
            return jsonify({"success": False, "message": "無法取得主任所屬科系"}), 403
        
        # 查詢所有班級的志願序統計
        cursor.execute("""
            SELECT 
                c.id AS class_id,
                c.name AS class_name,
                c.department,
                COUNT(DISTINCT u.id) AS total_students,
                COUNT(DISTINCT sp.id) AS total_preferences,
                COUNT(DISTINCT sp.student_id) AS students_with_preferences,
                COUNT(DISTINCT CASE WHEN sp.status = 'approved' THEN sp.student_id END) AS students_approved,
                COUNT(DISTINCT CASE WHEN sp.status = 'rejected' THEN sp.student_id END) AS students_rejected,
                COUNT(DISTINCT CASE WHEN sp.status = 'pending' THEN sp.student_id END) AS students_pending,
                ROUND(COUNT(DISTINCT sp.student_id) * 100.0 / NULLIF(COUNT(DISTINCT u.id), 0), 2) AS preference_completion_rate
            FROM classes c
            LEFT JOIN users u ON u.class_id = c.id AND u.role = 'student'
            LEFT JOIN student_preferences sp ON sp.student_id = u.id
            WHERE c.department = %s
            GROUP BY c.id, c.name, c.department
            ORDER BY c.name
        """, (department,))
        
        classes_stats = cursor.fetchall()
        
        # 格式化完成率（如果是 None，設為 0）
        for stat in classes_stats:
            if stat['preference_completion_rate'] is None:
                stat['preference_completion_rate'] = 0.0
        
        return jsonify({
            "success": True,
            "department": department,
            "classes": classes_stats
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 主任查看全系總覽（履歷 + 志願序）
# =========================================================
@director_overview_bp.route("/api/get_department_overview", methods=["GET"])
def get_department_overview():
    """主任查看全系總覽（履歷 + 志願序）"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    user_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取主任所屬科系
        department = get_director_department(cursor, user_id)
        if not department:
            return jsonify({"success": False, "message": "無法取得主任所屬科系"}), 403
        
        # 查詢所有班級的履歷和志願序統計
        cursor.execute("""
            SELECT 
                c.id AS class_id,
                c.name AS class_name,
                c.department,
                -- 學生統計
                COUNT(DISTINCT u.id) AS total_students,
                -- 履歷統計
                COUNT(DISTINCT r.user_id) AS students_with_resume,
                COUNT(DISTINCT CASE WHEN r.status = 'approved' THEN r.user_id END) AS students_resume_approved,
                COUNT(DISTINCT CASE WHEN r.status = 'rejected' THEN r.user_id END) AS students_resume_rejected,
                COUNT(DISTINCT CASE WHEN r.status = 'uploaded' THEN r.user_id END) AS students_resume_pending,
                -- 志願序統計
                COUNT(DISTINCT sp.student_id) AS students_with_preferences,
                COUNT(DISTINCT CASE WHEN sp.status = 'approved' THEN sp.student_id END) AS students_preferences_approved,
                COUNT(DISTINCT CASE WHEN sp.status = 'rejected' THEN sp.student_id END) AS students_preferences_rejected,
                COUNT(DISTINCT CASE WHEN sp.status = 'pending' THEN sp.student_id END) AS students_preferences_pending,
                -- 計算完成率
                ROUND(COUNT(DISTINCT r.user_id) * 100.0 / NULLIF(COUNT(DISTINCT u.id), 0), 2) AS resume_completion_rate,
                ROUND(COUNT(DISTINCT sp.student_id) * 100.0 / NULLIF(COUNT(DISTINCT u.id), 0), 2) AS preference_completion_rate
            FROM classes c
            LEFT JOIN users u ON u.class_id = c.id AND u.role = 'student'
            LEFT JOIN resumes r ON r.user_id = u.id
            LEFT JOIN student_preferences sp ON sp.student_id = u.id
            WHERE c.department = %s
            GROUP BY c.id, c.name, c.department
            ORDER BY c.name
        """, (department,))
        
        classes_stats = cursor.fetchall()
        
        # 計算全系總計
        total_students = sum(s['total_students'] or 0 for s in classes_stats)
        total_resume_completed = sum(s['students_with_resume'] or 0 for s in classes_stats)
        total_preference_completed = sum(s['students_with_preferences'] or 0 for s in classes_stats)
        
        overall_stats = {
            "total_classes": len(classes_stats),
            "total_students": total_students,
            "total_resume_completed": total_resume_completed,
            "total_preference_completed": total_preference_completed,
            "overall_resume_rate": round(total_resume_completed * 100.0 / total_students if total_students > 0 else 0, 2),
            "overall_preference_rate": round(total_preference_completed * 100.0 / total_students if total_students > 0 else 0, 2)
        }
        
        # 格式化完成率（如果是 None，設為 0）
        for stat in classes_stats:
            if stat['resume_completion_rate'] is None:
                stat['resume_completion_rate'] = 0.0
            if stat['preference_completion_rate'] is None:
                stat['preference_completion_rate'] = 0.0
        
        return jsonify({
            "success": True,
            "department": department,
            "overall_stats": overall_stats,
            "classes": classes_stats
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 主任查看單一班級的詳細履歷列表
# =========================================================
@director_overview_bp.route("/api/get_class_resumes_detail/<int:class_id>", methods=["GET"])
def get_class_resumes_detail(class_id):
    """主任查看單一班級的詳細履歷列表"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    user_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 驗證該班級屬於主任的科系
        cursor.execute("""
            SELECT c.id, c.name, c.department
            FROM classes c
            JOIN classes_teacher ct ON ct.class_id = c.id
            WHERE c.id = %s AND ct.teacher_id = %s
        """, (class_id, user_id))
        class_info = cursor.fetchone()
        
        if not class_info:
            return jsonify({"success": False, "message": "找不到該班級或無權限查看"}), 404
        
        # 查詢該班級所有學生的履歷
        cursor.execute("""
            SELECT 
                r.id AS resume_id,
                u.id AS student_id,
                u.name AS student_name,
                u.username AS student_number,
                r.original_filename,
                r.status,
                r.comment,
                r.created_at,
                r.updated_at
            FROM users u
            LEFT JOIN resumes r ON r.user_id = u.id
            WHERE u.class_id = %s AND u.role = 'student'
            ORDER BY u.name, r.created_at DESC
        """, (class_id,))
        
        resumes = cursor.fetchall()
        
        # 格式化日期
        for r in resumes:
            if isinstance(r.get('created_at'), datetime):
                r['created_at'] = r['created_at'].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(r.get('updated_at'), datetime):
                r['updated_at'] = r['updated_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        return jsonify({
            "success": True,
            "class_name": class_info['name'],
            "resumes": resumes
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 主任查看單一班級的詳細志願序列表
# =========================================================
@director_overview_bp.route("/api/get_class_preferences_detail/<int:class_id>", methods=["GET"])
def get_class_preferences_detail(class_id):
    """主任查看單一班級的詳細志願序列表"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    user_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 驗證該班級屬於主任的科系
        cursor.execute("""
            SELECT c.id, c.name, c.department
            FROM classes c
            JOIN classes_teacher ct ON ct.class_id = c.id
            WHERE c.id = %s AND ct.teacher_id = %s
        """, (class_id, user_id))
        class_info = cursor.fetchone()
        
        if not class_info:
            return jsonify({"success": False, "message": "找不到該班級或無權限查看"}), 404
        
        # 查詢該班級所有學生的志願序
        cursor.execute("""
            SELECT 
                sp.id AS preference_id,
                u.id AS student_id,
                u.name AS student_name,
                u.username AS student_number,
                sp.preference_order,
                sp.status,
                ic.company_name,
                ij.title AS job_title,
                sp.submitted_at
            FROM users u
            LEFT JOIN student_preferences sp ON sp.student_id = u.id
            LEFT JOIN internship_companies ic ON sp.company_id = ic.id
            LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
            WHERE u.class_id = %s AND u.role = 'student'
            ORDER BY u.name, sp.preference_order
        """, (class_id,))
        
        preferences = cursor.fetchall()
        
        # 格式化日期
        for p in preferences:
            if isinstance(p.get('submitted_at'), datetime):
                p['submitted_at'] = p['submitted_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        return jsonify({
            "success": True,
            "class_name": class_info['name'],
            "preferences": preferences
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()




