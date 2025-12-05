from datetime import datetime, timedelta
from decimal import Decimal
import traceback

from flask import Blueprint, jsonify, render_template, request, session

from config import get_db

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
    # 這裡使用 IF NOT EXISTS，並假設此函數在實際執行時會被調用，避免每次請求都執行 DDL
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_preference_history (
            id INT AUTO_INCREMENT PRIMARY KEY,
            preference_id INT NOT NULL,
            reviewer_id INT NOT NULL,
            action VARCHAR(20) NOT NULL,
            comment TEXT,
            created_at DATETIME NOT NULL,
            INDEX idx_vph_preference (preference_id),
            CONSTRAINT fk_vph_preference FOREIGN KEY (preference_id)
                REFERENCES student_preferences(id) ON DELETE CASCADE,
            CONSTRAINT fk_vph_reviewer FOREIGN KEY (reviewer_id)
                REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )
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
    **【優化點：移除硬編碼】**
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
    **【優化點：移除 vendor_email 參數，因為不再用於權限判斷】**
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


def _notify_student(cursor, student_id, title, message, link_url="/vendor/resume-review", category="resume"):
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
    # 證照
    cursor.execute(
        "SELECT CertName FROM Student_Certifications WHERE StuID = %s ORDER BY CertName",
        (student_id,),
    )
    certifications = cursor.fetchall() or []
    skills.extend([row["CertName"] for row in certifications if row.get("CertName")])

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
                  AND tsr.company_id = sp.company_id
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


def _get_application_access(cursor, preference_id, vendor_id, vendor_email):
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

@vendor_bp.route("/vendor/resume-review")
def vendor_resume_review():
    """廠商履歷審核頁面路由"""
    if "user_id" not in session or session.get("role") != "vendor":
        return render_template("auth/login.html")
    return render_template("resume/review_resume.html")


@vendor_bp.route("/vendor/api/resumes", methods=["GET"])
def get_vendor_resumes():
    """
    獲取廠商可以查看的已通過審核的學生履歷。
    邏輯：
    1. 老師已通過 (resumes.status = 'approved')。
    2. 學生對廠商所屬公司提交了志願申請 (student_preferences)。
    3. 廠商介面狀態取決於 student_preferences.status。
    **【優化點：修正查詢邏輯，基於 student_preferences.status 顯示廠商審核狀態】**
    """
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    vendor_id = session["user_id"]
    status_filter = request.args.get("status", "").strip()
    company_filter = request.args.get("company_id", type=int)
    keyword_filter = request.args.get("keyword", "").strip()

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile:
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403

        company_ids = [c["id"] for c in companies]
        if not company_ids:
            return jsonify({"success": True, "resumes": [], "companies": []})

        # 構建查詢：獲取老師已通過的最新履歷，並帶出學生在該公司的申請狀態 (sp.status)
        placeholders = ", ".join(["%s"] * len(company_ids))
        params = company_ids[:]

        query = f"""
            SELECT
                r.id, r.user_id AS student_id, u.name AS student_name, u.username AS student_number,
                c.name AS class_name, c.department, r.original_filename, r.filepath,
                r.comment, r.note, r.created_at, r.reviewed_at, r.reviewed_by,
                ic.company_name, ic.id AS company_id,
                sp.status AS vendor_review_status, sp.id AS preference_id
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
            
            -- 關聯學生的志願申請，只找目標公司的申請
            JOIN student_preferences sp ON sp.student_id = u.id AND sp.company_id IN ({placeholders})
            JOIN internship_companies ic ON sp.company_id = ic.id
            -- 這裡只篩選老師已通過的履歷 (r.status='approved')
            WHERE r.status = 'approved'
        """
        # 添加 company_ids 參數
        params.extend(company_ids)

        where_clauses = [f"sp.company_id IN ({placeholders})"]
        
        # 添加公司篩選
        if company_filter and company_filter in company_ids:
            where_clauses.append("ic.id = %s")
            params.append(company_filter)

        # 添加狀態篩選 (基於 student_preferences.status)
        if status_filter and status_filter in ['approved', 'rejected', 'pending']:
            where_clauses.append("sp.status = %s")
            params.append(status_filter)

        # 添加關鍵字搜尋
        if keyword_filter:
            keyword = f"%{keyword_filter}%"
            where_clauses.append("(u.name LIKE %s OR u.username LIKE %s OR r.original_filename LIKE %s)")
            params.extend([keyword, keyword, keyword])

        if where_clauses:
            query += " AND " + " AND ".join(where_clauses)

        query += " ORDER BY r.created_at DESC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall() or []

        resumes = []
        for row in rows:
            sp_status = row.get("vendor_review_status")
            
            # 廠商視角狀態：如果 student_preferences.status 是 pending/approved/rejected 則顯示其值
            # 預設為 pending (待審核)
            display_status = sp_status if sp_status in STATUS_LABELS else "pending"
            
            resume = {
                "id": row.get("id"),
                "student_id": row.get("student_id"),
                "name": row.get("student_name"),
                "username": row.get("student_number"),
                "className": row.get("class_name") or "",
                "department": row.get("department") or "",
                "original_filename": row.get("original_filename"),
                "filepath": row.get("filepath"),
                "status": display_status,  # 顯示基於 student_preferences 的狀態
                "comment": row.get("comment") or "",
                "note": row.get("note") or "",
                "upload_time": _format_datetime(row.get("created_at")),
                "reviewed_at": _format_datetime(row.get("reviewed_at")),
                "company_name": row.get("company_name"),
                "company_id": row.get("company_id"),
                "preference_id": row.get("preference_id"), # 用於廠商審核操作
            }
            resumes.append(resume)

        companies_payload = [
            {"id": c["id"], "name": c["company_name"]} 
            for c in companies
        ]

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
                u.name AS student_name, u.username AS student_number, c.id AS class_id,
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
                      AND tsr.company_id = sp.company_id
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

        access = _get_application_access(cursor, application_id, vendor_id, profile.get("email"))
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

        # 檢查廠商是否有權限操作此申請
        access = _get_application_access(cursor, application_id, vendor_id, profile.get("email"))
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
            cursor.execute(
                "UPDATE student_preferences SET status = %s WHERE id = %s",
                (new_status, application_id),
            )
            
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