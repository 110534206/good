"""
学生实习成果页面模块
学生查看录取结果和实习心得
"""
from flask import Blueprint, request, jsonify, session, render_template
from config import get_db
from datetime import datetime
import traceback

student_results_bp = Blueprint("student_results_bp", __name__, url_prefix="/student")

# =========================================================
# 页面路由：我的实习成果
# =========================================================
@student_results_bp.route("/my_internship_results")
def my_internship_results_page():
    """学生查看我的实习成果页面"""
    if 'user_id' not in session or session.get('role') != 'student':
        from flask import redirect, url_for
        return redirect(url_for('auth_bp.login_page'))
    
    return render_template('user_shared/my_internship_results.html')

# =========================================================
# API: 获取学生的录取结果
# =========================================================
@student_results_bp.route("/api/get_my_internship_results", methods=["GET"])
def get_my_internship_results():
    """学生查看自己的实习成果（录取结果 + 实习心得）"""
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    student_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 1. 獲取學生的錄取結果（從 teacher_student_relations）
        cursor.execute("""
            SELECT 
                tsr.id AS relation_id,
                tsr.semester,
                tsr.created_at AS admitted_at,
                ic.id AS company_id,
                ic.company_name,
                ic.location AS company_address,
                ic.contact_person AS contact_name,
                ic.contact_email,
                ic.contact_phone,
                u_teacher.id AS teacher_id,
                u_teacher.name AS teacher_name,
                u_teacher.email AS teacher_email
            FROM teacher_student_relations tsr
            JOIN internship_companies ic ON tsr.company_id = ic.id
            LEFT JOIN users u_teacher ON tsr.teacher_id = u_teacher.id
            WHERE tsr.student_id = %s
            ORDER BY tsr.created_at DESC
            LIMIT 1
        """, (student_id,))
        admission = cursor.fetchone()
        
        # 2. 獲取最終錄取志願（從 student_preferences）
        final_preference = None
        if admission:
            cursor.execute("""
                SELECT 
                    sp.preference_order,
                    sp.submitted_at,
                    ij.id AS job_id,
                    ij.title AS job_title,
                    ij.description AS job_description,
                    ij.period AS internship_period,
                    ij.work_time AS internship_time
                FROM student_preferences sp
                LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                WHERE sp.student_id = %s 
                  AND sp.company_id = %s
                  AND sp.status = 'approved'
                ORDER BY sp.preference_order ASC
                LIMIT 1
            """, (student_id, admission['company_id']))
            final_preference = cursor.fetchone()
        
        # 3. 獲取學生的實習心得（本屆 + 歷屆）
        # 本屆心得：當前學期的實習心得
        cursor.execute("""
            SELECT 
                ie.id AS experience_id,
                ie.year AS internship_year,
                ie.content AS experience_content,
                ie.rating,
                ie.created_at,
                ic.company_name,
                ij.title AS job_title
            FROM internship_experiences ie
            LEFT JOIN internship_companies ic ON ie.company_id = ic.id
            LEFT JOIN internship_jobs ij ON ie.job_id = ij.id
            WHERE ie.user_id = %s
            ORDER BY ie.year DESC, ie.created_at DESC
        """, (student_id,))
        experiences = cursor.fetchall()
        
        # 格式化日期
        if admission and isinstance(admission.get('admitted_at'), datetime):
            admission['admitted_at'] = admission['admitted_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        if final_preference and isinstance(final_preference.get('submitted_at'), datetime):
            final_preference['submitted_at'] = final_preference['submitted_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        for exp in experiences:
            if isinstance(exp.get('created_at'), datetime):
                exp['created_at'] = exp['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        # 4. 分類心得（本屆 vs 歷屆）
        current_year = datetime.now().year - 1911  # 民國年
        current_experiences = [e for e in experiences if e.get('internship_year') == current_year]
        past_experiences = [e for e in experiences if e.get('internship_year') != current_year]
        
        return jsonify({
            "success": True,
            "admission": admission,
            "final_preference": final_preference,
            "current_experiences": current_experiences,
            "past_experiences": past_experiences,
            "all_experiences": experiences
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 获取学生的实习期间信息
# =========================================================
@student_results_bp.route("/api/get_internship_period", methods=["GET"])
def get_internship_period():
    """获取学生的实习期间信息"""
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    student_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 從 teacher_student_relations 和 internship_experiences 獲取實習期間
        cursor.execute("""
            SELECT 
                tsr.semester,
                ie.year AS internship_year,
                ij.period AS internship_period
            FROM teacher_student_relations tsr
            LEFT JOIN internship_experiences ie ON tsr.student_id = ie.user_id 
                AND tsr.company_id = ie.company_id
            LEFT JOIN internship_jobs ij ON ie.job_id = ij.id
            WHERE tsr.student_id = %s
            ORDER BY tsr.created_at DESC
            LIMIT 1
        """, (student_id,))
        period_info = cursor.fetchone()
        
        return jsonify({
            "success": True,
            "period_info": period_info
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()




