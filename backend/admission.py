from flask import Blueprint, request, jsonify, session, render_template, redirect
from config import get_db
from datetime import datetime
from semester import get_current_semester_code
import traceback

admission_bp = Blueprint("admission_bp", __name__, url_prefix="/admission")

# =========================================================
# 頁面路由：查看錄取結果
# =========================================================
@admission_bp.route("/results", methods=["GET"])
def admission_results_page():
    """查看學生錄取結果頁面"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role')
    # 允許班導、老師、主任、ta、admin 訪問
    if user_role not in ['class_teacher', 'teacher', 'director', 'ta', 'admin']:
        return "無權限訪問此頁面", 403
    
    return render_template('user_shared/admission_results.html')

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
        
        # 3. 設置學期代碼為 1132（固定值）
        semester_code = '1132'
        
        # 4. 檢查是否已經存在該關係（避免重複）
        cursor.execute("""
            SELECT id FROM teacher_student_relations 
            WHERE teacher_id = %s AND student_id = %s AND semester = %s
        """, (advisor_user_id, student_id, semester_code))
        existing_relation = cursor.fetchone()
        
        if existing_relation:
            # 如果已存在，更新 created_at 為當天日期（媒合時間）
            cursor.execute("""
                UPDATE teacher_student_relations 
                SET created_at = CURDATE()
                WHERE id = %s
            """, (existing_relation['id'],))
        else:
            # 5. 創建師生關係記錄（不包含 company_id，因為該欄位可能不存在）
            # 媒合時間使用當天日期（CURDATE()），學期為 1132
            cursor.execute("""
                INSERT INTO teacher_student_relations 
                (teacher_id, student_id, semester, role, created_at)
                VALUES (%s, %s, %s, '指導老師', CURDATE())
            """, (advisor_user_id, student_id, semester_code))
        
        # 6. 在 internship_experiences 表中記錄錄取結果（廠商確認的錄取結果）
        # 注意：這裡只記錄錄取結果，不包含實習心得
        # 這是廠商實際錄取的記錄，用於在學生實習成果頁面顯示
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
        else:
            # 即使沒有 job_id，也記錄公司錄取結果（使用 NULL job_id）
            cursor.execute("""
                SELECT id FROM internship_experiences
                WHERE user_id = %s AND company_id = %s AND job_id IS NULL
            """, (student_id, company_id))
            existing_exp = cursor.fetchone()
            
            if not existing_exp:
                # 獲取當前年度（民國年）
                current_year = datetime.now().year - 1911
                cursor.execute("""
                    INSERT INTO internship_experiences
                    (user_id, company_id, job_id, year, content, is_public, created_at)
                    VALUES (%s, %s, NULL, %s, '已錄取', 0, NOW())
                """, (student_id, company_id, current_year))
        
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
        # 先檢查是否有 company_id 欄位，如果沒有則從 student_preferences 獲取
        cursor.execute("""
            SELECT 
                tsr.id AS relation_id,
                tsr.semester,
                tsr.created_at AS admitted_at,
                u_teacher.id AS teacher_id,
                u_teacher.name AS teacher_name,
                u_teacher.email AS teacher_email
            FROM teacher_student_relations tsr
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
        
        # 優先從 internship_experiences 獲取公司資訊（廠商確認媒合結果時記錄的）
        # 這代表廠商實際錄取的結果，而不是按照志願序
        cursor.execute("""
            SELECT 
                ie.company_id,
                ie.job_id,
                ie.year,
                ie.created_at AS admitted_at,
                ic.company_name,
                ic.location AS company_address,
                ic.contact_person AS contact_name,
                ic.contact_email,
                ic.contact_phone,
                ij.title AS job_title,
                ij.description AS job_description,
                ij.period AS internship_period,
                ij.work_time AS internship_time
            FROM internship_experiences ie
            LEFT JOIN internship_companies ic ON ie.company_id = ic.id
            LEFT JOIN internship_jobs ij ON ie.job_id = ij.id
            WHERE ie.user_id = %s 
              AND ie.content = '已錄取'
            ORDER BY ie.created_at DESC
            LIMIT 1
        """, (student_id,))
        company_info = cursor.fetchone()
        
        # 如果從 internship_experiences 獲取到公司資訊，使用它
        if company_info:
            admission['company_id'] = company_info.get('company_id')
            admission['company_name'] = company_info.get('company_name')
            admission['company_address'] = company_info.get('company_address')
            admission['contact_name'] = company_info.get('contact_name')
            admission['contact_email'] = company_info.get('contact_email')
            admission['contact_phone'] = company_info.get('contact_phone')
            
            # 更新錄取時間為 internship_experiences 的創建時間（廠商確認的時間）
            if company_info.get('admitted_at'):
                admission['admitted_at'] = company_info.get('admitted_at')
            
            # 從對應的 student_preferences 獲取志願相關資訊（用於顯示志願序等）
            job_id_for_query = company_info.get('job_id')
            if job_id_for_query:
                cursor.execute("""
                    SELECT 
                        sp.preference_order,
                        sp.submitted_at
                    FROM student_preferences sp
                    WHERE sp.student_id = %s 
                      AND sp.company_id = %s
                      AND sp.job_id = %s
                    ORDER BY sp.preference_order ASC
                    LIMIT 1
                """, (student_id, company_info.get('company_id'), job_id_for_query))
            else:
                cursor.execute("""
                    SELECT 
                        sp.preference_order,
                        sp.submitted_at
                    FROM student_preferences sp
                    WHERE sp.student_id = %s 
                      AND sp.company_id = %s
                      AND sp.job_id IS NULL
                    ORDER BY sp.preference_order ASC
                    LIMIT 1
                """, (student_id, company_info.get('company_id')))
            preference_info = cursor.fetchone()
            
            final_preference = {
                'preference_order': preference_info.get('preference_order') if preference_info else None,
                'submitted_at': preference_info.get('submitted_at') if preference_info else None,
                'job_id': company_info.get('job_id'),
                'job_title': company_info.get('job_title'),
                'job_description': company_info.get('job_description'),
                'internship_period': company_info.get('internship_period'),
                'internship_time': company_info.get('internship_time')
            }
        else:
            # 如果沒有從 internship_experiences 獲取到，則從 student_preferences 獲取（備用方案）
            # 但按照錄取時間排序，而不是志願序
            cursor.execute("""
                SELECT 
                    sp.company_id,
                    sp.preference_order,
                    sp.submitted_at,
                    ic.company_name,
                    ic.location AS company_address,
                    ic.contact_person AS contact_name,
                    ic.contact_email,
                    ic.contact_phone,
                    ij.id AS job_id,
                    ij.title AS job_title,
                    ij.description AS job_description,
                    ij.period AS internship_period,
                    ij.work_time AS internship_time
                FROM student_preferences sp
                LEFT JOIN internship_companies ic ON sp.company_id = ic.id
                LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                WHERE sp.student_id = %s 
                  AND sp.status = 'approved'
                ORDER BY sp.submitted_at DESC
                LIMIT 1
            """, (student_id,))
            final_preference = cursor.fetchone()
            
            # 如果從 student_preferences 獲取到公司資訊，合併到 admission 中
            if final_preference:
                admission['company_id'] = final_preference.get('company_id')
                admission['company_name'] = final_preference.get('company_name')
                admission['company_address'] = final_preference.get('company_address')
                admission['contact_name'] = final_preference.get('contact_name')
                admission['contact_email'] = final_preference.get('contact_email')
                admission['contact_phone'] = final_preference.get('contact_phone')
                
                # 清理 final_preference，只保留志願相關資訊
                final_preference_clean = {
                    'preference_order': final_preference.get('preference_order'),
                    'submitted_at': final_preference.get('submitted_at'),
                    'job_id': final_preference.get('job_id'),
                    'job_title': final_preference.get('job_title'),
                    'job_description': final_preference.get('job_description'),
                    'internship_period': final_preference.get('internship_period'),
                    'internship_time': final_preference.get('internship_time')
                }
                final_preference = final_preference_clean
            else:
                final_preference = None
        
        # 獲取實習心得（從 internship_experiences）
        company_id = admission.get('company_id')
        experiences = []
        if company_id:
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
            """, (student_id, company_id))
            experiences = cursor.fetchall()
        
        # 格式化日期
        if isinstance(admission.get('admitted_at'), datetime):
            admission['admitted_at'] = admission['admitted_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        if final_preference and isinstance(final_preference.get('submitted_at'), datetime):
            # 錄取志願的提交時間只顯示年月日
            final_preference['submitted_at'] = final_preference['submitted_at'].strftime("%Y-%m-%d")
        elif final_preference and final_preference.get('submitted_at'):
            # 如果已經是字串格式，確保只顯示日期部分
            submitted_at_str = str(final_preference.get('submitted_at'))
            if ' ' in submitted_at_str:
                final_preference['submitted_at'] = submitted_at_str.split(' ')[0]
        
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
                JOIN student_preferences sp ON tsr.student_id = sp.student_id AND sp.status = 'approved'
                JOIN internship_companies ic ON sp.company_id = ic.id
                LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                WHERE tsr.teacher_id = %s AND sp.company_id = %s
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
                JOIN student_preferences sp ON tsr.student_id = sp.student_id AND sp.status = 'approved'
                JOIN internship_companies ic ON sp.company_id = ic.id
                LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
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

# =========================================================
# API: 獲取所有學生的錄取結果列表（支援篩選）
# =========================================================
@admission_bp.route("/api/get_all_admissions", methods=["GET"])
def get_all_admissions():
    """獲取所有學生的錄取結果列表，支援按班級、學期、公司等篩選"""
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    user_id = session.get('user_id')
    user_role = session.get('role')
    
    # 獲取篩選參數
    class_id = request.args.get('class_id', type=int)
    semester = request.args.get('semester', '').strip()
    company_id = request.args.get('company_id', type=int)
    keyword = request.args.get('keyword', '').strip()
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 根據角色決定查詢範圍
        base_query = """
            SELECT 
                tsr.id AS relation_id,
                tsr.semester,
                tsr.created_at AS admitted_at,
                u_student.id AS student_id,
                u_student.name AS student_name,
                u_student.username AS student_number,
                c.id AS class_id,
                c.name AS class_name,
                c.department,
                ic.id AS company_id,
                ic.company_name,
                ij.id AS job_id,
                ij.title AS job_title,
                u_teacher.id AS teacher_id,
                u_teacher.name AS teacher_name,
                sp.preference_order,
                sp.status AS preference_status
            FROM teacher_student_relations tsr
            JOIN users u_student ON tsr.student_id = u_student.id
            LEFT JOIN classes c ON u_student.class_id = c.id
            LEFT JOIN student_preferences sp ON tsr.student_id = sp.student_id
            LEFT JOIN internship_companies ic ON sp.company_id = ic.id
            LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
            LEFT JOIN users u_teacher ON tsr.teacher_id = u_teacher.id
            WHERE 1=1
        """
        params = []
        
        # 根據角色限制查詢範圍
        if user_role == 'class_teacher' or user_role == 'teacher':
            # 班導或老師只能看到自己管理的班級
            cursor.execute("""
                SELECT class_id FROM classes_teacher 
                WHERE teacher_id = %s
            """, (user_id,))
            teacher_classes = cursor.fetchall()
            if teacher_classes:
                class_ids = [tc['class_id'] for tc in teacher_classes]
                placeholders = ','.join(['%s'] * len(class_ids))
                base_query += f" AND u_student.class_id IN ({placeholders})"
                params.extend(class_ids)
            else:
                # 如果沒有管理的班級，返回空結果
                return jsonify({
                    "success": True,
                    "students": [],
                    "count": 0
                })
        elif user_role == 'director':
            # 主任可以看到自己科系的學生
            cursor.execute("SELECT department FROM users WHERE id = %s", (user_id,))
            user_dept = cursor.fetchone()
            if user_dept and user_dept.get('department'):
                base_query += " AND c.department = %s"
                params.append(user_dept['department'])
        # ta 和 admin 可以看到所有學生，不需要額外限制
        
        # 應用篩選條件
        if class_id:
            base_query += " AND u_student.class_id = %s"
            params.append(class_id)
        
        if semester:
            base_query += " AND tsr.semester = %s"
            params.append(semester)
        
        if company_id:
            base_query += " AND sp.company_id = %s"
            params.append(company_id)
        
        if keyword:
            base_query += " AND (u_student.name LIKE %s OR u_student.username LIKE %s OR ic.company_name LIKE %s OR c.name LIKE %s)"
            keyword_pattern = f"%{keyword}%"
            params.extend([keyword_pattern, keyword_pattern, keyword_pattern, keyword_pattern])
        
        base_query += " ORDER BY tsr.created_at DESC, u_student.name ASC"
        
        cursor.execute(base_query, params)
        students = cursor.fetchall()
        
        # 格式化日期
        for s in students:
            if isinstance(s.get('admitted_at'), datetime):
                s['admitted_at'] = s['admitted_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        return jsonify({
            "success": True,
            "students": students,
            "count": len(students)
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 廠商查看媒合結果（包含所有狀態為 approved 的學生履歷）
# =========================================================
@admission_bp.route("/api/vendor_matching_results", methods=["GET"])
def vendor_matching_results():
    """廠商查看媒合結果，返回所有狀態為 approved 的學生履歷"""
    if 'user_id' not in session or session.get('role') != 'vendor':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    vendor_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取廠商關聯的公司（通過 advisor_user_id，與 vendor.py 中的邏輯一致）
        # 先獲取廠商的 teacher_name，然後找到對應的指導老師，再找到該指導老師對接的公司
        cursor.execute("""
            SELECT teacher_name FROM users WHERE id = %s AND role = 'vendor'
        """, (vendor_id,))
        vendor_row = cursor.fetchone()
        
        if not vendor_row or not vendor_row.get("teacher_name"):
            return jsonify({
                "success": True,
                "matches": [],
                "summary": {
                    "total_jobs": 0,
                    "total_students": 0,
                    "by_company": []
                },
                "message": "廠商帳號資料不完整，無法查詢媒合結果"
            })
        
        teacher_name = vendor_row.get("teacher_name").strip()
        if not teacher_name:
            return jsonify({
                "success": True,
                "matches": [],
                "summary": {
                    "total_jobs": 0,
                    "total_students": 0,
                    "by_company": []
                },
                "message": "廠商尚未指派指導老師，無法查詢媒合結果"
            })
        
        # 找到指導老師的 ID
        cursor.execute("""
            SELECT id FROM users WHERE name = %s AND role IN ('teacher', 'director')
        """, (teacher_name,))
        teacher_row = cursor.fetchone()
        
        if not teacher_row:
            return jsonify({
                "success": True,
                "matches": [],
                "summary": {
                    "total_jobs": 0,
                    "total_students": 0,
                    "by_company": []
                },
                "message": "找不到對應的指導老師，無法查詢媒合結果"
            })
        
        teacher_id = teacher_row["id"]
        
        # 找到該指導老師對接的公司（只回傳已審核通過的公司）
        cursor.execute("""
            SELECT DISTINCT ic.id, ic.company_name
            FROM internship_companies ic
            WHERE ic.advisor_user_id = %s AND ic.status = 'approved'
            ORDER BY ic.company_name
        """, (teacher_id,))
        companies = cursor.fetchall() or []
        company_ids = [c['id'] for c in companies] if companies else []
        
        if not company_ids:
            return jsonify({
                "success": True,
                "matches": [],
                "summary": {
                    "total_jobs": 0,
                    "total_students": 0,
                    "by_company": []
                },
                "message": "您尚未上傳任何公司或沒有關聯的公司"
            })
        
        # 獲取所有狀態為 approved 的學生履歷（選擇了該廠商公司的學生）
        placeholders = ','.join(['%s'] * len(company_ids))
        cursor.execute(f"""
            SELECT DISTINCT
                u.id AS student_id,
                u.name AS student_name,
                u.username AS student_number,
                u.email AS student_email,
                c.name AS class_name,
                c.department AS class_department,
                ic.id AS company_id,
                ic.company_name,
                ij.id AS job_id,
                ij.title AS job_title,
                sp.preference_order,
                sp.submitted_at AS preference_submitted_at,
                sp.status AS preference_status,
                COALESCE(tsr.created_at, CURDATE()) AS admitted_at,
                COALESCE(tsr.semester, '1132') AS semester
            FROM student_preferences sp
            JOIN users u ON sp.student_id = u.id
            LEFT JOIN classes c ON u.class_id = c.id
            JOIN internship_companies ic ON sp.company_id = ic.id
            LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
            LEFT JOIN teacher_student_relations tsr ON tsr.student_id = u.id AND tsr.semester = '1132'
            WHERE sp.company_id IN ({placeholders})
              AND sp.status = 'approved'
            ORDER BY ic.company_name, sp.preference_order, u.name
        """, tuple(company_ids))
        
        matches = cursor.fetchall()
        
        # 格式化日期
        for match in matches:
            if isinstance(match.get('preference_submitted_at'), datetime):
                # 錄取志願的提交時間只顯示年月日
                match['preference_submitted_at'] = match['preference_submitted_at'].strftime("%Y-%m-%d")
            elif match.get('preference_submitted_at'):
                # 如果已經是字串格式，確保只顯示日期部分
                submitted_at_str = str(match.get('preference_submitted_at'))
                if ' ' in submitted_at_str:
                    match['preference_submitted_at'] = submitted_at_str.split(' ')[0]
            if isinstance(match.get('admitted_at'), datetime):
                # 媒合時間只顯示日期部分（YYYY-MM-DD）
                match['admitted_at'] = match['admitted_at'].strftime("%Y-%m-%d")
            elif match.get('admitted_at'):
                # 如果已經是字串格式，確保只顯示日期部分
                admitted_at_str = str(match.get('admitted_at'))
                if ' ' in admitted_at_str:
                    match['admitted_at'] = admitted_at_str.split(' ')[0]
            else:
                # 如果沒有媒合時間，使用當天日期
                match['admitted_at'] = datetime.now().strftime("%Y-%m-%d")
            
            # 確保學期為 1132
            if not match.get('semester'):
                match['semester'] = '1132'
        
        # 統計信息：計算所有狀態為 approved 的學生履歷數量（去重，每個學生只計算一次）
        total_students = len(set(m['student_id'] for m in matches)) if matches else 0
        
        # 按公司統計
        by_company = {}
        for match in matches:
            company_name = match['company_name']
            if company_name not in by_company:
                by_company[company_name] = {
                    'company_name': company_name,
                    'matched_students': set()
                }
            by_company[company_name]['matched_students'].add(match['student_id'])
        
        # 轉換為列表格式
        by_company_list = [
            {
                'company_name': k,
                'matched_students': len(v['matched_students'])
            }
            for k, v in by_company.items()
        ]
        
        # 獲取職缺總數（從 vendor/api/positions API 獲取，這裡先返回 0，由前端補充）
        total_jobs = 0
        
        return jsonify({
            "success": True,
            "matches": matches,
            "summary": {
                "total_jobs": total_jobs,
                "total_students": total_students,
                "by_company": by_company_list
            }
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

