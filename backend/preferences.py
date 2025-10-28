from flask import Blueprint, render_template, request, jsonify, session, send_file, redirect, url_for
from config import get_db
from datetime import datetime
import traceback
from collections import defaultdict
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

preferences_bp = Blueprint("preferences_bp", __name__)

# -------------------------
# 共用：取得班級學生志願（與欄位）
# -------------------------
def get_class_preferences(cursor, class_id):
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
# -------------------------
# 志願填寫頁面 (最終修正版本)
# -------------------------
@preferences_bp.route("/fill_preferences", methods=["GET"])
def fill_preferences_page():
    is_student = ("user_id" in session and session.get("role") == "student")
    student_id = session.get("user_id") if is_student else None
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 1) 查詢學生已填志願
        prefs = []
        student_used_jobs = set() 
        if is_student:
            cursor.execute("""
                SELECT preference_order, company_id, job_id, 
                       (SELECT title FROM internship_jobs WHERE id = sp.job_id) AS job_title
                FROM student_preferences sp
                WHERE student_id=%s
                ORDER BY preference_order
            """, (student_id,))
            prefs = cursor.fetchall() or []
            student_used_jobs = {p['job_id'] for p in prefs if p.get('job_id') is not None} 

        submitted = {int(p['preference_order']): p for p in prefs}

        # 2) 取得所有已核准公司及其所有職缺 ID 與總名額 (已移除 ij.is_active 條件)
        cursor.execute("""
            SELECT 
                ic.id AS company_id, 
                ic.company_name AS name,
                GROUP_CONCAT(ij.id) AS job_ids_list,
                SUM(ij.slots) AS total_slots
            FROM internship_companies ic
            JOIN internship_jobs ij ON ic.id = ij.company_id
            WHERE ic.status='approved'
            GROUP BY ic.id, ic.company_name
            HAVING SUM(ij.slots) > 0;
        """)
        company_data = cursor.fetchall() or []
        
        # 3) 計算公司剩餘職缺數 (company_remaining) 並篩選要顯示的公司列表
        company_remaining = {}
        companies_to_display = []
        
        for c in company_data:
            cid = str(c['company_id'])
            
            # --- 計算剩餘名額：總 slots 減去所有學生已選該公司職缺的總次數 ---
            
            # 取得所有學生已選該公司職缺的次數
            cursor.execute("SELECT COUNT(*) AS used_count FROM student_preferences WHERE company_id = %s", (cid,))
            company_used_count = cursor.fetchone()['used_count']
            
            # 總職缺 slots 減去所有學生已選次數 
            remaining_slots = c['total_slots'] - company_used_count
            
            # -------------------------------------------------------------------
            
            company_remaining[cid] = remaining_slots
            
            # 只有當公司還有剩餘職缺 (名額 > 0) 時，才讓它出現在下拉選單
            if remaining_slots > 0:
                companies_to_display.append({'id': c['company_id'], 'name': c['name']})
                
        # 4) 確保公司列表唯一
        unique_companies = []
        seen_ids = set()
        for company in companies_to_display:
            if company['id'] not in seen_ids:
                unique_companies.append(company)
                seen_ids.add(company['id'])


        return render_template(
            "preferences/fill_preferences.html",
            # 使用唯一且有剩餘名額的公司列表
            companies=unique_companies, 
            submitted=submitted,
            company_remaining=company_remaining, 
            preview=(not is_student)
        )

    except Exception:
        traceback.print_exc()
        return "伺服器錯誤", 500
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass
   
# -------------------------
# 取得公司所有職缺
# -------------------------
@preferences_bp.route("/api/get_jobs_by_company", methods=["GET"])
def get_jobs_by_company():
    company_id = request.args.get("company_id")
    if not company_id:
        return jsonify({"success": False, "message": "缺少公司 ID"})

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, title FROM internship_jobs WHERE company_id=%s", (company_id,))
        jobs = cursor.fetchall() or []
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
            SELECT id, title, department, work_time, period, slots, remark
            FROM internship_jobs WHERE company_id=%s
        """, (company_id,))
        jobs = cursor.fetchall() or []

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
    data = request.get_json(silent=True) or {}
    preferences = data.get("preferences", [])

    if not preferences:
        return jsonify({"success": False, "message": "請至少選擇一個志願。"}), 400

    MAX_PREFS = 5
    if len(preferences) > MAX_PREFS:
        return jsonify({"success": False, "message": f"最多只能填寫 {MAX_PREFS} 個志願。"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 1) 核心校驗：檢查所有提交的 job_id 是否唯一
        submitted_job_ids = [int(p.get("job_id")) for p in preferences if p.get("job_id")]
        if len(submitted_job_ids) != len(set(submitted_job_ids)):
            return jsonify({"success": False, "message": "每個志願必須選擇不同的職缺 (職缺 ID 不得重複)。"}), 400
        
        # 2) 檢查每筆志願是否包含 company_id 和 job_id
        for p in preferences:
            cid = p.get("company_id")
            jid = p.get("job_id")
            if not cid or not jid:
                return jsonify({"success": False, "message": "每筆志願需包含 company_id 與 job_id。"}), 400

        # *** 已移除舊的步驟：不再檢查公司選擇總次數 ***

        # 3) 清除舊資料並插入新志願
        cursor.execute("DELETE FROM student_preferences WHERE student_id=%s", (student_id,))

        for p in preferences:
            pref_order = int(p.get("order"))
            company_id = int(p.get("company_id"))
            job_id = int(p.get("job_id"))

            # 再次驗證職缺有效性，防止使用者竄改資料
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
            """, (student_id, pref_order, company_id, job_id, job_title, datetime.now()))

        conn.commit()
        return jsonify({"success": True, "message": "志願序已成功送出。"})

    except Exception:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "message": "儲存失敗，請稍後再試。"}), 500
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass
