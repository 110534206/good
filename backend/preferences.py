from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for, send_file
from config import get_db
from datetime import datetime
from collections import defaultdict
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
import io
import os
import csv
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import xlsxwriter
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

preferences_bp = Blueprint("preferences_bp", __name__)

# -------------------------
# API - 志願填寫
# -------------------------
@preferences_bp.route('/fill_preferences', methods=['GET', 'POST'])
def fill_preferences():
    # 1. 登入檢查
    if 'user_id' not in session or session.get('role') != 'student':
        return redirect(url_for('auth_bp.login_page'))

    student_id = session['user_id']

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    message = None

    if request.method == 'POST':
        preferences = []
        for i in range(1, 6):
            company_id = request.form.get(f'preference_{i}')
            if company_id:
                preferences.append((student_id, i, company_id, datetime.now()))

        try:
            # 刪除舊志願
            cursor.execute("DELETE FROM student_preferences WHERE student_id = %s", (student_id,))
            conn.commit()

            # 新增志願
            if preferences:
                cursor.executemany("""
                    INSERT INTO student_preferences (student_id, preference_order, company_id, submitted_at)
                    VALUES (%s, %s, %s, %s)
                """, preferences)
                conn.commit()
                message = "✅ 志願序已成功送出"
            else:
                message = "⚠️ 未選擇任何志願，公司清單已重置"
        except Exception as e:
            print("寫入志願錯誤：", e)
            message = "❌ 發生錯誤，請稍後再試"

    # 不管是 GET 還是 POST，都要載入公司列表及該學生已填的志願
    cursor.execute("SELECT id, company_name FROM internship_companies WHERE status = 'approved'")
    companies = cursor.fetchall()

    cursor.execute("""
        SELECT preference_order, company_id 
        FROM student_preferences 
        WHERE student_id = %s 
        ORDER BY preference_order
    """, (student_id,))
    prefs = cursor.fetchall()

    cursor.close()
    conn.close()

    # 把 prefs 轉成 list，index 對應志願順序 -1
    submitted_preferences = [None] * 5
    for pref in prefs:
        order = pref['preference_order']
        company_id = pref['company_id']
        if 1 <= order <= 5:
            submitted_preferences[order - 1] = company_id

    return render_template('preferences/fill_preferences.html',
        companies=companies,
        submitted_preferences=submitted_preferences,
        message=message
    )

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
    if 'username' not in session or session.get('role') not in ['teacher', 'director']:
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

        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    except Exception as e:
        print("Word 匯出錯誤：", e)
        return "伺服器錯誤", 500
    finally:
        cursor.close()
        conn.close()


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
