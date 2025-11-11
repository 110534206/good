from flask import Blueprint, request, jsonify, session
from config import get_db
from datetime import datetime
from semester import get_current_semester_code
import traceback

admission_bp = Blueprint("admission_bp", __name__, url_prefix="/admission")

# =========================================================
# API: 記錄實習錄取結果（錄取後自動綁定指導老師與學生）
# =========================================================
@admission_bp.route("/api/record_admission", methods=["POST"])
def record_admission():
    """
    記錄實習錄取結果，並自動綁定指導老師與學生
    可由廠商、指導老師或管理員調用
    """
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    student_id = data.get("student_id")
    company_id = data.get("company_id")
    job_id = data.get("job_id")  # 可選
    preference_order = data.get("preference_order")  # 可選，記錄最終錄取志願
    
    if not student_id or not company_id:
        return jsonify({"success": False, "message": "請提供學生ID和公司ID"}), 400
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 1. 驗證學生和公司是否存在
        cursor.execute("SELECT id, name, username FROM users WHERE id = %s AND role = 'student'", (student_id,))
        student = cursor.fetchone()
        if not student:
            return jsonify({"success": False, "message": "找不到該學生"}), 404
        
        cursor.execute("SELECT id, company_name, advisor_user_id FROM internship_companies WHERE id = %s", (company_id,))
        company = cursor.fetchone()
        if not company:
            return jsonify({"success": False, "message": "找不到該公司"}), 404
        
        # 2. 獲取指導老師ID（從公司的 advisor_user_id）
        advisor_user_id = company.get('advisor_user_id')
        if not advisor_user_id:
            return jsonify({"success": False, "message": "該公司尚未指派指導老師"}), 400
        
        # 驗證指導老師是否存在
        cursor.execute("SELECT id, name FROM users WHERE id = %s AND role IN ('teacher', 'director')", (advisor_user_id,))
        advisor = cursor.fetchone()
        if not advisor:
            return jsonify({"success": False, "message": "找不到該指導老師"}), 404
        
        # 3. 獲取當前學期代碼
        semester_code = get_current_semester_code(cursor)
        if not semester_code:
            return jsonify({"success": False, "message": "目前沒有設定當前學期"}), 400
        
        # 4. 檢查是否已經存在該關係（避免重複）
        cursor.execute("""
            SELECT id FROM teacher_student_relations 
            WHERE teacher_id = %s AND student_id = %s AND semester = %s
        """, (advisor_user_id, student_id, semester_code))
        existing_relation = cursor.fetchone()
        
        if existing_relation:
            # 如果已存在，更新公司ID（可能學生換了公司）
            cursor.execute("""
                UPDATE teacher_student_relations
                SET company_id = %s, updated_at = NOW()
                WHERE id = %s
            """, (company_id, existing_relation['id']))
        else:
            # 5. 創建師生關係記錄
            cursor.execute("""
                INSERT INTO teacher_student_relations 
                (teacher_id, student_id, company_id, semester, role, created_at)
                VALUES (%s, %s, %s, %s, '指導老師', NOW())
            """, (advisor_user_id, student_id, company_id, semester_code))
        
        # 6. 可選：在 internship_experiences 表中記錄錄取結果
        # 注意：這裡只記錄錄取結果，不包含實習心得
        if job_id:
            # 檢查是否已存在該記錄
            cursor.execute("""
                SELECT id FROM internship_experiences
                WHERE user_id = %s AND company_id = %s AND job_id = %s
            """, (student_id, company_id, job_id))
            existing_exp = cursor.fetchone()
            
            if not existing_exp:
                # 獲取當前年度（民國年）
                current_year = datetime.now().year - 1911
                cursor.execute("""
                    INSERT INTO internship_experiences
                    (user_id, company_id, job_id, year, content, is_public, created_at)
                    VALUES (%s, %s, %s, %s, '已錄取', 0, NOW())
                """, (student_id, company_id, job_id, current_year))
        
        # 7. 更新學生的志願序狀態（如果提供了 preference_order）
        if preference_order:
            cursor.execute("""
                UPDATE student_preferences
                SET status = 'approved'
                WHERE student_id = %s AND preference_order = %s
            """, (student_id, preference_order))
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "message": f"錄取結果已記錄，已自動綁定指導老師 {advisor['name']} 與學生 {student['name']}",
            "teacher_id": advisor_user_id,
            "teacher_name": advisor['name'],
            "student_id": student_id,
            "student_name": student['name'],
            "company_id": company_id,
            "company_name": company['company_name']
        })
    
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": f"記錄錄取結果失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 獲取學生的錄取結果（我的實習成果）
# =========================================================
@admission_bp.route("/api/get_my_admission", methods=["GET"])
def get_my_admission():
    """學生查看自己的錄取結果"""
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    student_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取學生的錄取結果（從 teacher_student_relations）
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
        
        if not admission:
            return jsonify({
                "success": True,
                "admission": None,
                "message": "目前尚未錄取任何實習公司"
            })
        
        # 獲取最終錄取志願（從 student_preferences）
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
        
        # 獲取實習心得（從 internship_experiences）
        cursor.execute("""
            SELECT 
                ie.id AS experience_id,
                ie.year AS internship_year,
                ie.content AS experience_content,
                ie.rating,
                ie.created_at
            FROM internship_experiences ie
            WHERE ie.user_id = %s AND ie.company_id = %s
            ORDER BY ie.year DESC, ie.created_at DESC
        """, (student_id, admission['company_id']))
        experiences = cursor.fetchall()
        
        # 格式化日期
        if isinstance(admission.get('admitted_at'), datetime):
            admission['admitted_at'] = admission['admitted_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        if final_preference and isinstance(final_preference.get('submitted_at'), datetime):
            final_preference['submitted_at'] = final_preference['submitted_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        for exp in experiences:
            if isinstance(exp.get('created_at'), datetime):
                exp['created_at'] = exp['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        return jsonify({
            "success": True,
            "admission": admission,
            "final_preference": final_preference,
            "experiences": experiences
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 指導老師查看錄取該公司學生的列表
# =========================================================
@admission_bp.route("/api/get_company_students", methods=["GET"])
def get_company_students():
    """指導老師查看錄取該公司學生的列表"""
    if 'user_id' not in session or session.get('role') not in ['teacher', 'director']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    teacher_id = session.get('user_id')
    company_id = request.args.get('company_id', type=int)
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 如果提供了 company_id，只查詢該公司的學生
        if company_id:
            cursor.execute("""
                SELECT 
                    tsr.id AS relation_id,
                    tsr.semester,
                    tsr.created_at AS admitted_at,
                    u_student.id AS student_id,
                    u_student.name AS student_name,
                    u_student.username AS student_number,
                    c.name AS class_name,
                    ic.company_name,
                    ij.title AS job_title
                FROM teacher_student_relations tsr
                JOIN users u_student ON tsr.student_id = u_student.id
                LEFT JOIN classes c ON u_student.class_id = c.id
                JOIN internship_companies ic ON tsr.company_id = ic.id
                LEFT JOIN internship_jobs ij ON tsr.company_id = ic.id
                WHERE tsr.teacher_id = %s AND tsr.company_id = %s
                ORDER BY tsr.created_at DESC
            """, (teacher_id, company_id))
        else:
            # 查詢所有該指導老師的學生
            cursor.execute("""
                SELECT 
                    tsr.id AS relation_id,
                    tsr.semester,
                    tsr.created_at AS admitted_at,
                    u_student.id AS student_id,
                    u_student.name AS student_name,
                    u_student.username AS student_number,
                    c.name AS class_name,
                    ic.company_name,
                    ij.title AS job_title
                FROM teacher_student_relations tsr
                JOIN users u_student ON tsr.student_id = u_student.id
                LEFT JOIN classes c ON u_student.class_id = c.id
                JOIN internship_companies ic ON tsr.company_id = ic.id
                LEFT JOIN internship_jobs ij ON tsr.company_id = ic.id
                WHERE tsr.teacher_id = %s
                ORDER BY tsr.created_at DESC
            """, (teacher_id,))
        
        students = cursor.fetchall()
        
        # 格式化日期
        for s in students:
            if isinstance(s.get('admitted_at'), datetime):
                s['admitted_at'] = s['admitted_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        return jsonify({
            "success": True,
            "students": students
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

