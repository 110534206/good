from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, send_file
from config import get_db
from io import BytesIO
from datetime import datetime
from collections import defaultdict
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from docx import Document
import traceback
import pandas as pd

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
# 志願填寫頁面 (GET/POST)
# -------------------------
@preferences_bp.route("/fill_preferences", methods=["GET", "POST"])
def fill_preferences():
    # 登入檢查 (學生)
    if "user_id" not in session or session.get("role") != "student":
        return redirect(url_for("auth_bp.login_page"))

    student_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    message = None

    try:
        # 取得所有已通過的公司（供選擇）
        cursor.execute("SELECT id, company_name FROM internship_companies WHERE status = 'approved' ORDER BY company_name")
        companies = cursor.fetchall()

        # 讀出學生目前已填寫的志願（若有）
        cursor.execute("""
            SELECT preference_order, company_id, job_id
            FROM student_preferences
            WHERE student_id = %s
            ORDER BY preference_order
        """, (student_id,))
        prefs = cursor.fetchall()

        # 轉成前端方便使用格式（index 0 -> 第1志願）
        submitted_preferences = [None] * 5
        submitted_job_ids = [None] * 5
        for p in prefs:
            order = p.get('preference_order')
            if order and 1 <= order <= 5:
                submitted_preferences[order - 1] = p.get('company_id')
                submitted_job_ids[order - 1] = p.get('job_id')

        # 處理 POST 提交
        if request.method == "POST":
            preferences = []
            for i in range(1, 6):
                company_id = request.form.get(f"company_{i}")
                job_title = request.form.get(f"job_{i}")  # 前端傳來的是職缺名稱 title

                if company_id:
                    job_id = None
                    if job_title:
                        # 依公司與職缺名稱查 job.id
                        cursor.execute(
                            "SELECT id FROM internship_jobs WHERE company_id = %s AND title = %s",
                            (company_id, job_title)
                        )
                        job_row = cursor.fetchone()
                        job_id = job_row["id"] if job_row else None

                    preferences.append((student_id, i, company_id, job_id, datetime.now()))

            # 防呆：至少選一個；不可重複選同公司（後端最終驗證）
            if not preferences:
                message = "⚠️ 請至少填寫一個志願。"
            else:
                selected_companies = [p[2] for p in preferences]
                if len(selected_companies) != len(set(selected_companies)):
                    message = "⚠️ 不可重複選擇相同公司（請重新檢查）。"
                else:
                    # 刪除舊資料並寫入新資料
                    cursor.execute("DELETE FROM student_preferences WHERE student_id = %s", (student_id,))
                    cursor.executemany("""
                        INSERT INTO student_preferences (student_id, preference_order, company_id, job_id, submitted_at)
                        VALUES (%s, %s, %s, %s, %s)
                    """, preferences)
                    conn.commit()
                    message = "✅ 志願序已成功送出。"

                    # 重新讀取已填寫值
                    cursor.execute("""
                        SELECT preference_order, company_id
                        FROM student_preferences
                        WHERE student_id = %s
                        ORDER BY preference_order
                    """, (student_id,))
                    prefs = cursor.fetchall()
                    submitted_preferences = [None] * 5
                    for p in prefs:
                        order = p.get('preference_order')
                        if order and 1 <= order <= 5:
                            submitted_preferences[order - 1] = p.get('company_id')

        return render_template("preferences/fill_preferences.html",
                               companies=companies,
                               submitted_preferences=submitted_preferences,
                               message=message)
    except Exception as e:
        traceback.print_exc()
        return "伺服器錯誤", 500
    finally:
        cursor.close()
        conn.close()
        
# -------------------------
# 班導 / 主任 查看志願（可讓主任選班級）
# -------------------------
@preferences_bp.route("/review_preferences", methods=["GET"])
def review_preferences():
    if "user_id" not in session or session.get("role") not in ["teacher", "director"]:
        return redirect(url_for("auth_bp.login_page"))

    user_id = session["user_id"]
    role = session["role"]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        classes = []
        selected_class = None

        if role == "director":
            # 主任可選所有班級
            cursor.execute("SELECT id, name FROM classes ORDER BY name")
            classes = cursor.fetchall()
            selected_class = request.args.get("class_id") or (classes[0]["id"] if classes else None)
        else:
            # 老師（班導）只看自己是班導的班級
            cursor.execute("""
                SELECT c.id, c.name
                FROM classes c
                JOIN classes_teacher ct ON c.id = ct.class_id
                WHERE ct.teacher_id = %s AND ct.role = '班導師'
                """, (user_id,))
            classes = cursor.fetchall()
            selected_class = request.args.get("class_id") or (classes[0]["id"] if classes else None)

        if not selected_class:
            return "你沒有可檢視的班級或無授權。", 403

        rows = get_class_preferences(cursor, selected_class)
        # 整理成 student -> list of prefs
        student_data = defaultdict(list)
        for r in rows:
            if r.get('preference_order') and r.get('company_name'):
                student_data[r['student_name']].append({
                    'order': r['preference_order'],
                    'company': r['company_name'],
                    'job_title': r.get('job_title'),
                    'submitted_at': r.get('submitted_at').strftime('%Y-%m-%d %H:%M') if r.get('submitted_at') else ''
                })

        return render_template("preferences/review_preferences.html",
                               classes=classes,
                               selected_class=int(selected_class),
                               student_data=student_data,
                               role=role)
    except Exception as e:
        traceback.print_exc()
        return "伺服器錯誤", 500
    finally:
        cursor.close()
        conn.close()


# -------------------------
# 匯出（excel / word / pdf）
# route example: /preferences/export/123/excel
# -------------------------
@preferences_bp.route("/export/<int:class_id>/<string:fmt>", methods=["GET"])
def export_preferences(class_id, fmt):
    if "user_id" not in session or session.get("role") not in ["teacher", "director"]:
        return redirect(url_for("auth_bp.login_page"))

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        rows = get_class_preferences(cursor, class_id)
        if not rows:
            return "此班級無資料", 404

        # 取得班級名稱
        cursor.execute("SELECT name FROM classes WHERE id = %s", (class_id,))
        c = cursor.fetchone()
        class_name = c['name'] if c else f"class_{class_id}"

        # 組成 DataFrame（方便轉檔）
        df_rows = []
        for r in rows:
            df_rows.append({
                "學生姓名": r.get('student_name') or '',
                "學號": r.get('student_number') or '',
                "志願序": r.get('preference_order') or '',
                "公司名稱": r.get('company_name') or '',
                "職缺": r.get('job_title') or '',
                "公司地址": r.get('company_address') or '',
                "聯絡人": r.get('contact_name') or '',
                "聯絡電話": r.get('contact_phone') or '',
                "聯絡信箱": r.get('contact_email') or '',
                "提交時間": r.get('submitted_at').strftime('%Y-%m-%d %H:%M') if r.get('submitted_at') else ''
            })
        df = pd.DataFrame(df_rows)

        if fmt == "excel":
            buf = BytesIO()
            # 使用 pandas to_excel（簡單）
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="志願資料")
            buf.seek(0)
            filename = f"{class_name}_志願序_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            return send_file(buf, as_attachment=True, download_name=filename,
                             mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        elif fmt == "word":
            doc = Document()
            doc.add_heading(f"{class_name} - 學生志願序", 0)
            table = doc.add_table(rows=1, cols=len(df.columns))
            hdr_cells = table.rows[0].cells
            for i, col in enumerate(df.columns):
                hdr_cells[i].text = col
            for _, row in df.iterrows():
                r_cells = table.add_row().cells
                for i, val in enumerate(row):
                    r_cells[i].text = str(val)
            buf = BytesIO()
            doc.save(buf)
            buf.seek(0)
            filename = f"{class_name}_志願序_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
            return send_file(buf, as_attachment=True, download_name=filename,
                             mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        elif fmt == "pdf":
            buf = BytesIO()
            # 註冊中文字型（使用 reportlab 的 CID font）
            pdfmetrics.registerFont(UnicodeCIDFont('HeiseiMin-W3'))

            doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                                    leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18)
            styles = getSampleStyleSheet()
            normal = ParagraphStyle('NormalWrap', parent=styles['Normal'], fontName='HeiseiMin-W3', fontSize=8, leading=10)

            # 標題
            title_style = ParagraphStyle('Title', parent=styles['Heading1'], alignment=1, fontSize=14, fontName='HeiseiMin-W3', textColor=colors.HexColor('#0066CC'))
            story = [Paragraph(f"{class_name} - 學生志願序", title_style), Spacer(1, 8)]

            # Table headers + data
            headers = ["學生姓名","學號","志願序","公司名稱","職缺","公司地址","聯絡人","聯絡電話","聯絡信箱","提交時間"]
            data = [headers]
            for _, row in df.iterrows():
                data.append([Paragraph(str(row[col]), normal) for col in df.columns])

            table = Table(data, repeatRows=1, colWidths=None)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0066CC')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
                ('FONTNAME', (0, 0), (-1, -1), 'HeiseiMin-W3'),
            ]))
            story.append(table)
            doc.build(story)
            buf.seek(0)
            filename = f"{class_name}_志願序_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            return send_file(buf, as_attachment=True, download_name=filename, mimetype='application/pdf')
        else:
            return "格式錯誤", 400

    except Exception as e:
        traceback.print_exc()
        return "伺服器錯誤", 500
    finally:
        cursor.close()
        conn.close()
