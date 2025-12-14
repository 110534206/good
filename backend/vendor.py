from datetime import datetime, timedelta
from decimal import Decimal
import traceback

from flask import Blueprint, jsonify, render_template, request, session

from config import get_db
from semester import get_current_semester_id

vendor_bp = Blueprint('vendor', __name__)

# --- 常量定義 ---
STATUS_LABELS = {
    "pending": "待審核",
    "approved": "已通過",
    "rejected": "已退回",
}

ACTION_TEXT = {
    "approve": "審核通過",
    "reject": "審核退回",
    "reopen": "重新開啟審核",
    "comment": "新增備註",
}

DEFAULT_AVATAR = "/static/images/avatar-default.png"
HISTORY_TABLE_READY = False

# --- 輔助函數 ---

def _format_datetime(value):
    """格式化 datetime 物件為 YYYY/MM/DD HH:MM 格式"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y/%m/%d %H:%M")
    try:
        # 嘗試從 ISO 格式字串解析，如果失敗則返回原始字串
        parsed = datetime.fromisoformat(str(value))
        return parsed.strftime("%Y/%m/%d %H:%M")
    except Exception:
        return str(value)


def _ensure_history_table(cursor):
    """確保廠商志願偏好歷史紀錄表存在"""
    global HISTORY_TABLE_READY
    if HISTORY_TABLE_READY:
        return
    
    try:
        # 先檢查表是否存在
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name = 'vendor_preference_history'
        """)
        table_exists = cursor.fetchone().get('count', 0) > 0
        
        if not table_exists:
            # 檢查 student_preferences 表是否存在
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                AND table_name = 'student_preferences'
            """)
            pref_table_exists = cursor.fetchone().get('count', 0) > 0
            
            if not pref_table_exists:
                print("⚠️ student_preferences 表不存在，無法創建 vendor_preference_history 表")
                HISTORY_TABLE_READY = True  # 標記為已處理，避免重複嘗試
                return
            
            # 檢查 users 表是否存在
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                AND table_name = 'users'
            """)
            users_table_exists = cursor.fetchone().get('count', 0) > 0
            
            if not users_table_exists:
                print("⚠️ users 表不存在，無法創建 vendor_preference_history 表")
                HISTORY_TABLE_READY = True
                return
            
            # 創建表（不包含外鍵約束，先創建表結構）
            cursor.execute("""
                CREATE TABLE vendor_preference_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    preference_id INT NOT NULL,
                    reviewer_id INT NOT NULL,
                    action VARCHAR(20) NOT NULL,
                    comment TEXT,
                    created_at DATETIME NOT NULL,
                    INDEX idx_vph_preference (preference_id),
                    INDEX idx_vph_reviewer (reviewer_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            
            # 嘗試添加外鍵約束（如果失敗，不影響表的使用）
            try:
                cursor.execute("""
                    ALTER TABLE vendor_preference_history
                    ADD CONSTRAINT fk_vph_preference 
                    FOREIGN KEY (preference_id)
                    REFERENCES student_preferences(id) ON DELETE CASCADE
                """)
            except Exception as fk_error:
                print(f"⚠️ 無法添加 preference_id 外鍵約束: {fk_error}")
                # 繼續執行，不影響功能
            
            try:
                cursor.execute("""
                    ALTER TABLE vendor_preference_history
                    ADD CONSTRAINT fk_vph_reviewer 
                    FOREIGN KEY (reviewer_id)
                    REFERENCES users(id) ON DELETE CASCADE
                """)
            except Exception as fk_error:
                print(f"⚠️ 無法添加 reviewer_id 外鍵約束: {fk_error}")
                # 繼續執行，不影響功能
        
        HISTORY_TABLE_READY = True
    except Exception as e:
        print(f"⚠️ 創建 vendor_preference_history 表時發生錯誤: {e}")
        # 標記為已處理，避免重複嘗試
        HISTORY_TABLE_READY = True


def _get_vendor_profile(cursor, vendor_id):
    """獲取廠商的基本資料"""
    cursor.execute(
        "SELECT id, name, email FROM users WHERE id = %s AND role = 'vendor'",
        (vendor_id,),
    )
    return cursor.fetchone()


def _get_vendor_companies(cursor, vendor_id):
    """
    獲取廠商對應的公司列表。
    邏輯：廠商通過指導老師（teacher_name）關聯到公司。
    """
    # 1. 獲取廠商的 teacher_name
    cursor.execute("SELECT teacher_name FROM users WHERE id = %s", (vendor_id,))
    vendor_row = cursor.fetchone()
    if not vendor_row or not vendor_row.get("teacher_name"):
        return []
    
    teacher_name = vendor_row.get("teacher_name").strip()
    if not teacher_name:
        return []
    
    # 2. 找到指導老師的 ID
    cursor.execute("SELECT id FROM users WHERE name = %s AND role IN ('teacher', 'director')", (teacher_name,))
    teacher_row = cursor.fetchone()
    if not teacher_row:
        return []
    
    teacher_id = teacher_row["id"]
    
    # 3. 找到該指導老師對接的公司（只回傳已審核通過的公司）
    query = """
        SELECT id, company_name, contact_email, advisor_user_id
        FROM internship_companies
        WHERE advisor_user_id = %s AND status = 'approved'
        ORDER BY company_name
    """
    params = [teacher_id]
    
    cursor.execute(query, tuple(params))
    return cursor.fetchall() or []


def _get_vendor_scope(cursor, vendor_id):
    """獲取廠商的個人資料、公司權限範圍和信箱"""
    profile = _get_vendor_profile(cursor, vendor_id)
    if not profile:
        return None, [], None
    email = profile.get("email")
    # 傳入 cursor 和 vendor_id 即可
    companies = _get_vendor_companies(cursor, vendor_id)
    return profile, companies, email


def _to_bool(value):
    """將輸入值轉換為布林值"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    raise ValueError("Invalid boolean value")


def _serialize_job(row):
    """格式化職缺資料"""
    if not row:
        return None
    salary_val = row.get("salary")
    if isinstance(salary_val, Decimal):
        # 確保 Decimal 類型正確轉換
        salary_val = float(salary_val)
    return {
        "id": row.get("id"),
        "company_id": row.get("company_id"),
        "company_name": row.get("company_name"),
        "title": row.get("title") or "",
        "slots": int(row.get("slots") or 0),
        "description": row.get("description") or "",
        "period": row.get("period") or "",
        "work_time": row.get("work_time") or "",
        "salary": salary_val,
        "remark": row.get("remark") or "",
        "is_active": bool(row.get("is_active")),
    }


def _fetch_job_for_vendor(cursor, job_id, vendor_id, allow_teacher_created=False):
    """
    獲取廠商有權限訪問的職缺。
    權限邏輯：通過指導老師（teacher_name）關聯到公司。
    """
    # 1. 獲取廠商的 teacher_name
    cursor.execute("SELECT teacher_name FROM users WHERE id = %s", (vendor_id,))
    vendor_row = cursor.fetchone()
    if not vendor_row or not vendor_row.get("teacher_name"):
        return None
    
    teacher_name = vendor_row.get("teacher_name").strip()
    if not teacher_name:
        return None
    
    # 2. 找到指導老師的 ID
    cursor.execute("SELECT id FROM users WHERE name = %s AND role IN ('teacher', 'director')", (teacher_name,))
    teacher_row = cursor.fetchone()
    if not teacher_row:
        return None
    
    teacher_id = teacher_row["id"]
    
    # 3. 構建查詢條件
    if allow_teacher_created:
        # 允許查看廠商自己建立的或指導老師建立的職缺 (created_by_vendor_id IS NULL)
        created_condition = "(ij.created_by_vendor_id = %s OR ij.created_by_vendor_id IS NULL)"
        params = (job_id, teacher_id, vendor_id)
    else:
        # 只允許查看/操作廠商自己建立的職缺
        created_condition = "ij.created_by_vendor_id = %s"
        params = (job_id, teacher_id, vendor_id)
    
    # 使用參數化查詢，防止 SQL 注入
    query = f"""
        SELECT
            ij.id, ij.company_id, ic.company_name, ij.title, ij.slots, ij.description,
            ij.period, ij.work_time, ij.salary, ij.remark, ij.is_active,
            ij.created_by_vendor_id
        FROM internship_jobs ij
        JOIN internship_companies ic ON ij.company_id = ic.id
        WHERE ij.id = %s AND ic.advisor_user_id = %s AND {created_condition}
    """
    cursor.execute(query, params)
    row = cursor.fetchone()
    return row


def _record_history(cursor, preference_id, reviewer_id, action, comment):
    """記錄廠商對志願申請的審核或備註歷史"""
    if action not in ACTION_TEXT:
        return
    _ensure_history_table(cursor)
    cursor.execute(
        """
        INSERT INTO vendor_preference_history
            (preference_id, reviewer_id, action, comment, created_at)
        VALUES (%s, %s, %s, %s, NOW())
        """,
        (preference_id, reviewer_id, action, comment),
    )


def _notify_student(cursor, student_id, title, message, link_url="/vendor_review_resume", category="resume"):
    """發送通知給學生"""
    cursor.execute(
        """
        INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
        VALUES (%s, %s, %s, %s, %s, 0, NOW())
        """,
        (student_id, title, message, category, link_url),
    )


def _fetch_latest_resume(cursor, student_id):
    """獲取學生最新的一份履歷"""
    cursor.execute(
        """
        SELECT r.id, r.original_filename, r.status, r.comment, r.note,
               r.created_at, r.updated_at, r.filepath
        FROM resumes r
        WHERE r.user_id = %s
        ORDER BY r.created_at DESC
        LIMIT 1
        """,
        (student_id,),
    )
    return cursor.fetchone()


def _fetch_skill_tags(cursor, student_id):
    """獲取學生的證照和語言技能作為標籤"""
    skills = []
    # 證照 - 嘗試多種可能的表名和欄位名
    try:
        # 先嘗試使用與 resume.py 一致的方式（通過 JOIN 獲取證照名稱）
        cursor.execute("""
            SELECT
                CONCAT(COALESCE(cc.job_category, ''), COALESCE(cc.level, '')) AS cert_name
            FROM student_certifications sc
            LEFT JOIN certificate_codes cc 
                ON sc.cert_code COLLATE utf8mb4_unicode_ci = cc.code COLLATE utf8mb4_unicode_ci
            WHERE sc.StuID = %s
            ORDER BY sc.AcquisitionDate DESC
        """, (student_id,))
        certifications = cursor.fetchall() or []
        skills.extend([row.get("cert_name") for row in certifications if row.get("cert_name")])
    except Exception as e1:
        # 如果上述查詢失敗，嘗試使用舊的表名和欄位名
        try:
            cursor.execute(
                "SELECT CertName FROM Student_Certifications WHERE StuID = %s ORDER BY CertName",
                (student_id,),
            )
            certifications = cursor.fetchall() or []
            skills.extend([row.get("CertName") for row in certifications if row.get("CertName")])
        except Exception as e2:
            # 如果都失敗，嘗試使用小寫欄位名
            try:
                cursor.execute(
                    "SELECT cert_name FROM student_certifications WHERE StuID = %s ORDER BY cert_name",
                    (student_id,),
                )
                certifications = cursor.fetchall() or []
                skills.extend([row.get("cert_name") for row in certifications if row.get("cert_name")])
            except Exception as e3:
                # 如果所有查詢都失敗，記錄錯誤但不中斷流程
                print(f"⚠️ 無法獲取證照資料: {e1}, {e2}, {e3}")
                certifications = []

    # 語言技能
    cursor.execute(
        "SELECT Language, Level FROM Student_LanguageSkills WHERE StuID = %s ORDER BY Language",
        (student_id,),
    )
    languages = cursor.fetchall() or []
    for lang in languages:
        language = lang.get("Language")
        level = lang.get("Level")
        if language:
            label = language if not level else f"{language}（{level}）"
            skills.append(label)
    return skills


def _fetch_history(cursor, preference_id, submitted_at, current_status):
    """獲取志願申請的歷史紀錄 (包含提交紀錄和廠商審核紀錄)"""
    history = []
    if submitted_at:
        history.append(
            {
                "timestamp": _format_datetime(submitted_at),
                "text": "學生提交志願申請",
                "type": "system",
            }
        )
    try:
        _ensure_history_table(cursor)
        cursor.execute(
            """
            SELECT action, comment, created_at
            FROM vendor_preference_history
            WHERE preference_id = %s
            ORDER BY created_at DESC
            """,
            (preference_id,),
        )
        rows = cursor.fetchall() or []
        for row in rows:
            action = row.get("action")
            action_text = ACTION_TEXT.get(action, "狀態更新")
            comment = row.get("comment") or ""
            text = action_text
            if comment:
                text = f"{action_text}：{comment}"
            history.append(
                {
                    "timestamp": _format_datetime(row.get("created_at")),
                    "text": text,
                    "type": "comment" if action == "comment" else "status",
                }
            )
    except Exception:
        # 若歷程表不存在或讀取失敗，忽略錯誤並僅回傳提交紀錄
        pass

    if current_status in STATUS_LABELS and current_status != "pending":
        history.append(
            {
                # 使用當前時間作為狀態更新時間，除非有更準確的欄位
                "timestamp": _format_datetime(datetime.now()),
                "text": f"目前狀態：{STATUS_LABELS[current_status]}",
                "type": "status",
            }
        )

    # 依時間由新到舊排序
    history.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return history


def _build_application_summary_row(row):
    """將志願申請的資料列轉換為摘要字典"""
    submitted_at = row.get("submitted_at")
    skills = []
    # 假設 skill_tags 是從其他地方獲取並以 '||' 分隔
    if row.get("skill_tags"):
        skills = row["skill_tags"].split("||")
    
    # 加入履歷下載連結
    resume_id = row.get("resume_id")
    resume_url = None
    if resume_id:
        resume_url = f"/api/download_resume/{resume_id}"
    
    return {
        "id": str(row.get("id")),
        "student_id": row.get("student_id"),
        "name": row.get("student_name"),
        "student_number": row.get("student_number"),
        "student_email": row.get("student_email") or "",
        "avatar": row.get("photo_path") or DEFAULT_AVATAR,
        "status": row.get("status"),
        "status_label": STATUS_LABELS.get(row.get("status"), row.get("status") or "—"),
        "position_label": row.get("job_title") or row.get("job_title_db") or "—",
        "position_key": row.get("job_id"),
        "company_id": row.get("company_id"),
        "company_name": row.get("company_name"),
        "school_label": row.get("school_label") or "—",
        "school_key": row.get("class_id"),
        "applied_date": _format_datetime(submitted_at),
        "skills": [skill for skill in skills if skill],
        "summary": row.get("autobiography") or "",
        "interview_scheduled": bool(row.get("has_relation")),
        "resume_id": resume_id,
        "resume_url": resume_url,
    }


def _fetch_application_detail(cursor, preference_id):
    """獲取單一志願申請的詳細資料"""
    cursor.execute(
        """
        SELECT
            sp.id, sp.status, sp.preference_order, sp.submitted_at,
            sp.student_id, sp.company_id, sp.job_id, sp.job_title,
            ic.company_name, ic.contact_person, ic.contact_email, ic.contact_phone,
            ij.title AS job_title_db,
            u.name AS student_name, u.username AS student_number, u.email AS student_email,
            c.id AS class_id, c.name AS class_name, c.department,
            si.Phone AS student_phone, si.Autobiography AS autobiography,
            si.PhotoPath AS photo_path, si.Email AS info_email, si.Address AS student_address,
            EXISTS (
                SELECT 1
                FROM teacher_student_relations tsr
                WHERE tsr.student_id = sp.student_id
            ) AS has_relation
        FROM student_preferences sp
        JOIN internship_companies ic ON sp.company_id = ic.id
        JOIN users u ON sp.student_id = u.id
        LEFT JOIN classes c ON u.class_id = c.id
        LEFT JOIN Student_Info si ON si.StuID = u.id
        LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
        WHERE sp.id = %s
        """,
        (preference_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    # 獲取最新履歷、技能標籤、歷史紀錄
    resume = _fetch_latest_resume(cursor, row["student_id"])
    skills = _fetch_skill_tags(cursor, row["student_id"])
    history = _fetch_history(
        cursor,
        preference_id,
        row.get("submitted_at"),
        row.get("status"),
    )

    portfolio = []
    if resume and resume.get("id"):
        portfolio.append(
            {
                "label": resume.get("original_filename") or "最新履歷檔案",
                "url": f"/api/download_resume/{resume['id']}",
            }
        )

    school_parts = [part for part in [row.get("class_name"), row.get("department")] if part]
    school_label = " ".join(school_parts) if school_parts else None

    detail = {
        "id": str(row.get("id")),
        "student_id": row.get("student_id"),
        "name": row.get("student_name"),
        "student_number": row.get("student_number"),
        "student_email": row.get("student_email") or row.get("info_email"),
        "student_phone": row.get("student_phone"),
        "student_address": row.get("student_address"),
        "avatar": row.get("photo_path") or DEFAULT_AVATAR,
        "status": row.get("status"),
        "status_label": STATUS_LABELS.get(row.get("status"), row.get("status") or "—"),
        "position_label": row.get("job_title") or row.get("job_title_db") or "—",
        "position_key": row.get("job_id"),
        "company_id": row.get("company_id"),
        "company_name": row.get("company_name"),
        "applied_date": _format_datetime(row.get("submitted_at")),
        "school_label": school_label or "—",
        "start_date": None,
        "summary": row.get("autobiography") or "",
        "skills": skills,
        "portfolio": portfolio,
        "history": history,
        "interview_scheduled": bool(row.get("has_relation")),
        "resume": resume,
    }
    return detail


def _get_application_access(cursor, preference_id, vendor_id):
    """
    獲取廠商有權限訪問的申請。
    權限邏輯：通過指導老師（teacher_name）關聯到公司。
    """
    # 獲取廠商的 teacher_name
    cursor.execute("SELECT teacher_name FROM users WHERE id = %s", (vendor_id,))
    vendor_row = cursor.fetchone()
    if not vendor_row or not vendor_row.get("teacher_name"):
        return None
    
    teacher_name = vendor_row.get("teacher_name").strip()
    if not teacher_name:
        return None
    
    # 找到指導老師的 ID
    cursor.execute("SELECT id FROM users WHERE name = %s AND role IN ('teacher', 'director')", (teacher_name,))
    teacher_row = cursor.fetchone()
    if not teacher_row:
        return None
    
    teacher_id = teacher_row["id"]
    
    cursor.execute(
        """
        SELECT
            sp.id, sp.student_id, sp.company_id, sp.status, ic.company_name
        FROM student_preferences sp
        JOIN internship_companies ic ON sp.company_id = ic.id
        WHERE sp.id = %s AND ic.advisor_user_id = %s
        """,
        (preference_id, teacher_id),
    )
    record = cursor.fetchone()
    return record


# --- 路由定義 ---

@vendor_bp.route("/vendor_review_resume")
def vendor_resume_review():
    """廠商履歷審核頁面路由（允許廠商和老師訪問）"""
    if "user_id" not in session:
        return render_template("auth/login.html")
    # 允許 vendor 和 teacher 角色訪問
    if session.get("role") not in ["vendor", "teacher", "ta"]:
        return render_template("auth/login.html")
    return render_template("resume/vendor_review_resume.html")


@vendor_bp.route("/vendor/api/resumes", methods=["GET"])
def get_vendor_resumes():
    """
    獲取廠商可以查看的已通過審核的學生履歷。
    邏輯：
    1. 老師已通過 (resumes.status = 'approved')。
    2. 履歷會自動進入廠商的學生履歷審核流程。
    3. 廠商介面狀態取決於 student_preferences.status（如果存在），否則為 pending。
    
    允許 vendor 和 teacher 角色訪問（老師可以查看廠商審核結果）。
    """
    if "user_id" not in session:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    user_role = session.get("role")
    if user_role not in ["vendor", "teacher", "ta"]:
        return jsonify({"success": False, "message": "未授權"}), 403

    status_filter = request.args.get("status", "").strip()
    company_filter = request.args.get("company_id", type=int)
    keyword_filter = request.args.get("keyword", "").strip()

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    # 如果是老師，需要根據 company_id 找到對應的廠商
    if user_role in ["teacher", "ta"]:
        if not company_filter:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "需要提供 company_id 參數"}), 400
        
        # 查找該公司對應的廠商 ID
        cursor.execute("""
            SELECT DISTINCT v.user_id
            FROM vendors v
            JOIN vendor_companies vc ON v.id = vc.vendor_id
            WHERE vc.company_id = %s
            LIMIT 1
        """, (company_filter,))
        vendor_result = cursor.fetchone()
        if not vendor_result:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該公司對應的廠商"}), 404
        vendor_id = vendor_result["user_id"]
    else:
        # 廠商直接使用自己的 ID
        vendor_id = session["user_id"]
    try:
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile:
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403

        # 只顯示該廠商自己的公司，不顯示所有公司
        company_ids = [c["id"] for c in companies] if companies else []
        if not company_ids:
            print(f"⚠️ 廠商 {vendor_id} 未關聯任何公司，返回空列表")
            return jsonify({
                "success": True,
                "resumes": [],
                "companies": [],
                "message": "您尚未關聯任何公司"
            })

        # 步驟 1: 獲取所有老師已通過的最新履歷
        # 這裡不進行公司/志願序的過濾，只找出所有老師通過的最新履歷
        # 如果廠商有關聯公司，可以進一步篩選；如果沒有，顯示所有已通過的履歷
        base_query = """
            SELECT
                r.id, r.user_id AS student_id, u.name AS student_name, u.username AS student_number,
                c.name AS class_name, c.department, r.original_filename, r.filepath,
                r.comment, r.note, r.created_at, r.reviewed_at, r.reviewed_by
            FROM resumes r
            JOIN users u ON r.user_id = u.id
            LEFT JOIN classes c ON u.class_id = c.id
            
            -- 只取最新一份已通過老師審核的履歷
            JOIN (
                SELECT user_id, MAX(created_at) AS max_created_at
                FROM resumes
                WHERE status = 'approved'
                GROUP BY user_id
            ) latest ON latest.user_id = r.user_id AND latest.max_created_at = r.created_at
            
            -- 這裡只篩選老師已通過的履歷 (r.status='approved')
            WHERE r.status = 'approved'
        """
        
        # 如果廠商有關聯公司，可以選擇只顯示對這些公司填寫志願序的學生
        # 但為了讓資料更完整，我們先顯示所有已通過的履歷
        
        # 步驟 2: 處理關鍵字篩選
        params = []
        where_clauses = []
        
        if keyword_filter:
            keyword = f"%{keyword_filter}%"
            where_clauses.append("(u.name LIKE %s OR u.username LIKE %s OR r.original_filename LIKE %s)")
            params.extend([keyword, keyword, keyword])

        if where_clauses:
            base_query += " AND " + " AND ".join(where_clauses)
            
        base_query += " ORDER BY r.created_at DESC"
        
        cursor.execute(base_query, tuple(params))
        latest_resumes = cursor.fetchall() or []

        # 步驟 3: 查詢學生對該廠商所屬公司填寫的志願序，並用來覆蓋狀態
        preferences_map = {}
        if company_ids:
            # 只查詢選擇了該廠商公司的學生志願序
            # 同時檢查是否有審核歷史記錄，如果狀態是 'approved' 但沒有審核記錄，則視為 'pending'
            preference_placeholders = ", ".join(["%s"] * len(company_ids))
            _ensure_history_table(cursor)  # 確保歷史表存在
            
            # 檢查 vendor_preference_history 表是否存在
            try:
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                    AND table_name = 'vendor_preference_history'
                """)
                history_table_exists = cursor.fetchone().get('count', 0) > 0
            except Exception:
                history_table_exists = False
            
            # 根據表是否存在選擇不同的查詢
            if history_table_exists:
                cursor.execute(f"""
                    SELECT 
                        sp.student_id, 
                        sp.id AS preference_id,
                        sp.company_id, 
                        sp.job_id,
                        sp.job_title,
                        ic.company_name,
                        COALESCE(ij.title, sp.job_title) AS job_title_display,
                        CASE 
                            WHEN sp.status = 'approved' AND NOT EXISTS (
                                SELECT 1 FROM vendor_preference_history vph 
                                WHERE vph.preference_id = sp.id AND vph.action = 'approve'
                            ) THEN 'pending'
                            WHEN EXISTS (
                                SELECT 1 FROM vendor_preference_history vph 
                                WHERE vph.preference_id = sp.id AND vph.action = 'approve'
                            ) THEN 'approved'
                            WHEN EXISTS (
                                SELECT 1 FROM vendor_preference_history vph 
                                WHERE vph.preference_id = sp.id AND vph.action = 'reject'
                            ) THEN 'rejected'
                            ELSE COALESCE(sp.status, 'pending')
                        END AS vendor_review_status
                    FROM student_preferences sp
                    JOIN internship_companies ic ON sp.company_id = ic.id
                    LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                    WHERE sp.company_id IN ({preference_placeholders})
                """, tuple(company_ids))
            else:
                # 如果歷史表不存在，使用簡化的查詢
                cursor.execute(f"""
                    SELECT 
                        sp.student_id, 
                        sp.id AS preference_id,
                        sp.company_id, 
                        sp.job_id,
                        sp.job_title,
                        ic.company_name,
                        COALESCE(ij.title, sp.job_title) AS job_title_display,
                        COALESCE(sp.status, 'pending') AS vendor_review_status
                    FROM student_preferences sp
                    JOIN internship_companies ic ON sp.company_id = ic.id
                    LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                    WHERE sp.company_id IN ({preference_placeholders})
                """, tuple(company_ids))
            
            # 使用字典儲存學生的志願申請，鍵為 student_id
            for pref in cursor.fetchall() or []:
                student_id = pref['student_id']
                if student_id not in preferences_map:
                    preferences_map[student_id] = []
                preferences_map[student_id].append(pref)
            
            print(f"📋 找到 {len(preferences_map)} 位學生選擇了該廠商的公司")
        else:
            # 如果沒有公司關聯，查詢所有志願序（用於顯示所有履歷，但這不是正常情況）
            print("⚠️ 廠商沒有關聯公司，顯示所有志願序")
            _ensure_history_table(cursor)  # 確保歷史表存在
            
            # 檢查 vendor_preference_history 表是否存在
            try:
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                    AND table_name = 'vendor_preference_history'
                """)
                history_table_exists = cursor.fetchone().get('count', 0) > 0
            except Exception:
                history_table_exists = False
            
            # 根據表是否存在選擇不同的查詢
            if history_table_exists:
                cursor.execute("""
                    SELECT 
                        sp.student_id, 
                        sp.id AS preference_id,
                        sp.company_id, 
                        sp.job_id,
                        sp.job_title,
                        ic.company_name,
                        COALESCE(ij.title, sp.job_title) AS job_title_display,
                        CASE 
                            WHEN sp.status = 'approved' AND NOT EXISTS (
                                SELECT 1 FROM vendor_preference_history vph 
                                WHERE vph.preference_id = sp.id AND vph.action = 'approve'
                            ) THEN 'pending'
                            WHEN EXISTS (
                                SELECT 1 FROM vendor_preference_history vph 
                                WHERE vph.preference_id = sp.id AND vph.action = 'approve'
                            ) THEN 'approved'
                            WHEN EXISTS (
                                SELECT 1 FROM vendor_preference_history vph 
                                WHERE vph.preference_id = sp.id AND vph.action = 'reject'
                            ) THEN 'rejected'
                            ELSE COALESCE(sp.status, 'pending')
                        END AS vendor_review_status
                    FROM student_preferences sp
                    JOIN internship_companies ic ON sp.company_id = ic.id
                    LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                """)
            else:
                # 如果歷史表不存在，使用簡化的查詢
                cursor.execute("""
                    SELECT 
                        sp.student_id, 
                        sp.id AS preference_id,
                        sp.company_id, 
                        sp.job_id,
                        sp.job_title,
                        ic.company_name,
                        COALESCE(ij.title, sp.job_title) AS job_title_display,
                        COALESCE(sp.status, 'pending') AS vendor_review_status
                    FROM student_preferences sp
                    JOIN internship_companies ic ON sp.company_id = ic.id
                    LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                """)
            for pref in cursor.fetchall() or []:
                student_id = pref['student_id']
                if student_id not in preferences_map:
                    preferences_map[student_id] = []
                preferences_map[student_id].append(pref)

        # 步驟 4: 整合資料並應用狀態與公司篩選
        # 重點：只顯示選擇了該廠商公司的學生履歷
        resumes = []
        for row in latest_resumes:
            student_id = row["student_id"]
            
            # 預設狀態：老師通過，廠商尚未審核 (或學生沒有填志願序)
            # 對於廠商來說，初始狀態應該是 'pending'（待審核）
            display_status = "pending" 
            company_id = None
            company_name = ""
            job_id = None
            job_title = ""
            preference_id = None
            
            # 檢查是否有對該廠商公司的志願序
            student_preferences = preferences_map.get(student_id, [])
            
            # 如果廠商有關聯公司，只顯示選擇了這些公司的學生
            if company_ids and not student_preferences:
                # 如果學生沒有選擇該廠商的任何公司，跳過此履歷
                continue
            
            # 篩選出學生對 *當前廠商* 的 *特定公司* 的志願
            filtered_preferences = []
            if company_filter:
                 # 如果有公司篩選，只看該公司的志願
                if isinstance(company_filter, str):
                    # 公司名稱篩選
                    filtered_preferences = [
                        p for p in student_preferences 
                        if p['company_name'] == company_filter
                    ]
                else:
                    # 公司 ID 篩選
                    filtered_preferences = [
                        p for p in student_preferences 
                        if p['company_id'] == company_filter
                    ]
            else:
                # 如果沒有公司篩選，看學生對 *任何* 相關公司的志願
                filtered_preferences = student_preferences
            
            # 如果廠商有關聯公司，必須有選擇該廠商公司的志願序才能顯示
            if company_ids and not filtered_preferences:
                # 如果學生沒有選擇該廠商的任何公司，跳過此履歷
                continue
            
            # 如果存在志願序，則使用志願序的狀態和公司資訊。
            if filtered_preferences:
                # 簡單地取第一個志願序的狀態作為展示狀態。
                pref_to_show = filtered_preferences[0]
                sp_status = pref_to_show.get('vendor_review_status')
                preference_id = pref_to_show.get("preference_id")
                
                # 調試信息：記錄原始狀態
                print(f"🔍 學生 {student_id} 的志願序狀態: {sp_status} (preference_id: {preference_id})")
                print(f"   從 SQL 查詢返回的 vendor_review_status: {sp_status}")
                
                # 如果狀態是 'approved'，檢查是否有審核歷史記錄
                if sp_status == 'approved' and preference_id:
                    _ensure_history_table(cursor)
                    cursor.execute("""
                        SELECT COUNT(*) as count, MAX(created_at) as last_approve_time
                        FROM vendor_preference_history 
                        WHERE preference_id = %s AND action = 'approve'
                    """, (preference_id,))
                    history_result = cursor.fetchone()
                    has_approve_history = history_result and history_result.get('count', 0) > 0
                    last_approve_time = history_result.get('last_approve_time') if history_result else None
                    
                    if not has_approve_history:
                        # 如果狀態是 'approved' 但沒有審核記錄，強制改為 'pending'
                        print(f"⚠️ 狀態為 'approved' 但沒有審核記錄，強制改為 'pending' (preference_id: {preference_id})")
                        sp_status = 'pending'
                        display_status = 'pending'
                    else:
                        # 有審核記錄，使用 'approved'
                        display_status = 'approved'
                        print(f"✅ 狀態為 'approved' 且有審核記錄，使用 'approved' (preference_id: {preference_id}, 最後審核時間: {last_approve_time})")
                else:
                    # 廠商視角狀態：如果狀態為 NULL、空值或不在 STATUS_LABELS 中，則使用 "pending"（待審核）
                    if sp_status and sp_status in STATUS_LABELS:
                        display_status = sp_status
                        print(f"✅ 使用志願序狀態: {display_status}")
                    else:
                        display_status = "pending"  # 預設為待審核
                        print(f"⚠️ 狀態無效或為空，使用預設狀態: {display_status}")
                company_id = pref_to_show.get("company_id")
                company_name = pref_to_show.get("company_name") or ""
                job_id = pref_to_show.get("job_id")
                job_title = pref_to_show.get("job_title_display") or pref_to_show.get("job_title") or ""
            elif company_ids:
                # 如果沒有志願序，但廠商有關聯的公司，顯示第一個公司名稱
                # 這種情況不應該出現（因為上面已經過濾掉了），但保留作為備用
                if companies and len(companies) > 0:
                    company_name = companies[0].get("company_name", "")

            # 狀態篩選：如果篩選器啟用，檢查是否匹配
            if status_filter:
                if status_filter == 'pending':
                    # pending 篩選匹配 'pending' 狀態
                    if display_status != 'pending':
                        continue # 不匹配，跳過
                elif display_status != status_filter:
                    continue # 不匹配，跳過
            
            # 公司篩選：如果前面已經根據 filtered_preferences 做了判斷
            # 這裡需要確保，如果進行了公司篩選 (company_filter)，那麼該履歷必須與之相關聯
            if company_filter:
                # 如果使用公司名稱篩選（前端可能傳遞公司名稱而非 ID）
                if isinstance(company_filter, str):
                    if company_name != company_filter:
                        continue
                elif company_id != company_filter:
                    continue
                
            # 獲取廠商留言（從 vendor_preference_history）
            vendor_comment = None
            if preference_id:
                try:
                    _ensure_history_table(cursor)
                    cursor.execute("""
                        SELECT comment 
                        FROM vendor_preference_history 
                        WHERE preference_id = %s 
                        ORDER BY created_at DESC 
                        LIMIT 1
                    """, (preference_id,))
                    vendor_comment_row = cursor.fetchone()
                    if vendor_comment_row and vendor_comment_row.get('comment'):
                        vendor_comment = vendor_comment_row.get('comment')
                except Exception:
                    pass  # 如果歷史表不存在或查詢失敗，忽略
            
            # 構建結果
            resume = {
                "id": row.get("id"),
                "student_id": row.get("student_id"),
                "name": row.get("student_name"),
                "username": row.get("student_number"),
                "className": row.get("class_name") or "",
                "department": row.get("department") or "",
                "original_filename": row.get("original_filename"),
                "filepath": row.get("filepath"),
                "status": display_status,  # 顯示基於 student_preferences 的狀態，如果沒有則為 pending
                "comment": vendor_comment or "", # 廠商的留言（優先），如果沒有則為空
                "vendor_comment": vendor_comment or "", # 明確標記為廠商留言
                "note": row.get("note") or "",
                "upload_time": _format_datetime(row.get("created_at")),
                "reviewed_at": _format_datetime(row.get("reviewed_at")),
                "company_name": company_name,
                "company_id": company_id,
                "job_id": job_id,
                "job_title": job_title,
                "preference_id": preference_id, # 用於廠商審核操作，如果沒有填寫志願序則為 None
            }
            resumes.append(resume)

        # 構建公司列表
        # 此時 companies 已經包含了所有已審核通過的公司（如果沒有關聯公司，已在前面查詢過）
        companies_payload = [
            {"id": c["id"], "name": c["company_name"]} 
            for c in companies
        ]
        
        # 從履歷中提取公司名稱，也加入列表（作為補充）
        company_names_from_resumes = set()
        for resume in resumes:
            if resume.get("company_name") and resume.get("company_name").strip():
                company_names_from_resumes.add(resume["company_name"].strip())
        
        # 將從履歷中提取的公司名稱也加入列表（如果不在現有列表中）
        for company_name in company_names_from_resumes:
            if not any(c["name"] == company_name for c in companies_payload):
                companies_payload.append({"id": None, "name": company_name})
        
        # 調試：輸出公司列表資訊
        print(f"📋 最終公司列表數量: {len(companies_payload)}")
        if companies_payload:
            print(f"📋 公司列表: {[c['name'] for c in companies_payload]}")
        else:
            print("⚠️ 警告：最終公司列表為空，可能資料庫中沒有任何已審核通過的公司")

        return jsonify({
            "success": True,
            "resumes": resumes,
            "companies": companies_payload
        })

    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/applications", methods=["GET"])
def list_applications():
    """獲取廠商可查看的志願申請列表"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"error": "未授權"}), 403

    vendor_id = session["user_id"]
    status_filter = request.args.get("status")
    position_filter = request.args.get("position")
    school_filter = request.args.get("school")
    keyword_filter = request.args.get("keyword")
    student_id_filter = request.args.get("student_id", type=int)

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        profile = _get_vendor_profile(cursor, vendor_id)
        if not profile:
            empty_summary = {"pending": 0, "approved": 0, "rejected": 0, "new_this_week": 0}
            return jsonify({"items": [], "summary": empty_summary})

        companies = _get_vendor_companies(cursor, vendor_id)
        if not companies:
            empty_summary = {"pending": 0, "approved": 0, "rejected": 0, "new_this_week": 0}
            return jsonify({"items": [], "summary": empty_summary})

        company_ids = [company["id"] for company in companies]
        placeholders = ", ".join(["%s"] * len(company_ids))
        params = company_ids[:]

        query = f"""
            SELECT
                sp.id, sp.status, sp.submitted_at, sp.student_id, sp.company_id,
                sp.job_id, sp.job_title, ic.company_name, ij.title AS job_title_db,
                u.name AS student_name, u.username AS student_number, u.email AS student_email,
                c.id AS class_id,
                CONCAT_WS(' ', c.name, c.department) AS school_label,
                si.Autobiography AS autobiography, si.PhotoPath AS photo_path,
                (
                    SELECT r.id
                    FROM resumes r
                    WHERE r.user_id = sp.student_id
                    ORDER BY r.created_at DESC
                    LIMIT 1
                ) AS resume_id,
                EXISTS (
                    SELECT 1
                    FROM teacher_student_relations tsr
                    WHERE tsr.student_id = sp.student_id
                ) AS has_relation
            FROM student_preferences sp
            JOIN users u ON sp.student_id = u.id
            JOIN internship_companies ic ON sp.company_id = ic.id
            LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
            LEFT JOIN classes c ON u.class_id = c.id
            LEFT JOIN Student_Info si ON si.StuID = u.id
            WHERE sp.company_id IN ({placeholders})
        """

        if status_filter:
            query += " AND sp.status = %s"
            params.append(status_filter)
        if position_filter:
            query += " AND sp.job_id = %s"
            params.append(position_filter)
        if school_filter:
            query += " AND c.id = %s"
            params.append(school_filter)
        if keyword_filter:
            keyword = f"%{keyword_filter.strip()}%"
            query += " AND (u.name LIKE %s OR u.username LIKE %s OR sp.job_title LIKE %s)"
            params.extend([keyword, keyword, keyword])
        
        if student_id_filter:
            query += " AND sp.student_id = %s"
            params.append(student_id_filter)

        query += " ORDER BY sp.submitted_at DESC"
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall() or []

        items = []
        counts = {"pending": 0, "approved": 0, "rejected": 0}
        new_this_week = 0
        now = datetime.utcnow()
        for row in rows:
            status = row.get("status")
            if status in counts:
                counts[status] += 1
            submitted_at = row.get("submitted_at")
            if submitted_at and isinstance(submitted_at, datetime):
                # 假設 submitted_at 已經是 UTC 格式
                if submitted_at >= now - timedelta(days=7):
                    new_this_week += 1
            items.append(_build_application_summary_row(row))

        summary = {
            "pending": counts["pending"],
            "approved": counts["approved"],
            "rejected": counts["rejected"],
            "new_this_week": new_this_week,
        }
        return jsonify({"items": items, "summary": summary})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"查詢失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/applications/<int:application_id>", methods=["GET"])
def retrieve_application(application_id):
    """獲取單一志願申請的詳細資料"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"error": "未授權"}), 403

    vendor_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        profile = _get_vendor_profile(cursor, vendor_id)
        if not profile:
            return jsonify({"error": "帳號資訊有誤"}), 403

        # 修正：移除 vendor_email 參數
        access = _get_application_access(cursor, application_id, vendor_id)
        if not access:
            return jsonify({"error": "未找到資料或無權限查看"}), 404

        detail = _fetch_application_detail(cursor, application_id)
        if not detail:
            return jsonify({"error": "找不到此履歷"}), 404
        return jsonify({"item": detail})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"查詢失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/positions/next_code", methods=["GET"])
def get_next_position_code():
    """獲取下一個職缺編號（前3碼：民國年度，後3碼：順序號碼）"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 獲取當前民國年度（前3碼）
        now = datetime.now()
        roc_year = now.year - 1911
        year_prefix = str(roc_year).zfill(3)
        
        # 計算該年度內創建的職缺數量（根據創建時間）
        # 計算該年度的起始和結束日期（西元年）
        gregorian_year_start = roc_year + 1911
        gregorian_year_end = gregorian_year_start + 1
        
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM internship_jobs 
            WHERE created_at >= %s AND created_at < %s
        """, (
            datetime(gregorian_year_start, 1, 1),
            datetime(gregorian_year_end, 1, 1)
        ))
        
        result = cursor.fetchone()
        count = result.get("count", 0) if result else 0
        
        # 下一個序號 = 該年度的職缺數量 + 1
        next_sequence = count + 1
        
        # 生成完整編號
        sequence_suffix = str(next_sequence).zfill(3)
        full_code = year_prefix + sequence_suffix
        
        return jsonify({
            "success": True,
            "code": full_code,
            "year": year_prefix,
            "sequence": next_sequence
        })
    except Exception as exc:
        traceback.print_exc()
        # 如果出錯，返回預設值
        now = datetime.now()
        roc_year = now.year - 1911
        year_prefix = str(roc_year).zfill(3)
        return jsonify({
            "success": True,
            "code": year_prefix + "001",
            "year": year_prefix,
            "sequence": 1
        })
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/positions", methods=["GET"])
def list_positions_for_vendor():
    """獲取廠商可查看的職缺列表"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    vendor_id = session["user_id"]
    company_filter = request.args.get("company_id", type=int)
    status_filter = (request.args.get("status") or "").strip().lower()
    keyword = (request.args.get("q") or "").strip()

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile:
            payload = {"success": True, "companies": [], "items": [], "stats": {"total": 0, "active": 0, "inactive": 0}}
            return jsonify(payload)

        company_ids = [c["id"] for c in companies]
        if not company_ids:
            payload = {"success": True, "companies": [], "items": [], "stats": {"total": 0, "active": 0, "inactive": 0}}
            return jsonify(payload)

        if company_filter and company_filter not in company_ids:
            return jsonify({"success": False, "message": "無權限查看此公司"}), 403

        # 基礎權限判斷：屬於廠商公司範圍 AND (廠商建立 OR 老師建立)
        where_clauses = [
            f"ij.company_id IN ({', '.join(['%s'] * len(company_ids))})",
            "(ij.created_by_vendor_id = %s OR ij.created_by_vendor_id IS NULL)"
        ]
        params = company_ids[:]
        params.append(vendor_id)

        # 篩選條件
        if company_filter:
            where_clauses.append("ij.company_id = %s")
            params.append(company_filter)

        if status_filter in {"active", "inactive"}:
            where_clauses.append("ij.is_active = %s")
            params.append(1 if status_filter == "active" else 0)
        elif status_filter and status_filter not in {"all", ""}:
            return jsonify({"success": False, "message": "狀態參數錯誤"}), 400

        if keyword:
            like = f"%{keyword}%"
            where_clauses.append("(ij.title LIKE %s OR ij.description LIKE %s OR ij.remark LIKE %s)")
            params.extend([like, like, like])

        query = f"""
            SELECT
                ij.id, ij.company_id, ic.company_name, ij.title, ij.slots, ij.description,
                ij.period, ij.work_time, ij.salary, ij.remark, ij.is_active,
                ij.created_by_vendor_id
            FROM internship_jobs ij
            JOIN internship_companies ic ON ij.company_id = ic.id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY ij.is_active DESC, ij.id DESC
        """
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall() or []
        items = []
        for row in rows:
            job = _serialize_job(row)
            if job:
                # 標記是否為廠商建立的職缺
                job["is_created_by_vendor"] = row.get("created_by_vendor_id") == vendor_id
            items.append(job)

        stats = {
            "total": len(items),
            "active": sum(1 for item in items if item["is_active"]),
            "inactive": sum(1 for item in items if not item["is_active"]),
        }
        companies_payload = [{"id": c["id"], "name": c["company_name"], "advisor_user_id": c.get("advisor_user_id")} for c in companies]
        return jsonify({"success": True, "companies": companies_payload, "items": items, "stats": stats})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"載入失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/positions", methods=["POST"])
def create_position_for_vendor():
    """廠商新增職缺"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    company_id_raw = data.get("company_id")
    slots_raw = data.get("slots")

    if not company_id_raw:
        return jsonify({"success": False, "message": "請選擇公司"}), 400
    if not title:
        return jsonify({"success": False, "message": "請填寫職缺名稱"}), 400

    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "公司參數錯誤"}), 400

    try:
        slots = int(slots_raw)
        if slots <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "名額必須為正整數"}), 400

    description = (data.get("description") or "").strip()
    period = (data.get("period") or "").strip()
    work_time = (data.get("work_time") or "").strip()
    remark = (data.get("remark") or "").strip()
    salary_value = data.get("salary")
    salary = None
    if salary_value not in (None, "", "null"):
        salary = str(salary_value).strip() if salary_value else None

    is_active = True
    if "is_active" in data:
        try:
            is_active = _to_bool(data.get("is_active"))
        except ValueError:
            return jsonify({"success": False, "message": "狀態參數錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        profile, companies, _ = _get_vendor_scope(cursor, session["user_id"])
        if not profile:
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403

        company_ids = {c["id"] for c in companies}
        if company_id not in company_ids:
            return jsonify({"success": False, "message": "無權限操作此公司"}), 403

        vendor_id = session["user_id"]
        cursor.execute(
            """
            INSERT INTO internship_jobs
                (company_id, title, slots, description, period, work_time, salary, remark, is_active, created_by_vendor_id)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                company_id,
                title,
                slots,
                description or None,
                period or None,
                work_time or None,
                salary,
                remark or None,
                1 if is_active else 0,
                vendor_id,
            ),
        )
        conn.commit()
        job_row = _fetch_job_for_vendor(cursor, cursor.lastrowid, session["user_id"])
        return jsonify({"success": True, "item": _serialize_job(job_row)})
    except Exception as exc:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "message": f"新增失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/positions/<int:job_id>", methods=["GET"])
def get_position_for_vendor(job_id):
    """取得單一職缺資料"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        profile, _, _ = _get_vendor_scope(cursor, session["user_id"])
        if not profile:
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403

        vendor_id = session["user_id"]
        job_row = _fetch_job_for_vendor(cursor, job_id, vendor_id, allow_teacher_created=True)
        if not job_row:
            return jsonify({"success": False, "message": "找不到職缺或無權限查看"}), 404

        job = _serialize_job(job_row)
        if job:
            job["is_created_by_vendor"] = job_row.get("created_by_vendor_id") == vendor_id
        return jsonify({"success": True, "item": job})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/positions/<int:job_id>", methods=["PUT"])
def update_position_for_vendor(job_id):
    """廠商更新職缺資料"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    slots_raw = data.get("slots")

    if not title:
        return jsonify({"success": False, "message": "請填寫職缺名稱"}), 400

    try:
        slots = int(slots_raw)
        if slots <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "名額必須為正整數"}), 400

    description = (data.get("description") or "").strip()
    period = (data.get("period") or "").strip()
    work_time = (data.get("work_time") or "").strip()
    remark = (data.get("remark") or "").strip()
    salary_value = data.get("salary")
    salary = None
    if salary_value not in (None, "", "null"):
        salary = str(salary_value).strip() if salary_value else None

    try:
        is_active = _to_bool(data.get("is_active", True))
    except ValueError:
        return jsonify({"success": False, "message": "狀態參數錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        profile, _, _ = _get_vendor_scope(cursor, session["user_id"])
        if not profile:
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403

        # 檢查權限
        job_row = _fetch_job_for_vendor(cursor, job_id, session["user_id"], allow_teacher_created=True)
        if not job_row:
            return jsonify({"success": False, "message": "找不到職缺或無權限編輯"}), 404

        cursor.execute(
            """
            UPDATE internship_jobs
            SET title = %s,
                slots = %s,
                description = %s,
                period = %s,
                work_time = %s,
                salary = %s,
                remark = %s,
                is_active = %s
            WHERE id = %s
            """,
            (
                title,
                slots,
                description or None,
                period or None,
                work_time or None,
                salary,
                remark or None,
                1 if is_active else 0,
                job_id,
            ),
        )
        conn.commit()
        updated = _fetch_job_for_vendor(cursor, job_id, session["user_id"])
        return jsonify({"success": True, "item": _serialize_job(updated)})
    except Exception as exc:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "message": f"更新失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/positions/<int:job_id>/status", methods=["PATCH"])
def toggle_position_status(job_id):
    """切換職缺的啟用狀態"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json(silent=True) or {}
    if "is_active" not in data:
        return jsonify({"success": False, "message": "缺少狀態參數"}), 400
    try:
        desired = _to_bool(data.get("is_active"))
    except ValueError:
        return jsonify({"success": False, "message": "狀態參數錯誤"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        profile, _, _ = _get_vendor_scope(cursor, session["user_id"])
        if not profile:
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403

        # 檢查權限
        job_row = _fetch_job_for_vendor(cursor, job_id, session["user_id"], allow_teacher_created=True)
        if not job_row:
            return jsonify({"success": False, "message": "找不到職缺或無權限操作"}), 404

        cursor.execute(
            "UPDATE internship_jobs SET is_active = %s WHERE id = %s",
            (1 if desired else 0, job_id),
        )
        conn.commit()
        updated = _fetch_job_for_vendor(cursor, job_id, session["user_id"], allow_teacher_created=True)
        return jsonify({"success": True, "item": _serialize_job(updated)})
    except Exception as exc:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "message": f"更新狀態失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/positions/<int:job_id>", methods=["DELETE"])
def delete_position_for_vendor(job_id):
    """刪除職缺"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        profile, _, _ = _get_vendor_scope(cursor, session["user_id"])
        if not profile:
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403

        # 檢查權限
        job_row = _fetch_job_for_vendor(cursor, job_id, session["user_id"], allow_teacher_created=True)
        if not job_row:
            return jsonify({"success": False, "message": "找不到職缺或無權限刪除"}), 404

        # 先移除學生志願序中引用該職缺的紀錄，避免 FK 阻擋刪除
        cursor.execute("DELETE FROM student_preferences WHERE job_id = %s", (job_id,))

        cursor.execute("DELETE FROM internship_jobs WHERE id = %s", (job_id,))
        conn.commit()
        return jsonify({"success": True, "message": "職缺已刪除"})
    except Exception as exc:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "message": f"刪除失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


def _record_admission_and_bind_relation(cursor, student_id, company_id, job_id=None, preference_order=None):
    """
    記錄錄取結果並自動綁定公司 ↔ 指導老師 ↔ 學生關係
    優先採用學生第一志願（preference_order = 1）
    """
    try:
        # 1. 驗證學生和公司是否存在
        cursor.execute("SELECT id, name, username FROM users WHERE id = %s AND role = 'student'", (student_id,))
        student = cursor.fetchone()
        if not student:
            return {"success": False, "message": "找不到該學生"}
        
        cursor.execute("SELECT id, company_name, advisor_user_id FROM internship_companies WHERE id = %s", (company_id,))
        company = cursor.fetchone()
        if not company:
            return {"success": False, "message": "找不到該公司"}
        
        # 2. 獲取指導老師ID（從公司的 advisor_user_id）
        advisor_user_id = company.get('advisor_user_id')
        if not advisor_user_id:
            return {"success": False, "message": "該公司尚未指派指導老師"}
        
        # 驗證指導老師是否存在
        cursor.execute("SELECT id, name FROM users WHERE id = %s AND role IN ('teacher', 'director')", (advisor_user_id,))
        advisor = cursor.fetchone()
        if not advisor:
            return {"success": False, "message": "找不到該指導老師"}
        
        # 3. 優先採用學生第一志願（preference_order = 1）
        # 如果當前錄取的不是第一志願，查找學生的第一志願
        if preference_order != 1:
            cursor.execute("""
                SELECT id, company_id, job_id, preference_order, status
                FROM student_preferences
                WHERE student_id = %s AND preference_order = 1
                ORDER BY submitted_at DESC
                LIMIT 1
            """, (student_id,))
            first_preference = cursor.fetchone()
            
            if first_preference and first_preference.get('status') != 'approved':
                # 使用第一志願的公司和職缺（僅當第一志願尚未被錄取時）
                first_company_id = first_preference['company_id']
                first_job_id = first_preference.get('job_id')
                
                # 重新獲取第一志願的公司資訊
                cursor.execute("SELECT id, company_name, advisor_user_id FROM internship_companies WHERE id = %s", (first_company_id,))
                first_company = cursor.fetchone()
                
                if first_company and first_company.get('advisor_user_id'):
                    # 如果第一志願的公司有指導老師，使用第一志願
                    company_id = first_company_id
                    job_id = first_job_id
                    preference_order = 1
                    company = first_company
                    advisor_user_id = first_company.get('advisor_user_id')
                    cursor.execute("SELECT id, name FROM users WHERE id = %s AND role IN ('teacher', 'director')", (advisor_user_id,))
                    advisor = cursor.fetchone()
        
        # 4. 設置學期代碼為 1132（固定值）
        semester_code = '1132'
        
        # 5. 檢查是否已經存在該關係（避免重複）
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
            # 6. 創建師生關係記錄
            cursor.execute("""
                INSERT INTO teacher_student_relations 
                (teacher_id, student_id, semester, role, created_at)
                VALUES (%s, %s, %s, '指導老師', CURDATE())
            """, (advisor_user_id, student_id, semester_code))
        
        # 7. 更新學生的第一志願狀態為 approved（如果 preference_order = 1 且尚未被錄取）
        if preference_order == 1:
            cursor.execute("""
                UPDATE student_preferences
                SET status = 'approved'
                WHERE student_id = %s AND preference_order = 1 AND status != 'approved'
            """, (student_id,))
        
        return {
            "success": True,
            "message": f"錄取結果已記錄，已自動綁定指導老師 {advisor['name']} 與學生 {student['name']}",
            "teacher_id": advisor_user_id,
            "teacher_name": advisor['name'],
            "student_id": student_id,
            "student_name": student['name'],
            "company_id": company_id,
            "company_name": company['company_name'],
            "preference_order": preference_order
        }
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "message": f"記錄錄取結果失敗: {str(e)}"}


def _handle_status_update(application_id, action):
    """處理志願申請狀態的通用更新函數"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"error": "未授權"}), 403

    vendor_id = session["user_id"]
    payload = request.get_json(silent=True) or {}
    comment = (payload.get("comment") or "").strip()

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        profile = _get_vendor_profile(cursor, vendor_id)
        if not profile:
            return jsonify({"error": "帳號資訊有誤"}), 403

        # 修正：移除 vendor_email 參數
        access = _get_application_access(cursor, application_id, vendor_id)
        if not access:
            return jsonify({"error": "找不到此申請或無權限操作"}), 404

        _ensure_history_table(cursor)

        status_map = {
            "approve": "approved",
            "reject": "rejected",
            "reopen": "pending",
        }

        if action == "comment" and not comment:
            return jsonify({"error": "請輸入備註內容"}), 400

        if action in status_map:
            new_status = status_map[action]
            
            # 如果是錄取操作，先獲取申請詳情（包含 preference_order）
            preference_order = None
            job_id = None
            if action == "approve":
                # 獲取申請詳情以獲取 preference_order 和 job_id
                cursor.execute("""
                    SELECT preference_order, job_id, company_id
                    FROM student_preferences
                    WHERE id = %s
                """, (application_id,))
                pref_info = cursor.fetchone()
                if pref_info:
                    preference_order = pref_info.get('preference_order')
                    job_id = pref_info.get('job_id')
                    company_id = pref_info.get('company_id')
            
            cursor.execute(
                "UPDATE student_preferences SET status = %s WHERE id = %s",
                (new_status, application_id),
            )
            
            # 如果是錄取操作，自動記錄錄取結果並綁定關係
            if action == "approve":
                admission_result = _record_admission_and_bind_relation(
                    cursor,
                    access["student_id"],
                    company_id,
                    job_id,
                    preference_order
                )
                if not admission_result.get("success"):
                    # 記錄警告但不阻止錄取操作
                    print(f"⚠️ 錄取結果記錄失敗: {admission_result.get('message')}")
            
            # 發送通知
            title = "履歷審核結果"
            message = f"您的履歷申請已被更新為「{STATUS_LABELS.get(new_status, new_status)}」。"
            if comment:
                message = f"{message}\n\n廠商備註：{comment}"
            _notify_student(cursor, access["student_id"], title, message)
        elif action == "comment":
            # 僅加入備註，不改變狀態、也不推播通知
            pass
        else:
            return jsonify({"error": "未知的操作"}), 400

        # 記錄歷史
        _record_history(cursor, application_id, vendor_id, action, comment or None)
        conn.commit()

        # 返回最新資料
        detail = _fetch_application_detail(cursor, application_id)
        if not detail:
            return jsonify({"error": "更新成功但無法重新載入資料"}), 200
        return jsonify({"item": detail})
    except Exception as exc:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"error": f"操作失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/applications/<int:application_id>/approve", methods=["POST"])
def approve_application(application_id):
    """廠商通過志願申請"""
    return _handle_status_update(application_id, "approve")


@vendor_bp.route("/vendor/api/applications/<int:application_id>/reject", methods=["POST"])
def reject_application(application_id):
    """廠商退回志願申請"""
    return _handle_status_update(application_id, "reject")


@vendor_bp.route("/vendor/api/applications/<int:application_id>/reopen", methods=["POST"])
def reopen_application(application_id):
    """廠商重啟志願申請 (狀態設為待審核)"""
    return _handle_status_update(application_id, "reopen")


@vendor_bp.route("/vendor/api/applications/<int:application_id>/comment", methods=["POST"])
def comment_application(application_id):
    """廠商對志願申請新增備註"""
    return _handle_status_update(application_id, "comment")


@vendor_bp.route("/publish_announcements")
def publish_announcements_page():
    """廠商發布公告頁面"""
    if "user_id" not in session or session.get("role") != "vendor":
        return render_template("auth/login.html")
    return render_template("user_shared/publish_announcements.html")


@vendor_bp.route("/reviews_resumes_notifications")
def reviews_resumes_notifications_page():
    """廠商查看履歷與通知頁面"""
    if "user_id" not in session or session.get("role") != "vendor":
        return render_template("auth/login.html")
    return render_template("user_shared/reviews_resumes_notifications.html")


@vendor_bp.route("/vendor/api/announcement_history", methods=["GET"])
def get_announcement_history():
    """獲取廠商發布的公告歷史"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    vendor_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 獲取廠商關聯的公司
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile:
            return jsonify({"success": True, "announcements": []})

        company_ids = [c["id"] for c in companies] if companies else []
        
        # 從通知記錄中獲取廠商發布的公告（只顯示公告，排除面試通知、錄取通知等）
        if company_ids:
            placeholders = ", ".join(["%s"] * len(company_ids))
            # 查詢類別為 "announcement" 的記錄，或標題中包含「公告」的記錄（兼容舊數據）
            cursor.execute(f"""
                SELECT 
                    n.title,
                    n.message AS content,
                    n.created_at,
                    COUNT(DISTINCT n.user_id) AS recipient_count
                FROM notifications n
                WHERE (n.category = 'announcement' OR (n.category = 'company' AND n.title LIKE '%公告%'))
                  AND n.title NOT LIKE '%面試通知%'
                  AND n.title NOT LIKE '%錄取通知%'
                  AND EXISTS (
                      SELECT 1 
                      FROM student_preferences sp 
                      WHERE sp.student_id = n.user_id 
                        AND sp.company_id IN ({placeholders})
                  )
                GROUP BY n.title, n.message, n.created_at
                ORDER BY n.created_at DESC
                LIMIT 50
            """, tuple(company_ids))
        else:
            # 如果沒有關聯公司，返回空列表
            announcements = []
            return jsonify({
                "success": True,
                "announcements": []
            })

        announcements = cursor.fetchall()
        
        # 格式化日期
        for ann in announcements:
            if ann.get('created_at'):
                if isinstance(ann['created_at'], datetime):
                    ann['created_at'] = ann['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                else:
                    ann['created_at'] = str(ann['created_at'])

        return jsonify({
            "success": True,
            "announcements": announcements
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"獲取公告歷史失敗：{str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@vendor_bp.route("/vendor/api/publish_announcement", methods=["POST"])
def publish_announcement():
    """廠商發布公告給相關學生"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    job_id = data.get("job_id")  # 可選，指定特定職缺
    company_id = data.get("company_id")  # 可選，指定特定公司（向後兼容）
    
    # 調試日誌
    print(f"📢 發布公告請求 - vendor_id: {session.get('user_id')}, title: {title[:50]}, job_id: {job_id}, company_id: {company_id}")
    
    # 處理 job_id
    if job_id:
        try:
            job_id = int(job_id)
        except (ValueError, TypeError):
            print(f"⚠️ job_id 轉換失敗: {job_id}")
            job_id = None
    
    # 處理 company_id（向後兼容）
    if company_id:
        try:
            company_id = int(company_id)
        except (ValueError, TypeError):
            print(f"⚠️ company_id 轉換失敗: {company_id}")
            company_id = None

    if not title:
        print("❌ 錯誤：標題為空")
        return jsonify({"success": False, "message": "標題不可為空"}), 400
    if not content:
        print("❌ 錯誤：內容為空")
        return jsonify({"success": False, "message": "內容不可為空"}), 400

    vendor_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 獲取廠商關聯的公司
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile:
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403

        if not companies:
            print("❌ 錯誤：廠商未關聯任何公司")
            return jsonify({"success": False, "message": "您尚未關聯任何公司，無法發布公告"}), 400

        company_ids = [c["id"] for c in companies]
        print(f"📋 廠商關聯的公司 ID: {company_ids}")

        # 如果指定了 job_id，查詢選擇了該職缺的學生
        if job_id:
            print(f"🔍 查詢職缺 {job_id} 的學生...")
            # 驗證職缺是否屬於廠商關聯的公司
            placeholders = ", ".join(["%s"] * len(company_ids))
            cursor.execute(f"""
                SELECT ij.id, ij.company_id, ij.title, ic.company_name
                FROM internship_jobs ij
                JOIN internship_companies ic ON ij.company_id = ic.id
                WHERE ij.id = %s AND ij.company_id IN ({placeholders})
            """, (job_id, *company_ids))
            job_info = cursor.fetchone()
            
            if not job_info:
                return jsonify({"success": False, "message": "無權限向該職缺發布公告"}), 403
            
            # 查詢選擇了該職缺的學生（優先查詢當前學期，如果沒有則查詢所有）
            current_semester_id = get_current_semester_id(cursor)
            if current_semester_id:
                cursor.execute("""
                    SELECT DISTINCT u.id AS student_id
                    FROM student_preferences sp
                    JOIN users u ON sp.student_id = u.id
                    WHERE sp.job_id = %s
                      AND u.role = 'student'
                      AND (sp.semester_id = %s OR sp.semester_id IS NULL)
                """, (job_id, current_semester_id))
            else:
                cursor.execute("""
                    SELECT DISTINCT u.id AS student_id
                    FROM student_preferences sp
                    JOIN users u ON sp.student_id = u.id
                    WHERE sp.job_id = %s
                      AND u.role = 'student'
                """, (job_id,))
            
            students = cursor.fetchall()
            student_ids = [s["student_id"] for s in students]
            print(f"✅ 找到 {len(student_ids)} 位選擇了職缺 {job_id} 的學生")
            company_name = job_info["company_name"]
            job_title = job_info["title"]
            
        # 如果指定了 company_id（向後兼容），查詢選擇了該公司的學生
        elif company_id:
            print(f"🔍 查詢公司 {company_id} 的學生...")
            if company_id not in company_ids:
                return jsonify({"success": False, "message": "無權限向該公司發布公告"}), 403
            
            # 查詢選擇了該公司的學生（優先查詢當前學期，如果沒有則查詢所有）
            current_semester_id = get_current_semester_id(cursor)
            if current_semester_id:
                cursor.execute("""
                    SELECT DISTINCT u.id AS student_id
                    FROM student_preferences sp
                    JOIN users u ON sp.student_id = u.id
                    WHERE sp.company_id = %s
                      AND u.role = 'student'
                      AND (sp.semester_id = %s OR sp.semester_id IS NULL)
                """, (company_id, current_semester_id))
            else:
                cursor.execute("""
                    SELECT DISTINCT u.id AS student_id
                    FROM student_preferences sp
                    JOIN users u ON sp.student_id = u.id
                    WHERE sp.company_id = %s
                      AND u.role = 'student'
                """, (company_id,))
            
            students = cursor.fetchall()
            student_ids = [s["student_id"] for s in students]
            print(f"✅ 找到 {len(student_ids)} 位選擇了公司 {company_id} 的學生")
            
            # 獲取公司名稱
            for c in companies:
                if c["id"] == company_id:
                    company_name = c["company_name"]
                    break
            else:
                company_name = "公司"
            job_title = None
        else:
            # 向所有關聯公司的學生發布（優先查詢當前學期，如果沒有則查詢所有）
            print(f"🔍 查詢所有關聯公司的學生...")
            current_semester_id = get_current_semester_id(cursor)
            placeholders = ", ".join(["%s"] * len(company_ids))
            if current_semester_id:
                cursor.execute(f"""
                    SELECT DISTINCT u.id AS student_id
                    FROM student_preferences sp
                    JOIN users u ON sp.student_id = u.id
                    WHERE sp.company_id IN ({placeholders})
                      AND u.role = 'student'
                      AND (sp.semester_id = %s OR sp.semester_id IS NULL)
                """, (*company_ids, current_semester_id))
            else:
                cursor.execute(f"""
                    SELECT DISTINCT u.id AS student_id
                    FROM student_preferences sp
                    JOIN users u ON sp.student_id = u.id
                    WHERE sp.company_id IN ({placeholders})
                      AND u.role = 'student'
                """, tuple(company_ids))
            
            students = cursor.fetchall()
            student_ids = [s["student_id"] for s in students]
            print(f"✅ 找到 {len(student_ids)} 位選擇了所有關聯公司的學生")
            company_name = companies[0]["company_name"] if companies else "公司"
            job_title = None

        if not student_ids:
            print(f"❌ 錯誤：沒有找到任何學生")
            current_semester_id = get_current_semester_id(cursor)
            semester_info = f"（當前學期ID: {current_semester_id}）" if current_semester_id else "（未設定當前學期）"
            
            if job_id:
                error_msg = f"目前沒有學生選擇該職缺，無法發布公告。{semester_info} 請確認是否有學生已填寫志願序。"
            else:
                error_msg = f"目前沒有學生選擇您的公司，無法發布公告。{semester_info} 請確認是否有學生已填寫志願序。"
            
            return jsonify({"success": False, "message": error_msg}), 400

        # 構建通知標題
        if job_id and job_title:
            notification_title = f"【{company_name} - {job_title}】公告：{title}"
        else:
            notification_title = f"【{company_name}】公告：{title}"

        # 向所有相關學生發送通知
        notification_message = content
        link_url = "/notifications"  # 連結到通知中心，學生可以在那裡查看所有公告
        category = "announcement"  # 使用 "announcement" 類別，讓學生可以在通知中心通過「公告」類別篩選看到

        notification_count = 0
        for student_id in student_ids:
            _notify_student(cursor, student_id, notification_title, notification_message, link_url, category)
            notification_count += 1

        conn.commit()

        return jsonify({
            "success": True,
            "message": f"公告已成功發布給 {notification_count} 位學生",
            "notification_count": notification_count
        })

    except Exception as e:
        traceback.print_exc()
        if conn:
            conn.rollback()
        return jsonify({"success": False, "message": f"發布公告失敗：{str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@vendor_bp.route("/vendor/api/debug_info", methods=["GET"])
def get_vendor_debug_info():
    """獲取廠商調試資訊（用於檢查資料庫關聯）"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    try:
        vendor_id = session["user_id"]
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 1. 獲取廠商基本資訊
        cursor.execute("""
            SELECT id, username, name, email, role, teacher_name
            FROM users
            WHERE id = %s
        """, (vendor_id,))
        vendor_info = cursor.fetchone()
        
        debug_info = {
            "vendor_info": vendor_info,
            "teacher_info": None,
            "companies": [],
            "resumes_count": 0,
            "preferences_count": 0
        }
        
        # 2. 如果有 teacher_name，查找指導老師
        if vendor_info and vendor_info.get("teacher_name"):
            teacher_name = vendor_info.get("teacher_name").strip()
            cursor.execute("""
                SELECT id, name, email, role
                FROM users
                WHERE name = %s AND role IN ('teacher', 'director')
            """, (teacher_name,))
            debug_info["teacher_info"] = cursor.fetchone()
            
            if debug_info["teacher_info"]:
                teacher_id = debug_info["teacher_info"]["id"]
                
                # 3. 查找該指導老師的公司
                cursor.execute("""
                    SELECT id, company_name, status, advisor_user_id
                    FROM internship_companies
                    WHERE advisor_user_id = %s
                    ORDER BY company_name
                """, (teacher_id,))
                debug_info["companies"] = cursor.fetchall() or []
        
        # 4. 統計履歷數量
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM resumes
            WHERE status = 'approved'
        """)
        debug_info["resumes_count"] = cursor.fetchone().get("count", 0)
        
        # 5. 統計志願序數量（如果有公司）
        if debug_info["companies"]:
            company_ids = [c["id"] for c in debug_info["companies"]]
            placeholders = ", ".join(["%s"] * len(company_ids))
            cursor.execute(f"""
                SELECT COUNT(*) as count
                FROM student_preferences
                WHERE company_id IN ({placeholders})
            """, tuple(company_ids))
            debug_info["preferences_count"] = cursor.fetchone().get("count", 0)
        
        cursor.close()
        conn.close()
        
        return jsonify({"success": True, "debug_info": debug_info})
        
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗：{exc}"}), 500


@vendor_bp.route("/vendor/api/send_notification", methods=["POST"])
def send_notification():
    """廠商發送 Email 通知（面試或錄取）"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json(silent=True) or {}
    student_id = data.get("student_id")
    student_email = data.get("student_email")  # 前端可能提供，也可能為空
    student_name = data.get("student_name", "")
    notification_type = data.get("notification_type", "interview")
    content = data.get("content", "")
    company_name = data.get("company_name", "")  # 快速通知可能直接提供公司名稱

    # 允許快速通知模式：如果提供了 student_email 和 student_name，可以不需要 student_id
    if not student_id and not (student_email and student_name):
        return jsonify({"success": False, "message": "請提供學生ID，或同時提供學生Email和姓名"}), 400

    if not content and notification_type == "interview":
        return jsonify({"success": False, "message": "請輸入通知內容"}), 400

    try:
        from email_service import send_interview_email, send_admission_email
        
        vendor_id = session["user_id"]
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 從資料庫獲取學生資訊（如果有 student_id）
        if student_id:
            cursor.execute("""
                SELECT id, name, email, username
                FROM users
                WHERE id = %s AND role = 'student'
            """, (student_id,))
            student_info = cursor.fetchone()
            
            if not student_info:
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "找不到該學生資料"}), 404
            
            # 優先使用資料庫中的資訊，如果前端有提供則使用前端的（但以資料庫為準）
            student_email = student_info.get("email") or student_email
            student_name = student_info.get("name") or student_name
        
        if not student_email:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "學生Email資訊不完整，無法發送通知"}), 400
        
        if not student_name:
            student_name = "同學"
        
        # 獲取廠商和公司資訊
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403
        
        vendor_name = profile.get("name", "廠商")
        # 如果前端提供了公司名稱，優先使用；否則從資料庫獲取
        if not company_name:
            company_name = companies[0].get("company_name", "公司") if companies else "公司"
        
        # 根據通知類型發送不同的郵件
        if notification_type == "interview":
            email_success, email_message, log_id = send_interview_email(
                student_email, student_name, company_name, vendor_name, content
            )
        elif notification_type == "admission":
            email_success, email_message, log_id = send_admission_email(
                student_email, student_name, company_name
            )
        else:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "無效的通知類型"}), 400
        
        # 發送系統通知（如果有 student_id）
        if student_id:
            try:
                _notify_student(
                    cursor, 
                    student_id, 
                    f"【{company_name}】{'面試通知' if notification_type == 'interview' else '錄取通知'}",
                    content if content else f"您已收到來自 {company_name} 的{'面試通知' if notification_type == 'interview' else '錄取通知'}",
                    "/vendor_review_resume",
                    "company"
                )
                conn.commit()
            except Exception as notify_error:
                # 系統通知失敗不影響 Email 發送
                print(f"⚠️ 系統通知發送失敗（不影響 Email）：{notify_error}")
        
        cursor.close()
        conn.close()
        
        if email_success:
            return jsonify({
                "success": True, 
                "message": "通知發送成功",
                "email_log_id": log_id,
                "student_email": student_email,
                "student_name": student_name,
                "company_name": company_name
            })
        else:
            # email_message 已經包含完整的錯誤訊息，不需要再加「郵件發送失敗」
            return jsonify({"success": False, "message": email_message}), 500
            
    except Exception as exc:
        traceback.print_exc()
        if 'conn' in locals():
            try:
                cursor.close()
                conn.close()
            except:
                pass
        return jsonify({"success": False, "message": f"發送失敗：{exc}"}), 500


@vendor_bp.route("/vendor/api/email_logs", methods=["GET"])
def get_email_logs():
    """獲取廠商發送的 Email 記錄（用於測試和查看）"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    try:
        vendor_id = session["user_id"]
        limit = request.args.get("limit", type=int) or 20
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 查詢與該廠商相關的學生收到的郵件記錄
        # 通過 student_preferences 找到該廠商公司的學生
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile or not companies:
            cursor.close()
            conn.close()
            return jsonify({"success": True, "logs": []})
        
        company_ids = [c["id"] for c in companies]
        placeholders = ", ".join(["%s"] * len(company_ids))
        
        # 查詢 email_logs，關聯到該廠商公司的學生
        # 檢查 error_message 欄位是否存在
        try:
            cursor.execute("SHOW COLUMNS FROM email_logs LIKE 'error_message'")
            has_error_message = cursor.fetchone() is not None
        except Exception:
            has_error_message = False
        
        error_message_field = "el.error_message," if has_error_message else "NULL AS error_message,"
        
        query = f"""
            SELECT 
                el.id, el.recipient_email, el.recipient, el.subject, 
                el.status, el.sent_at, {error_message_field}
                u.id AS student_id, u.name AS student_name, u.username AS student_number
            FROM email_logs el
            LEFT JOIN users u ON el.related_user_id = u.id
            LEFT JOIN student_preferences sp ON sp.student_id = u.id
            WHERE (sp.company_id IN ({placeholders}) OR el.related_user_id IN (
                SELECT DISTINCT student_id 
                FROM student_preferences 
                WHERE company_id IN ({placeholders})
            ))
            ORDER BY el.sent_at DESC
            LIMIT %s
        """
        
        params = company_ids + company_ids + [limit]
        cursor.execute(query, tuple(params))
        logs = cursor.fetchall() or []
        
        # 格式化結果
        formatted_logs = []
        for log in logs:
            formatted_logs.append({
                "id": log.get("id"),
                "recipient_email": log.get("recipient_email") or log.get("recipient"),
                "subject": log.get("subject"),
                "status": log.get("status"),
                "sent_at": _format_datetime(log.get("sent_at")),
                "error_message": log.get("error_message"),
                "student_name": log.get("student_name"),
                "student_number": log.get("student_number")
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({"success": True, "logs": formatted_logs})
        
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗：{exc}"}), 500

@vendor_bp.route("/vendor/api/test_email", methods=["POST"])
def test_email():
    """測試 Email 發送功能"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json(silent=True) or {}
    recipient_email = data.get('recipient_email', '').strip()
    
    if not recipient_email:
        return jsonify({"success": False, "message": "請輸入收件人 Email"}), 400
    
    if '@' not in recipient_email:
        return jsonify({"success": False, "message": "Email 格式不正確"}), 400
    
    try:
        from email_service import send_email
        from datetime import datetime, timezone, timedelta
        
        # 發送測試郵件
        subject = "【智慧實習平台】Email 發送測試"
        content = f"""
親愛的測試使用者：

您好！

這是一封測試郵件，用來確認 Email 發送功能正常運作。

如果您收到這封郵件，表示系統的 Email 發送功能已成功設定並運作正常。

測試資訊：
- 收件人：{recipient_email}
- 發送時間：{datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")}
- 發送方式：SMTP

--

智慧實習平台
自動測試系統
"""
        
        success, message, log_id = send_email(
            recipient_email=recipient_email,
            subject=subject,
            content=content,
            related_user_id=session.get('user_id')
        )
        
        if success:
            return jsonify({
                "success": True,
                "message": "測試郵件發送成功！請檢查收件箱。",
                "log_id": log_id
            })
        else:
            return jsonify({
                "success": False,
                "message": f"郵件發送失敗：{message}",
                "log_id": log_id
            }), 500
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"發生錯誤：{str(e)}"}), 500