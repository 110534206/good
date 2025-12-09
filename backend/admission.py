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
            
        # 7. 在 internship_experiences 表中記錄錄取結果
        # (原程式碼的邏輯，用於在學生實習成果頁面顯示或舊邏輯兼容)
        if job_id:
            cursor.execute("""
                SELECT id FROM internship_experiences
                WHERE user_id = %s AND company_id = %s AND job_id = %s
            """, (student_id, company_id, job_id))
            existing_exp = cursor.fetchone()
            
            if not existing_exp:
                current_year = datetime.now().year - 1911
                cursor.execute("""
                    INSERT INTO internship_experiences
                    (user_id, company_id, job_id, year, content, is_public, created_at)
                    VALUES (%s, %s, %s, %s, '已錄取', 0, NOW())
                """, (student_id, company_id, job_id, current_year))
        else:
            cursor.execute("""
                SELECT id FROM internship_experiences
                WHERE user_id = %s AND company_id = %s AND job_id IS NULL
            """, (student_id, company_id))
            existing_exp = cursor.fetchone()
            
            if not existing_exp:
                current_year = datetime.now().year - 1911
                cursor.execute("""
                    INSERT INTO internship_experiences
                    (user_id, company_id, job_id, year, content, is_public, created_at)
                    VALUES (%s, %s, NULL, %s, '已錄取', 0, NOW())
                """, (student_id, company_id, current_year))
        
        # 8. 更新學生的志願序狀態
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
                'semester': None  # 可以從其他地方獲取
            }
            
            # 構建 final_preference 物件
            final_preference = {
                'job_id': offer_info.get('job_id'),
                'job_title': offer_info.get('job_title'),
                'job_description': offer_info.get('job_description'),
                'internship_period': offer_info.get('internship_period'),
                'internship_time': offer_info.get('internship_time')
            }
            
            # 如果 job_title 為空，嘗試從 internship_jobs 獲取
            if not final_preference.get('job_title') and offer_info.get('job_id'):
                cursor.execute("""
                    SELECT title, description, period, work_time
                    FROM internship_jobs
                    WHERE id = %s
                """, (offer_info.get('job_id'),))
                job_info = cursor.fetchone()
                if job_info:
                    final_preference['job_title'] = job_info.get('title')
                    final_preference['job_description'] = job_info.get('description')
                    final_preference['internship_period'] = job_info.get('period')
                    final_preference['internship_time'] = job_info.get('work_time')
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
                        ij.work_time AS internship_time
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
                        'internship_time': top_preference_info.get('internship_time')
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
                        'internship_time': company_info.get('internship_time')
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
                        ij.work_time AS internship_time
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