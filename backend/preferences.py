from flask import Blueprint, render_template, request, jsonify, session,send_file, redirect, url_for
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
    # 允許未登入/非學生以預覽模式進入
    is_student = ("user_id" in session and session.get("role") == "student")
    student_id = session.get("user_id") if is_student else None
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 1) 取得所有公司（id, name）
        cursor.execute("SELECT id, company_name AS name FROM internship_companies")
        companies = cursor.fetchall() or []

        # 2) 簡化：不再計算名額，改為取得所有公司的 ID 列表
        # job_slots: { company_id(str): 1, ... } (1表示該公司可選)
        job_slots = {str(c['id']): 1 for c in companies} #

        # 3) 讀取學生已填寫的志願（若有，預覽模式則為空）
        prefs = []
        if is_student:
            cursor.execute("""
                SELECT preference_order, company_id, job_id, job_title
                FROM student_preferences
                WHERE student_id=%s
                ORDER BY preference_order
            """, (student_id,))
            prefs = cursor.fetchall() or []

        # submitted: { order: row, ... } （方便 template 使用）
        submitted = {int(p['preference_order']): p for p in prefs}

        return render_template(
            "preferences/fill_preferences.html",
            companies=companies,
            submitted=submitted,
            job_slots=job_slots, # 僅用於前端 JS 判斷已選公司，不再代表名額
            company_remaining={}, 
            preview=(not is_student)
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

    MAX_PREFS = 5
    if len(preferences) > MAX_PREFS:
        return jsonify({"success": False, "message": f"最多只能填寫 {MAX_PREFS} 個志願。"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 1) 檢查公司是否重複 - 移除此邏輯，以配合前端的「公司可重複選，職缺互斥」
        selected_job_ids = set() # 用來檢查職缺是否重複，以防萬一
        for p in preferences:
            cid = p.get("company_id")
            jid = p.get("job_id")
            if not cid or not jid:
                return jsonify({"success": False, "message": "每筆志願需包含 company_id 與 job_id。"}), 400
            
            # **重點：檢查職缺是否重複**
            if jid in selected_job_ids:
                return jsonify({"success": False, "message": f"職缺(ID: {jid}) 已在其他志願中選擇，同一職缺只能選擇一次。"}), 400
            selected_job_ids.add(jid)

        # 2) 刪除學生舊紀錄並插入新志願
        cursor.execute("DELETE FROM student_preferences WHERE student_id=%s", (student_id,))

        for p in preferences:
            pref_order = int(p.get("order"))
            company_id = int(p.get("company_id"))
            job_id = int(p.get("job_id"))

            # 檢查 job_id 是否屬於該公司
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

        # 3) 提交 transaction
        conn.commit()
        return jsonify({"success": True, "message": "志願序已成功送出。"})

    except Exception:
        # rollback
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

# -------------------------
# API - 選擇角色
# -------------------------
@preferences_bp.route('/api/select_role', methods=['POST'])
def select_role():
    data = request.json
    username = data.get("username")
    role = data.get("role")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM users WHERE username=%s AND role=%s", (username, role))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if user:
        session["user_id"] = user["id"]
        session["role"] = role
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "無此角色"}), 404

# -------------------------
# 班導查看志願序
# -------------------------
@preferences_bp.route('/review_preferences')
def review_preferences():
    if 'username' not in session or session.get('role') not in ['teacher', 'director', "class_teacher"]:
        return redirect(url_for('auth_bp.login_page'))

    user_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 確認是否為班導
        cursor.execute("""
            SELECT c.id AS class_id
            FROM classes c
            JOIN classes_teacher ct ON c.id = ct.class_id
            WHERE ct.teacher_id = %s AND ct.role = '班導師'
        """, (user_id,))
        class_info = cursor.fetchone()
        if not class_info:
            return "你不是班導，無法查看志願序", 403

        class_id = class_info['class_id']

        # 查詢班上學生及其志願
        cursor.execute("""
            SELECT 
                u.id AS student_id,
                u.name AS student_name,
                sp.preference_order,
                ic.company_name,
                sp.submitted_at
            FROM users u
            LEFT JOIN student_preferences sp ON u.id = sp.student_id
            LEFT JOIN internship_companies ic ON sp.company_id = ic.id
            WHERE u.class_id = %s
            ORDER BY u.name, sp.preference_order
        """, (class_id,))
        results = cursor.fetchall()

        # 整理資料結構給前端使用
        student_data = defaultdict(list)
        for row in results:
            if row['preference_order'] and row['company_name']:
                student_data[row['student_name']].append({
                    'order': row['preference_order'],
                    'company': row['company_name'],
                    'submitted_at': row['submitted_at']
                })

        return render_template('preferences/review_preferences.html', student_data=student_data)

    except Exception as e:
        print("取得志願資料錯誤：", e)
        return "伺服器錯誤", 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# Excel 導出功能
# -------------------------
@preferences_bp.route('/export_preferences_excel')
def export_preferences_excel():
    if 'username' not in session or session.get('role') not in ['teacher', 'director']:
        return redirect(url_for('auth_bp.login_page'))

    user_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 確認是否為班導
        cursor.execute("""
        SELECT c.id AS class_id, c.name AS class_name
        FROM classes c
        JOIN classes_teacher ct ON c.id = ct.class_id
        WHERE ct.teacher_id = %s AND ct.role = '班導師'
        """, (user_id,))
        class_info = cursor.fetchone()
        if not class_info:
            return "你不是班導，無法導出志願序", 403

        class_id = class_info['class_id']
        class_name = class_info['class_name']

        # 查詢班上學生及其志願
        cursor.execute("""
            SELECT 
                u.id AS student_id,
                u.name AS student_name,
                u.username AS student_number, 
                sp.preference_order,
                ic.company_name,
                sp.submitted_at
            FROM users u
            LEFT JOIN student_preferences sp ON u.id = sp.student_id
            LEFT JOIN internship_companies ic ON sp.company_id = ic.id
            WHERE u.class_id = %s AND u.role = 'student'
            ORDER BY u.name, sp.preference_order
        """, (class_id,))
        results = cursor.fetchall()

        # 創建 Excel 工作簿
        wb = Workbook()
        ws = wb.active
        ws.title = f"{class_name}志願序"

        # 設定樣式
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="0066CC", end_color="0066CC", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

        # 寫入標題
        ws.merge_cells('A1:G1')
        title_cell = ws['A1']
        title_cell.value = f"{class_name} - 學生實習志願序統計表"
        title_cell.font = Font(bold=True, size=16, color="0066CC")
        title_cell.alignment = Alignment(horizontal="center", vertical="center")

        # 寫入日期
        ws.merge_cells('A2:G2')
        date_cell = ws['A2']
        date_cell.value = f"導出時間：{datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}"
        date_cell.alignment = Alignment(horizontal="center")

        # 設定表頭
        headers = ['學生姓名', '學號', '第一志願', '第二志願', '第三志願', '第四志願', '第五志願']
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=4, column=col)
            cell.value = header
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = border

        # 整理學生資料
        student_data = defaultdict(lambda: {
            'name': '',
            'student_number': '',
            'preferences': [''] * 5,
            'submitted_times': [''] * 5
        })

        for row in results:
            student_name = row['student_name']
            if student_name:
                student_data[student_name]['name'] = student_name
                student_data[student_name]['student_number'] = row['student_number'] or ''
                
                if row['preference_order'] and row['company_name']:
                    order = row['preference_order'] - 1  # 轉為 0-based index
                    if 0 <= order < 5:
                        student_data[student_name]['preferences'][order] = row['company_name']
                        if row['submitted_at']:
                            student_data[student_name]['submitted_times'][order] = row['submitted_at'].strftime('%m/%d %H:%M')

        # 寫入學生資料
        row_num = 5
        for student_name in sorted(student_data.keys()):
            data = student_data[student_name]
            
            # 學生姓名
            ws.cell(row=row_num, column=1, value=data['name']).border = border
            # 學號
            ws.cell(row=row_num, column=2, value=data['student_number']).border = border
            
            # 志願序
            for i in range(5):
                pref_text = data['preferences'][i]
                if pref_text and data['submitted_times'][i]:
                    pref_text += f"\n({data['submitted_times'][i]})"
                
                cell = ws.cell(row=row_num, column=3+i, value=pref_text)
                cell.border = border
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            
            row_num += 1

        # 添加統計資訊
        ws.cell(row=row_num + 1, column=1, value="統計資訊：").font = Font(bold=True)
        
        # 統計各公司被選擇次數
        company_counts = defaultdict(int)
        for data in student_data.values():
            for pref in data['preferences']:
                if pref:
                    company_counts[pref] += 1

        stats_row = row_num + 2
        ws.cell(row=stats_row, column=1, value="公司名稱").font = Font(bold=True)
        ws.cell(row=stats_row, column=2, value="被選擇次數").font = Font(bold=True)
        
        stats_row += 1
        for company, count in sorted(company_counts.items(), key=lambda x: x[1], reverse=True):
            ws.cell(row=stats_row, column=1, value=company)
            ws.cell(row=stats_row, column=2, value=count)
            stats_row += 1

        # 調整欄寬
        column_widths = [15, 12, 20, 20, 20, 20, 20]
        for i, width in enumerate(column_widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width

        # 設定行高
        for row in range(5, row_num):
            ws.row_dimensions[row].height = 40

        # 保存到記憶體
        excel_buffer = io.BytesIO()
        wb.save(excel_buffer)
        excel_buffer.seek(0)

        # 生成檔案名稱
        filename = f"{class_name}_學生志願序_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

        return send_file(
            excel_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        print("導出 Excel 錯誤：", e)
        return "伺服器錯誤", 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# word 導出功能
# -------------------------
@preferences_bp.route('/export_preferences_word')
def export_preferences_word():
    if 'username' not in session or session.get('role') not in ['teacher', 'director']:
        return redirect(url_for('auth_bp.login_page'))

    user_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 確認是否為班導
        cursor.execute("""
          SELECT c.id AS class_id, c.name AS class_name
          FROM classes c
          JOIN classes_teacher ct ON c.id = ct.class_id
          WHERE ct.teacher_id = %s AND ct.role = '班導師'
        """, (user_id,))
        class_info = cursor.fetchone()
        if not class_info:
            return "你不是班導，無法導出志願序", 403

        class_id = class_info['class_id']
        class_name = class_info['class_name']

        # 查詢學生志願
        cursor.execute("""
            SELECT 
                u.id AS student_id,
                u.name AS student_name,
                u.username AS student_number, 
                sp.preference_order,
                ic.company_name,
                sp.submitted_at
            FROM users u
            LEFT JOIN student_preferences sp ON u.id = sp.student_id
            LEFT JOIN internship_companies ic ON sp.company_id = ic.id
            WHERE u.class_id = %s AND u.role = 'student'
            ORDER BY u.name, sp.preference_order
        """, (class_id,))
        results = cursor.fetchall()

        # 整理資料
        student_data = defaultdict(lambda: {
            'name': '',
            'student_number': '',
            'preferences': [''] * 5,
            'submitted_times': [''] * 5
        })

        for row in results:
            student_name = row['student_name']
            if student_name:
                student_data[student_name]['name'] = student_name
                student_data[student_name]['student_number'] = row['student_number'] or ''
                if row['preference_order'] and row['company_name']:
                    order = row['preference_order'] - 1
                    if 0 <= order < 5:
                        student_data[student_name]['preferences'][order] = row['company_name']
                        if row['submitted_at']:
                            student_data[student_name]['submitted_times'][order] = row['submitted_at'].strftime('%m/%d %H:%M')

        # 建立 Word 文件
        doc = Document()
        title = doc.add_heading(f"{class_name} - 學生實習志願序統計表", 0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.add_paragraph(f"導出時間：{datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}")
        doc.add_paragraph("")

        # 學生表格
        table = doc.add_table(rows=1, cols=7)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.style = "Table Grid"
        headers = ['學生姓名', '學號', '第一志願', '第二志願', '第三志願', '第四志願', '第五志願']
        for i, header in enumerate(headers):
            table.rows[0].cells[i].text = header

        for student_name in sorted(student_data.keys()):
            data = student_data[student_name]
            row = table.add_row().cells
            row[0].text = data['name']
            row[1].text = data['student_number']
            for i in range(5):
                pref_text = data['preferences'][i]
                if pref_text and data['submitted_times'][i]:
                    pref_text += f"\n({data['submitted_times'][i]})"
                row[2+i].text = pref_text

        doc.add_paragraph("")
        doc.add_heading("統計資訊", level=1)

        # 統計資訊
        company_counts = defaultdict(int)
        for data in student_data.values():
            for pref in data['preferences']:
                if pref:
                    company_counts[pref] += 1

        if company_counts:
            stats_table = doc.add_table(rows=1, cols=2)
            stats_table.style = "Table Grid"
            stats_table.rows[0].cells[0].text = "公司名稱"
            stats_table.rows[0].cells[1].text = "被選擇次數"
            for company, count in sorted(company_counts.items(), key=lambda x: x[1], reverse=True):
                row = stats_table.add_row().cells
                row[0].text = company
                row[1].text = str(count)

        # 匯出檔案
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)

        filename = f"{class_name}_學生志願序_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"

        from flask import send_file
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )

    except Exception as e:
        print("導出 Word 錯誤：", e)
        traceback.print_exc()
        return "伺服器錯誤", 500

    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass

# -------------------------
# PDF 導出功能
# -------------------------
@preferences_bp.route('/export_preferences_pdf')
def export_preferences_pdf():
    if 'username' not in session or session.get('role') not in ['teacher', 'director']:
        return redirect(url_for('auth_bp.login_page'))

    user_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 確認是否為班導
        cursor.execute("""
        SELECT c.id AS class_id, c.name AS class_name
        FROM classes c
        JOIN classes_teacher ct ON c.id = ct.class_id
        WHERE ct.teacher_id = %s AND ct.role = '班導師'
        """, (user_id,))
        class_info = cursor.fetchone()
        if not class_info:
            return "你不是班導，無法導出志願序", 403

        class_id = class_info['class_id']
        class_name = class_info['class_name']

        # 查詢班上學生及其志願
        cursor.execute("""
            SELECT 
                u.id AS student_id,
                u.name AS student_name,
                u.username AS student_number, 
                sp.preference_order,
                ic.company_name,
                sp.submitted_at
            FROM users u
            LEFT JOIN student_preferences sp ON u.id = sp.student_id
            LEFT JOIN internship_companies ic ON sp.company_id = ic.id
            WHERE u.class_id = %s AND u.role = 'student'
            ORDER BY u.name, sp.preference_order
        """, (class_id,))
        results = cursor.fetchall()

        # 創建 PDF 緩衝區
        pdf_buffer = io.BytesIO()
        doc = SimpleDocTemplate(pdf_buffer, pagesize=A4, topMargin=1*inch, bottomMargin=1*inch)
        
        # 設定樣式
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            spaceAfter=30,
            alignment=1,  # 置中
            textColor=colors.HexColor('#0066CC')
        )
        
        normal_style = styles['Normal']
        normal_style.fontSize = 10

        # 建立內容
        story = []
        
        # 標題
        title = Paragraph(f"{class_name} - 學生實習志願序統計表", title_style)
        story.append(title)
        
        # 日期
        date_text = f"導出時間：{datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}"
        date_para = Paragraph(date_text, normal_style)
        story.append(date_para)
        story.append(Spacer(1, 20))

        # 整理學生資料
        student_data = defaultdict(lambda: {
            'name': '',
            'student_number': '',
            'preferences': [''] * 5,
            'submitted_times': [''] * 5
        })

        for row in results:
            student_name = row['student_name']
            if student_name:
                student_data[student_name]['name'] = student_name
                student_data[student_name]['student_number'] = row['student_number'] or ''
                
                if row['preference_order'] and row['company_name']:
                    order = row['preference_order'] - 1  # 轉為 0-based index
                    if 0 <= order < 5:
                        student_data[student_name]['preferences'][order] = row['company_name']
                        if row['submitted_at']:
                            student_data[student_name]['submitted_times'][order] = row['submitted_at'].strftime('%m/%d %H:%M')

        # 建立表格資料
        table_data = []
        
        # 表頭
        headers = ['學生姓名', '學號', '第一志願', '第二志願', '第三志願', '第四志願', '第五志願']
        table_data.append(headers)
        
        # 學生資料
        for student_name in sorted(student_data.keys()):
            data = student_data[student_name]
            row = [data['name'], data['student_number']]
            
            # 志願序
            for i in range(5):
                pref_text = data['preferences'][i]
                if pref_text and data['submitted_times'][i]:
                    pref_text += f"\n({data['submitted_times'][i]})"
                row.append(pref_text)
            
            table_data.append(row)

        # 建立表格
        table = Table(table_data, colWidths=[1.2*inch, 1*inch, 1.5*inch, 1.5*inch, 1.5*inch, 1.5*inch, 1.5*inch])
        
        # 設定表格樣式
        table_style = TableStyle([
            # 表頭樣式
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0066CC')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            
            # 資料行樣式
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            
            # 邊框
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            
            # 行高
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
        ])
        
        table.setStyle(table_style)
        story.append(table)
        story.append(Spacer(1, 30))

        # 統計資訊
        stats_title = Paragraph("統計資訊", styles['Heading2'])
        story.append(stats_title)
        
        # 統計各公司被選擇次數
        company_counts = defaultdict(int)
        for data in student_data.values():
            for pref in data['preferences']:
                if pref:
                    company_counts[pref] += 1

        # 建立統計表格
        stats_data = [['公司名稱', '被選擇次數']]
        for company, count in sorted(company_counts.items(), key=lambda x: x[1], reverse=True):
            stats_data.append([company, str(count)])

        if len(stats_data) > 1:  # 有統計資料才顯示
            stats_table = Table(stats_data, colWidths=[3*inch, 1*inch])
            stats_table_style = TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0066CC')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ])
            stats_table.setStyle(stats_table_style)
            story.append(stats_table)

        # 生成 PDF
        doc.build(story)

        pdf_buffer.seek(0)
        filename = f"{class_name}_學生志願序_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )

    except Exception as e:
        print("導出 PDF 錯誤：", e)
        return "伺服器錯誤", 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 指導老師查看選擇其公司學生列表
# -------------------------
@preferences_bp.route('/admission_results')
def admission_results_page():
    if 'username' not in session or session.get('role') != 'teacher':
        return redirect(url_for('auth_bp.login_page'))

    teacher_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 找出該老師所屬公司
        cursor.execute("""
            SELECT ic.id AS company_id, ic.company_name
            FROM internship_companies ic
            WHERE ic.teacher_id = %s
        """, (teacher_id,))
        companies = cursor.fetchall()

        if not companies:
            return render_template(
                'preferences/admission_results.html',
                companies=[],
                student_data={},
                message="目前尚未綁定任何實習公司。"
            )

        # 找出選擇這些公司的學生
        company_ids = tuple([c['company_id'] for c in companies])
        cursor.execute(f"""
            SELECT 
                u.name AS student_name,
                u.username AS student_number,
                sp.preference_order,
                sp.submitted_at,
                ic.company_name,
                ij.title AS job_title
            FROM student_preferences sp
            JOIN users u ON sp.student_id = u.id
            JOIN internship_companies ic ON sp.company_id = ic.id
            JOIN internship_jobs ij ON sp.job_id = ij.id
            WHERE sp.company_id IN {company_ids}
            ORDER BY ic.company_name, sp.preference_order, u.name
        """)
        rows = cursor.fetchall()

        # 整理成 {公司名稱: [學生資料...]} 結構
        student_data = {}
        for c in companies:
            cname = c['company_name']
            student_data[cname] = [
                r for r in rows if r['company_name'] == cname
            ]

        return render_template(
            'preferences/admission_results.html',
            companies=companies,
            student_data=student_data,
            message=None
        )

    except Exception as e:
        print("取得指導老師學生列表錯誤：", e)
        return "伺服器錯誤", 500

    finally:
        cursor.close()
        conn.close()
