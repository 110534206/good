from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from config import get_db
from datetime import datetime
import traceback

preferences_bp = Blueprint("preferences_bp", __name__)

# -------------------------
# 志願填寫頁面
# -------------------------
@preferences_bp.route("/fill_preferences", methods=["GET"])
def fill_preferences_page():
    if "user_id" not in session or session.get("role") != "student":
        return redirect(url_for("auth_bp.login_page"))

    student_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 所有核准公司
        cursor.execute("SELECT id, company_name FROM internship_companies WHERE status='approved'")
        companies = cursor.fetchall()

        # 各公司總名額數（slots）
        cursor.execute("""
            SELECT company_id, SUM(slots) AS total_slots
            FROM internship_jobs
            GROUP BY company_id
        """)
        job_slots_raw = cursor.fetchall()
        job_slots = {row['company_id']: row['total_slots'] or 0 for row in job_slots_raw}

        # 學生已填寫的志願
        cursor.execute("""
            SELECT preference_order, company_id, job_id, job_title
            FROM student_preferences
            WHERE student_id=%s
            ORDER BY preference_order
        """, (student_id,))
        prefs = cursor.fetchall()

        submitted = {p['preference_order']: p for p in prefs}

        return render_template(
            "preferences/fill_preferences.html",
            companies=companies,
            submitted=submitted,
            job_slots=job_slots  
        )

    except Exception as e:
        traceback.print_exc()
        return "伺服器錯誤", 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 取得該公司所有職缺
# -------------------------
@preferences_bp.route("/api/get_jobs_by_company", methods=["GET"])
def get_jobs_by_company():
    company_id = request.args.get("company_id")
    if not company_id:
        return jsonify({"success": False, "message": "缺少公司 ID"})
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id, title FROM internship_jobs WHERE company_id=%s
        """, (company_id,))
        jobs = cursor.fetchall()
        return jsonify({"success": True, "jobs": jobs})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "查詢失敗"})
    finally:
        cursor.close()
        conn.close()


# -------------------------
# 取得公司詳細資料
# -------------------------
@preferences_bp.route("/api/get_company_detail", methods=["GET"])
def get_company_detail():
    company_id = request.args.get("company_id")
    if not company_id:
        return jsonify({"success": False, "message": "缺少公司 ID"})
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id, company_name, company_address, contact_name, contact_phone, contact_email
            FROM internship_companies WHERE id=%s
        """, (company_id,))
        company = cursor.fetchone()

        cursor.execute("""
            SELECT title, department, work_time, period, slots, remark
            FROM internship_jobs WHERE company_id=%s
        """, (company_id,))
        jobs = cursor.fetchall()

        return jsonify({"success": True, "company": company, "jobs": jobs})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "查詢公司資料失敗"})
    finally:
        cursor.close()
        conn.close()


# -------------------------
# 儲存學生志願
# -------------------------
@preferences_bp.route("/api/save_preferences", methods=["POST"])
def save_preferences():
    if "user_id" not in session or session.get("role") != "student":
        return jsonify({"success": False, "message": "未授權"}), 403

    student_id = session["user_id"]
    data = request.get_json()
    preferences = data.get("preferences", [])

    # 必要性檢查
    if not preferences:
        return jsonify({"success": False, "message": "請至少選擇一個志願。"})

    # 檢查是否有重複公司
    company_ids = [p["company_id"] for p in preferences]
    if len(company_ids) != len(set(company_ids)):
        return jsonify({"success": False, "message": "公司不可重複填寫。"})

    # 限制最多5筆
    if len(preferences) > 5:
        return jsonify({"success": False, "message": "最多只能填寫五個志願。"})

    conn = get_db()
    cursor = conn.cursor()
    try:
        conn.start_transaction()

        # 清除原有志願
        cursor.execute("DELETE FROM student_preferences WHERE student_id=%s", (student_id,))

        # 檢查每一個 company_id 和 job_id 是否有效
        for p in preferences:
            cursor.execute("SELECT COUNT(*) FROM internship_companies WHERE id=%s AND status='approved'", (p["company_id"],))
            if cursor.fetchone()[0] == 0:
                conn.rollback()
                return jsonify({"success": False, "message": f"公司 ID {p['company_id']} 無效或未核准。"})

            cursor.execute("SELECT title FROM internship_jobs WHERE id=%s AND company_id=%s", (p["job_id"], p["company_id"]))
            result = cursor.fetchone()
            if not result:
                conn.rollback()
                return jsonify({"success": False, "message": f"職缺無效，ID: {p['job_id']}。"})

            # 若 job_title 與資料庫不符，可強制使用資料庫值
            job_title = result[0]

            cursor.execute("""
                INSERT INTO student_preferences 
                (student_id, preference_order, company_id, job_id, job_title, submitted_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                student_id,
                p["order"],
                p["company_id"],
                p["job_id"],
                job_title,
                datetime.now()
            ))

        conn.commit()
        return jsonify({"success": True, "message": "志願序已成功送出。"})
    except Exception:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "message": "儲存失敗，請稍後再試。"})
    finally:
        cursor.close()
        conn.close()