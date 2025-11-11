from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request, session

from config import get_db

vendor_bp = Blueprint('vendor', __name__)

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


def _format_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y/%m/%d %H:%M")
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.strftime("%Y/%m/%d %H:%M")
    except Exception:
        return str(value)


def _ensure_history_table(cursor):
    global HISTORY_TABLE_READY
    if HISTORY_TABLE_READY:
        return
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
    cursor.execute(
        "SELECT id, name, email FROM users WHERE id = %s AND role = 'vendor'",
        (vendor_id,),
    )
    return cursor.fetchone()


def _get_vendor_companies(cursor, vendor_id, vendor_email):
    params = [vendor_id]
    query = """
        SELECT id, company_name, contact_email
        FROM internship_companies
        WHERE uploaded_by_user_id = %s
    """
    if vendor_email:
        query += " OR contact_email = %s"
        params.append(vendor_email)
    cursor.execute(query, tuple(params))
    return cursor.fetchall() or []


def _record_history(cursor, preference_id, reviewer_id, action, comment):
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


def _notify_student(cursor, student_id, title, message, link_url="/vendor/resume-review"):
    cursor.execute(
        """
        INSERT INTO notifications (user_id, title, message, link_url, is_read, created_at)
        VALUES (%s, %s, %s, %s, 0, NOW())
        """,
        (student_id, title, message, link_url),
    )


def _fetch_latest_resume(cursor, student_id):
    cursor.execute(
        """
        SELECT r.id, r.original_filename, r.status, r.comment, r.note,
               r.created_at, r.updated_at, r.filepath
        FROM resumes r
        JOIN (
            SELECT user_id, MAX(created_at) AS max_created_at
            FROM resumes
            GROUP BY user_id
        ) latest ON latest.user_id = r.user_id AND latest.max_created_at = r.created_at
        WHERE r.user_id = %s
        """,
        (student_id,),
    )
    return cursor.fetchone()


def _fetch_skill_tags(cursor, student_id):
    skills = []
    cursor.execute(
        "SELECT CertName FROM Student_Certifications WHERE StuID = %s ORDER BY CertName",
        (student_id,),
    )
    certifications = cursor.fetchall() or []
    skills.extend([row["CertName"] for row in certifications if row.get("CertName")])

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
                "timestamp": _format_datetime(datetime.utcnow()),
                "text": f"目前狀態：{STATUS_LABELS[current_status]}",
                "type": "status",
            }
        )

    # 依時間由新到舊排序
    history.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return history


def _build_application_summary_row(row):
    submitted_at = row.get("submitted_at")
    skills = []
    if row.get("skill_tags"):
        skills = row["skill_tags"].split("||")
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
    }


def _fetch_application_detail(cursor, preference_id):
    cursor.execute(
        """
        SELECT
            sp.id,
            sp.status,
            sp.preference_order,
            sp.submitted_at,
            sp.student_id,
            sp.company_id,
            sp.job_id,
            sp.job_title,
            ic.company_name,
            ic.contact_person,
            ic.contact_email,
            ic.contact_phone,
            ij.title AS job_title_db,
            u.name AS student_name,
            u.username AS student_number,
            u.email AS student_email,
            c.id AS class_id,
            c.name AS class_name,
            c.department,
            si.Phone AS student_phone,
            si.Autobiography AS autobiography,
            si.PhotoPath AS photo_path,
            si.Email AS info_email,
            si.Address AS student_address,
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
    cursor.execute(
        """
        SELECT
            sp.id,
            sp.student_id,
            sp.company_id,
            sp.status,
            ic.company_name,
            ic.uploaded_by_user_id,
            ic.contact_email
        FROM student_preferences sp
        JOIN internship_companies ic ON sp.company_id = ic.id
        WHERE sp.id = %s
        """,
        (preference_id,),
    )
    record = cursor.fetchone()
    if not record:
        return None

    allowed = record.get("uploaded_by_user_id") == vendor_id
    if not allowed and vendor_email:
        allowed = record.get("contact_email") == vendor_email
    if not allowed:
        return None
    return record


@vendor_bp.route("/vendor/resume-review")
def vendor_resume_review():
    if "user_id" not in session or session.get("role") != "vendor":
        return render_template("auth/login.html")
    return render_template("user_shared/review_resumes.html")


@vendor_bp.route("/vendor/api/applications", methods=["GET"])
def list_applications():
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
            return jsonify({"items": [], "summary": {"pending": 0, "approved": 0, "rejected": 0, "new_this_week": 0}})

        companies = _get_vendor_companies(cursor, vendor_id, profile.get("email"))
        if not companies:
            return jsonify({"items": [], "summary": {"pending": 0, "approved": 0, "rejected": 0, "new_this_week": 0}})

        company_ids = [company["id"] for company in companies]
        placeholders = ", ".join(["%s"] * len(company_ids))
        params = company_ids[:]

        query = f"""
            SELECT
                sp.id,
                sp.status,
                sp.submitted_at,
                sp.student_id,
                sp.company_id,
                sp.job_id,
                sp.job_title,
                ic.company_name,
                ij.title AS job_title_db,
                u.name AS student_name,
                u.username AS student_number,
                c.id AS class_id,
                CONCAT_WS(' ', c.name, c.department) AS school_label,
                si.Autobiography AS autobiography,
                si.PhotoPath AS photo_path,
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
        return jsonify({"error": f"查詢失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/applications/<int:application_id>", methods=["GET"])
def retrieve_application(application_id):
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
        return jsonify({"error": f"查詢失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


def _handle_status_update(application_id, action):
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

        _record_history(cursor, application_id, vendor_id, action, comment or None)
        conn.commit()

        detail = _fetch_application_detail(cursor, application_id)
        if not detail:
            return jsonify({"error": "更新成功但無法重新載入資料"}), 200
        return jsonify({"item": detail})
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": f"操作失敗：{exc}"}), 500
    finally:
        cursor.close()
        conn.close()


@vendor_bp.route("/vendor/api/applications/<int:application_id>/approve", methods=["POST"])
def approve_application(application_id):
    return _handle_status_update(application_id, "approve")


@vendor_bp.route("/vendor/api/applications/<int:application_id>/reject", methods=["POST"])
def reject_application(application_id):
    return _handle_status_update(application_id, "reject")


@vendor_bp.route("/vendor/api/applications/<int:application_id>/reopen", methods=["POST"])
def reopen_application(application_id):
    return _handle_status_update(application_id, "reopen")


@vendor_bp.route("/vendor/api/applications/<int:application_id>/comment", methods=["POST"])
def comment_application(application_id):
    return _handle_status_update(application_id, "comment")