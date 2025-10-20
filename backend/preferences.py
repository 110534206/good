from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from config import get_db
from datetime import datetime
import traceback

preferences_bp = Blueprint("preferences_bp", __name__)

# -------------------------
# 共用：取得班級學生志願（與欄位）
# -------------------------
def get_class_preferences(cursor, class_id):
    """
    依照你原本 schema 回傳類似的欄位。
    回傳 rows: student_id, student_name, student_number, preference_order, company_name, job_title, submitted_at,
                 company_address, contact_name, contact_phone, contact_email
    """
    cursor.execute("""
        SELECT 
            u.id AS student_id,
            u.name AS student_name,
            u.username AS student_number,
            sp.preference_order,
            sp.submitted_at,
            ic.id AS company_id,
            ic.company_name,
            ic.company_address,
            ic.contact_name,
            ic.contact_phone,
            ic.contact_email,
            ij.id AS job_id,
            ij.title AS job_title
        FROM users u
        LEFT JOIN student_preferences sp ON u.id = sp.student_id
        LEFT JOIN internship_companies ic ON sp.company_id = ic.id
        LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
        WHERE u.class_id = %s AND u.role = 'student'
        ORDER BY u.name, sp.preference_order
    """, (class_id,))
    return cursor.fetchall()

# -------------------------
# 志願填寫頁面
# -------------------------
@preferences_bp.route("/fill_preferences", methods=["GET"])
def fill_preferences_page():
    # 權限檢查：需為學生登入
    if "user_id" not in session or session.get("role") != "student":
        return redirect(url_for("auth_bp.login_page"))

    student_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 1) 取得所有已核准的公司（id, name）
        cursor.execute("SELECT id, company_name AS name FROM internship_companies WHERE status='approved'")
        companies = cursor.fetchall() or []

        # 2) 計算每家公司總名額 (SUM of slots)
        cursor.execute("""
            SELECT company_id, COALESCE(SUM(slots), 0) AS total_slots
            FROM internship_jobs
            GROUP BY company_id
        """)
        job_slots_raw = cursor.fetchall() or []
        # job_slots: { company_id(str): total_slots(int), ... }
        job_slots = {str(row['company_id']): int(row['total_slots'] or 0) for row in job_slots_raw}

        # 3) 讀取學生已填寫的志願（若有）
        cursor.execute("""
            SELECT preference_order, company_id, job_id, job_title
            FROM student_preferences
            WHERE student_id=%s
            ORDER BY preference_order
        """, (student_id,))
        prefs = cursor.fetchall() or []

        # submitted: { order: row, ... } （方便 template 使用）
        submitted = {int(p['preference_order']): p for p in prefs}

        # 4) 計算學生已使用每家公司多少次（以同公司出現次數計）
        student_used_slots = {}
        for p in prefs:
            cid = p.get('company_id')
            if cid is not None:
                student_used_slots[cid] = student_used_slots.get(cid, 0) + 1

        # 5) 計算每家公司對該學生還剩多少可選次數
        company_remaining = {}
        for cid, total in job_slots.items():
            used = student_used_slots.get(cid, 0)
            remain = max(int(total) - int(used), 0)
            company_remaining[cid] = remain

        return render_template(
            "preferences/fill_preferences.html",
            companies=companies,
            submitted=submitted,
            job_slots=job_slots,
            company_remaining=company_remaining
        )

    except Exception as e:
        traceback.print_exc()
        return "伺服器錯誤", 500

    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass

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
        jobs = cursor.fetchall() or []
        return jsonify({"success": True, "jobs": jobs})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "查詢失敗"})
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


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
        # 公司基本資料
        cursor.execute("""
            SELECT id, company_name, company_address, contact_name, contact_phone, contact_email
            FROM internship_companies WHERE id=%s
        """, (company_id,))
        company = cursor.fetchone()

        # 該公司所有職缺（供前端顯示）
        cursor.execute("""
            SELECT id, title, department, work_time, period, slots, remark
            FROM internship_jobs WHERE company_id=%s
        """, (company_id,))
        jobs = cursor.fetchall() or []

        return jsonify({"success": True, "company": company, "jobs": jobs})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "查詢公司資料失敗"})
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


# -------------------------
# 儲存學生志願
# -------------------------
@preferences_bp.route("/api/save_preferences", methods=["POST"])
def save_preferences():
    # 權限檢查
    if "user_id" not in session or session.get("role") != "student":
        return jsonify({"success": False, "message": "未授權"}), 403

    student_id = session["user_id"]
    data = request.get_json(silent=True) or {}
    preferences = data.get("preferences", [])

    # 基本驗證
    if not preferences:
        return jsonify({"success": False, "message": "請至少選擇一個志願。"}), 400

    # 限制最多 5 筆（與前端一致）
    MAX_PREFS = 5
    if len(preferences) > MAX_PREFS:
        return jsonify({"success": False, "message": f"最多只能填寫 {MAX_PREFS} 個志願。"}), 400

    conn = get_db()
    # 為了 fetch 與寫入都方便，這裡使用 dictionary cursor（也可以分開用兩個 cursor）
    cursor = conn.cursor(dictionary=True)

    try:
        # 1) 取得公司總 slots（供檢查）
        cursor.execute("""
            SELECT company_id, COALESCE(SUM(slots), 0) AS total_slots
            FROM internship_jobs
            GROUP BY company_id
        """)
        slots_data = cursor.fetchall() or []
        company_slots = {row["company_id"]: int(row["total_slots"] or 0) for row in slots_data}

        # 2) 檢查 preferences 裡是否有重複 company（允許重複，但不能超過 company_slots）
        #    計算每家公司在此次提交中被選的次數
        company_count_in_submission = {}
        for p in preferences:
            cid = p.get("company_id")
            # 輸入驗證：必須有 company_id 與 job_id
            if not cid or not p.get("job_id"):
                return jsonify({"success": False, "message": "每筆志願需包含 company_id 與 job_id。"}), 400

            company_count_in_submission[cid] = company_count_in_submission.get(cid, 0) + 1

        # 3) （重要）檢查：對於每家公司，學生已存在的記錄會被覆蓋（我們的策略是：先刪除該學生舊紀錄再插入）
        #    因此只需檢查「此次提交的次數」是否 <= company_slots[cid]
        for cid, cnt in company_count_in_submission.items():
            allowed = company_slots.get(int(cid), 0)
            if allowed == 0:
                # 若公司沒有在 internship_jobs 出現（slots=0），視為不可選
                return jsonify({"success": False, "message": f"公司(ID: {cid}) 尚無可用名額或不可選。"}), 400
            if cnt > allowed:
                return jsonify({"success": False, "message": f"公司(ID: {cid}) 的志願次數超過可用名額（{allowed}）。"}), 400

        # 4) 開 transaction：先刪除該學生舊的志願，再插入新的
        conn.start_transaction()

        cursor.execute("DELETE FROM student_preferences WHERE student_id=%s", (student_id,))

        # 插入順序：尊重 user 傳入的 order（前端使用 order 屬性）
        for p in preferences:
            pref_order = int(p.get("order"))
            company_id = int(p.get("company_id"))
            job_id = int(p.get("job_id"))

            # 檢查該 job_id 是否屬於該公司（避免非法組合）
            cursor.execute("""
                SELECT title FROM internship_jobs WHERE id=%s AND company_id=%s
            """, (job_id, company_id))
            job_row = cursor.fetchone()
            if not job_row:
                conn.rollback()
                return jsonify({"success": False, "message": f"職缺無效或不屬於該公司：job_id={job_id}, company_id={company_id}"}), 400

            job_title = job_row.get("title") if isinstance(job_row, dict) else job_row[0]

            cursor.execute("""
                INSERT INTO student_preferences
                (student_id, preference_order, company_id, job_id, job_title, submitted_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                student_id,
                pref_order,
                company_id,
                job_id,
                job_title,
                datetime.now()
            ))

        conn.commit()
        return jsonify({"success": True, "message": "志願序已成功送出。"})

    except Exception as e:
        # 若發生例外，rollback 並回報錯誤（後端 log）
        try:
            conn.rollback()
        except Exception:
            pass
        traceback.print_exc()
        return jsonify({"success": False, "message": "儲存失敗，請稍後再試。"}), 500
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass