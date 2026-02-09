from flask import Blueprint, request, jsonify, session, render_template, redirect, send_file
from config import get_db
from datetime import datetime
from semester import get_current_semester_code, get_current_semester_id
from notification import create_notification
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
import io
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
# 頁面路由：實習生管理
# =========================================================
@admission_bp.route("/intern_management", methods=["GET"])
def intern_management_page():
    """實習生管理頁面"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role')
    # 允許老師、主任、ta、admin、vendor 訪問
    if user_role not in ['teacher', 'director', 'ta', 'admin', 'vendor']:
        return "無權限訪問此頁面", 403
    
    return render_template('user_shared/Intern management.html')

# =========================================================
# 頁面路由：實習生／未錄取名單管理
# =========================================================
@admission_bp.route("/unadmitted_list", methods=["GET"])
def unadmitted_list_page():
    """實習生／未錄取名單管理頁面（科助、主任、老師、管理員）"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role')
    if user_role not in ['ta', 'admin', 'director', 'teacher']:
        return "無權限訪問此頁面", 403
    
    return render_template('user_shared/intern_unadmitted_management.html')

# =========================================================
# 頁面路由：主任媒合
# =========================================================
@admission_bp.route("/manage_director", methods=["GET"])
def manage_director_page():
    """主任媒合頁面"""
    if 'user_id' not in session:
        return redirect('/login')
    
    user_role = session.get('role')
    # 只允許主任訪問
    if user_role != 'director':
        return "無權限訪問此頁面", 403
    
    return render_template('user_shared/manage_director.html')

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
        current_datetime_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
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
            # 5. 創建師生關係記錄
            cursor.execute("""
                INSERT INTO teacher_student_relations 
                (teacher_id, student_id, semester, role, created_at)
                VALUES (%s, %s, %s, '指導老師', CURDATE())
            """, (advisor_user_id, student_id, semester_code))
        
        # 6. 在 internship_offers 表中記錄錄取結果 (新增的邏輯)
        # 這是 get_my_admission API 優先讀取的資料來源
        print(f"🔍 [DEBUG] record_admission - 準備寫入 internship_offers: student_id={student_id}, job_id={job_id}")
        
        # 檢查是否已存在於 internship_offers
        if job_id:
            cursor.execute("""
                SELECT id FROM internship_offers
                WHERE student_id = %s AND job_id = %s
            """, (student_id, job_id))
        else:
            # 如果 job_id 為 NULL，檢查是否有該學生的其他錄取記錄
            cursor.execute("""
                SELECT id FROM internship_offers
                WHERE student_id = %s AND job_id IS NULL
            """, (student_id,))
        existing_offer = cursor.fetchone()
        print(f"🔍 [DEBUG] existing_offer: {existing_offer}")

        if existing_offer:
            # 如果已存在，更新錄取狀態和時間（使用 'accepted' 狀態，與資料庫中的值一致）
            cursor.execute("""
                UPDATE internship_offers
                SET status = 'accepted', offered_at = %s, responded_at = %s
                WHERE id = %s
            """, (current_datetime_str, current_datetime_str, existing_offer['id']))
            print(f"✅ [DEBUG] 更新 internship_offers 記錄: id={existing_offer['id']}")
        else:
            # 插入新的錄取記錄（使用 'accepted' 狀態）
            cursor.execute("""
                INSERT INTO internship_offers 
                (student_id, job_id, status, offered_at, responded_at)
                VALUES (%s, %s, %s, %s, %s)
            """, (student_id, job_id, 'accepted', current_datetime_str, current_datetime_str))
            inserted_id = cursor.lastrowid
            print(f"✅ [DEBUG] 插入新 internship_offers 記錄: id={inserted_id}, student_id={student_id}, job_id={job_id}")
            
        # 7. 更新學生的志願序狀態
        if preference_order:
            cursor.execute("""
                UPDATE student_preferences
                SET status = 'approved'
                WHERE student_id = %s AND preference_order = %s
            """, (student_id, preference_order))
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "message": f"錄取結果已記錄，已自動綁定指導老師 {advisor['name']} 與學生 {student['name']}。資料已寫入 internship_offers。",
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
# (此處保持不變，因為它已經設計為優先讀取 internship_offers)
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
        # 優先從 internship_offers 表獲取錄取資料（廠商選擇學生時記錄的）
        # 這是主要的資料來源，代表廠商實際錄取的結果
        cursor.execute("""
            SELECT 
                io.id AS offer_id,
                io.job_id,
                io.status AS offer_status,
                io.offered_at,
                io.responded_at,
                ij.company_id,
                ij.title AS job_title,
                ij.description AS job_description,
                ij.period AS internship_period,
                ij.work_time AS internship_time,
                ij.salary AS job_salary,
                ic.company_name,
                ic.location AS company_address,
                ic.contact_person AS contact_name,
                ic.contact_email,
                ic.contact_phone,
                ic.advisor_user_id
            FROM internship_offers io
            LEFT JOIN internship_jobs ij ON io.job_id = ij.id
            LEFT JOIN internship_companies ic ON ij.company_id = ic.id
            WHERE io.student_id = %s
              AND io.status IN ('offered', 'accepted', 'Approved')
            ORDER BY io.offered_at DESC
            LIMIT 1
        """, (student_id,))
        offer_info = cursor.fetchone()
        
        # 調試：打印查詢結果
        print(f"🔍 [DEBUG] get_my_admission - student_id={student_id}")
        print(f"🔍 [DEBUG] offer_info from internship_offers: {offer_info}")
        
        # 如果從 internship_offers 獲取到資料，使用它
        if offer_info:
            print(f"✅ [DEBUG] 找到 internship_offers 記錄")
            print(f"    job_id={offer_info.get('job_id')}, company_id={offer_info.get('company_id')}")
            
            # 如果 company_id 為 NULL，嘗試從 student_preferences 獲取公司資訊
            company_id = offer_info.get('company_id')
            if not company_id and offer_info.get('job_id'):
                # 從 student_preferences 獲取公司資訊
                cursor.execute("""
                    SELECT company_id
                    FROM student_preferences
                    WHERE student_id = %s AND job_id = %s
                    LIMIT 1
                """, (student_id, offer_info.get('job_id')))
                pref_company = cursor.fetchone()
                if pref_company and pref_company.get('company_id'):
                    company_id = pref_company.get('company_id')
                    print(f"🔍 [DEBUG] 從 student_preferences 獲取到 company_id={company_id}")
            
            # 如果還是沒有 company_id，嘗試直接從 internship_jobs 獲取
            if not company_id and offer_info.get('job_id'):
                cursor.execute("""
                    SELECT company_id
                    FROM internship_jobs
                    WHERE id = %s
                """, (offer_info.get('job_id'),))
                job_row = cursor.fetchone()
                if job_row and job_row.get('company_id'):
                    company_id = job_row.get('company_id')
                    print(f"🔍 [DEBUG] 從 internship_jobs 獲取到 company_id={company_id}")
            
            # 如果有 company_id，重新查詢完整的公司資訊
            if company_id:
                cursor.execute("""
                    SELECT 
                        id AS company_id,
                        company_name,
                        location AS company_address,
                        contact_person AS contact_name,
                        contact_email,
                        contact_phone,
                        advisor_user_id
                    FROM internship_companies
                    WHERE id = %s
                """, (company_id,))
                company_row = cursor.fetchone()
                if company_row:
                    offer_info['company_id'] = company_row.get('company_id')
                    offer_info['company_name'] = company_row.get('company_name')
                    offer_info['company_address'] = company_row.get('company_address')
                    offer_info['contact_name'] = company_row.get('contact_name')
                    offer_info['contact_email'] = company_row.get('contact_email')
                    offer_info['contact_phone'] = company_row.get('contact_phone')
                    offer_info['advisor_user_id'] = company_row.get('advisor_user_id')
                    print(f"✅ [DEBUG] 重新查詢到完整的公司資訊: {company_row.get('company_name')}")
            
            # 獲取指導老師資訊（從公司的 advisor_user_id）
            teacher_id = offer_info.get('advisor_user_id')
            teacher_name = None
            teacher_email = None
            if teacher_id:
                cursor.execute("""
                    SELECT id, name, email
                    FROM users
                    WHERE id = %s AND role IN ('teacher', 'director')
                """, (teacher_id,))
                teacher_info = cursor.fetchone()
                if teacher_info:
                    teacher_name = teacher_info.get('name')
                    teacher_email = teacher_info.get('email')
            
            # 獲取學期代碼（從 teacher_student_relations 表）
            semester_code = None
            cursor.execute("""
                SELECT semester
                FROM teacher_student_relations
                WHERE student_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (student_id,))
            tsr_result = cursor.fetchone()
            if tsr_result and tsr_result.get('semester'):
                semester_code = tsr_result.get('semester')
            
            # 如果沒有從 teacher_student_relations 獲取到，嘗試使用當前學期
            if not semester_code:
                semester_code = get_current_semester_code(cursor)
            
            # 從 semesters 表獲取學期的開始和結束日期
            semester_start_date = None
            semester_end_date = None
            if semester_code:
                cursor.execute("""
                    SELECT start_date, end_date
                    FROM semesters
                    WHERE code = %s
                    LIMIT 1
                """, (semester_code,))
                semester_info = cursor.fetchone()
                if semester_info:
                    semester_start_date = semester_info.get('start_date')
                    semester_end_date = semester_info.get('end_date')
                    # 格式化日期
                    if isinstance(semester_start_date, datetime):
                        semester_start_date = semester_start_date.strftime("%Y-%m-%d")
                    if isinstance(semester_end_date, datetime):
                        semester_end_date = semester_end_date.strftime("%Y-%m-%d")
            
            # 構建 admission 物件
            admission = {
                'company_id': offer_info.get('company_id'),
                'company_name': offer_info.get('company_name'),
                'company_address': offer_info.get('company_address'),
                'contact_name': offer_info.get('contact_name'),
                'contact_email': offer_info.get('contact_email'),
                'contact_phone': offer_info.get('contact_phone'),
                'admitted_at': offer_info.get('offered_at'),
                'teacher_id': teacher_id,
                'teacher_name': teacher_name,
                'teacher_email': teacher_email,
                'semester': semester_code,
                'semester_start_date': semester_start_date,
                'semester_end_date': semester_end_date
            }
            
            # 構建 final_preference 物件
            final_preference = {
                'job_id': offer_info.get('job_id'),
                'job_title': offer_info.get('job_title'),
                'job_description': offer_info.get('job_description'),
                'internship_period': offer_info.get('internship_period'),
                'internship_time': offer_info.get('internship_time'),
                'salary': offer_info.get('job_salary')
            }
            
            # 如果 job_title 為空，嘗試從 internship_jobs 獲取
            if not final_preference.get('job_title') and offer_info.get('job_id'):
                cursor.execute("""
                    SELECT title, description, period, work_time, salary
                    FROM internship_jobs
                    WHERE id = %s
                """, (offer_info.get('job_id'),))
                job_info = cursor.fetchone()
                if job_info:
                    final_preference['job_title'] = job_info.get('title')
                    final_preference['job_description'] = job_info.get('description')
                    final_preference['internship_period'] = job_info.get('period')
                    final_preference['internship_time'] = job_info.get('work_time')
                    if job_info.get('salary') is not None:
                        final_preference['salary'] = job_info.get('salary')
                    print(f"✅ [DEBUG] 重新查詢到職缺資訊: {job_info.get('title')}")
            
            # 嘗試從 student_preferences 獲取志願序資訊
            # 優先選擇 preference_order 最小且 status = 'approved' 的志願（已通過廠商審核的志願）
            if offer_info.get('job_id'):
                # 先查找該 job_id 且已通過審核的志願
                cursor.execute("""
                    SELECT preference_order, submitted_at, company_id
                    FROM student_preferences
                    WHERE student_id = %s AND job_id = %s AND status = 'approved'
                    ORDER BY preference_order ASC
                    LIMIT 1
                """, (student_id, offer_info.get('job_id')))
                pref_info = cursor.fetchone()
                if pref_info:
                    final_preference['preference_order'] = pref_info.get('preference_order')
                    final_preference['submitted_at'] = pref_info.get('submitted_at')
            else:
                # 如果沒有 job_id，查找該學生所有已通過審核的志願，選擇 preference_order 最小的
                cursor.execute("""
                    SELECT 
                        sp.preference_order, 
                        sp.submitted_at,
                        sp.job_id,
                        sp.company_id,
                        ij.title AS job_title,
                        ij.description AS job_description,
                        ij.period AS internship_period,
                        ij.work_time AS internship_time,
                        ij.salary AS job_salary
                    FROM student_preferences sp
                    LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                    WHERE sp.student_id = %s 
                      AND sp.status = 'approved'
                    ORDER BY sp.preference_order ASC
                    LIMIT 1
                """, (student_id,))
                top_preference = cursor.fetchone()
                if top_preference:
                    # 如果找到排名更前面的志願，使用它
                    if not final_preference.get('preference_order') or top_preference.get('preference_order') < final_preference.get('preference_order', 999):
                        final_preference['preference_order'] = top_preference.get('preference_order')
                        final_preference['submitted_at'] = top_preference.get('submitted_at')
                        if top_preference.get('job_title'):
                            final_preference['job_title'] = top_preference.get('job_title')
                        if top_preference.get('job_description'):
                            final_preference['job_description'] = top_preference.get('job_description')
                        if top_preference.get('internship_period'):
                            final_preference['internship_period'] = top_preference.get('internship_period')
                        if top_preference.get('internship_time'):
                            final_preference['internship_time'] = top_preference.get('internship_time')
                        if top_preference.get('job_salary') is not None:
                            final_preference['salary'] = top_preference.get('job_salary')
                        if top_preference.get('job_id'):
                            final_preference['job_id'] = top_preference.get('job_id')
                        # 如果公司資訊不同，也需要更新
                        if top_preference.get('company_id') and top_preference.get('company_id') != offer_info.get('company_id'):
                            cursor.execute("""
                                SELECT 
                                    id AS company_id,
                                    company_name,
                                    location AS company_address,
                                    contact_person AS contact_name,
                                    contact_email,
                                    contact_phone,
                                    advisor_user_id
                                FROM internship_companies
                                WHERE id = %s
                            """, (top_preference.get('company_id'),))
                            top_company = cursor.fetchone()
                            if top_company:
                                admission['company_id'] = top_company.get('company_id')
                                admission['company_name'] = top_company.get('company_name')
                                admission['company_address'] = top_company.get('company_address')
                                admission['contact_name'] = top_company.get('contact_name')
                                admission['contact_email'] = top_company.get('contact_email')
                                admission['contact_phone'] = top_company.get('contact_phone')
                                # 更新指導老師資訊
                                if top_company.get('advisor_user_id'):
                                    cursor.execute("""
                                        SELECT id, name, email
                                        FROM users
                                        WHERE id = %s AND role IN ('teacher', 'director')
                                    """, (top_company.get('advisor_user_id'),))
                                    top_teacher = cursor.fetchone()
                                    if top_teacher:
                                        admission['teacher_id'] = top_teacher.get('id')
                                        admission['teacher_name'] = top_teacher.get('name')
                                        admission['teacher_email'] = top_teacher.get('email')
                        print(f"✅ [DEBUG] 使用排名最前面的志願: preference_order={top_preference.get('preference_order')}")
            
            # 標記已從 internship_offers 獲取到資料，跳過後續的 company_info 處理
            company_info = None
            print(f"✅ [DEBUG] 使用 internship_offers 資料，跳過舊邏輯")
        else:
            # 如果沒有從 internship_offers 獲取到，則使用舊的邏輯（向後兼容）
            print(f"⚠️ [DEBUG] 未找到 internship_offers 記錄，使用舊邏輯")
            # 獲取學生的錄取結果（從 teacher_student_relations）
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
            
            # 從 semesters 表獲取學期的開始和結束日期
            semester_code = admission.get('semester') if admission else None
            if not semester_code:
                semester_code = get_current_semester_code(cursor)
            
            semester_start_date = None
            semester_end_date = None
            if semester_code:
                cursor.execute("""
                    SELECT start_date, end_date
                    FROM semesters
                    WHERE code = %s
                    LIMIT 1
                """, (semester_code,))
                semester_info = cursor.fetchone()
                if semester_info:
                    semester_start_date = semester_info.get('start_date')
                    semester_end_date = semester_info.get('end_date')
                    # 格式化日期
                    if isinstance(semester_start_date, datetime):
                        semester_start_date = semester_start_date.strftime("%Y-%m-%d")
                    if isinstance(semester_end_date, datetime):
                        semester_end_date = semester_end_date.strftime("%Y-%m-%d")
            
            # 將學期日期資訊添加到 admission 物件
            if admission:
                admission['semester_start_date'] = semester_start_date
                admission['semester_end_date'] = semester_end_date
            
            if not admission:
                return jsonify({
                    "success": True,
                    "admission": None,
                    "message": "目前尚未錄取任何實習公司"
                })
            
            # 優先從 internship_experiences 獲取公司資訊（廠商確認媒合結果時記錄的）
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
                    ic.advisor_user_id,
                    ij.title AS job_title,
                    ij.description AS job_description,
                    ij.period AS internship_period,
                    ij.work_time AS internship_time,
                    ij.salary AS job_salary
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
                # 優先使用公司的 advisor_user_id 作為指導老師（這是該公司實際的指導老師）
                company_advisor_id = company_info.get('advisor_user_id')
                if company_advisor_id:
                    cursor.execute("""
                        SELECT id, name, email
                        FROM users
                        WHERE id = %s AND role IN ('teacher', 'director')
                    """, (company_advisor_id,))
                    company_advisor = cursor.fetchone()
                    if company_advisor:
                        # 使用公司的指導老師資訊，而不是 teacher_student_relations 中的
                        admission['teacher_id'] = company_advisor.get('id')
                        admission['teacher_name'] = company_advisor.get('name')
                        admission['teacher_email'] = company_advisor.get('email')
                        print(f"✅ [DEBUG] 使用公司的指導老師: {company_advisor.get('name')} (advisor_user_id={company_advisor_id})")
                admission['company_id'] = company_info.get('company_id')
                admission['company_name'] = company_info.get('company_name')
                admission['company_address'] = company_info.get('company_address')
                admission['contact_name'] = company_info.get('contact_name')
                admission['contact_email'] = company_info.get('contact_email')
                admission['contact_phone'] = company_info.get('contact_phone')
                
                # 更新錄取時間為 internship_experiences 的創建時間（廠商確認的時間）
                if company_info.get('admitted_at'):
                    admission['admitted_at'] = company_info.get('admitted_at')
                
                # 優先從 student_preferences 獲取排名最前面的志願（preference_order 最小）
                # 而不是只查詢當前 company_info 對應的志願
                cursor.execute("""
                    SELECT 
                        sp.preference_order,
                        sp.submitted_at,
                        sp.job_id,
                        sp.company_id,
                        ij.title AS job_title,
                        ij.description AS job_description,
                        ij.period AS internship_period,
                        ij.work_time AS internship_time,
                        ij.salary AS job_salary,
                        ic.company_name,
                        ic.location AS company_address,
                        ic.contact_person AS contact_name,
                        ic.contact_email,
                        ic.contact_phone,
                        ic.advisor_user_id
                    FROM student_preferences sp
                    LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                    LEFT JOIN internship_companies ic ON sp.company_id = ic.id
                    WHERE sp.student_id = %s 
                      AND sp.status = 'approved'
                    ORDER BY sp.preference_order ASC, sp.submitted_at DESC
                    LIMIT 1
                """, (student_id,))
                top_preference_info = cursor.fetchone()
                
                if top_preference_info:
                    # 使用排名最前面的志願資訊
                    final_preference = {
                        'preference_order': top_preference_info.get('preference_order'),
                        'submitted_at': top_preference_info.get('submitted_at'),
                        'job_id': top_preference_info.get('job_id'),
                        'job_title': top_preference_info.get('job_title'),
                        'job_description': top_preference_info.get('job_description'),
                        'internship_period': top_preference_info.get('internship_period'),
                        'internship_time': top_preference_info.get('internship_time'),
                        'salary': top_preference_info.get('job_salary')
                    }
                    # 如果排名最前面的志願與當前 company_info 不同，更新公司資訊
                    if top_preference_info.get('company_id') != company_info.get('company_id'):
                        admission['company_id'] = top_preference_info.get('company_id')
                        admission['company_name'] = top_preference_info.get('company_name')
                        admission['company_address'] = top_preference_info.get('company_address')
                        admission['contact_name'] = top_preference_info.get('contact_name')
                        admission['contact_email'] = top_preference_info.get('contact_email')
                        admission['contact_phone'] = top_preference_info.get('contact_phone')
                        # 更新指導老師資訊
                        if top_preference_info.get('advisor_user_id'):
                            cursor.execute("""
                                SELECT id, name, email
                                FROM users
                                WHERE id = %s AND role IN ('teacher', 'director')
                            """, (top_preference_info.get('advisor_user_id'),))
                            top_teacher = cursor.fetchone()
                            if top_teacher:
                                admission['teacher_id'] = top_teacher.get('id')
                                admission['teacher_name'] = top_teacher.get('name')
                                admission['teacher_email'] = top_teacher.get('email')
                        print(f"✅ [DEBUG] 使用排名最前面的志願: preference_order={top_preference_info.get('preference_order')}, company={top_preference_info.get('company_name')}")
                else:
                    # 如果沒有找到 approved 的志願，使用 company_info 的資料
                    final_preference = {
                        'preference_order': None,
                        'submitted_at': None,
                        'job_id': company_info.get('job_id'),
                        'job_title': company_info.get('job_title'),
                        'job_description': company_info.get('job_description'),
                        'internship_period': company_info.get('internship_period'),
                        'internship_time': company_info.get('internship_time'),
                        'salary': company_info.get('job_salary')
                    }
            else:
                # 如果沒有從 internship_experiences 獲取到，則從 student_preferences 獲取（備用方案）
                # 優先選擇 preference_order 最小的志願（排名最前面的）
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
                        ic.advisor_user_id,
                        ij.id AS job_id,
                        ij.title AS job_title,
                        ij.description AS job_description,
                        ij.period AS internship_period,
                        ij.work_time AS internship_time,
                        ij.salary AS job_salary
                    FROM student_preferences sp
                    LEFT JOIN internship_companies ic ON sp.company_id = ic.id
                    LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                    WHERE sp.student_id = %s 
                      AND sp.status = 'approved'
                    ORDER BY sp.preference_order ASC, sp.submitted_at DESC
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
                    
                    # 優先使用公司的 advisor_user_id 作為指導老師（這是該公司實際的指導老師）
                    company_advisor_id = final_preference.get('advisor_user_id')
                    if company_advisor_id:
                        cursor.execute("""
                            SELECT id, name, email
                            FROM users
                            WHERE id = %s AND role IN ('teacher', 'director')
                        """, (company_advisor_id,))
                        company_advisor = cursor.fetchone()
                        if company_advisor:
                            # 使用公司的指導老師資訊，而不是 teacher_student_relations 中的
                            admission['teacher_id'] = company_advisor.get('id')
                            admission['teacher_name'] = company_advisor.get('name')
                            admission['teacher_email'] = company_advisor.get('email')
                            print(f"✅ [DEBUG] 使用公司的指導老師: {company_advisor.get('name')} (advisor_user_id={company_advisor_id})")
                    
                    # 清理 final_preference，只保留志願相關資訊
                    final_preference_clean = {
                        'preference_order': final_preference.get('preference_order'),
                        'submitted_at': final_preference.get('submitted_at'),
                        'job_id': final_preference.get('job_id'),
                        'job_title': final_preference.get('job_title'),
                        'job_description': final_preference.get('job_description'),
                        'internship_period': final_preference.get('internship_period'),
                        'internship_time': final_preference.get('internship_time'),
                        'salary': final_preference.get('job_salary')
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
        
        # 將錄取資料寫入 placement_results 表
        if admission and admission.get('company_id'):
            try:
                # 檢查是否已存在該學生的記錄
                cursor.execute("""
                    SELECT id FROM placement_results
                    WHERE student_id = %s
                    LIMIT 1
                """, (student_id,))
                existing_record = cursor.fetchone()
                
                # 準備要寫入的資料
                company_id = admission.get('company_id')
                mentor_id = admission.get('teacher_id')
                job_title = final_preference.get('job_title') if final_preference else None
                semester_code = admission.get('semester')
                
                # 處理實習開始和結束日期
                internship_start_date = admission.get('semester_start_date')
                internship_end_date = admission.get('semester_end_date')
                
                # 如果日期是字串格式，確保格式正確（YYYY-MM-DD）
                if internship_start_date and isinstance(internship_start_date, str):
                    # 如果已經是正確格式，保持不變
                    if len(internship_start_date) == 10 and internship_start_date.count('-') == 2:
                        pass  # 格式正確
                    else:
                        # 嘗試解析其他格式
                        try:
                            dt = datetime.strptime(internship_start_date, "%Y-%m-%d %H:%M:%S")
                            internship_start_date = dt.strftime("%Y-%m-%d")
                        except:
                            try:
                                dt = datetime.strptime(internship_start_date, "%Y-%m-%d")
                                internship_start_date = dt.strftime("%Y-%m-%d")
                            except:
                                internship_start_date = None
                
                if internship_end_date and isinstance(internship_end_date, str):
                    if len(internship_end_date) == 10 and internship_end_date.count('-') == 2:
                        pass  # 格式正確
                    else:
                        try:
                            dt = datetime.strptime(internship_end_date, "%Y-%m-%d %H:%M:%S")
                            internship_end_date = dt.strftime("%Y-%m-%d")
                        except:
                            try:
                                dt = datetime.strptime(internship_end_date, "%Y-%m-%d")
                                internship_end_date = dt.strftime("%Y-%m-%d")
                            except:
                                internship_end_date = None
                
                # 處理錄取時間
                matched_at = admission.get('admitted_at')
                if matched_at and isinstance(matched_at, str):
                    # 如果已經是字串，嘗試解析為 datetime
                    try:
                        matched_at = datetime.strptime(matched_at, "%Y-%m-%d %H:%M:%S")
                    except:
                        try:
                            matched_at = datetime.strptime(matched_at, "%Y-%m-%d")
                        except:
                            matched_at = datetime.now()
                elif not matched_at:
                    matched_at = datetime.now()
                
                if existing_record:
                    # 更新現有記錄
                    cursor.execute("""
                        UPDATE placement_results
                        SET company_id = %s,
                            mentor_id = %s,
                            job_title = %s,
                            semester_code = %s,
                            internship_start_date = %s,
                            internship_end_date = %s,
                            matched_at = %s
                        WHERE student_id = %s
                    """, (
                        company_id,
                        mentor_id,
                        job_title,
                        semester_code,
                        internship_start_date,
                        internship_end_date,
                        matched_at,
                        student_id
                    ))
                    print(f"✅ [DEBUG] 更新 placement_results: student_id={student_id}")
                else:
                    # 插入新記錄
                    cursor.execute("""
                        INSERT INTO placement_results
                        (student_id, company_id, mentor_id, job_title, semester_code,
                         internship_start_date, internship_end_date, matched_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        student_id,
                        company_id,
                        mentor_id,
                        job_title,
                        semester_code,
                        internship_start_date,
                        internship_end_date,
                        matched_at
                    ))
                    print(f"✅ [DEBUG] 插入 placement_results: student_id={student_id}")
                
                conn.commit()
            except Exception as e:
                # 如果寫入失敗，記錄錯誤但不影響主要功能
                print(f"⚠️ [WARNING] 寫入 placement_results 失敗: {str(e)}")
                traceback.print_exc()
                # 不拋出異常，讓主要功能繼續執行
        
        # 調試：打印最終返回的資料
        print(f"🔍 [DEBUG] 最終返回的 admission: {admission}")
        print(f"🔍 [DEBUG] 最終返回的 final_preference: {final_preference}")
        
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
# 臨時 API: 為現有錄取記錄補寫 internship_offers 資料
# =========================================================
@admission_bp.route("/api/sync_internship_offers", methods=["POST"])
def sync_internship_offers():
    """為現有錄取記錄補寫 internship_offers 資料（一次性操作）"""
    if 'user_id' not in session or session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取所有有錄取關係但沒有 internship_offers 記錄的學生
        cursor.execute("""
            SELECT DISTINCT
                tsr.student_id,
                sp.job_id,
                sp.company_id,
                tsr.created_at AS admitted_at
            FROM teacher_student_relations tsr
            INNER JOIN student_preferences sp ON tsr.student_id = sp.student_id
            WHERE sp.status = 'approved'
              AND NOT EXISTS (
                  SELECT 1 FROM internship_offers io
                  WHERE io.student_id = tsr.student_id
                    AND (io.job_id = sp.job_id OR (io.job_id IS NULL AND sp.job_id IS NULL))
              )
            ORDER BY tsr.student_id, sp.preference_order
        """)
        missing_records = cursor.fetchall()
        
        inserted_count = 0
        updated_count = 0
        
        for record in missing_records:
            student_id = record['student_id']
            job_id = record['job_id']
            admitted_at = record['admitted_at']
            
            # 檢查是否已存在
            if job_id:
                cursor.execute("""
                    SELECT id FROM internship_offers
                    WHERE student_id = %s AND job_id = %s
                """, (student_id, job_id))
            else:
                cursor.execute("""
                    SELECT id FROM internship_offers
                    WHERE student_id = %s AND job_id IS NULL
                """, (student_id,))
            
            existing = cursor.fetchone()
            
            if existing:
                # 更新現有記錄
                cursor.execute("""
                    UPDATE internship_offers
                    SET status = 'accepted', offered_at = %s, responded_at = %s
                    WHERE id = %s
                """, (admitted_at, admitted_at, existing['id']))
                updated_count += 1
            else:
                # 插入新記錄
                cursor.execute("""
                    INSERT INTO internship_offers
                    (student_id, job_id, status, offered_at, responded_at)
                    VALUES (%s, %s, 'accepted', %s, %s)
                """, (student_id, job_id, admitted_at, admitted_at))
                inserted_count += 1
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "message": f"同步完成：新增 {inserted_count} 筆記錄，更新 {updated_count} 筆記錄",
            "inserted": inserted_count,
            "updated": updated_count,
            "total_processed": len(missing_records)
        })
    
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": f"同步失敗: {str(e)}"}), 500
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
# API: 退實習生（刪除師生關係）
# =========================================================
@admission_bp.route("/api/withdraw_student", methods=["POST"])
def withdraw_student():
    """退實習生，刪除 teacher_student_relations 記錄"""
    if 'user_id' not in session or session.get('role') not in ['teacher', 'director', 'ta', 'admin', 'vendor']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    relation_id = data.get("relation_id")
    student_id = data.get("student_id")
    
    if not relation_id and not student_id:
        return jsonify({"success": False, "message": "請提供關係ID或學生ID"}), 400
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        teacher_id = session.get('user_id')
        
        # 如果提供了 relation_id，直接刪除
        if relation_id:
            # 驗證該關係是否屬於當前老師（除非是 admin 或 ta）
            if session.get('role') not in ['admin', 'ta']:
                cursor.execute("""
                    SELECT id FROM teacher_student_relations 
                    WHERE id = %s AND teacher_id = %s
                """, (relation_id, teacher_id))
                relation = cursor.fetchone()
                if not relation:
                    return jsonify({"success": False, "message": "找不到該關係或無權限"}), 404
            
            cursor.execute("DELETE FROM teacher_student_relations WHERE id = %s", (relation_id,))
        else:
            # 如果只提供了 student_id，需要找到對應的關係
            if session.get('role') not in ['admin', 'ta']:
                cursor.execute("""
                    SELECT id FROM teacher_student_relations 
                    WHERE student_id = %s AND teacher_id = %s
                """, (student_id, teacher_id))
            else:
                cursor.execute("""
                    SELECT id FROM teacher_student_relations 
                    WHERE student_id = %s
                """, (student_id,))
            
            relation = cursor.fetchone()
            if not relation:
                # 如果找不到實習關係，視為已經退出，直接返回成功
                # 同時更新志願序狀態
                if student_id:
                    cursor.execute("""
                        UPDATE student_preferences
                        SET status = 'pending'
                        WHERE student_id = %s AND status = 'approved'
                    """, (student_id,))
                    conn.commit()
                return jsonify({
                    "success": True,
                    "message": "已成功退實習生"
                })
            
            cursor.execute("DELETE FROM teacher_student_relations WHERE id = %s", (relation['id'],))
        
        # 同時將學生的志願序狀態改為 pending（取消錄取）
        if student_id:
            cursor.execute("""
                UPDATE student_preferences
                SET status = 'pending'
                WHERE student_id = %s AND status = 'approved'
            """, (student_id,))
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "message": "已成功退實習生"
        })
    
    except Exception as e:
        traceback.print_exc()
        if conn:
            conn.rollback()
        return jsonify({"success": False, "message": f"退實習生失敗: {str(e)}"}), 500
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
        if user_role == 'class_teacher':
            # 班導只能看到自己管理的班級的學生（所有公司）
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
        elif user_role == 'teacher':
            # 指導老師只能看到自己指導的學生（通過 teacher_student_relations）
            # 並且這些學生錄取的必須是自己管理的公司
            cursor.execute("""
                SELECT id FROM internship_companies 
                WHERE advisor_user_id = %s AND status = 'approved'
            """, (user_id,))
            teacher_companies = cursor.fetchall()
            if teacher_companies:
                company_ids = [tc['id'] for tc in teacher_companies]
                # 限制：1. teacher_student_relations 中的 teacher_id 必須是當前老師
                #       2. 學生選擇的公司必須是該老師管理的公司
                base_query += " AND tsr.teacher_id = %s"
                params.append(user_id)
                placeholders = ','.join(['%s'] * len(company_ids))
                base_query += f" AND sp.company_id IN ({placeholders})"
                params.extend(company_ids)
            else:
                # 如果沒有管理的公司，返回空結果
                return jsonify({
                    "success": True,
                    "students": [],
                    "count": 0
                })
        elif user_role == 'director':
            # 主任可以看到自己科系的學生
            # 透過 classes_teacher 和 classes 表取得主任所屬科系
            cursor.execute("""
                SELECT DISTINCT c.department
                FROM classes c
                JOIN classes_teacher ct ON ct.class_id = c.id
                WHERE ct.teacher_id = %s
                LIMIT 1
            """, (user_id,))
            dept_result = cursor.fetchone()
            if dept_result and dept_result.get('department'):
                base_query += " AND c.department = %s"
                params.append(dept_result['department'])
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
# API: 獲取所有學生列表（用於未被錄取學生顯示）
# =========================================================
@admission_bp.route("/api/get_all_students", methods=["GET"])
def get_all_students():
    """獲取所有學生列表（根據角色過濾），標記哪些已在媒合結果中"""
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    user_id = session.get('user_id')
    user_role = session.get('role')
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取當前學期代碼
        current_semester_code = get_current_semester_code(cursor)
        if not current_semester_code:
            return jsonify({"success": False, "message": "無法取得當前學期"}), 500
        
        # 獲取當前學期ID
        current_semester_id = get_current_semester_id(cursor)
        if not current_semester_id:
            return jsonify({"success": False, "message": "無法取得當前學期"}), 500
        
        # 獲取所有已在媒合結果中的學生 ID（只包括 Approved 或 Pending）
        # 以 student_preferences.semester_id 篩選，避免依賴 manage_director.semester_id（該欄位可能不存在）
        cursor.execute("""
            SELECT DISTINCT md.student_id
            FROM manage_director md
            INNER JOIN student_preferences sp ON md.preference_id = sp.id AND sp.semester_id = %s
            WHERE md.director_decision IN ('Approved', 'Pending')
        """, (current_semester_id,))
        matched_student_ids = {row['student_id'] for row in cursor.fetchall()}
        
        # 基礎查詢：獲取所有學生
        base_query = """
            SELECT 
                u.id AS student_id,
                u.id,
                u.name AS student_name,
                u.username AS student_number,
                c.id AS class_id,
                c.name AS class_name,
                c.department
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            WHERE u.role = 'student'
        """
        params = []
        
        # 根據角色限制查詢範圍
        if user_role == 'director':
            # 主任可以看到自己科系的學生
            cursor.execute("""
                SELECT DISTINCT c.department
                FROM classes c
                JOIN classes_teacher ct ON ct.class_id = c.id
                WHERE ct.teacher_id = %s
                LIMIT 1
            """, (user_id,))
            dept_result = cursor.fetchone()
            if dept_result and dept_result.get('department'):
                base_query += " AND c.department = %s"
                params.append(dept_result['department'])
        elif user_role == 'class_teacher':
            # 班導只能看到自己管理的班級的學生
            cursor.execute("""
                SELECT class_id FROM classes_teacher 
                WHERE teacher_id = %s
            """, (user_id,))
            teacher_classes = cursor.fetchall()
            if teacher_classes:
                class_ids = [tc['class_id'] for tc in teacher_classes]
                placeholders = ','.join(['%s'] * len(class_ids))
                base_query += f" AND u.class_id IN ({placeholders})"
                params.extend(class_ids)
            else:
                return jsonify({
                    "success": True,
                    "students": [],
                    "count": 0
                })
        # ta 和 admin 可以看到所有學生，不需要額外限制
        
        base_query += " ORDER BY u.username ASC"
        
        cursor.execute(base_query, params)
        all_students = cursor.fetchall()
        
        # 為每個學生標記是否已在媒合結果中，並獲取志願序資訊
        for student in all_students:
            student_id = student['student_id']
            # 標記是否已在媒合結果中
            student['is_matched'] = student_id in matched_student_ids
            
            # 為每個學生獲取志願序資訊（只包括 preference_order 在 1-5 範圍內的）
            if current_semester_id:
                cursor.execute("""
                    SELECT 
                        sp.preference_order,
                        ic.company_name,
                        ij.title AS job_title
                    FROM student_preferences sp
                    LEFT JOIN internship_companies ic ON sp.company_id = ic.id
                    LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                    WHERE sp.student_id = %s
                      AND sp.semester_id = %s
                      AND sp.preference_order >= 1
                      AND sp.preference_order <= 5
                    ORDER BY sp.preference_order ASC
                """, (student_id, current_semester_id))
            else:
                cursor.execute("""
                    SELECT 
                        sp.preference_order,
                        ic.company_name,
                        ij.title AS job_title
                    FROM student_preferences sp
                    LEFT JOIN internship_companies ic ON sp.company_id = ic.id
                    LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                    WHERE sp.student_id = %s
                      AND sp.preference_order >= 1
                      AND sp.preference_order <= 5
                    ORDER BY sp.preference_order ASC
                """, (student_id,))
            
            preferences = cursor.fetchall() or []
            student['preferences'] = preferences
        
        return jsonify({
            "success": True,
            "students": all_students,
            "count": len(all_students)
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 獲取學生的志願序資料（主任查看）
# =========================================================
@admission_bp.route("/api/get_student_preferences", methods=["GET"])
def get_student_preferences():
    """獲取指定學生的志願序資料（主任可以查看）"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    student_id = request.args.get('student_id')
    if not student_id:
        return jsonify({"success": False, "message": "請提供學生ID"}), 400
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取當前學期ID
        current_semester_id = get_current_semester_id(cursor)
        
        # 查詢學生的志願序
        if current_semester_id:
            cursor.execute("""
                SELECT 
                    sp.id AS preference_id,
                    sp.preference_order,
                    sp.company_id,
                    sp.job_id,
                    sp.status,
                    sp.submitted_at,
                    ic.company_name,
                    ij.title AS job_title
                FROM student_preferences sp
                LEFT JOIN internship_companies ic ON sp.company_id = ic.id
                LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                WHERE sp.student_id = %s
                  AND sp.semester_id = %s
                ORDER BY sp.preference_order ASC
            """, (student_id, current_semester_id))
        else:
            cursor.execute("""
                SELECT 
                    sp.id AS preference_id,
                    sp.preference_order,
                    sp.company_id,
                    sp.job_id,
                    sp.status,
                    sp.submitted_at,
                    ic.company_name,
                    ij.title AS job_title
                FROM student_preferences sp
                LEFT JOIN internship_companies ic ON sp.company_id = ic.id
                LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                WHERE sp.student_id = %s
                ORDER BY sp.preference_order ASC
            """, (student_id,))
        
        preferences = cursor.fetchall() or []
        
        # 格式化日期
        for pref in preferences:
            if isinstance(pref.get('submitted_at'), datetime):
                pref['submitted_at'] = pref['submitted_at'].strftime("%Y-%m-%d %H:%M:%S")
            elif pref.get('submitted_at'):
                pref['submitted_at'] = str(pref['submitted_at'])
            else:
                pref['submitted_at'] = ""
        
        return jsonify({
            "success": True,
            "preferences": preferences
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

# =========================================================
# API: 主任查看所有廠商媒合結果（包含重複中選檢測）
# =========================================================
@admission_bp.route("/api/director_matching_results", methods=["GET"])
def director_matching_results():
    """主任查看所有廠商的媒合結果，自動檢測重複中選的學生（從 manage_director 表讀取）"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取當前學期ID和代碼
        current_semester_id = get_current_semester_id(cursor)
        current_semester_code = get_current_semester_code(cursor)
        if not current_semester_id or not current_semester_code:
            return jsonify({"success": False, "message": "無法取得當前學期"}), 500
        
        # 從 manage_director 表讀取資料
        # 顯示所有狀態的記錄（Pending, Approved, Rejected），讓主任可以看到並處理
        # 使用 LEFT JOIN 以處理 preference_id 為 NULL 的情況
        # 當 preference_id 為 NULL 時，從該公司的第一個職缺獲取 job_id
        query = """
            SELECT 
                md.match_id,
                md.vendor_id,
                md.student_id,
                md.preference_id,
                md.original_type,
                md.original_rank,
                md.is_conflict,
                md.director_decision,
                md.final_rank,
                md.is_adjusted,
                md.updated_at,
                COALESCE(sp.company_id, md.vendor_id) AS company_id,
                sp.preference_order,
                COALESCE(sp.job_id, (
                    SELECT id FROM internship_jobs 
                    WHERE company_id = COALESCE(sp.company_id, md.vendor_id) 
                    ORDER BY id ASC LIMIT 1
                )) AS job_id,
                COALESCE(ic.company_name, v.name) AS company_name,
                u.name AS student_name,
                u.username AS student_number,
                u.email AS student_email,
                c.name AS class_name,
                c.department AS class_department,
                v.name AS vendor_name,
                COALESCE(ij.title, (
                    SELECT title FROM internship_jobs 
                    WHERE company_id = COALESCE(sp.company_id, md.vendor_id) 
                    ORDER BY id ASC LIMIT 1
                )) AS job_title,
                COALESCE(ij.slots, (
                    SELECT slots FROM internship_jobs 
                    WHERE company_id = COALESCE(sp.company_id, md.vendor_id) 
                    ORDER BY id ASC LIMIT 1
                )) AS job_slots
            FROM manage_director md
            LEFT JOIN student_preferences sp ON md.preference_id = sp.id
            LEFT JOIN internship_companies ic ON COALESCE(sp.company_id, md.vendor_id) = ic.id
            LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
            LEFT JOIN users u ON md.student_id = u.id
            LEFT JOIN classes c ON u.class_id = c.id
            LEFT JOIN users v ON md.vendor_id = v.id
            WHERE md.semester_id = %s
            AND md.director_decision != 'Rejected'  -- 排除已移除（Rejected）的學生，這些學生應該顯示在未錄取名單中
            ORDER BY 
                CASE md.director_decision 
                    WHEN 'Approved' THEN 1 
                    WHEN 'Pending' THEN 2 
                    WHEN 'Rejected' THEN 3 
                    ELSE 4 
                END,
                COALESCE(sp.company_id, md.vendor_id), 
                COALESCE(sp.job_id, (
                    SELECT id FROM internship_jobs 
                    WHERE company_id = COALESCE(sp.company_id, md.vendor_id) 
                    ORDER BY id ASC LIMIT 1
                ), 0), 
                CASE WHEN md.director_decision = 'Approved' AND md.final_rank IS NOT NULL THEN 0 ELSE 1 END,
                COALESCE(md.final_rank, 999) ASC,
                md.original_rank ASC
        """
        cursor.execute(query, (current_semester_id,))
        all_results = cursor.fetchall() or []
        
        # 格式化結果並組織資料結構
        formatted_results = []
        student_company_map = {}  # 用於檢測重複中選：{student_id: [company_ids]}
        
        for result in all_results:
            student_id = result.get("student_id")
            company_id = result.get("company_id")
            
            # 記錄每個學生被哪些公司選中
            if student_id not in student_company_map:
                student_company_map[student_id] = []
            if company_id not in student_company_map[student_id]:
                student_company_map[student_id].append(company_id)
            
            # 判斷是否為正取或備取
            # 如果 director_decision 是 Approved 且有 final_rank，則為正取
            # 如果 director_decision 是 Pending 且 original_type 是 Regular，則為正取
            # 否則為備取
            is_reserve = False
            slot_index = None
            
            if result.get("director_decision") == "Approved" and result.get("final_rank") is not None:
                # 主任已核定為正取
                is_reserve = False
                slot_index = result.get("final_rank")
            elif result.get("director_decision") == "Pending":
                # 待定狀態，根據原始設定判斷
                if result.get("original_type") == "Regular" and result.get("original_rank") is not None:
                    is_reserve = False
                    slot_index = result.get("original_rank")
                else:
                    is_reserve = True
            else:
                is_reserve = True
            
            formatted_result = {
                "id": result.get("match_id"),  # 使用 match_id 作為識別符
                "match_id": result.get("match_id"),
                "vendor_id": result.get("vendor_id"),
                "vendor_name": result.get("vendor_name"),
                "company_id": company_id,
                "company_name": result.get("company_name"),
                "job_id": result.get("job_id"),
                "job_title": result.get("job_title") or "未指定職缺",
                "student_id": student_id,
                "student_name": result.get("student_name"),
                "student_number": result.get("student_number"),
                "student_email": result.get("student_email"),
                "class_name": result.get("class_name"),
                "class_department": result.get("class_department"),
                "preference_order": result.get("preference_order"),
                "preference_id": result.get("preference_id"),
                "slot_index": slot_index,
                "is_reserve": is_reserve,
                "director_decision": result.get("director_decision"),
                "final_rank": result.get("final_rank"),
                "is_adjusted": bool(result.get("is_adjusted")),
                "is_conflict": bool(result.get("is_conflict")),
                "original_type": result.get("original_type"),
                "original_rank": result.get("original_rank"),
                "updated_at": result.get("updated_at").strftime("%Y-%m-%d %H:%M:%S") if isinstance(result.get("updated_at"), datetime) else str(result.get("updated_at", ""))
            }
            formatted_results.append(formatted_result)
        
        # 標記重複中選的學生（根據 is_conflict 或實際重複情況）
        duplicate_students = {}
        for sid, companies in student_company_map.items():
            if len(companies) > 1:
                duplicate_students[sid] = companies
        
        # 也檢查 is_conflict 標記
        for result in formatted_results:
            if result.get("is_conflict") or result["student_id"] in duplicate_students:
                result["is_duplicate"] = True
                result["duplicate_companies"] = duplicate_students.get(result["student_id"], [])
            else:
                result["is_duplicate"] = False
                result["duplicate_companies"] = []
        
        # 先獲取所有已審核的公司（即使沒有媒合結果也要顯示）
        cursor.execute("""
            SELECT DISTINCT ic.id AS company_id, ic.company_name
            FROM internship_companies ic
            WHERE ic.status = 'approved'
            ORDER BY ic.company_name
        """)
        all_companies = cursor.fetchall() or []
        
        # 獲取所有已審核公司的職缺
        cursor.execute("""
            SELECT ij.id AS job_id, ij.company_id, ij.title AS job_title, ij.slots AS job_slots
            FROM internship_jobs ij
            JOIN internship_companies ic ON ij.company_id = ic.id
            WHERE ic.status = 'approved' AND ij.is_active = 1
            ORDER BY ij.company_id, ij.id
        """)
        all_jobs = cursor.fetchall() or []
        
        # 按公司組織資料
        companies_data = {}
            
        # 先初始化所有已審核的公司
        for company in all_companies:
            company_id = company["company_id"]
            company_name = company["company_name"]
            companies_data[company_id] = {
                "company_id": company_id,
                "company_name": company_name,
                "jobs": {}
            }
            
        # 為每個公司添加職缺（即使沒有媒合結果）
        for job in all_jobs:
            company_id = job["company_id"]
            job_id = job["job_id"]
            job_title = job["job_title"] or "未指定職缺"
            job_slots = job["job_slots"] or 1
            
            if company_id in companies_data:
                companies_data[company_id]["jobs"][job_id] = {
                    "job_id": job_id,
                    "job_title": job_title,
                    "job_slots": job_slots,
                    "regulars": [],
                    "reserves": []
                }
        
        # 將媒合結果分配到對應的公司和職缺
        for result in formatted_results:
            company_id = result["company_id"]
            job_id = result.get("job_id") or 0
            job_title = result.get("job_title") or "未指定職缺"
            
            # 如果公司不在列表中，添加它
            if company_id not in companies_data:
                companies_data[company_id] = {
                    "company_id": company_id,
                    "company_name": result["company_name"],
                    "jobs": {}
                }
            
            # 如果職缺不在列表中，添加它
            if job_id not in companies_data[company_id]["jobs"]:
                job_slots = result.get("job_slots") or 1
                companies_data[company_id]["jobs"][job_id] = {
                    "job_id": job_id,
                    "job_title": job_title,
                    "job_slots": job_slots,
                    "regulars": [],
                    "reserves": []
                }
            
            # 分配學生到正取或備取
            if result["is_reserve"]:
                companies_data[company_id]["jobs"][job_id]["reserves"].append(result)
            else:
                companies_data[company_id]["jobs"][job_id]["regulars"].append(result)
        
        # 轉換為列表格式
        companies_list = []
        for company_id, company_data in companies_data.items():
            jobs_list = list(company_data["jobs"].values())
            companies_list.append({
                "company_id": company_id,
                "company_name": company_data["company_name"],
                "jobs": jobs_list
            })
        
        return jsonify({
            "success": True,
            "companies": companies_list,
            "duplicate_students": list(duplicate_students.keys()),
            "total_matches": len(formatted_results)
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 主任移除學生（從媒合結果中移除）
# =========================================================
@admission_bp.route("/api/director_remove_student", methods=["POST"])
def director_remove_student():
    """主任從媒合結果中移除學生（更新 manage_director 表的 director_decision 為 Rejected）"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    match_id = data.get("history_id") or data.get("match_id")  # 支援兩種參數名稱
    
    if not match_id:
        return jsonify({"success": False, "message": "請提供記錄ID"}), 400
    
    director_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 更新 manage_director 表，將 director_decision 設為 Rejected
        cursor.execute("""
            UPDATE manage_director
            SET director_decision = 'Rejected',
                updated_at = CURRENT_TIMESTAMP
            WHERE match_id = %s
        """, (match_id,))
        
        if cursor.rowcount == 0:
            return jsonify({"success": False, "message": "找不到該記錄"}), 404
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "message": "已移除學生"
        })
    
    except Exception as e:
        traceback.print_exc()
        if conn:
            conn.rollback()
        return jsonify({"success": False, "message": f"移除失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 主任從備取名單補上學生
# =========================================================
@admission_bp.route("/api/director_promote_reserve", methods=["POST"])
def director_promote_reserve():
    """主任將備取學生提升為正取（更新 manage_director 表）"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    match_id = data.get("history_id") or data.get("match_id")  # 支援兩種參數名稱
    slot_index = data.get("slot_index")  # 新的正取位置
    
    if not match_id or slot_index is None:
        return jsonify({"success": False, "message": "請提供記錄ID和正取位置"}), 400
    
    director_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 檢查記錄是否存在
        cursor.execute("""
            SELECT match_id, original_type, final_rank
            FROM manage_director
            WHERE match_id = %s
        """, (match_id,))
        record = cursor.fetchone()
        
        if not record:
            return jsonify({"success": False, "message": "找不到該記錄"}), 404
        
        # 判斷是否為調整（如果 original_type 不是 Regular 或 original_rank 不等於 final_rank）
        is_adjusted = True
        if record.get("original_type") == "Regular" and record.get("original_rank") == slot_index:
            is_adjusted = False
        
        # 更新 manage_director 表
        cursor.execute("""
            UPDATE manage_director
            SET director_decision = 'Approved',
                final_rank = %s,
                is_adjusted = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE match_id = %s
        """, (slot_index, is_adjusted, match_id))
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "message": "已將備取學生提升為正取"
        })
    
    except Exception as e:
        traceback.print_exc()
        if conn:
            conn.rollback()
        return jsonify({"success": False, "message": f"提升失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 主任添加未錄取學生到公司
# =========================================================
@admission_bp.route("/api/director_add_student", methods=["POST"])
def director_add_student():
    """主任將未錄取的學生添加到公司的職缺"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    try:
        data = request.get_json()
        if not data:
            print("❌ 錯誤：請求體為空")
            return jsonify({"success": False, "message": "請求體為空"}), 400
        
        print(f"📥 收到請求數據: {data}")
        
        student_id = data.get("student_id")
        company_id = data.get("company_id")
        job_id = data.get("job_id")
        type = data.get("type", "regular")  # 'regular' 或 'reserve'
        slot_index = data.get("slot_index")  # 正取位置（如果是正取）
        
        print(f"📋 解析後的參數: student_id={student_id}, company_id={company_id}, job_id={job_id}, type={type}, slot_index={slot_index}")
        
        # 詳細的參數驗證和錯誤訊息
        if student_id is None:
            print("❌ 錯誤：缺少學生ID")
            return jsonify({"success": False, "message": "缺少學生ID (student_id)"}), 400
        if company_id is None:
            print("❌ 錯誤：缺少公司ID")
            return jsonify({"success": False, "message": "缺少公司ID (company_id)"}), 400
        if job_id is None:
            print("❌ 錯誤：缺少職缺ID")
            return jsonify({"success": False, "message": "缺少職缺ID (job_id)"}), 400
        
        # 確保 ID 是整數
        try:
            student_id = int(student_id)
            company_id = int(company_id)
            job_id = int(job_id)
            if slot_index is not None:
                slot_index = int(slot_index)
            print(f"✅ 參數驗證通過: student_id={student_id}, company_id={company_id}, job_id={job_id}, slot_index={slot_index}")
        except (ValueError, TypeError) as e:
            print(f"❌ ID 格式錯誤: {e}")
            return jsonify({"success": False, "message": f"ID 格式錯誤: {str(e)}"}), 400
    except Exception as parse_error:
        print(f"❌ 解析請求數據時出錯: {parse_error}")
        traceback.print_exc()
        return jsonify({"success": False, "message": f"解析請求數據失敗: {str(parse_error)}"}), 400
    
    director_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        print(f"🔍 開始處理：student_id={student_id}, company_id={company_id}, job_id={job_id}, type={type}, slot_index={slot_index}")
        
        # 獲取當前學期代碼
        current_semester_code = get_current_semester_code(cursor)
        if not current_semester_code:
            return jsonify({"success": False, "message": "無法取得當前學期"}), 500
        
        # 1. 驗證學生是否存在
        cursor.execute("SELECT id, name, username FROM users WHERE id = %s AND role = 'student'", (student_id,))
        student = cursor.fetchone()
        cursor.fetchall()  # 確保所有結果都被讀取
        if not student:
            return jsonify({"success": False, "message": "找不到該學生"}), 404
        
        # 1.5. 確保 students 表中有對應記錄（如果外鍵約束需要）
        # 檢查 students 表是否存在，如果存在則確保有對應記錄
        try:
            # 先檢查 students 表是否存在
            cursor.execute("""
                SELECT TABLE_NAME 
                FROM information_schema.TABLES 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'students'
            """)
            students_table_exists = cursor.fetchone()
            # 確保所有結果都被讀取
            cursor.fetchall()
            
            if students_table_exists:
                # 檢查 students 表中是否有該學生記錄
                cursor.execute("""
                    SELECT id FROM students WHERE id = %s
                """, (student_id,))
                student_in_students = cursor.fetchone()
                # 確保所有結果都被讀取
                cursor.fetchall()
                
                if not student_in_students:
                    # 獲取 students 表的欄位結構
                    cursor.execute("""
                        SELECT COLUMN_NAME, DATA_TYPE 
                        FROM information_schema.COLUMNS 
                        WHERE TABLE_SCHEMA = DATABASE() 
                        AND TABLE_NAME = 'students'
                        ORDER BY ORDINAL_POSITION
                    """)
                    columns = cursor.fetchall()
                    column_names = [col['COLUMN_NAME'] for col in columns] if columns else []
                    
                    # 根據實際欄位構建 INSERT 語句
                    if 'id' in column_names:
                        # 構建欄位和值的列表
                        insert_columns = ['id']
                        insert_values = [student_id]
                        
                        # 添加其他常見欄位
                        if 'name' in column_names:
                            insert_columns.append('name')
                            insert_values.append(student.get('name', ''))
                        if 'username' in column_names:
                            insert_columns.append('username')
                            insert_values.append(student.get('username', ''))
                        if 'user_id' in column_names:
                            insert_columns.append('user_id')
                            insert_values.append(student_id)
                        
                        # 構建並執行 INSERT 語句
                        columns_str = ', '.join(insert_columns)
                        placeholders = ', '.join(['%s'] * len(insert_values))
                        insert_query = f"INSERT INTO students ({columns_str}) VALUES ({placeholders})"
                        cursor.execute(insert_query, insert_values)
        except Exception as students_error:
            # 如果處理 students 表時出錯，記錄但不中斷流程
            # 外鍵約束可能實際指向 users 表，或者 students 表結構不同
            print(f"警告：處理 students 表時出錯: {students_error}")
            pass
        
        # 2. 驗證公司和職缺是否存在
        cursor.execute("SELECT id, company_name FROM internship_companies WHERE id = %s", (company_id,))
        company = cursor.fetchone()
        cursor.fetchall()  # 確保所有結果都被讀取
        if not company:
            return jsonify({"success": False, "message": "找不到該公司"}), 404
        
        cursor.execute("SELECT id, title, company_id, slots FROM internship_jobs WHERE id = %s", (job_id,))
        job = cursor.fetchone()
        cursor.fetchall()  # 確保所有結果都被讀取
        if not job:
            return jsonify({"success": False, "message": "找不到該職缺"}), 404
        
        if job['company_id'] != company_id:
            print(f"❌ 錯誤：職缺 {job_id} 不屬於公司 {company_id}，實際屬於公司 {job['company_id']}")
            return jsonify({"success": False, "message": "職缺不屬於該公司"}), 400
        
        # 3. 獲取當前學期ID
        current_semester_id = get_current_semester_id(cursor)
        if not current_semester_id:
            return jsonify({"success": False, "message": "無法取得當前學期"}), 500
        
        # 4. 檢查是否已經存在該學生的媒合記錄
        cursor.execute("""
            SELECT match_id, director_decision, preference_id, vendor_id
            FROM manage_director
            WHERE student_id = %s AND semester_id = %s
        """, (student_id, current_semester_id))
        existing = cursor.fetchone()
        cursor.fetchall()  # 確保所有結果都被讀取
        
        if existing:
            # 如果記錄存在且狀態為 Rejected，允許更新
            if existing.get('director_decision') == 'Rejected':
                print(f"ℹ️ 學生 {student_id} 有 Rejected 記錄，將更新為新記錄")
                # 繼續處理，後續會更新或創建新記錄
            else:
                # 檢查是否是要更新到同一個公司/職缺
                existing_preference_id = existing.get('preference_id')
                if existing_preference_id:
                    cursor.execute("""
                        SELECT company_id, job_id FROM student_preferences WHERE id = %s
                    """, (existing_preference_id,))
                    existing_pref = cursor.fetchone()
                    cursor.fetchall()  # 確保所有結果都被讀取
                    if existing_pref:
                        existing_company_id = existing_pref.get('company_id')
                        existing_job_id = existing_pref.get('job_id')
                        # 如果是同一個公司/職缺，允許更新
                        if existing_company_id == company_id and existing_job_id == job_id:
                            print(f"ℹ️ 學生 {student_id} 已存在於相同公司/職缺，將更新記錄")
                            # 繼續處理，後續會更新記錄
                        else:
                            # 不同的公司/職缺，需要先移除舊記錄或提示錯誤
                            print(f"❌ 錯誤：學生 {student_id} 已經在媒合結果中 (match_id: {existing.get('match_id')}, 狀態: {existing.get('director_decision')})")
                            return jsonify({
                                "success": False, 
                                "message": "該學生已經在媒合結果中，請先移除舊記錄"
                            }), 400
                else:
                    print(f"❌ 錯誤：學生 {student_id} 已經在媒合結果中但 preference_id 為空")
                    return jsonify({"success": False, "message": "該學生已經在媒合結果中"}), 400
        
        # 4. 獲取或創建 student_preference 記錄
        cursor.execute("""
            SELECT id FROM student_preferences
            WHERE student_id = %s AND company_id = %s AND job_id = %s
            LIMIT 1
        """, (student_id, company_id, job_id))
        preference = cursor.fetchone()
        cursor.fetchall()  # 確保所有結果都被讀取
        
        preference_id = None
        if preference:
            preference_id = preference['id']
        else:
            # 創建新的 student_preference 記錄
            # 計算下一個 preference_order
            cursor.execute("""
                SELECT COALESCE(MAX(preference_order), 0) + 1 AS next_order
                FROM student_preferences
                WHERE student_id = %s
            """, (student_id,))
            next_order_result = cursor.fetchone()
            cursor.fetchall()  # 確保所有結果都被讀取
            next_order = next_order_result['next_order'] if next_order_result else 1
            
            # 獲取職缺標題
            job_title = job.get('title', '未指定職缺')
            
            # 獲取當前學期ID（如果有的話）
            cursor.execute("SELECT id FROM semesters WHERE is_active = 1 LIMIT 1")
            semester_row = cursor.fetchone()
            cursor.fetchall()  # 確保所有結果都被讀取
            semester_id = semester_row['id'] if semester_row else None
            
            # 插入 student_preference 記錄
            if semester_id:
                cursor.execute("""
                    INSERT INTO student_preferences 
                    (student_id, semester_id, preference_order, company_id, job_id, job_title, status, submitted_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'approved', CURRENT_TIMESTAMP)
                """, (student_id, semester_id, next_order, company_id, job_id, job_title))
            else:
                cursor.execute("""
                    INSERT INTO student_preferences 
                    (student_id, preference_order, company_id, job_id, job_title, status, submitted_at)
                    VALUES (%s, %s, %s, %s, %s, 'approved', CURRENT_TIMESTAMP)
                """, (student_id, next_order, company_id, job_id, job_title))
            preference_id = cursor.lastrowid
        
        # 5. 在 manage_director 表中創建或更新記錄
        is_reserve = (type == 'reserve')
        original_type = "Regular" if not is_reserve else "Reserve"
        original_rank = slot_index if not is_reserve else None
        final_rank = slot_index if not is_reserve else None
        
        # 如果已存在記錄，更新它；否則創建新記錄
        if existing and existing.get('match_id'):
            match_id = existing.get('match_id')
            print(f"🔄 更新現有記錄 match_id={match_id}")
            cursor.execute("""
                UPDATE manage_director
                SET vendor_id = %s,
                    preference_id = %s,
                    original_type = %s,
                    original_rank = %s,
                    is_conflict = 0,
                    director_decision = 'Approved',
                    final_rank = %s,
                    is_adjusted = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE match_id = %s
            """, (
                company_id, preference_id,
                original_type, original_rank,
                final_rank,
                match_id
            ))
        else:
            print(f"➕ 創建新記錄")
            cursor.execute("""
                INSERT INTO manage_director (
                    semester_id, vendor_id, student_id, preference_id,
                    original_type, original_rank, is_conflict,
                    director_decision, final_rank, is_adjusted,
                    updated_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, 0,
                    'Approved', %s, 0,
                    CURRENT_TIMESTAMP
                )
            """, (
                current_semester_id, company_id, student_id, preference_id,
                original_type, original_rank,
                final_rank
            ))
        
        conn.commit()
        
        type_name = '正取' if type == 'regular' else '備取'
        return jsonify({
            "success": True,
            "message": f"已將學生添加到{type_name}名單"
        })
    
    except Exception as e:
        traceback.print_exc()
        if conn:
            conn.rollback()
        return jsonify({"success": False, "message": f"添加失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 主任調整學生位置
# =========================================================
@admission_bp.route("/api/director_update_position", methods=["POST"])
def director_update_position():
    """主任調整學生在媒合結果中的位置"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    history_id = data.get("history_id")
    slot_index = data.get("slot_index")
    is_reserve = data.get("is_reserve", False)
    
    if not history_id:
        return jsonify({"success": False, "message": "請提供記錄ID"}), 400
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        from vendor import _ensure_history_table
        _ensure_history_table(cursor)
        
        # 檢查欄位是否存在
        cursor.execute("""
            SELECT COLUMN_NAME 
            FROM information_schema.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'vendor_preference_history'
            AND COLUMN_NAME IN ('slot_index', 'is_reserve')
        """)
        existing_columns = {row['COLUMN_NAME'] for row in cursor.fetchall()}
        
        if 'slot_index' in existing_columns and 'is_reserve' in existing_columns:
            # 更新位置
            cursor.execute("""
                UPDATE vendor_preference_history
                SET slot_index = %s, is_reserve = %s
                WHERE id = %s
            """, (slot_index, is_reserve, history_id))
        else:
            # 如果欄位不存在，更新 comment
            if is_reserve:
                comment = "媒合排序：候補"
            else:
                comment = f"媒合排序：正取{slot_index}"
            cursor.execute("""
                UPDATE vendor_preference_history
                SET comment = %s
                WHERE id = %s
            """, (comment, history_id))
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "message": "已更新學生位置"
        })
    
    except Exception as e:
        traceback.print_exc()
        if conn:
            conn.rollback()
        return jsonify({"success": False, "message": f"更新失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 主任調整正取名單中學生的順序
# =========================================================
@admission_bp.route("/api/director_swap_positions", methods=["POST"])
def director_swap_positions():
    """主任調整正取名單中兩個學生的位置順序"""
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    match_id1 = data.get("match_id1")
    match_id2 = data.get("match_id2")
    
    if not match_id1 or not match_id2:
        return jsonify({"success": False, "message": "請提供兩個記錄ID"}), 400
    
    director_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取兩個記錄的當前 final_rank
        cursor.execute("""
            SELECT match_id, final_rank, director_decision
            FROM manage_director
            WHERE match_id IN (%s, %s)
        """, (match_id1, match_id2))
        records = cursor.fetchall()
        
        if len(records) != 2:
            return jsonify({"success": False, "message": "找不到指定的記錄"}), 404
        
        record1 = next((r for r in records if r['match_id'] == match_id1), None)
        record2 = next((r for r in records if r['match_id'] == match_id2), None)
        
        if not record1 or not record2:
            return jsonify({"success": False, "message": "找不到指定的記錄"}), 404
        
        # 確保兩個記錄都是正取狀態
        if record1.get('director_decision') != 'Approved' or record2.get('director_decision') != 'Approved':
            return jsonify({"success": False, "message": "只能調整正取學生的順序"}), 400
        
        rank1 = record1.get('final_rank')
        rank2 = record2.get('final_rank')
        
        if rank1 is None or rank2 is None:
            return jsonify({"success": False, "message": "學生必須有正取位置才能調整順序"}), 400
        
        # 交換兩個學生的 final_rank
        cursor.execute("""
            UPDATE manage_director
            SET final_rank = %s,
                is_adjusted = TRUE,
                updated_at = CURRENT_TIMESTAMP
            WHERE match_id = %s
        """, (rank2, match_id1))
        
        cursor.execute("""
            UPDATE manage_director
            SET final_rank = %s,
                is_adjusted = TRUE,
                updated_at = CURRENT_TIMESTAMP
            WHERE match_id = %s
        """, (rank1, match_id2))
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "message": "已交換學生位置"
        })
    
    except Exception as e:
        traceback.print_exc()
        if conn:
            conn.rollback()
        return jsonify({"success": False, "message": f"調整失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 主任確認媒合結果
# =========================================================
@admission_bp.route("/api/director_confirm_matching", methods=["POST"])
def director_confirm_matching():
    """
    主任確認媒合結果後：
    1. 通知指導老師與班導最後結果已經出來
    2. 傳給廠商做確認
    3. 由科助進行最後發布
    """
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取當前學期ID
        current_semester_id = get_current_semester_id(cursor)
        if not current_semester_id:
            return jsonify({"success": False, "message": "無法取得當前學期"}), 500
        
        # 1. 收集所有需要通知的指導老師和班導（去重，避免同一個人收到兩個通知）
        notified_user_ids = set()
        
        # 收集所有指導老師（role='teacher'）
        cursor.execute("SELECT id FROM users WHERE role = 'teacher'")
        teachers = cursor.fetchall() or []
        for teacher in teachers:
            notified_user_ids.add(teacher['id'])
        
        # 收集所有班導（從 classes_teacher 表獲取）
        cursor.execute("""
            SELECT DISTINCT ct.teacher_id
            FROM classes_teacher ct
            JOIN users u ON ct.teacher_id = u.id
            WHERE ct.role = '班導師'
        """)
        class_teachers = cursor.fetchall() or []
        for class_teacher in class_teachers:
            notified_user_ids.add(class_teacher['teacher_id'])
        
        # 只發送一個通知給所有需要通知的用戶（指導老師和班導）
        title = "媒合結果已出爐"
        message = "媒合結果已出爐"
        link_url = "/admission/results"
        
        for user_id in notified_user_ids:
            create_notification(
                user_id=user_id,
                title=title,
                message=message,
                category="matching",
                link_url=link_url
            )
        
        # 3. 通知所有廠商（role='vendor'）進行確認
        cursor.execute("SELECT id, name FROM users WHERE role = 'vendor'")
        vendors = cursor.fetchall() or []
        
        for vendor in vendors:
            title = "媒合結果待確認"
            message = "主任已確認媒合結果，請前往確認您的實習生名單。"
            link_url = "/vendor/matching_results"  # 廠商查看媒合結果的頁面
            create_notification(
                user_id=vendor['id'],
                title=title,
                message=message,
                category="approval",
                link_url=link_url
            )
        
        # 4. 通知所有科助（role='ta'）進行最後發布
        cursor.execute("SELECT id, name FROM users WHERE role = 'ta'")
        tas = cursor.fetchall() or []
        
        for ta in tas:
            title = "媒合結果待發布"
            message = "主任已確認媒合結果，廠商確認後請進行最後發布。"
            link_url = "/final_results"  # 科助查看最終結果的頁面
            create_notification(
                user_id=ta['id'],
                title=title,
                message=message,
                category="approval",
                link_url=link_url
            )
        
        # 5. 更新媒合結果狀態（可選：在 manage_director 表中添加狀態欄位，或創建新的狀態表）
        # 這裡可以添加狀態更新的邏輯，例如標記為「已確認，待廠商確認」
        # 目前先不更新資料庫狀態，只發送通知
        
        return jsonify({
            "success": True,
            "message": "媒合結果確認成功，已通知相關人員",
            "notified": {
                "teachers_and_class_teachers": len(notified_user_ids),
                "vendors": len(vendors),
                "tas": len(tas)
            }
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"確認失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 科助工作台統計（媒合已核定數、未錄取人數）
# =========================================================
@admission_bp.route("/api/ta_dashboard_stats", methods=["GET"])
def ta_dashboard_stats():
    """
    科助工作台用：回傳已核定媒合數、未錄取學生人數。
    僅允許 role 為 ta 或 admin。
    """
    if 'user_id' not in session or session.get('role') not in ['ta', 'admin']:
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        current_semester_id = get_current_semester_id(cursor)
        current_semester_code = get_current_semester_code(cursor)
        if not current_semester_id:
            return jsonify({"success": False, "message": "無法取得當前學期"}), 500

        # 已核定／待公告的媒合人數（Approved + Pending，以「不重複學生」計）
        # 以 student_preferences.semester_id 篩選學期（不依賴 manage_director.semester_id，因該欄位可能不存在）
        cursor.execute("""
            SELECT COUNT(DISTINCT md.student_id) AS cnt
            FROM manage_director md
            INNER JOIN student_preferences sp ON md.preference_id = sp.id AND sp.semester_id = %s
            WHERE md.director_decision IN ('Approved', 'Pending')
        """, (current_semester_id,))
        row = cursor.fetchone()
        matching_approved_count = (row.get("cnt") or 0) if row else 0

        # 所有學生人數（role = 'student'）
        cursor.execute("""
            SELECT COUNT(*) AS cnt
            FROM users u
            WHERE u.role = 'student'
        """)
        row = cursor.fetchone()
        total_students = (row.get("cnt") or 0) if row else 0

        # 未錄取人數 = 全部學生 - 已核定媒合學生數
        unadmitted_count = max(0, total_students - matching_approved_count)

        return jsonify({
            "success": True,
            "semester_code": current_semester_code or "",
            "matching_approved_count": matching_approved_count,
            "unadmitted_count": unadmitted_count,
            "total_students": total_students,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 匯出媒合結果 Excel（網格格式）
# =========================================================
@admission_bp.route("/api/export_matching_results_excel", methods=["GET"])
def export_matching_results_excel():
    """
    匯出媒合結果為 Excel 格式，按照圖片樣式：
    - 3列網格布局
    - 每個公司一個區塊
    - 公司名稱用黃色背景
    - 學生列表（學號 + 姓名）
    - 總人數統計
    """
    if 'user_id' not in session or session.get('role') != 'director':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取當前學期ID
        current_semester_id = get_current_semester_id(cursor)
        if not current_semester_id:
            return jsonify({"success": False, "message": "無法取得當前學期"}), 500
        
        # 獲取媒合結果數據（與 director_matching_results 相同的邏輯）
        query = """
            SELECT 
                md.match_id,
                md.vendor_id,
                md.student_id,
                md.preference_id,
                md.original_type,
                md.original_rank,
                md.is_conflict,
                md.director_decision,
                md.final_rank,
                md.is_adjusted,
                COALESCE(sp.company_id, md.vendor_id) AS company_id,
                sp.preference_order,
                COALESCE(sp.job_id, (
                    SELECT id FROM internship_jobs 
                    WHERE company_id = COALESCE(sp.company_id, md.vendor_id) 
                    ORDER BY id ASC LIMIT 1
                )) AS job_id,
                COALESCE(ic.company_name, v.name) AS company_name,
                u.name AS student_name,
                u.username AS student_number,
                c.name AS class_name,
                COALESCE(ij.title, (
                    SELECT title FROM internship_jobs 
                    WHERE company_id = COALESCE(sp.company_id, md.vendor_id) 
                    ORDER BY id ASC LIMIT 1
                )) AS job_title
            FROM manage_director md
            LEFT JOIN student_preferences sp ON md.preference_id = sp.id
            LEFT JOIN internship_companies ic ON COALESCE(sp.company_id, md.vendor_id) = ic.id
            LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
            LEFT JOIN users u ON md.student_id = u.id
            LEFT JOIN classes c ON u.class_id = c.id
            LEFT JOIN users v ON md.vendor_id = v.id
            WHERE md.semester_id = %s
            AND md.director_decision IN ('Approved', 'Pending')
            ORDER BY COALESCE(sp.company_id, md.vendor_id), 
                     COALESCE(sp.job_id, 0),
                     CASE WHEN md.director_decision = 'Approved' AND md.final_rank IS NOT NULL THEN 0 ELSE 1 END,
                     COALESCE(md.final_rank, 999) ASC
        """
        cursor.execute(query, (current_semester_id,))
        all_results = cursor.fetchall() or []
        
        # 按公司分組數據
        companies_data = {}
        for result in all_results:
            company_id = result.get("company_id")
            company_name = result.get("company_name") or "未知公司"
            job_title = result.get("job_title") or "未指定職缺"
            
            if company_id not in companies_data:
                companies_data[company_id] = {
                    "company_name": company_name,
                    "jobs": {}
                }
            
            if job_title not in companies_data[company_id]["jobs"]:
                companies_data[company_id]["jobs"][job_title] = []
            
            companies_data[company_id]["jobs"][job_title].append({
                "student_number": result.get("student_number") or "",
                "student_name": result.get("student_name") or "",
                "job_title": job_title
            })
        
        # 創建 Excel 工作簿
        wb = Workbook()
        ws = wb.active
        ws.title = "媒合結果"
        
        # 設定樣式
        company_header_fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")  # 黃色背景
        company_header_font = Font(bold=True, size=12)
        student_font = Font(size=11)
        total_fill = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")  # 灰色背景
        total_font = Font(bold=True, size=11)
        
        # 邊框樣式
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # 4列網格布局
        COLUMNS = 4
        COLUMN_WIDTH = 20  # 每列寬度（字符）
        
        # 準備公司數據
        companies_list = []
        for company in companies_data.values():
            company_name = company["company_name"]
            all_students = []
            
            # 收集該公司所有職缺的學生
            for job_title, students in company["jobs"].items():
                all_students.extend(students)
            
            if all_students:
                companies_list.append({
                    "name": company_name,
                    "students": all_students
                })
        
        # 將公司分配到4列
        columns_data = [[], [], [], []]  # 4列
        for idx, company in enumerate(companies_list):
            col_idx = idx % COLUMNS
            columns_data[col_idx].append(company)
        
        # 為每列填充數據
        for col_idx in range(COLUMNS):
            # 計算欄位：第1列用A-B-C，第2列用D-E-F，第3列用G-H-I，第4列用J-K-L
            # 每個公司區塊佔用3欄（前兩欄用於內容，第三欄為空）
            col_number_start = col_idx * 3 + 1  # A=1, D=4, G=7, J=10
            col_letter_start = get_column_letter(col_number_start)
            col_letter_end = get_column_letter(col_number_start + 1)
            col_letter_right = get_column_letter(col_number_start + 2)  # 右邊空一格
            current_row = 1
            
            for company in columns_data[col_idx]:
                company_name = company["name"]
                students = company["students"]
                
                # 公司名稱標題（黃色背景，跨兩欄置中，右邊空一格）
                header_cell = ws[f"{col_letter_start}{current_row}"]
                header_cell.value = company_name
                header_cell.fill = company_header_fill
                header_cell.font = company_header_font
                header_cell.border = thin_border
                header_cell.alignment = Alignment(horizontal='center', vertical='center')
                # 合併兩欄
                ws.merge_cells(f"{col_letter_start}{current_row}:{col_letter_end}{current_row}")
                # 確保合併後的單元格也有邊框
                ws[f"{col_letter_end}{current_row}"].border = thin_border
                # 右邊空一格（第三欄留空）
                right_empty_cell = ws[f"{col_letter_right}{current_row}"]
                right_empty_cell.value = ""
                right_empty_cell.border = thin_border
                current_row += 1
                
                # 學生列表（學號和姓名分開兩欄，右邊空一格）
                for student in students:
                    student_number = student.get('student_number') or ''
                    student_name = student.get('student_name') or ''
                    
                    # 學號欄位
                    number_cell = ws[f"{col_letter_start}{current_row}"]
                    number_cell.value = student_number
                    number_cell.font = student_font
                    number_cell.border = thin_border
                    number_cell.alignment = Alignment(horizontal='left', vertical='center')
                    
                    # 姓名欄位
                    name_cell = ws[f"{col_letter_end}{current_row}"]
                    name_cell.value = student_name
                    name_cell.font = student_font
                    name_cell.border = thin_border
                    name_cell.alignment = Alignment(horizontal='left', vertical='center')
                    
                    # 右邊空一格（第三欄留空）
                    right_empty_cell = ws[f"{col_letter_right}{current_row}"]
                    right_empty_cell.value = ""
                    right_empty_cell.border = thin_border
                    
                    current_row += 1
                
                # 總人數
                # 左欄留空
                ws[f"{col_letter_start}{current_row}"].value = ""
                ws[f"{col_letter_start}{current_row}"].border = thin_border
                # 右欄顯示總人數
                total_text = f"{len(students)}人"
                total_cell = ws[f"{col_letter_end}{current_row}"]
                total_cell.value = total_text
                total_cell.fill = total_fill
                total_cell.font = total_font
                total_cell.border = thin_border
                total_cell.alignment = Alignment(horizontal='center', vertical='center')
                # 右邊空一格（第三欄留空）
                right_empty_cell = ws[f"{col_letter_right}{current_row}"]
                right_empty_cell.value = ""
                right_empty_cell.border = thin_border
                current_row += 1  # 移到下一行
                
                # 公司與公司之間的間隔行（三欄都留空）
                ws[f"{col_letter_start}{current_row}"].value = ""
                ws[f"{col_letter_start}{current_row}"].border = thin_border
                ws[f"{col_letter_end}{current_row}"].value = ""
                ws[f"{col_letter_end}{current_row}"].border = thin_border
                ws[f"{col_letter_right}{current_row}"].value = ""
                ws[f"{col_letter_right}{current_row}"].border = thin_border
                current_row += 1  # 移到下一行
        
        # 設定列寬（每列佔用3個欄位，所以總共12欄）
        for col in range(1, COLUMNS * 3 + 1):
            col_letter = get_column_letter(col)
            ws.column_dimensions[col_letter].width = COLUMN_WIDTH / 3  # 每欄寬度為原寬度的1/3
        
        # 設定行高
        for row in range(1, ws.max_row + 1):
            ws.row_dimensions[row].height = 20
        
        # 保存到內存
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        # 生成文件名
        filename = f"媒合結果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"匯出失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 科助匯出媒合結果 Excel（用於公告）
# =========================================================
@admission_bp.route("/api/ta/export_matching_results_excel", methods=["GET"])
def ta_export_matching_results_excel():
    """
    科助匯出媒合結果為 Excel 格式（用於公告）。
    允許 role 為 ta 或 admin。
    使用 student_preferences.semester_id 篩選，避免依賴 manage_director.semester_id。
    """
    if 'user_id' not in session or session.get('role') not in ['ta', 'admin']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取當前學期ID
        current_semester_id = get_current_semester_id(cursor)
        if not current_semester_id:
            return jsonify({"success": False, "message": "無法取得當前學期"}), 500
        
        # 獲取媒合結果數據（使用 student_preferences.semester_id 篩選）
        query = """
            SELECT 
                md.match_id,
                md.vendor_id,
                md.student_id,
                md.preference_id,
                md.original_type,
                md.original_rank,
                md.is_conflict,
                md.director_decision,
                md.final_rank,
                md.is_adjusted,
                COALESCE(sp.company_id, md.vendor_id) AS company_id,
                sp.preference_order,
                COALESCE(sp.job_id, (
                    SELECT id FROM internship_jobs 
                    WHERE company_id = COALESCE(sp.company_id, md.vendor_id) 
                    ORDER BY id ASC LIMIT 1
                )) AS job_id,
                COALESCE(ic.company_name, v.name) AS company_name,
                u.name AS student_name,
                u.username AS student_number,
                c.name AS class_name,
                COALESCE(ij.title, (
                    SELECT title FROM internship_jobs 
                    WHERE company_id = COALESCE(sp.company_id, md.vendor_id) 
                    ORDER BY id ASC LIMIT 1
                )) AS job_title
            FROM manage_director md
            INNER JOIN student_preferences sp ON md.preference_id = sp.id AND sp.semester_id = %s
            LEFT JOIN internship_companies ic ON COALESCE(sp.company_id, md.vendor_id) = ic.id
            LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
            LEFT JOIN users u ON md.student_id = u.id
            LEFT JOIN classes c ON u.class_id = c.id
            LEFT JOIN users v ON md.vendor_id = v.id
            WHERE md.director_decision IN ('Approved', 'Pending')
            ORDER BY COALESCE(sp.company_id, md.vendor_id), 
                     COALESCE(sp.job_id, 0),
                     CASE WHEN md.director_decision = 'Approved' AND md.final_rank IS NOT NULL THEN 0 ELSE 1 END,
                     COALESCE(md.final_rank, 999) ASC
        """
        cursor.execute(query, (current_semester_id,))
        all_results = cursor.fetchall() or []
        
        # 按公司分組數據（與原函數相同的邏輯）
        companies_data = {}
        for result in all_results:
            company_id = result.get("company_id")
            company_name = result.get("company_name") or "未知公司"
            job_title = result.get("job_title") or "未指定職缺"
            
            if company_id not in companies_data:
                companies_data[company_id] = {
                    "company_name": company_name,
                    "jobs": {}
                }
            
            if job_title not in companies_data[company_id]["jobs"]:
                companies_data[company_id]["jobs"][job_title] = []
            
            companies_data[company_id]["jobs"][job_title].append({
                "student_number": result.get("student_number") or "",
                "student_name": result.get("student_name") or "",
                "job_title": job_title
            })
        
        # 創建 Excel 工作簿（與原函數相同的邏輯）
        wb = Workbook()
        ws = wb.active
        ws.title = "媒合結果"
        
        # 設定樣式
        company_header_fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
        company_header_font = Font(bold=True, size=12)
        student_font = Font(size=11)
        total_fill = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")
        total_font = Font(bold=True, size=11)
        
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        COLUMNS = 4
        COLUMN_WIDTH = 20
        
        companies_list = []
        for company in companies_data.values():
            company_name = company["company_name"]
            all_students = []
            for job_title, students in company["jobs"].items():
                all_students.extend(students)
            if all_students:
                companies_list.append({
                    "name": company_name,
                    "students": all_students
                })
        
        columns_data = [[], [], [], []]
        for idx, company in enumerate(companies_list):
            col_idx = idx % COLUMNS
            columns_data[col_idx].append(company)
        
        for col_idx in range(COLUMNS):
            col_number_start = col_idx * 3 + 1
            col_letter_start = get_column_letter(col_number_start)
            col_letter_end = get_column_letter(col_number_start + 1)
            col_letter_right = get_column_letter(col_number_start + 2)
            current_row = 1
            
            for company in columns_data[col_idx]:
                company_name = company["name"]
                students = company["students"]
                
                header_cell = ws[f"{col_letter_start}{current_row}"]
                header_cell.value = company_name
                header_cell.fill = company_header_fill
                header_cell.font = company_header_font
                header_cell.border = thin_border
                header_cell.alignment = Alignment(horizontal='center', vertical='center')
                ws.merge_cells(f"{col_letter_start}{current_row}:{col_letter_end}{current_row}")
                ws[f"{col_letter_end}{current_row}"].border = thin_border
                ws[f"{col_letter_right}{current_row}"].value = ""
                ws[f"{col_letter_right}{current_row}"].border = thin_border
                current_row += 1
                
                for student in students:
                    student_number = student.get('student_number') or ''
                    student_name = student.get('student_name') or ''
                    
                    number_cell = ws[f"{col_letter_start}{current_row}"]
                    number_cell.value = student_number
                    number_cell.font = student_font
                    number_cell.border = thin_border
                    number_cell.alignment = Alignment(horizontal='left', vertical='center')
                    
                    name_cell = ws[f"{col_letter_end}{current_row}"]
                    name_cell.value = student_name
                    name_cell.font = student_font
                    name_cell.border = thin_border
                    name_cell.alignment = Alignment(horizontal='left', vertical='center')
                    
                    ws[f"{col_letter_right}{current_row}"].value = ""
                    ws[f"{col_letter_right}{current_row}"].border = thin_border
                    current_row += 1
                
                ws[f"{col_letter_start}{current_row}"].value = ""
                ws[f"{col_letter_start}{current_row}"].border = thin_border
                total_text = f"{len(students)}人"
                total_cell = ws[f"{col_letter_end}{current_row}"]
                total_cell.value = total_text
                total_cell.fill = total_fill
                total_cell.font = total_font
                total_cell.border = thin_border
                total_cell.alignment = Alignment(horizontal='center', vertical='center')
                ws[f"{col_letter_right}{current_row}"].value = ""
                ws[f"{col_letter_right}{current_row}"].border = thin_border
                current_row += 1
                
                ws[f"{col_letter_start}{current_row}"].value = ""
                ws[f"{col_letter_start}{current_row}"].border = thin_border
                ws[f"{col_letter_end}{current_row}"].value = ""
                ws[f"{col_letter_end}{current_row}"].border = thin_border
                ws[f"{col_letter_right}{current_row}"].value = ""
                ws[f"{col_letter_right}{current_row}"].border = thin_border
                current_row += 1
        
        for col in range(1, COLUMNS * 3 + 1):
            col_letter = get_column_letter(col)
            ws.column_dimensions[col_letter].width = COLUMN_WIDTH / 3
        
        for row in range(1, ws.max_row + 1):
            ws.row_dimensions[row].height = 20
        
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        filename = f"媒合結果公告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"匯出失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()