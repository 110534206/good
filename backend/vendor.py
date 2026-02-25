from datetime import datetime, timedelta
from decimal import Decimal
import traceback

from flask import Blueprint, jsonify, render_template, request, session

from config import get_db
from semester import get_current_semester_id

vendor_bp = Blueprint('vendor', __name__)

# --- 常量定義 ---
STATUS_LABELS = {
    "uploaded": "待審核",  # 對應資料庫 enum，與 resume_applications.apply_status 一致
    "approved": "已通過",
    "rejected": "已退回",
}

# interview_status 欄位只用於存儲面試狀態
ACTION_TEXT = {
    "none": "未面試",
    "scheduled": "面試中",
    "finished": "已面試",
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


# 【已移除】vendor_preference_history 表已不再使用，改用 resume_applications 表
def _ensure_history_table(cursor):
    """已移除：vendor_preference_history 表不再使用"""
    pass


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
    邏輯：廠商通過指導老師（teacher_id）關聯到公司。
    分組規則：
    - vendor、vendorA 的指導老師是 teacherA，只能看到 advisor_user_id = teacherA_id 的公司
    - vendorB、vendorD 的指導老師是 directorB，只能看到 advisor_user_id = directorB_id 的公司
    """
    # 1. 獲取廠商的 teacher_id
    cursor.execute("SELECT teacher_id FROM users WHERE id = %s", (vendor_id,))
    vendor_row = cursor.fetchone()
    if not vendor_row or not vendor_row.get("teacher_id"):
        print(f"⚠️ 廠商 {vendor_id} 沒有設定 teacher_id")
        return []
    
    teacher_id = vendor_row.get("teacher_id")
    if not teacher_id:
        print(f"⚠️ 廠商 {vendor_id} 的 teacher_id 為空")
        return []
    
    # 2. 驗證該 ID 是否為有效的指導老師
    cursor.execute("SELECT id, name FROM users WHERE id = %s AND role IN ('teacher', 'director')", (teacher_id,))
    teacher_row = cursor.fetchone()
    if not teacher_row:
        print(f"⚠️ 找不到指導老師 ID {teacher_id} (廠商 {vendor_id})")
        return []
    
    teacher_name = teacher_row.get("name", "")
    print(f"✅ 廠商 {vendor_id} 的指導老師: {teacher_name} (ID: {teacher_id})")
    
    # 3. 找到該指導老師對接的公司（只回傳已審核通過的公司）
    # 根據 advisor_user_id 來過濾，確保只有該指導老師的公司才會被返回
    query = """
        SELECT id, company_name, contact_email, advisor_user_id
        FROM internship_companies
        WHERE advisor_user_id = %s AND status = 'approved'
        ORDER BY company_name
    """
    params = [teacher_id]
    
    cursor.execute(query, tuple(params))
    companies = cursor.fetchall() or []
    print(f"📋 廠商 {vendor_id} 找到 {len(companies)} 家公司 (指導老師 ID: {teacher_id})")
    
    return companies


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
    權限邏輯：通過指導老師（teacher_id）關聯到公司。
    """
    # 1. 獲取廠商的 teacher_id
    cursor.execute("SELECT teacher_id FROM users WHERE id = %s", (vendor_id,))
    vendor_row = cursor.fetchone()
    if not vendor_row or not vendor_row.get("teacher_id"):
        return None
    
    teacher_id = vendor_row.get("teacher_id")
    if not teacher_id:
        return None
    
    # 2. 驗證該 ID 是否為有效的指導老師
    cursor.execute("SELECT id FROM users WHERE id = %s AND role IN ('teacher', 'director')", (teacher_id,))
    teacher_row = cursor.fetchone()
    if not teacher_row:
        return None
    
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


def _record_history(cursor, preference_id, reviewer_id, action, comment, student_id=None):
    """記錄廠商對志願申請的審核或備註歷史（更新 resume_applications 表，並插入到 vendor_preference_history 表）"""
    # action 映射到 resume_applications.interview_status
    # 直接使用 resume_applications 的 enum 值：'scheduled', 'finished'
    status_map = {
        "in interview": "scheduled",  # 向後兼容舊的 action 值
        "done": "finished",  # 向後兼容舊的 action 值
        "scheduled": "scheduled",  # 新的 action 值
        "finished": "finished",  # 新的 action 值
    }
    
    if action not in status_map:
        return  # 只處理面試相關的操作
    
    # 獲取 job_id
    if preference_id:
        try:
            cursor.execute("SELECT job_id FROM student_preferences WHERE id = %s", (preference_id,))
            pref_row = cursor.fetchone()
            if not pref_row or not pref_row.get("job_id"):
                return  # 沒有 job_id，無法更新
            job_id = pref_row.get("job_id")
        except Exception:
            return  # 獲取失敗，無法更新
    
    # 更新 resume_applications 表
    try:
        new_status = status_map[action]
        cursor.execute("""
            UPDATE resume_applications
            SET interview_status = %s,
                company_comment = %s,
                updated_at = NOW()
            WHERE application_id = %s AND job_id = %s
        """, (new_status, comment, preference_id, job_id))
    except Exception as e:
        print(f"⚠️ 更新 resume_applications 失敗: {e}")
    
    # 同時插入到 vendor_preference_history 表（用於學生行事曆顯示）
    # 只有當 action 是 "in interview" 或 "scheduled" 時才插入（面試排程）
    if action in ["in interview", "scheduled"] and preference_id:
        try:
            # 檢查 vendor_preference_history 表是否存在
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                AND table_name = 'vendor_preference_history'
            """)
            table_exists = cursor.fetchone().get('count', 0) > 0
            
            if table_exists:
                # 插入記錄到 vendor_preference_history
                cursor.execute("""
                    INSERT INTO vendor_preference_history
                    (preference_id, reviewer_id, student_id, interview_status, comment, created_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (preference_id, reviewer_id, student_id, action, comment))
                print(f"✅ 已插入記錄到 vendor_preference_history: preference_id={preference_id}, student_id={student_id}, action={action}")
        except Exception as e:
            # 如果插入失敗，不影響主要流程，只記錄錯誤
            print(f"⚠️ 插入 vendor_preference_history 失敗（不影響主要流程）: {e}")


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
    # 從 resume_applications 表讀取面試歷史
    try:
        # 獲取 job_id
        cursor.execute("SELECT job_id FROM student_preferences WHERE id = %s", (preference_id,))
        pref_row = cursor.fetchone()
        if pref_row and pref_row.get("job_id"):
            job_id = pref_row.get("job_id")
            cursor.execute("""
                SELECT interview_status, company_comment, updated_at
                FROM resume_applications
                WHERE application_id = %s AND job_id = %s
            """, (preference_id, job_id))
            ra_row = cursor.fetchone()
            if ra_row:
                interview_status = ra_row.get("interview_status")
                comment = ra_row.get("company_comment") or ""
                # 映射狀態文字（使用 ACTION_TEXT）
                action_text = ACTION_TEXT.get(interview_status, "狀態更新")
                text = action_text
                if comment:
                    text = f"{action_text}：{comment}"
                history.append(
                    {
                        "timestamp": _format_datetime(ra_row.get("updated_at")),
                        "text": text,
                        "type": "status",
                    }
                )
    except Exception:
        # 若讀取失敗，忽略錯誤並僅回傳提交紀錄
        pass

    if current_status in STATUS_LABELS:
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
    權限邏輯：通過指導老師（teacher_id）關聯到公司。
    """
    # 獲取廠商的 teacher_id
    cursor.execute("SELECT teacher_id FROM users WHERE id = %s", (vendor_id,))
    vendor_row = cursor.fetchone()
    if not vendor_row or not vendor_row.get("teacher_id"):
        return None
    
    teacher_id = vendor_row.get("teacher_id")
    if not teacher_id:
        return None
    
    # 驗證該 ID 是否為有效的指導老師
    cursor.execute("SELECT id FROM users WHERE id = %s AND role IN ('teacher', 'director')", (teacher_id,))
    teacher_row = cursor.fetchone()
    if not teacher_row:
        return None
    
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


@vendor_bp.route("/vendor/api/companies/locations", methods=["GET"])
def get_company_locations():
    """獲取公司的地址列表（從 internship_companies 表的 location 欄位）"""
    conn = None
    cursor = None
    try:
        if "user_id" not in session:
            return jsonify({"success": False, "message": "請先登入"}), 403
        
        user_role = session.get("role")
        if user_role not in ["vendor", "teacher", "ta"]:
            return jsonify({"success": False, "message": "未授權"}), 403
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 如果是廠商，只獲取該廠商關聯的公司地址
        # 如果是老師/TA，獲取所有已審核通過的公司地址
        if user_role == "vendor":
            user_id = session.get("user_id")
            # 找到該廠商關聯的公司
            cursor.execute("""
                SELECT DISTINCT ic.location
                FROM internship_companies ic
                JOIN users u ON u.teacher_id = ic.advisor_user_id
                WHERE u.id = %s 
                AND ic.status = 'approved'
                AND ic.location IS NOT NULL
                AND ic.location != ''
                ORDER BY ic.location
            """, (user_id,))
        else:
            # 老師/TA 可以查看所有已審核通過的公司地址
            cursor.execute("""
                SELECT DISTINCT location
                FROM internship_companies
                WHERE status = 'approved'
                AND location IS NOT NULL
                AND location != ''
                ORDER BY location
            """)
        
        locations = cursor.fetchall()
        
        # 轉換為簡單的列表格式
        location_list = [{"value": loc["location"], "label": loc["location"]} for loc in locations]
        
        return jsonify({
            "success": True,
            "locations": location_list
        })
        
    except Exception as e:
        print(f"❌ 獲取公司地址失敗：{e}")
        traceback.print_exc()
        return jsonify({"success": False, "message": f"載入失敗：{str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@vendor_bp.route("/vendor/api/resumes", methods=["GET"])
def get_vendor_resumes():
    """
    獲取廠商可以查看的已通過審核的學生履歷。
    重要機制：必須等指導老師審核完後，才會給廠商學生的資料。
    
    邏輯：
    1. 只顯示已經被指導老師（role='teacher'）審核通過的履歷
    2. 必須同時滿足：resume_teacher.review_status = 'approved' 且審核者是 teacher 角色（新架構）
       或 reviewed_by 是 teacher 角色（舊架構）
    3. 履歷會自動進入廠商的學生履歷審核流程
    4. 廠商介面狀態優先從 resume_applications 表讀取，如果沒有則從 student_preferences 讀取
    
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
    cursor = conn.cursor(dictionary=True, buffered=True)
    
    # 如果是老師，需要根據 company_id 找到對應的廠商
    if user_role in ["teacher", "ta"]:
        if not company_filter:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "需要提供 company_id 參數"}), 400
        
        # 先驗證該公司是否屬於當前老師管理
        cursor.execute("""
            SELECT advisor_user_id 
            FROM internship_companies 
            WHERE id = %s AND status = 'approved'
        """, (company_filter,))
        company_result = cursor.fetchone()
        if not company_result:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該公司或公司未審核通過"}), 404
        
        advisor_user_id = company_result.get("advisor_user_id")
        # 如果公司沒有指導老師，或者指導老師不是當前用戶，拒絕訪問
        if advisor_user_id is None:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "該公司尚未指派指導老師，無法查看"}), 403
        if advisor_user_id != session["user_id"]:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": f"無權限查看此公司（公司指導老師 ID: {advisor_user_id}, 當前用戶 ID: {session['user_id']}）"}), 403
        
        # 查找該老師對應的廠商（通過 teacher_id 匹配）
        # 驗證指導老師是否存在
        cursor.execute("""
            SELECT id, name 
            FROM users 
            WHERE id = %s AND role IN ('teacher', 'director')
        """, (advisor_user_id,))
        teacher_result = cursor.fetchone()
        if not teacher_result:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到指導老師資料"}), 404
        
        # 找到所有該老師的廠商
        cursor.execute("""
            SELECT id 
            FROM users 
            WHERE role = 'vendor' AND teacher_id = %s
        """, (advisor_user_id,))
        vendor_results = cursor.fetchall()
        
        if not vendor_results:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該公司對應的廠商"}), 404
        
        # 檢查哪個廠商有關聯到這個公司
        vendor_id = None
        for vendor_row in vendor_results:
            test_vendor_id = vendor_row["id"]
            test_companies = _get_vendor_companies(cursor, test_vendor_id)
            # 檢查這個公司是否在該廠商的公司列表中
            if any(c["id"] == company_filter for c in test_companies):
                vendor_id = test_vendor_id
                print(f"✅ 找到對應的廠商 ID: {vendor_id} (公司 ID: {company_filter})")
                break
        
        if not vendor_id:
            # 如果找不到，使用第一個廠商（向後兼容）
            vendor_id = vendor_results[0]["id"]
            print(f"⚠️ 找不到完全匹配的廠商，使用第一個廠商 ID: {vendor_id}")
    else:
        # 廠商直接使用自己的 ID
        vendor_id = session["user_id"]
    try:
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile:
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403

        # 只顯示該廠商自己的公司，不顯示所有公司
        company_ids = [c["id"] for c in companies] if companies else []
        
        # 如果是老師訪問且有指定 company_filter，確保該公司包含在 company_ids 中
        if user_role in ["teacher", "ta"] and company_filter:
            if company_filter not in company_ids:
                # 驗證該公司是否屬於當前老師管理（之前已經驗證過）
                # 直接將 company_filter 加入 company_ids
                company_ids.append(company_filter)
                print(f"✅ 老師訪問：將公司 {company_filter} 加入 company_ids")
        
        if not company_ids:
            print(f"⚠️ 廠商 {vendor_id} 未關聯任何公司，返回空列表")
            return jsonify({
                "success": True,
                "resumes": [],
                "companies": [],
                "message": "您尚未關聯任何公司"
            })

        # 步驟 1: 獲取所有已通過指導老師審核的最新履歷
        # 重要：只顯示已經被指導老師（role='teacher'）審核通過的履歷
        # 必須等指導老師審核完後，才會給廠商學生的資料
        # 檢查 resume_teacher 表是否存在
        cursor.execute("SHOW TABLES LIKE 'resume_teacher'")
        resume_teacher_exists = cursor.fetchone() is not None
        
        if resume_teacher_exists:
            # 使用 resume_teacher 表查詢（新架構）
            base_query = """
                SELECT
                    r.id, r.user_id AS student_id, u.name AS student_name, u.username AS student_number,
                    c.name AS class_name, c.department, r.original_filename, r.filepath,
                    r.comment, r.note, r.created_at, r.reviewed_at, r.reviewed_by
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                INNER JOIN student_job_applications sja ON sja.resume_id = r.id AND sja.student_id = r.user_id
                INNER JOIN resume_teacher rt ON rt.application_id = sja.id
                INNER JOIN users reviewer ON rt.teacher_id = reviewer.id
                
                -- 只取最新一份已通過指導老師審核的履歷
                JOIN (
                    SELECT r2.user_id, MAX(r2.created_at) AS max_created_at
                    FROM resumes r2
                    INNER JOIN student_job_applications sja2 ON sja2.resume_id = r2.id AND sja2.student_id = r2.user_id
                    INNER JOIN resume_teacher rt2 ON rt2.application_id = sja2.id
                    WHERE rt2.review_status = 'approved'
                    GROUP BY r2.user_id
                ) latest ON latest.user_id = r.user_id AND latest.max_created_at = r.created_at
                
                -- 嚴格要求：只顯示已經被指導老師（role='teacher'）審核通過的履歷
                WHERE rt.review_status = 'approved'
                AND reviewer.role = 'teacher'
            """
        else:
            # 使用舊架構（如果 resume_teacher 表不存在，使用 reviewed_by 欄位）
            base_query = """
                SELECT
                    r.id, r.user_id AS student_id, u.name AS student_name, u.username AS student_number,
                    c.name AS class_name, c.department, r.original_filename, r.filepath,
                    r.comment, r.note, r.created_at, r.reviewed_at, r.reviewed_by
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                
                -- 只取最新一份已通過指導老師審核的履歷
                JOIN (
                    SELECT user_id, MAX(created_at) AS max_created_at
                    FROM resumes
                    WHERE reviewed_by IS NOT NULL
                    GROUP BY user_id
                ) latest ON latest.user_id = r.user_id AND latest.max_created_at = r.created_at
                
                -- 嚴格要求：只顯示已經被指導老師（role='teacher'）審核通過的履歷
                WHERE r.reviewed_by IS NOT NULL
                AND EXISTS (
                    SELECT 1 FROM users reviewer
                    WHERE reviewer.id = r.reviewed_by
                    AND reviewer.role = 'teacher'
                )
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
        
        # 調試信息：記錄查詢結果和詳細信息
        print(f"🔍 [DEBUG] 廠商履歷查詢結果：找到 {len(latest_resumes)} 筆履歷")
        if latest_resumes:
            if resume_teacher_exists:
                print(f"   ⚠️ 注意：這些履歷的 review_status 都是 'approved'，且審核者是 teacher 角色")
            else:
                print(f"   ⚠️ 注意：這些履歷的 reviewed_by 是 teacher 角色")
            print(f"   如果這些履歷不應該顯示，請檢查資料庫中這些履歷的審核狀態")
            for r in latest_resumes[:5]:  # 顯示前5筆
                resume_id = r.get('id')
                student_name = r.get('student_name')
                reviewed_by = r.get('reviewed_by')
                # 查詢該履歷的詳細審核信息
                try:
                    if resume_teacher_exists:
                        # 使用 resume_teacher 表查詢
                        cursor.execute("""
                            SELECT rt.review_status, rt.teacher_id, rt.reviewed_at
                            FROM resumes r
                            INNER JOIN student_job_applications sja ON sja.resume_id = r.id
                            INNER JOIN resume_teacher rt ON rt.application_id = sja.id
                            WHERE r.id = %s AND rt.review_status = 'approved'
                            LIMIT 1
                        """, (resume_id,))
                        resume_detail = cursor.fetchone()
                        # 確保結果被完全讀取（即使為 None）
                        if resume_detail:
                            teacher_status = resume_detail.get('review_status')
                            reviewed_by_id = resume_detail.get('teacher_id')
                            reviewed_at = resume_detail.get('reviewed_at')
                            # 檢查審核者角色
                            if reviewed_by_id:
                                cursor.execute("SELECT role, name FROM users WHERE id = %s", (reviewed_by_id,))
                                reviewer_info = cursor.fetchone()
                                # 確保結果被完全讀取
                                reviewer_role = reviewer_info.get('role') if reviewer_info else 'unknown'
                                reviewer_name = reviewer_info.get('name') if reviewer_info else 'unknown'
                                print(f"   - 履歷 ID: {resume_id}, 學生: {student_name}")
                                print(f"     review_status: {teacher_status}, teacher_id: {reviewed_by_id} ({reviewer_role}: {reviewer_name})")
                                print(f"     reviewed_at: {reviewed_at}")
                            else:
                                print(f"   - 履歷 ID: {resume_id}, 學生: {student_name}, teacher_id 為 NULL（不應該顯示）")
                    else:
                        # 使用舊架構（reviewed_by 欄位）
                        cursor.execute("""
                            SELECT reviewed_by, reviewed_at
                            FROM resumes
                            WHERE id = %s
                        """, (resume_id,))
                        resume_detail = cursor.fetchone()
                        # 確保結果被完全讀取（即使為 None）
                        if resume_detail:
                            reviewed_by_id = resume_detail.get('reviewed_by')
                            reviewed_at = resume_detail.get('reviewed_at')
                            # 檢查審核者角色
                            if reviewed_by_id:
                                cursor.execute("SELECT role, name FROM users WHERE id = %s", (reviewed_by_id,))
                                reviewer_info = cursor.fetchone()
                                # 確保結果被完全讀取
                                reviewer_role = reviewer_info.get('role') if reviewer_info else 'unknown'
                                reviewer_name = reviewer_info.get('name') if reviewer_info else 'unknown'
                                print(f"   - 履歷 ID: {resume_id}, 學生: {student_name}")
                                print(f"     reviewed_by: {reviewed_by_id} ({reviewer_role}: {reviewer_name})")
                                print(f"     reviewed_at: {reviewed_at}")
                            else:
                                print(f"   - 履歷 ID: {resume_id}, 學生: {student_name}, reviewed_by 為 NULL（不應該顯示）")
                except Exception as debug_exc:
                    # 如果調試代碼出錯，不影響主流程
                    print(f"   ⚠️ 調試查詢出錯: {debug_exc}")
                    continue
        else:
            if resume_teacher_exists:
                print(f"   ✅ 沒有找到符合條件的履歷（review_status = 'approved' 且審核者是 teacher）")
            else:
                print(f"   ✅ 沒有找到符合條件的履歷（reviewed_by 是 teacher）")
        
        # 確保所有未讀取的結果都被清空（防止 "Unread result found" 錯誤）
        # 通過執行一個簡單的查詢來清空任何未讀取的結果
        try:
            cursor.fetchall()  # 嘗試讀取所有剩餘的結果
        except:
            # 如果沒有更多結果，忽略錯誤
            pass

        # 步驟 3: 查詢學生對該廠商所屬公司填寫的志願序，並用來覆蓋狀態
        preferences_map = {}
        if company_ids:
            # 只查詢選擇了該廠商公司的學生志願序
            # 不再檢查 vendor_preference_history 表（已移除），直接使用 resume_applications 表
            preference_placeholders = ", ".join(["%s"] * len(company_ids))
            
            # 根據 resume_teacher 表是否存在，選擇不同的 EXISTS 子查詢
            if resume_teacher_exists:
                # 使用 resume_teacher 表查詢（新架構）
                exists_clause = """
                    AND EXISTS (
                        SELECT 1 FROM resumes r
                        INNER JOIN student_job_applications sja ON sja.resume_id = r.id AND sja.student_id = r.user_id
                        INNER JOIN resume_teacher rt ON rt.application_id = sja.id
                        INNER JOIN users reviewer ON rt.teacher_id = reviewer.id
                        WHERE r.user_id = sp.student_id
                        AND rt.review_status = 'approved'
                        AND reviewer.role = 'teacher'
                    )
                """
            else:
                # 使用舊架構（reviewed_by 欄位）
                exists_clause = """
                    AND EXISTS (
                        SELECT 1 FROM resumes r
                        WHERE r.user_id = sp.student_id
                        AND r.reviewed_by IS NOT NULL
                        AND EXISTS (
                            SELECT 1 FROM users reviewer
                            WHERE reviewer.id = r.reviewed_by
                            AND reviewer.role = 'teacher'
                        )
                    )
                """
            
            # 直接使用查詢（不再檢查 vendor_preference_history 表）
            cursor.execute(f"""
                SELECT 
                    sp.student_id, 
                    sp.id AS preference_id,
                    sp.company_id, 
                    sp.job_id,
                    sp.job_title,
                    sp.preference_order,
                    ic.company_name,
                    COALESCE(ij.title, sp.job_title) AS job_title_display,
                    COALESCE(ij.slots, 0) AS job_slots,
                    -- 由於 interview_status 欄位只用於面試狀態，直接使用 student_preferences 表的 status 欄位
                    COALESCE(sp.status, 'uploaded') AS vendor_review_status
                FROM student_preferences sp
                JOIN internship_companies ic ON sp.company_id = ic.id
                LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                WHERE sp.company_id IN ({preference_placeholders})
                -- 如果是老師訪問，顯示所有職缺；如果是廠商訪問，只顯示該廠商建立的職缺或老師建立的職缺
                AND (%s IN ('teacher', 'ta') OR ij.created_by_vendor_id = %s OR ij.created_by_vendor_id IS NULL)
                -- 只顯示已經被指導老師審核通過的志願序
                -- 必須等指導老師審核完後，才會給廠商學生的資料
                {exists_clause}
            """, tuple(company_ids) + (user_role, vendor_id))
            
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
            # 根據 resume_teacher 表是否存在，選擇不同的 EXISTS 子查詢
            if resume_teacher_exists:
                # 使用 resume_teacher 表查詢（新架構）
                exists_clause = """
                    AND EXISTS (
                        SELECT 1 FROM resumes r
                        INNER JOIN student_job_applications sja ON sja.resume_id = r.id AND sja.student_id = r.user_id
                        INNER JOIN resume_teacher rt ON rt.application_id = sja.id
                        INNER JOIN users reviewer ON rt.teacher_id = reviewer.id
                        WHERE r.user_id = sp.student_id
                        AND rt.review_status = 'approved'
                        AND reviewer.role = 'teacher'
                    )
                """
            else:
                # 使用舊架構（reviewed_by 欄位）
                exists_clause = """
                    AND EXISTS (
                        SELECT 1 FROM resumes r
                        WHERE r.user_id = sp.student_id
                        AND r.reviewed_by IS NOT NULL
                        AND EXISTS (
                            SELECT 1 FROM users reviewer
                            WHERE reviewer.id = r.reviewed_by
                            AND reviewer.role = 'teacher'
                        )
                    )
                """
            # 直接使用 resume_applications 表查詢（不再檢查 vendor_preference_history）
            cursor.execute(f"""
                SELECT 
                    sp.student_id, 
                    sp.id AS preference_id,
                    sp.company_id, 
                    sp.job_id,
                    sp.job_title,
                    sp.preference_order,
                    ic.company_name,
                    COALESCE(ij.title, sp.job_title) AS job_title_display,
                    COALESCE(ij.slots, 0) AS job_slots,
                    COALESCE(sp.status, 'uploaded') AS vendor_review_status
                FROM student_preferences sp
                JOIN internship_companies ic ON sp.company_id = ic.id
                LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                WHERE (%s IN ('teacher', 'ta') OR ij.created_by_vendor_id = %s OR ij.created_by_vendor_id IS NULL)
                {exists_clause}
            """, (user_role, vendor_id))
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
            # 對於廠商來說，初始狀態應該是 'uploaded'（待審核）
            display_status = "uploaded" 
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
            
            # 如果存在志願序，為每個志願序創建單獨的履歷記錄
            # 這樣每個職缺都會有自己獨立的面試狀態
            if filtered_preferences:
                # 為每個志願序創建單獨的履歷記錄
                for pref_to_show in filtered_preferences:
                    sp_status = pref_to_show.get('vendor_review_status')
                    preference_id = pref_to_show.get("preference_id")
                    preference_order = pref_to_show.get("preference_order")
                    company_id = pref_to_show.get("company_id")
                    company_name = pref_to_show.get("company_name") or ""
                    job_id = pref_to_show.get("job_id")
                    job_title = pref_to_show.get("job_title_display") or pref_to_show.get("job_title") or ""
                    job_slots = pref_to_show.get("job_slots") or 0
                    
                    # 優先從 resume_applications 表讀取狀態和留言
                    # 注意：resume_applications.application_id 對應的是 student_job_applications.id，不是 student_preferences.id
                    # 需要從 student_job_applications 表獲取正確的 application_id
                    application_id = None
                    if student_id and company_id and job_id:
                        cursor.execute("""
                            SELECT id FROM student_job_applications
                            WHERE student_id = %s AND company_id = %s AND job_id = %s
                            ORDER BY applied_at DESC
                            LIMIT 1
                        """, (student_id, company_id, job_id))
                        sja_result = cursor.fetchone()
                        if sja_result:
                            application_id = sja_result['id']
                    
                    # 使用 application_id (student_job_applications.id) 和 job_id 來查詢 resume_applications
                    # 一次性查詢所有需要的資料，避免重複查詢導致未讀取結果的問題
                    display_status = "uploaded"  # 預設狀態
                    vendor_comment = None
                    has_interview = False  # 是否有面試記錄
                    interview_completed = False  # 是否已完成面試
                    interview_time = None  # 面試時間
                    interview_result = None  # 面試結果
                    interview_status = None  # 初始化 interview_status
                    
                    if application_id and job_id:
                        # 一次性查詢所有需要的資料
                        cursor.execute("""
                            SELECT apply_status, company_comment, interview_status, interview_time, interview_result
                            FROM resume_applications
                            WHERE application_id = %s AND job_id = %s
                        """, (application_id, job_id))
                        ra_result = cursor.fetchone()
                        
                        if ra_result:
                            # 從 resume_applications 表獲取狀態
                            ra_status = ra_result.get('apply_status')
                            # 映射狀態：uploaded -> uploaded, approved -> approved, rejected -> rejected
                            # resume_applications.apply_status 和 student_preferences.status 現在使用相同的 enum
                            status_map = {
                                'uploaded': 'uploaded',
                                'approved': 'approved',
                                'rejected': 'rejected'
                            }
                            display_status = status_map.get(ra_status, 'uploaded')
                            
                            # 同時獲取廠商留言和面試資訊（重用同一個查詢結果）
                            vendor_comment = ra_result.get('company_comment') or None
                            interview_status = ra_result.get('interview_status')
                            interview_time = ra_result.get('interview_time')
                            interview_result = ra_result.get('interview_result')
                            
                            # 判斷是否有面試記錄
                            # resume_applications.interview_status enum: ('none', 'scheduled', 'finished')
                            if interview_status and interview_status != 'none':
                                has_interview = True
                                if interview_status == 'finished':
                                    interview_completed = True
                                elif interview_status == 'scheduled':
                                    # 已排定面試但尚未完成
                                    has_interview = True
                            print(f"✅ 從 resume_applications 表讀取所有資料: application_id={application_id}, job_id={job_id}, apply_status={ra_status}, interview_status={interview_status}")
                        else:
                            # 如果 resume_applications 表沒有記錄，使用 student_preferences 的狀態（向後兼容）
                            if sp_status and sp_status in STATUS_LABELS:
                                display_status = sp_status
                                print(f"⚠️ resume_applications 表無記錄，使用 student_preferences 狀態: {display_status}")
                            else:
                                display_status = "uploaded"
                                print(f"⚠️ 狀態無效或為空，使用預設狀態: {display_status}")
                    else:
                        # 如果沒有 application_id 或 job_id，使用 student_preferences 的狀態（向後兼容）
                        if sp_status and sp_status in STATUS_LABELS:
                            display_status = sp_status
                        else:
                            display_status = "uploaded"
                    
                    # 狀態篩選：如果篩選器啟用，檢查是否匹配
                    if status_filter:
                        if status_filter == 'uploaded':
                            # uploaded 篩選匹配 'uploaded' 狀態
                            if display_status != 'uploaded':
                                continue # 不匹配，跳過此志願序
                        elif display_status != status_filter:
                            continue # 不匹配，跳過此志願序
                    
                    # 公司篩選：如果前面已經根據 filtered_preferences 做了判斷
                    # 這裡需要確保，如果進行了公司篩選 (company_filter)，那麼該履歷必須與之相關聯
                    if company_filter:
                        # 如果使用公司名稱篩選（前端可能傳遞公司名稱而非 ID）
                        if isinstance(company_filter, str):
                            if company_name != company_filter:
                                continue # 不匹配，跳過此志願序
                        elif company_id != company_filter:
                            continue # 不匹配，跳過此志願序
                    
                    # 如果 resume_applications 表沒有記錄，不再從 vendor_preference_history 讀取（表已移除）
                    # 所有資訊都應該從 resume_applications 表讀取
                    
                    # 構建結果
                    # 確保 interview_status 有預設值
                    interview_status_value = interview_status if interview_status else 'none'
                    
                    resume = {
                        "id": row.get("id"),
                        "student_id": row.get("student_id"),
                        "name": row.get("student_name"),
                        "username": row.get("student_number"),
                        "className": row.get("class_name") or "",
                        "department": row.get("department") or "",
                        "original_filename": row.get("original_filename"),
                        "filepath": row.get("filepath"),
                        "status": display_status,  # 顯示基於 resume_applications 或 student_preferences 的狀態
                        "display_status": display_status,  # 前端使用的顯示狀態欄位
                        "comment": vendor_comment or "", # 廠商的留言（優先從 resume_applications），如果沒有則為空
                        "vendor_comment": vendor_comment or "", # 明確標記為廠商留言
                        "note": row.get("note") or "",
                        "upload_time": _format_datetime(row.get("created_at")),
                        "reviewed_at": _format_datetime(row.get("reviewed_at")),
                        "company_name": company_name,
                        "company_id": company_id,
                        "application_id": application_id, # 添加 application_id (student_job_applications.id)
                        "job_id": job_id,
                        "job_title": job_title,
                        "job_slots": job_slots, # 職缺名額
                        "preference_id": preference_id, # 用於廠商審核操作，如果沒有填寫志願序則為 None
                        "preference_order": preference_order, # 志願序（1=第一志願, 2=第二志願...）
                        "interview_status": interview_status_value, # 面試狀態：'none', 'scheduled', 'finished'
                        "has_interview": has_interview, # 是否有面試記錄（向後兼容）
                        "interview_completed": interview_completed, # 是否已完成面試（向後兼容）
                        "interview_time": _format_datetime(interview_time) if interview_time else None, # 面試時間
                        "interview_result": interview_result, # 面試結果 (pending, pass, fail)
                    }
                    resumes.append(resume)
            elif company_ids:
                # 如果沒有志願序，但廠商有關聯的公司，顯示第一個公司名稱
                # 這種情況不應該出現（因為上面已經過濾掉了），但保留作為備用
                if companies and len(companies) > 0:
                    company_name = companies[0].get("company_name", "")
                    
                    # 狀態篩選：如果篩選器啟用，檢查是否匹配
                    if status_filter:
                        if status_filter == 'uploaded':
                            # uploaded 篩選匹配 'uploaded' 狀態
                            display_status = "uploaded"
                        elif display_status != status_filter:
                            continue # 不匹配，跳過
                    
                    # 構建結果（沒有志願序的情況）
                    resume = {
                        "id": row.get("id"),
                        "student_id": row.get("student_id"),
                        "name": row.get("student_name"),
                        "username": row.get("student_number"),
                        "className": row.get("class_name") or "",
                        "department": row.get("department") or "",
                        "original_filename": row.get("original_filename"),
                        "filepath": row.get("filepath"),
                        "status": "uploaded",  # 預設狀態
                        "display_status": "uploaded",  # 前端使用的顯示狀態欄位
                        "comment": "", # 廠商的留言
                        "vendor_comment": "", # 明確標記為廠商留言
                        "note": row.get("note") or "",
                        "upload_time": _format_datetime(row.get("created_at")),
                        "reviewed_at": _format_datetime(row.get("reviewed_at")),
                        "company_name": company_name,
                        "company_id": None,
                        "job_id": None,
                        "job_title": "",
                        "job_slots": 0,
                        "preference_id": None,
                        "preference_order": None,
                        "interview_status": "none", # 面試狀態：'none', 'scheduled', 'finished'
                        "has_interview": False,
                        "interview_completed": False,
                        "interview_time": None,
                        "interview_result": None,
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


@vendor_bp.route("/vendor/api/review_resume/<int:resume_id>", methods=["POST"])
def vendor_review_resume(resume_id):
    """廠商審核履歷（通過/退回）"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    preference_id = data.get("preference_id")
    comment = data.get("comment", "").strip()
    
    if status not in ["approved", "rejected"]:
        return jsonify({"success": False, "message": "無效的狀態碼"}), 400
    
    vendor_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor(dictionary=True, buffered=True)
    
    try:
        # 獲取廠商的公司列表
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile:
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403
        
        company_ids = [c["id"] for c in companies] if companies else []
        if not company_ids:
            return jsonify({"success": False, "message": "找不到該廠商關聯的公司"}), 404
        
        # 如果提供了 preference_id，直接使用
        if preference_id:
            # 確保 preference_id 是整數類型
            try:
                if isinstance(preference_id, str):
                    preference_id = int(preference_id) if preference_id != 'null' else None
                elif preference_id == 'null':
                    preference_id = None
            except (ValueError, TypeError):
                return jsonify({"success": False, "message": "無效的 preference_id 格式"}), 400
            
            if not preference_id:
                return jsonify({"success": False, "message": "請提供有效的 preference_id"}), 400
            
            # 驗證 preference_id 是否屬於該廠商的公司
            cursor.execute("""
                SELECT sp.id, sp.student_id, sp.company_id, sp.job_id, sp.preference_order
                FROM student_preferences sp
                WHERE sp.id = %s AND sp.company_id IN ({})
            """.format(','.join(['%s'] * len(company_ids))), [preference_id] + company_ids)
            pref_info = cursor.fetchone()
            
            if not pref_info:
                return jsonify({"success": False, "message": "找不到該申請或無權限操作"}), 404
            
            student_id = pref_info.get('student_id')
            company_id = pref_info.get('company_id')
            job_id = pref_info.get('job_id')
            preference_order = pref_info.get('preference_order')
            
            # 更新 student_preferences 表的狀態
            cursor.execute("""
                UPDATE student_preferences
                SET status = %s
                WHERE id = %s
            """, (status, preference_id))
            updated_pref_rows = cursor.rowcount
            print(f"✅ [vendor_review_resume] 更新 student_preferences: preference_id={preference_id}, status={status}, updated_rows={updated_pref_rows}")
            
            # 更新 resume_applications 表的狀態
            # 需要找到對應的 application_id（student_job_applications.id）
            # 使用與 get_vendor_resumes 相同的查詢條件：student_id, company_id, job_id
            if student_id and company_id and job_id:
                cursor.execute("""
                    SELECT sja.id AS application_id
                    FROM student_job_applications sja
                    WHERE sja.student_id = %s AND sja.company_id = %s AND sja.job_id = %s
                    ORDER BY sja.applied_at DESC
                    LIMIT 1
                """, (student_id, company_id, job_id))
                app_info = cursor.fetchone()
                
                if app_info:
                    application_id = app_info.get('application_id')
                    print(f"🔍 [vendor_review_resume] 找到 application_id: {application_id} (student_id={student_id}, company_id={company_id}, job_id={job_id})")
                    
                    # 先檢查是否存在記錄
                    cursor.execute("""
                        SELECT id, apply_status FROM resume_applications
                        WHERE application_id = %s AND job_id = %s
                    """, (application_id, job_id))
                    existing_ra = cursor.fetchone()
                    
                    if existing_ra:
                        # 更新現有記錄
                        cursor.execute("""
                            UPDATE resume_applications
                            SET apply_status = %s,
                                company_comment = %s,
                                updated_at = NOW()
                            WHERE application_id = %s AND job_id = %s
                        """, (status, comment, application_id, job_id))
                        updated_ra_rows = cursor.rowcount
                        print(f"✅ [vendor_review_resume] 更新 resume_applications: id={existing_ra.get('id')}, application_id={application_id}, job_id={job_id}, apply_status={status} (舊值: {existing_ra.get('apply_status')}), updated_rows={updated_ra_rows}")
                    else:
                        # 創建新記錄
                        cursor.execute("""
                            INSERT INTO resume_applications
                            (application_id, job_id, apply_status, company_comment, interview_status, interview_result, created_at)
                            VALUES (%s, %s, %s, %s, 'none', 'pending', NOW())
                        """, (application_id, job_id, status, comment))
                        updated_ra_rows = cursor.rowcount
                        print(f"✅ [vendor_review_resume] 創建 resume_applications: application_id={application_id}, job_id={job_id}, apply_status={status}, inserted_rows={updated_ra_rows}")
                    
                    # 驗證更新是否成功
                    cursor.execute("""
                        SELECT id, apply_status, company_comment FROM resume_applications
                        WHERE application_id = %s AND job_id = %s
                    """, (application_id, job_id))
                    verify_result = cursor.fetchone()
                    if verify_result:
                        print(f"✅ [vendor_review_resume] 驗證成功: id={verify_result.get('id')}, apply_status={verify_result.get('apply_status')}, company_comment={verify_result.get('company_comment')}")
                    else:
                        print(f"⚠️ [vendor_review_resume] 驗證失敗: 找不到對應的記錄")
                else:
                    print(f"⚠️ [vendor_review_resume] 找不到 application_id (student_id={student_id}, job_id={job_id})")
            
            # 如果是通過操作，自動記錄錄取結果並綁定關係
            if status == "approved":
                admission_result = _record_admission_and_bind_relation(
                    cursor,
                    student_id,
                    company_id,
                    job_id,
                    preference_order
                )
                if not admission_result.get("success"):
                    print(f"⚠️ 錄取結果記錄失敗: {admission_result.get('message')}")
            
            # 發送通知給學生
            title = "履歷審核結果"
            status_label = STATUS_LABELS.get(status, status)
            message = f"您的履歷申請已被更新為「{status_label}」。"
            if comment:
                message = f"{message}\n\n廠商備註：{comment}"
            _notify_student(cursor, student_id, title, message)
            
            conn.commit()
            return jsonify({"success": True, "message": f"已標記為{status_label}"})
        else:
            # 如果沒有提供 preference_id，嘗試從 resume_id 查找
            # 但這需要知道 resume 對應的 preference，可能需要額外的邏輯
            return jsonify({"success": False, "message": "請提供 preference_id"}), 400
            
    except Exception as exc:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "message": f"操作失敗：{exc}"}), 500
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
            empty_summary = {"uploaded": 0, "approved": 0, "rejected": 0, "new_this_week": 0}
            return jsonify({"items": [], "summary": empty_summary})

        companies = _get_vendor_companies(cursor, vendor_id)
        if not companies:
            empty_summary = {"uploaded": 0, "approved": 0, "rejected": 0, "new_this_week": 0}
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
        counts = {"uploaded": 0, "approved": 0, "rejected": 0}
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
            "uploaded": counts["uploaded"],
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

        # 基礎權限判斷：只顯示該廠商建立的職缺或老師建立的職缺
        # 不顯示其他廠商建立的職缺
        where_clauses = [
            f"ij.company_id IN ({', '.join(['%s'] * len(company_ids))})",
            "(ij.created_by_vendor_id = %s OR ij.created_by_vendor_id IS NULL)"
        ]
        params = company_ids[:] + [vendor_id]

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

        status_map = {
            "approve": "approved",
            "reject": "rejected",
            "reopen": "uploaded",  # 重新開啟時設為 'uploaded'（符合 enum 定義）
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

        # 記錄歷史（只有面試相關的操作才記錄到 vendor_preference_history，因為 interview_status 欄位只用於面試狀態）
        # approve, reject, comment 等操作不再記錄到 vendor_preference_history
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
    """發布公告頁面（科助、管理員、廠商均可使用）"""
    if "user_id" not in session:
        return render_template("auth/login.html")
    if session.get("role") not in ("vendor", "ta", "admin"):
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
        
        # 從通知記錄中獲取廠商發布的公告（只顯示該廠商發布的公告）
        # 廠商發布的公告標題格式為：【{company_name} - {job_title}】公告：{title} 或 【{company_name}】公告：{title}
        if company_ids:
            placeholders = ", ".join(["%s"] * len(company_ids))
            # 獲取廠商關聯的公司名稱列表，用於匹配標題
            cursor.execute(f"""
                SELECT company_name 
                FROM internship_companies 
                WHERE id IN ({placeholders})
            """, tuple(company_ids))
            company_names = [row['company_name'] for row in cursor.fetchall()]
            
            # 構建公司名稱的 LIKE 條件（用於匹配標題中的公司名稱）
            company_name_conditions = " OR ".join([f"n.title LIKE %s" for _ in company_names])
            company_name_params = [f"%【{name}%公告：%" for name in company_names]
            
            # 查詢類別為 "announcement" 且標題格式符合廠商發布格式的記錄
            # 只顯示標題中包含「【」和「】公告：」格式的記錄（這是廠商發布的標記）
            cursor.execute(f"""
                SELECT 
                    n.title,
                    n.message AS content,
                    n.created_at,
                    COUNT(DISTINCT n.user_id) AS recipient_count
                FROM notifications n
                WHERE n.category = 'announcement'
                  AND n.title LIKE '%【%】公告：%'
                  AND n.title NOT LIKE '%面試通知%'
                  AND n.title NOT LIKE '%錄取通知%'
                  AND ({company_name_conditions})
                  AND EXISTS (
                      SELECT 1 
                      FROM student_preferences sp 
                      WHERE sp.student_id = n.user_id 
                        AND sp.company_id IN ({placeholders})
                  )
                GROUP BY n.title, n.message, n.created_at
                ORDER BY n.created_at DESC
                LIMIT 50
            """, tuple(company_name_params + list(company_ids)))
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
            SELECT id, username, name, email, role, teacher_id
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
        
        # 2. 如果有 teacher_id，查找指導老師
        if vendor_info and vendor_info.get("teacher_id"):
            teacher_id = vendor_info.get("teacher_id")
            cursor.execute("""
                SELECT id, name, email, role
                FROM users
                WHERE id = %s AND role IN ('teacher', 'director')
            """, (teacher_id,))
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
    company_id = data.get("company_id")  # 公司 ID
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
        # 如果前端提供了 company_id，優先從資料庫獲取公司資訊
        if company_id:
            cursor.execute("""
                SELECT company_name, advisor_user_id 
                FROM internship_companies 
                WHERE id = %s
            """, (company_id,))
            company_info = cursor.fetchone()
            if company_info:
                company_name = company_info.get("company_name", company_name)
        # 如果前端提供了公司名稱，優先使用；否則從資料庫獲取
        elif not company_name:
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
        
        # 記錄面試通知到 resume_applications（如果有 preference_id）
        preference_id = data.get("preference_id")
        if notification_type == "interview" and preference_id:
            try:
                _record_history(cursor, preference_id, vendor_id, "in interview", content or "面試通知已發送")
                print(f"✅ 已記錄面試通知到 resume_applications (preference_id: {preference_id})")
            except Exception as history_error:
                # 歷史記錄失敗不影響通知發送
                print(f"⚠️ 記錄面試歷史失敗（不影響通知發送）：{history_error}")
                traceback.print_exc()
        
        # 發送系統通知（如果有 student_id）
        if student_id:
            try:
                from notification import create_notification
                
                # 發送通知給學生
                _notify_student(
                    cursor, 
                    student_id, 
                    f"【{company_name}】{'面試通知' if notification_type == 'interview' else '錄取通知'}",
                    content if content else f"您已收到來自 {company_name} 的{'面試通知' if notification_type == 'interview' else '錄取通知'}",
                    "/vendor_review_resume",
                    "company"
                )
                
                # 如果是指定公司的面試通知，也發送通知給該公司的指導老師
                if notification_type == "interview" and company_id:
                    cursor.execute("""
                        SELECT advisor_user_id 
                        FROM internship_companies 
                        WHERE id = %s AND advisor_user_id IS NOT NULL
                    """, (company_id,))
                    company_info = cursor.fetchone()
                    
                    if company_info and company_info.get('advisor_user_id'):
                        advisor_user_id = company_info['advisor_user_id']
                        # 獲取學生姓名
                        cursor.execute("SELECT name FROM users WHERE id = %s", (student_id,))
                        student_info = cursor.fetchone()
                        student_name = student_info.get('name', '學生') if student_info else '學生'
                        
                        # 發送通知給指導老師
                        create_notification(
                            user_id=advisor_user_id,
                            title=f"【{company_name}】面試通知",
                            message=f"{student_name} 已收到來自 {company_name} 的面試通知。",
                            category="company",
                            link_url="/review_resume"
                        )
                        print(f"✅ 已發送面試通知給指導老師 (advisor_user_id: {advisor_user_id})")
                
                conn.commit()
            except Exception as notify_error:
                # 系統通知失敗不影響 Email 發送
                print(f"⚠️ 系統通知發送失敗（不影響 Email）：{notify_error}")
                traceback.print_exc()
        
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


@vendor_bp.route("/vendor/api/all_interview_schedules", methods=["GET"])
def get_all_interview_schedules():
    """獲取所有廠商的面試排程（用於顯示其他廠商已預約的時間）"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403
    
    vendor_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取當前廠商關聯的公司ID列表
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        company_ids = [c["id"] for c in companies] if companies else []
        
        # 查詢所有廠商的面試排程（從 resume_applications 表中）
        # 查詢 interview_status = 'scheduled' 或 'finished' 的記錄（已面試的排程也要保留）
        # 注意：resume_applications.application_id 對應的是 student_job_applications.id，不是 student_preferences.id
        # 注意：不使用 DISTINCT，因為每個學生的排程都是獨立的記錄
        cursor.execute("""
            SELECT
                ra.company_comment AS comment,
                ic.company_name,
                ic.id AS company_id,
                ra.updated_at AS created_at,
                ra.interview_time,
                ra.interview_timeEnd,
                sja.student_id,
                u.name AS student_name,
                ra.application_id,
                ra.job_id
            FROM resume_applications ra
            JOIN student_job_applications sja ON ra.application_id = sja.id
            LEFT JOIN internship_companies ic ON sja.company_id = ic.id
            LEFT JOIN users u ON sja.student_id = u.id
            WHERE ra.interview_status IN ('scheduled', 'finished')
            AND ra.interview_time IS NOT NULL
            ORDER BY ra.updated_at DESC
        """)
        
        all_schedules = cursor.fetchall()
        print(f"📋 [all_interview_schedules] 查詢到 {len(all_schedules)} 筆排程記錄")
        
        # 解析面試資訊
        import re
        parsed_schedules = []
        
        for schedule in all_schedules:
            comment = schedule.get('comment', '')
            company_name = schedule.get('company_name', '未知公司')
            company_id = schedule.get('company_id')
            interview_time = schedule.get('interview_time')
            interview_timeEnd = schedule.get('interview_timeEnd')
            
            # 判斷是否為當前廠商的排程
            is_own = company_id and company_id in company_ids
            
            # 從 interview_time 提取日期和開始時間
            # 從 interview_timeEnd 提取結束時間
            if interview_time:
                if isinstance(interview_time, str):
                    # 解析 datetime 字串
                    try:
                        from datetime import datetime
                        dt = datetime.strptime(interview_time, '%Y-%m-%d %H:%M:%S')
                        interview_date = dt.strftime('%Y-%m-%d')
                        time_start = dt.strftime('%H:%M')
                    except:
                        # 如果解析失敗，嘗試從 comment 提取
                        date_match = re.search(r'面試日期：(\d{4}-\d{2}-\d{2})', comment)
                        if date_match:
                            interview_date = date_match.group(1)
                        else:
                            continue
                        # 嘗試提取時間段（格式：時間：HH:MM-HH:MM 或 時間：HH:MM）
                        time_match = re.search(r'時間：(\d{2}:\d{2})(?:-(\d{2}:\d{2}))?', comment)
                        if time_match:
                            time_start = time_match.group(1)
                            time_end = time_match.group(2) if time_match.group(2) else None
                        else:
                            time_start = None
                            time_end = None
                else:
                    # 如果是 datetime 物件
                    interview_date = interview_time.strftime('%Y-%m-%d')
                    time_start = interview_time.strftime('%H:%M')
            else:
                # 如果沒有 interview_time，嘗試從 comment 提取
                date_match = re.search(r'面試日期：(\d{4}-\d{2}-\d{2})', comment)
                if not date_match:
                    continue
                interview_date = date_match.group(1)
                time_match = re.search(r'時間：(\d{2}:\d{2})', comment)
                time_start = time_match.group(1) if time_match else None
            
            # 從 interview_timeEnd 提取結束時間
            time_end = None
            if interview_timeEnd:
                if isinstance(interview_timeEnd, str):
                    try:
                        from datetime import datetime
                        dt_end = datetime.strptime(interview_timeEnd, '%Y-%m-%d %H:%M:%S')
                        time_end = dt_end.strftime('%H:%M')
                    except:
                        # 如果解析失敗，嘗試從 comment 提取
                        time_end_match = re.search(r'時間：\d{2}:\d{2}-(\d{2}:\d{2})', comment)
                        time_end = time_end_match.group(1) if time_end_match else None
                else:
                    # 如果是 datetime 物件
                    time_end = interview_timeEnd.strftime('%H:%M')
            else:
                # 如果沒有 interview_timeEnd，嘗試從 comment 提取
                time_end_match = re.search(r'時間：\d{2}:\d{2}-(\d{2}:\d{2})', comment)
                time_end = time_end_match.group(1) if time_end_match else None
            
            # 提取地點
            location_match = re.search(r'地點：([^，]+)', comment)
            location = location_match.group(1) if location_match else ''
            
            # 提取備註（備註可能在最後，也可能包含多行或特殊字符）
            notes_match = re.search(r'備註：(.+)$', comment)
            notes = notes_match.group(1).strip() if notes_match else ''
            
            student_id = schedule.get('student_id')
            student_name = schedule.get('student_name', '')
            # 確保 student_id 被正確提取
            if student_id is None:
                print(f"⚠️ [all_interview_schedules] 警告：排程記錄缺少 student_id: {schedule}")
            
            print(f"📅 [all_interview_schedules] 解析排程: 日期={interview_date}, 時間={time_start}-{time_end}, 學生ID={student_id}, 學生姓名={student_name}, 公司={company_name}, is_own={is_own}, 地點={location}, 備註={notes[:30] if notes else '無'}")
            
            parsed_schedules.append({
                'date': interview_date,
                'time_start': time_start,
                'time_end': time_end,
                'location': location,
                'notes': notes,  # 添加備註
                'vendor_id': None,  # resume_applications 表沒有 reviewer_id
                'vendor_name': None,
                'company_name': company_name,
                'is_own': is_own,  # 判斷是否為當前廠商的排程
                'student_id': student_id,  # 添加學生ID
                'student_name': student_name,  # 添加學生姓名
                'application_id': schedule.get('application_id'),
                'job_id': schedule.get('job_id')
            })
        
        print(f"✅ [all_interview_schedules] 最終返回 {len(parsed_schedules)} 個解析後的排程")
        
        return jsonify({
            "success": True,
            "schedules": parsed_schedules
        })
        
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"獲取面試排程失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/schedule_interviews", methods=["POST"])
def schedule_interviews():
    """批量記錄面試排程到 vendor_preference_history"""
    from notification import create_notification  # 導入通知函數
    
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json(silent=True) or {}
    student_ids = data.get("student_ids", [])
    student_applications = data.get("student_applications", [])  # 前端傳遞的每個學生對應的 application_id 和 job_id
    interview_date = data.get("interview_date")
    interview_time_start = data.get("interview_time_start")
    interview_time_end = data.get("interview_time_end")
    interview_location = data.get("interview_location")
    interview_notes = data.get("interview_notes", "")
    
    if not student_ids or not isinstance(student_ids, list):
        return jsonify({"success": False, "message": "請提供學生ID列表"}), 400
    
    if not interview_date:
        return jsonify({"success": False, "message": "請提供面試日期"}), 400
    
    vendor_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 構建面試資訊描述
        time_info = ""
        if interview_time_start and interview_time_end:
            time_info = f"{interview_time_start} - {interview_time_end}"
        elif interview_time_start:
            time_info = interview_time_start
        
        location_info = interview_location or ""
        notes_info = interview_notes or ""
        
        # 構建面試描述，包含日期、時間、地點和備註（用於 vendor_preference_history）
        # 注意：學生行事曆 API 需要從 comment 中解析日期和時間
        comment_parts = []
        comment_parts.append(f"面試日期：{interview_date}")
        if time_info:
            comment_parts.append(f"時間：{time_info}")
        if location_info:
            comment_parts.append(f"地點：{location_info}")
        if notes_info:
            comment_parts.append(f"備註：{notes_info}")
        interview_description = "，".join(comment_parts) if comment_parts else ""
        
        # 構建簡化的描述（只用於 resume_applications 的 company_comment，只包含地點和備註）
        resume_comment_parts = []
        if location_info:
            resume_comment_parts.append(f"地點：{location_info}")
        if notes_info:
            resume_comment_parts.append(f"備註：{notes_info}")
        resume_comment = "，".join(resume_comment_parts) if resume_comment_parts else ""
        
        success_count = 0
        failed_students = []
        
        # 獲取廠商的公司列表
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403
        
        company_ids = [c["id"] for c in companies] if companies else []
        if not company_ids:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該廠商關聯的公司"}), 404
        
        # 獲取廠商名稱
        vendor_name = profile.get("name", "廠商")
        company_name = companies[0].get("company_name", "公司") if companies else "公司"
        
        # 建立學生ID到application信息的映射（如果前端有提供）
        student_app_map = {}
        if student_applications and isinstance(student_applications, list):
            print(f"📋 [schedule_interviews] 收到前端傳遞的 student_applications: {student_applications}")
            for app_info in student_applications:
                if isinstance(app_info, dict):
                    sid = str(app_info.get("student_id", ""))
                    if sid:
                        student_app_map[sid] = {
                            "application_id": app_info.get("application_id"),
                            "job_id": app_info.get("job_id")
                        }
                        print(f"  ✅ 學生 {sid}: application_id={app_info.get('application_id')}, job_id={app_info.get('job_id')}")
        else:
            print(f"⚠️ [schedule_interviews] 前端未傳遞 student_applications 或格式不正確")
        
        print(f"📋 [schedule_interviews] 處理 {len(student_ids)} 個學生的面試排程")
        for student_id in student_ids:
            try:
                application_id = None
                job_id = None
                company_id = None
                
                # 優先使用前端傳遞的 application_id 和 job_id
                student_id_str = str(student_id)
                print(f"🔍 [schedule_interviews] 處理學生 {student_id_str}")
                
                if student_id_str in student_app_map:
                    app_info = student_app_map[student_id_str]
                    application_id = app_info.get("application_id")
                    job_id = app_info.get("job_id")
                    print(f"  📥 從前端映射獲取: application_id={application_id}, job_id={job_id}")
                    
                    # 如果前端提供了 application_id 和 job_id，驗證它們是否屬於當前廠商的公司
                    if application_id and job_id:
                        cursor.execute("""
                            SELECT sja.id AS application_id, sja.job_id, sja.company_id
                            FROM student_job_applications sja
                            WHERE sja.id = %s AND sja.job_id = %s
                            AND sja.company_id IN ({})
                        """.format(','.join(['%s'] * len(company_ids))), [application_id, job_id] + company_ids)
                        application_row = cursor.fetchone()
                        
                        if application_row:
                            # 驗證通過，使用前端提供的值
                            company_id = application_row.get("company_id")
                            print(f"  ✅ 驗證通過: application_id={application_id}, job_id={job_id}, company_id={company_id}")
                        else:
                            # 驗證失敗，清空這些值，使用查詢邏輯
                            print(f"  ⚠️ 驗證失敗: 學生 {student_id} 的前端提供的 application_id={application_id}, job_id={job_id} 不屬於當前廠商的公司（company_ids={company_ids}），將使用查詢邏輯")
                            application_id = None
                            job_id = None
                    else:
                        print(f"  ⚠️ 前端映射中缺少 application_id 或 job_id")
                else:
                    print(f"  ⚠️ 學生 {student_id_str} 不在前端映射中，將使用查詢邏輯")
                
                # 如果前端沒有提供或驗證失敗，使用查詢邏輯
                if not application_id or not job_id:
                    print(f"  🔍 使用查詢邏輯查找學生 {student_id} 的投遞記錄（company_ids={company_ids}）")
                    # 查找該學生對應的投遞記錄（student_job_applications）
                    # 注意：resume_applications.application_id 對應的是 student_job_applications.id，不是 student_preferences.id
                    # 重要：只查找屬於當前廠商公司的記錄，並且優先使用有對應 resume_applications 記錄的
                    cursor.execute("""
                        SELECT sja.id AS application_id, sja.job_id, sja.company_id
                        FROM student_job_applications sja
                        WHERE sja.student_id = %s
                        AND sja.company_id IN ({})
                        ORDER BY 
                            CASE WHEN EXISTS (
                                SELECT 1 FROM resume_applications ra 
                                WHERE ra.application_id = sja.id AND ra.job_id = sja.job_id
                            ) THEN 0 ELSE 1 END,
                            sja.applied_at DESC
                        LIMIT 1
                    """.format(','.join(['%s'] * len(company_ids))), [student_id] + company_ids)
                    
                    application_row = cursor.fetchone()
                    
                    if application_row:
                        application_id = application_row.get("application_id")
                        job_id = application_row.get("job_id")
                        company_id = application_row.get("company_id")
                        print(f"  ✅ 查詢結果: application_id={application_id}, job_id={job_id}, company_id={company_id}")
                    else:
                        print(f"  ❌ 查詢失敗: 找不到學生 {student_id} 屬於當前廠商公司（company_ids={company_ids}）的投遞記錄")
                        failed_students.append({
                            "student_id": student_id,
                            "reason": f"找不到該學生屬於當前廠商公司的投遞記錄"
                        })
                        continue
                
                if application_id and job_id and company_id:
                    # 同時更新 resume_applications 表的 interview_status 為 'scheduled'
                    # 構建 interview_time（datetime 格式）
                    if interview_time_start:
                        # 如果有開始時間，組合日期和時間
                        interview_datetime_str = f"{interview_date} {interview_time_start}"
                        try:
                            # 嘗試解析為 datetime 物件
                            interview_datetime = datetime.strptime(interview_datetime_str, '%Y-%m-%d %H:%M')
                        except:
                            # 如果解析失敗，使用字串格式
                            interview_datetime = interview_datetime_str
                    else:
                        # 如果沒有時間，只使用日期（設為當天 00:00:00）
                        interview_datetime = f"{interview_date} 00:00:00"
                    
                    # 構建 interview_timeEnd（datetime 格式）
                    interview_datetime_end = None
                    if interview_time_end:
                        # 如果有結束時間，組合日期和時間
                        interview_datetime_end_str = f"{interview_date} {interview_time_end}"
                        try:
                            # 嘗試解析為 datetime 物件
                            interview_datetime_end = datetime.strptime(interview_datetime_end_str, '%Y-%m-%d %H:%M')
                        except:
                            # 如果解析失敗，使用字串格式
                            interview_datetime_end = interview_datetime_end_str
                    
                    # 檢查 resume_applications 記錄是否存在
                    cursor.execute("""
                        SELECT id FROM resume_applications
                        WHERE application_id = %s AND job_id = %s
                    """, (application_id, job_id))
                    existing_ra = cursor.fetchone()
                    
                    if existing_ra:
                        # 更新現有記錄
                        if interview_datetime_end:
                            cursor.execute("""
                                UPDATE resume_applications
                                SET interview_status = 'scheduled',
                                    interview_time = %s,
                                    interview_timeEnd = %s,
                                    company_comment = %s,
                                    interview_result = 'pending',
                                    updated_at = NOW()
                                WHERE application_id = %s AND job_id = %s
                            """, (interview_datetime, interview_datetime_end, resume_comment, application_id, job_id))
                        else:
                            cursor.execute("""
                                UPDATE resume_applications
                                SET interview_status = 'scheduled',
                                    interview_time = %s,
                                    interview_timeEnd = NULL,
                                    company_comment = %s,
                                    interview_result = 'pending',
                                    updated_at = NOW()
                                WHERE application_id = %s AND job_id = %s
                            """, (interview_datetime, resume_comment, application_id, job_id))
                        print(f"✅ [schedule_interviews] 更新 resume_applications: application_id={application_id}, job_id={job_id}, interview_status='scheduled', interview_timeEnd={interview_datetime_end}, company_comment={resume_comment[:50]}")
                    else:
                        # 如果記錄不存在，創建新記錄
                        if interview_datetime_end:
                            cursor.execute("""
                                INSERT INTO resume_applications
                                (application_id, job_id, apply_status, interview_status, interview_time, interview_timeEnd, company_comment, interview_result, created_at)
                                VALUES (%s, %s, 'uploaded', 'scheduled', %s, %s, %s, 'pending', NOW())
                            """, (application_id, job_id, interview_datetime, interview_datetime_end, resume_comment))
                        else:
                            cursor.execute("""
                                INSERT INTO resume_applications
                                (application_id, job_id, apply_status, interview_status, interview_time, interview_timeEnd, company_comment, interview_result, created_at)
                                VALUES (%s, %s, 'uploaded', 'scheduled', %s, NULL, %s, 'pending', NOW())
                            """, (application_id, job_id, interview_datetime, resume_comment))
                        print(f"✅ [schedule_interviews] 創建 resume_applications: application_id={application_id}, job_id={job_id}, interview_status='scheduled', interview_timeEnd={interview_datetime_end}, company_comment={resume_comment[:50]}")
                    
                    # 為了向後兼容，也嘗試從 student_preferences 獲取 preference_id（如果需要的話）
                    cursor.execute("""
                        SELECT sp.id AS preference_id
                        FROM student_preferences sp
                        WHERE sp.student_id = %s
                        AND sp.company_id = %s
                        ORDER BY sp.id DESC
                        LIMIT 1
                    """, (student_id, company_id))
                    preference_row = cursor.fetchone()
                    
                    preference_id = None
                    if preference_row:
                        preference_id = preference_row.get("preference_id")
                    
                    # 記錄到 vendor_preference_history（包含 student_id）
                    # 即使沒有 preference_id，也嘗試插入記錄（preference_id 可以為 NULL）
                    if preference_id:
                        _record_history(cursor, preference_id, vendor_id, "in interview", interview_description, student_id)
                    else:
                        # 如果沒有 preference_id，直接插入到 vendor_preference_history（preference_id 為 NULL）
                        try:
                            cursor.execute("""
                                SELECT COUNT(*) as count
                                FROM information_schema.tables
                                WHERE table_schema = DATABASE()
                                AND table_name = 'vendor_preference_history'
                            """)
                            table_exists = cursor.fetchone().get('count', 0) > 0
                            
                            if table_exists:
                                # 直接插入記錄，preference_id 為 NULL
                                cursor.execute("""
                                    INSERT INTO vendor_preference_history
                                    (preference_id, reviewer_id, student_id, interview_status, comment, created_at)
                                    VALUES (%s, %s, %s, %s, %s, NOW())
                                """, (None, vendor_id, student_id, "in interview", interview_description))
                                print(f"✅ [schedule_interviews] 已插入記錄到 vendor_preference_history (無 preference_id): student_id={student_id}, vendor_id={vendor_id}")
                        except Exception as e:
                            print(f"⚠️ [schedule_interviews] 插入 vendor_preference_history 失敗（無 preference_id）: {e}")
                    
                    # 獲取學生資訊
                    cursor.execute("""
                        SELECT id, name, email, class_id
                        FROM users
                        WHERE id = %s AND role = 'student'
                    """, (student_id,))
                    student_info = cursor.fetchone()
                    
                    if student_info:
                        student_name = student_info.get("name", "同學")
                        
                        # 構建通知內容
                        notification_title = f"{company_name} 面試通知"
                        notification_message = f"您已收到來自 {company_name} 的面試通知。\n\n"
                        notification_message += f"面試日期：{interview_date}\n"
                        if time_info:
                            notification_message += f"面試時間：{time_info}\n"
                        if location_info:
                            notification_message += f"面試地點：{location_info}\n"
                        if notes_info:
                            notification_message += f"面試須知：{notes_info}\n"
                        
                        # 發送通知給學生
                        try:
                            notification_success = create_notification(
                                user_id=student_id,
                                title=notification_title,
                                message=notification_message,
                                category="company",  # 實習公司分類
                                link_url="/notifications"
                            )
                            if notification_success:
                                print(f"✅ 已發送面試通知給學生 {student_name} (ID: {student_id})")
                            else:
                                print(f"⚠️ 發送面試通知給學生 {student_name} (ID: {student_id}) 失敗")
                        except Exception as notify_error:
                            print(f"⚠️ 發送通知時發生錯誤（學生 ID: {student_id}）：{notify_error}")
                            traceback.print_exc()
                        
                        # 發送通知給學生的指導老師（如果有的話）
                        class_id = student_info.get("class_id")
                        if class_id:
                            try:
                                # 查找該班級的指導老師
                                cursor.execute("""
                                    SELECT ct.teacher_id
                                    FROM classes_teacher ct
                                    WHERE ct.class_id = %s
                                    LIMIT 1
                                """, (class_id,))
                                teacher_row = cursor.fetchone()
                                
                                if teacher_row and teacher_row.get("teacher_id"):
                                    teacher_id = teacher_row.get("teacher_id")
                                    teacher_notification_title = f"{company_name} 學生面試通知"
                                    teacher_notification_message = f"您的學生 {student_name} 已收到來自 {company_name} 的面試通知。\n\n"
                                    teacher_notification_message += f"面試日期：{interview_date}\n"
                                    if time_info:
                                        teacher_notification_message += f"面試時間：{time_info}\n"
                                    if location_info:
                                        teacher_notification_message += f"面試地點：{location_info}\n"
                                    
                                    teacher_notification_success = create_notification(
                                        user_id=teacher_id,
                                        title=teacher_notification_title,
                                        message=teacher_notification_message,
                                        category="company",
                                        link_url="/notifications"
                                    )
                                    if teacher_notification_success:
                                        print(f"✅ 已發送面試通知給指導老師 (ID: {teacher_id})")
                                    else:
                                        print(f"⚠️ 發送面試通知給指導老師 (ID: {teacher_id}) 失敗")
                            except Exception as teacher_notify_error:
                                print(f"⚠️ 發送通知給指導老師時發生錯誤：{teacher_notify_error}")
                                # 不影響主流程，只記錄錯誤
                    
                    success_count += 1
                else:
                    failed_students.append(str(student_id))
            except Exception as e:
                print(f"⚠️ 記錄學生 {student_id} 的面試排程失敗：{e}")
                traceback.print_exc()
                failed_students.append(str(student_id))
        
        conn.commit()
        
        if success_count > 0:
            message = f"已成功記錄 {success_count} 位學生的面試排程"
            if failed_students:
                message += f"，{len(failed_students)} 位學生記錄失敗（可能找不到對應的志願序）"
            return jsonify({"success": True, "message": message, "success_count": success_count, "failed_count": len(failed_students)})
        else:
            return jsonify({"success": False, "message": "無法找到任何學生的志願序記錄"}), 404
            
    except Exception as exc:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "message": f"記錄面試排程失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/delete_interview_schedule", methods=["POST"])
def delete_interview_schedule():
    """刪除面試排程"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json(silent=True) or {}
    interview_date = data.get("interview_date")
    student_ids = data.get("student_ids", [])
    
    if not interview_date:
        return jsonify({"success": False, "message": "請提供面試日期"}), 400
    
    vendor_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取廠商的公司列表
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403
        
        company_ids = [c["id"] for c in companies] if companies else []
        if not company_ids:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該廠商關聯的公司"}), 404
        
        success_count = 0
        failed_students = []
        
        # 如果有提供學生ID列表，只刪除這些學生的面試排程
        if student_ids and isinstance(student_ids, list) and len(student_ids) > 0:
            for student_id in student_ids:
                try:
                    # 查找該學生對應的投遞記錄
                    cursor.execute("""
                        SELECT sja.id AS application_id, sja.job_id, sja.company_id
                        FROM student_job_applications sja
                        WHERE sja.student_id = %s
                        AND sja.company_id IN ({})
                        ORDER BY sja.applied_at DESC
                        LIMIT 1
                    """.format(','.join(['%s'] * len(company_ids))), [student_id] + company_ids)
                    
                    application_row = cursor.fetchone()
                    
                    if application_row:
                        application_id = application_row.get("application_id")
                        job_id = application_row.get("job_id")
                        
                        # 更新 resume_applications 表，將 interview_status 設為 'none'，清除 interview_time 和 interview_timeEnd
                        cursor.execute("""
                            UPDATE resume_applications
                            SET interview_status = 'none',
                                interview_time = NULL,
                                interview_timeEnd = NULL,
                                updated_at = NOW()
                            WHERE application_id = %s AND job_id = %s
                            AND interview_status = 'scheduled'
                        """, (application_id, job_id))
                        
                        if cursor.rowcount > 0:
                            print(f"✅ [delete_interview_schedule] 已刪除學生 {student_id} 的面試排程: application_id={application_id}, job_id={job_id}")
                            success_count += 1
                        else:
                            print(f"⚠️ [delete_interview_schedule] 學生 {student_id} 沒有找到 scheduled 狀態的面試排程")
                            failed_students.append(str(student_id))
                    else:
                        print(f"⚠️ [delete_interview_schedule] 找不到學生 {student_id} 的投遞記錄")
                        failed_students.append(str(student_id))
                except Exception as e:
                    print(f"⚠️ 刪除學生 {student_id} 的面試排程失敗：{e}")
                    traceback.print_exc()
                    failed_students.append(str(student_id))
        else:
            # 如果沒有提供學生ID列表，刪除該日期的所有面試排程
            # 查找該日期所有屬於當前廠商公司的面試排程
            cursor.execute("""
                UPDATE resume_applications ra
                INNER JOIN student_job_applications sja ON ra.application_id = sja.id
                SET ra.interview_status = 'none',
                    ra.interview_time = NULL,
                    ra.interview_timeEnd = NULL,
                    ra.updated_at = NOW()
                WHERE DATE(ra.interview_time) = %s
                AND sja.company_id IN ({})
                AND ra.interview_status = 'scheduled'
            """.format(','.join(['%s'] * len(company_ids))), [interview_date] + company_ids)
            
            success_count = cursor.rowcount
            print(f"✅ [delete_interview_schedule] 已刪除 {success_count} 筆 {interview_date} 的面試排程")
        
        conn.commit()
        
        if success_count > 0:
            message = f"已成功刪除 {success_count} 筆面試排程"
            if failed_students:
                message += f"，{len(failed_students)} 筆刪除失敗"
            return jsonify({"success": True, "message": message, "success_count": success_count, "failed_count": len(failed_students)})
        else:
            return jsonify({"success": False, "message": "找不到要刪除的面試排程"}), 404
            
    except Exception as exc:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "message": f"刪除面試排程失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/mark_interview_completed", methods=["POST"])
def mark_interview_completed():
    """廠商標記面試已完成"""
    if "user_id" not in session or session.get("role") != "vendor":
        return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json(silent=True) or {}
    preference_id = data.get("preference_id")
    
    if not preference_id:
        return jsonify({"success": False, "message": "請提供 preference_id"}), 400

    vendor_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 驗證 preference_id 是否屬於該廠商可以審核的範圍
        cursor.execute("""
            SELECT sp.id, sp.student_id, sp.company_id
            FROM student_preferences sp
            JOIN internship_companies ic ON sp.company_id = ic.id
            WHERE sp.id = %s
        """, (preference_id,))
        preference = cursor.fetchone()
        
        if not preference:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該志願序"}), 404
        
        # 檢查廠商是否有權限審核該公司
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "帳號資料不完整"}), 403
        
        company_ids = [c["id"] for c in companies] if companies else []
        if preference["company_id"] not in company_ids:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "無權限操作此志願序"}), 403
        
        # 記錄面試完成（更新 resume_applications 表）
        _record_history(cursor, preference_id, vendor_id, "done", "面試已完成")
        
        # 同時更新 resume_applications 表的 interview_status 為 'finished'
        # 注意：resume_applications.application_id 對應的是 student_job_applications.id，不是 student_preferences.id
        # 需要從 preference_id 找到對應的 student_id 和 job_id，然後找到 student_job_applications.id
        cursor.execute("""
            SELECT sp.student_id, sp.job_id
            FROM student_preferences sp
            WHERE sp.id = %s
        """, (preference_id,))
        pref_info = cursor.fetchone()
        
        if pref_info:
            student_id = pref_info.get('student_id')
            job_id = pref_info.get('job_id')
            
            if student_id and job_id:
                # 查找對應的 student_job_applications.id（application_id）
                cursor.execute("""
                    SELECT sja.id AS application_id
                    FROM student_job_applications sja
                    WHERE sja.student_id = %s AND sja.job_id = %s
                    ORDER BY sja.applied_at DESC
                    LIMIT 1
                """, (student_id, job_id))
                app_info = cursor.fetchone()
                
                if app_info:
                    application_id = app_info.get('application_id')
                    # 更新 resume_applications 表
                    # 面試完成時，interview_result 保持為 'pending'（除非有明確的通過/失敗結果）
                    cursor.execute("""
                        UPDATE resume_applications
                        SET interview_status = 'finished',
                            updated_at = NOW()
                        WHERE application_id = %s AND job_id = %s
                    """, (application_id, job_id))
                    print(f"✅ [mark_interview_completed] 更新 resume_applications: application_id={application_id}, job_id={job_id}, interview_status='finished'")
                else:
                    print(f"⚠️ [mark_interview_completed] 找不到對應的 student_job_applications 記錄: student_id={student_id}, job_id={job_id}")
            else:
                print(f"⚠️ [mark_interview_completed] preference_id={preference_id} 缺少 student_id 或 job_id")
        else:
            print(f"⚠️ [mark_interview_completed] 找不到 preference_id={preference_id}")
        
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "已標記為面試完成"
        })
        
    except Exception as exc:
        traceback.print_exc()
        if 'conn' in locals():
            try:
                conn.rollback()
                cursor.close()
                conn.close()
            except:
                pass
        return jsonify({"success": False, "message": f"操作失敗：{str(exc)}"}), 500


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

@vendor_bp.route("/vendor/api/save_matching_sort", methods=["POST"])
def save_matching_sort():
    """保存廠商媒合排序結果"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "請先登入"}), 403
    
    user_role = session.get("role")
    if user_role not in ["vendor", "teacher", "ta"]:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    try:
        vendor_id = session.get("user_id")
        data = request.get_json()
        
        if not data or not isinstance(data, dict) or "students" not in data:
            return jsonify({"success": False, "message": "資料格式錯誤"}), 400
        
        students = data.get("students", [])
        if not students or len(students) == 0:
            return jsonify({"success": False, "message": "請至少選擇一個學生"}), 400
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True, buffered=True)
        
        # 獲取廠商關聯的公司
        profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
        if not profile or not companies:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到廠商關聯的公司"}), 403
        
        company_ids = [c["id"] for c in companies]
        
        # 清除該廠商之前的媒合排序記錄（將 is_reserve 和 slot_index 設為 NULL/0）
        # 注意：resume_applications.application_id 對應的是 student_job_applications.id，不是 student_preferences.id
        try:
            cursor.execute("""
                UPDATE resume_applications ra
                JOIN student_job_applications sja ON ra.application_id = sja.id
                JOIN internship_companies ic ON sja.company_id = ic.id
                SET ra.is_reserve = 0,
                    ra.slot_index = NULL,
                    ra.updated_at = NOW()
                WHERE ic.id IN ({})
                AND (ra.is_reserve = 1 OR ra.slot_index IS NOT NULL)
            """.format(','.join(['%s'] * len(company_ids))), tuple(company_ids))
            deleted_count = cursor.rowcount
            deleted_count = cursor.rowcount
            print(f"✅ 已清除 {deleted_count} 筆舊的媒合排序記錄")
        except Exception as delete_error:
            print(f"⚠️ 清除舊媒合排序記錄時發生錯誤: {delete_error}")
            traceback.print_exc()
        
        # 插入新的媒合排序記錄到 vendor_preference_history
        inserted_count = 0
        print(f"📊 開始處理媒合排序，共 {len(students)} 筆學生資料")
        for idx, student in enumerate(students):
            student_id = student.get("student_id")
            job_id = student.get("job_id")
            preference_id = student.get("preference_id")
            student_name = student.get("student_name", "unknown")
            company_id = None
            print(f"  [{idx+1}/{len(students)}] 處理學生：{student_name}, student_id={student_id}, preference_id={preference_id}, job_id={job_id}")
            
            # 根據 job_id 找到對應的 company_id
            if job_id:
                cursor.execute("""
                    SELECT company_id FROM internship_jobs WHERE id = %s
                """, (job_id,))
                job_row = cursor.fetchone()
                if job_row:
                    company_id = job_row.get("company_id")
                    # 驗證該公司是否屬於該廠商
                    if company_id not in company_ids:
                        continue
            
            # 如果沒有 job_id，嘗試從 preference_id 獲取 company_id
            if not company_id and preference_id:
                cursor.execute("""
                    SELECT company_id FROM student_preferences WHERE id = %s
                """, (preference_id,))
                pref_row = cursor.fetchone()
                if pref_row:
                    company_id = pref_row.get("company_id")
                    # 驗證該公司是否屬於該廠商
                    if company_id not in company_ids:
                        print(f"    ⚠️ 跳過：公司ID {company_id} 不屬於該廠商（允許的公司ID：{company_ids}）")
                        continue
            
            if not preference_id:
                print(f"    ⚠️ 跳過學生 {student_name}：缺少 preference_id")
                continue
            
            if not student_id:
                print(f"    ⚠️ 跳過 preference_id {preference_id}：缺少 student_id")
                continue
            
            # 將媒合排序資訊存儲在 resume_applications 表的 is_reserve 和 slot_index 欄位中
            # 注意：resume_applications.application_id 對應的是 student_job_applications.id，不是 student_preferences.id
            try:
                slot_index_val = student.get('slot_index')
                is_reserve_val = student.get('is_reserve', False)
                
                # 從 preference_id 和 job_id 找到對應的 application_id（student_job_applications.id）
                application_id = None
                if preference_id and job_id and student_id:
                    # 從 student_preferences 獲取 company_id（如果還沒有）
                    if not company_id:
                        cursor.execute("""
                            SELECT company_id FROM student_preferences WHERE id = %s
                        """, (preference_id,))
                        pref_row = cursor.fetchone()
                        # 確保結果被完全讀取
                        if pref_row:
                            company_id = pref_row.get('company_id')
                        # 如果查詢返回 None，也要確保結果被讀取
                        cursor.fetchall()  # 清空任何剩餘的結果
                    
                    # 查詢 student_job_applications 表獲取 application_id
                    if company_id:
                        cursor.execute("""
                            SELECT id FROM student_job_applications
                            WHERE student_id = %s AND company_id = %s AND job_id = %s
                            ORDER BY applied_at DESC
                            LIMIT 1
                        """, (student_id, company_id, job_id))
                        sja_result = cursor.fetchone()
                        # 確保結果被完全讀取
                        cursor.fetchall()  # 清空任何剩餘的結果
                        if sja_result:
                            application_id = sja_result['id']
                            print(f"    🔍 找到 application_id: {application_id} (student_id={student_id}, company_id={company_id}, job_id={job_id})")
                
                if not application_id:
                    print(f"    ⚠️ 跳過：找不到對應的 application_id (preference_id={preference_id}, job_id={job_id}, student_id={student_id})")
                    continue
                
                # 更新或插入 resume_applications 記錄
                cursor.execute("""
                    SELECT id FROM resume_applications
                    WHERE application_id = %s AND job_id = %s
                """, (application_id, job_id))
                existing_ra = cursor.fetchone()
                # 確保結果被完全讀取
                cursor.fetchall()  # 清空任何剩餘的結果
                
                if existing_ra:
                    # 更新現有記錄的 is_reserve 和 slot_index
                    cursor.execute("""
                        UPDATE resume_applications
                        SET is_reserve = %s,
                            slot_index = %s,
                            updated_at = NOW()
                        WHERE application_id = %s AND job_id = %s
                    """, (1 if is_reserve_val else 0, slot_index_val, application_id, job_id))
                    print(f"    ✅ 更新 resume_applications: id={existing_ra['id']}, application_id={application_id}, job_id={job_id}, slot_index={slot_index_val}, is_reserve={is_reserve_val}")
                else:
                    # 創建新記錄
                    cursor.execute("""
                        INSERT INTO resume_applications
                        (application_id, job_id, apply_status, interview_status, interview_result, is_reserve, slot_index, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    """, (application_id, job_id, 'uploaded', 'none', 'pending', 1 if is_reserve_val else 0, slot_index_val))
                    print(f"    ✅ 創建 resume_applications: application_id={application_id}, job_id={job_id}, slot_index={slot_index_val}, is_reserve={is_reserve_val}")
                
                inserted_count += 1
                print(f"✅ 已保存媒合排序記錄到 resume_applications：preference_id={preference_id}, application_id={application_id}, student_id={student_id}, slot_index={slot_index_val}, is_reserve={is_reserve_val}")
            except Exception as insert_error:
                print(f"❌ 保存媒合排序記錄失敗：{insert_error}")
                traceback.print_exc()
                continue
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": f"已成功保存 {inserted_count} 筆媒合排序資料"
        })
        
    except Exception as exc:
        traceback.print_exc()
        if 'conn' in locals():
            try:
                conn.rollback()
                cursor.close()
                conn.close()
            except:
                pass
        return jsonify({"success": False, "message": f"保存失敗：{str(exc)}"}), 500


@vendor_bp.route("/vendor/api/get_matching_sort", methods=["GET"])
def get_matching_sort():
    """獲取廠商媒合排序結果（供科助查看）"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "請先登入"}), 403
    
    user_role = session.get("role")
    if user_role not in ["vendor", "teacher", "ta"]:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 從 resume_applications 表讀取媒合排序資訊（存儲在 is_reserve 和 slot_index 欄位中）
        # 注意：resume_applications.application_id 對應的是 student_job_applications.id，不是 student_preferences.id
        base_fields = """
                    ra.id,
                    ra.application_id,
                    sja.student_id,
                    sja.company_id,
                    ic.company_name,
                    ra.job_id,
                    ij.title AS job_title,
                    u.name AS student_name,
                    u.username AS student_number,
                    u.email AS student_email,
                    c.name AS class_name,
                    c.department AS class_department,
                    ra.is_reserve,
                    ra.slot_index,
                    sp.id AS preference_id,
                    ra.updated_at AS created_at
        """
        
        # 構建 WHERE 條件（從 resume_applications 表的 is_reserve 和 slot_index 讀取媒合排序資訊）
        where_condition = "AND (ra.is_reserve = 1 OR ra.slot_index IS NOT NULL)"
        company_filter = request.args.get("company_id", type=int)
        if company_filter:
            where_condition += f" AND sja.company_id = {company_filter}"
        
        # 如果是廠商，只返回該廠商相關公司的排序結果
        # 如果是老師/TA，返回所有廠商的排序結果
        if user_role == "vendor":
            vendor_id = session.get("user_id")
            # 獲取廠商的公司列表
            profile, companies, _ = _get_vendor_scope(cursor, vendor_id)
            company_ids = [c["id"] for c in companies] if companies else []
            if company_ids:
                placeholders = ','.join(['%s'] * len(company_ids))
                where_condition += f" AND sja.company_id IN ({placeholders})"
                query = f"""
                    SELECT 
                        {base_fields}
                    FROM resume_applications ra
                    JOIN student_job_applications sja ON ra.application_id = sja.id
                    LEFT JOIN student_preferences sp ON sja.student_id = sp.student_id 
                        AND sja.company_id = sp.company_id 
                        AND sja.job_id = sp.job_id
                    LEFT JOIN internship_companies ic ON sja.company_id = ic.id
                    LEFT JOIN internship_jobs ij ON ra.job_id = ij.id
                    LEFT JOIN users u ON sja.student_id = u.id
                    LEFT JOIN classes c ON u.class_id = c.id
                    WHERE 1=1
                    {where_condition}
                    ORDER BY sja.company_id, COALESCE(ra.job_id, 0), 
                        ra.is_reserve ASC,
                        ra.slot_index ASC,
                        ra.id ASC
                """
                cursor.execute(query, tuple(company_ids))
            else:
                cursor.execute("SELECT 1 WHERE 1=0")  # 返回空結果
        else:
            # 老師/TA 可以查看所有廠商的排序結果
            query = f"""
                SELECT 
                    {base_fields}
                FROM resume_applications ra
                JOIN student_job_applications sja ON ra.application_id = sja.id
                LEFT JOIN student_preferences sp ON sja.student_id = sp.student_id 
                    AND sja.company_id = sp.company_id 
                    AND sja.job_id = sp.job_id
                LEFT JOIN internship_companies ic ON sja.company_id = ic.id
                LEFT JOIN internship_jobs ij ON ra.job_id = ij.id
                LEFT JOIN users u ON sja.student_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                WHERE 1=1
                {where_condition}
                ORDER BY sja.company_id, COALESCE(ra.job_id, 0),
                    ra.is_reserve ASC,
                    ra.slot_index ASC,
                    ra.id ASC
            """
            cursor.execute(query)
        
        results = cursor.fetchall() or []
        
        # 格式化結果，直接從 is_reserve 和 slot_index 欄位讀取
        formatted_results = []
        for result in results:
            formatted_results.append({
                "id": result.get("id"),
                "vendor_id": None,  # resume_applications 表沒有 reviewer_id
                "vendor_name": None,
                "company_id": result.get("company_id"),
                "company_name": result.get("company_name"),
                "job_id": result.get("job_id"),
                "job_title": result.get("job_title"),
                "student_id": result.get("student_id"),
                "student_name": result.get("student_name"),
                "student_number": result.get("student_number"),
                "student_email": result.get("student_email"),
                "class_name": result.get("class_name"),
                "class_department": result.get("class_department"),
                "preference_id": result.get("preference_id"),
                "slot_index": result.get("slot_index"),
                "is_reserve": bool(result.get("is_reserve", 0)),
                "created_at": _format_datetime(result.get("created_at"))
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "results": formatted_results
        })
        
    except Exception as exc:
        traceback.print_exc()
        if 'conn' in locals():
            try:
                cursor.close()
                conn.close()
            except:
                pass
        return jsonify({"success": False, "message": f"查詢失敗：{str(exc)}"}), 500


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