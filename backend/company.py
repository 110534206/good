from flask import Blueprint, request, jsonify, render_template, session, send_file, current_app
from config import get_db
from datetime import datetime
from werkzeug.utils import secure_filename
import os
import traceback
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from notification import create_notification
from semester import get_current_semester_code

company_bp = Blueprint("company_bp", __name__)

# =========================================================
# 📁 上傳設定
# =========================================================
UPLOAD_FOLDER = "uploads/company_docs"
ALLOWED_EXTENSIONS = {"docx", "doc"}

def ensure_upload_folder():
    project_root = os.path.dirname(current_app.root_path)
    upload_path = os.path.join(project_root, UPLOAD_FOLDER)
    os.makedirs(upload_path, exist_ok=True)
    return upload_path

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# =========================================================
# 📄 生成實習單位基本資料表 Word 檔
# =========================================================
def generate_company_word_document(data):
    """
    根據表單資料生成實習單位基本資料表 Word 檔
    格式符合圖片中的表單格式
    """
    doc = Document()
    
    # 設定中文字體
    def set_chinese_font(run, font_name='標楷體'):
        run.font.name = font_name
        run._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)
    
    # 設定表格邊框
    def set_table_borders(table):
        """設定表格邊框為實線"""
        tbl = table._tbl
        tblBorders = OxmlElement('w:tblBorders')
        for border_name in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
            border = OxmlElement(f'w:{border_name}')
            border.set(qn('w:val'), 'single')
            border.set(qn('w:sz'), '4')
            border.set(qn('w:space'), '0')
            border.set(qn('w:color'), '000000')
            tblBorders.append(border)
        tbl.tblPr.append(tblBorders)
    
    # 標題
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title.add_run('實習單位基本資料表')
    title_run.font.size = Pt(18)
    title_run.bold = True
    set_chinese_font(title_run, '標楷體')
    
    # 學校資訊
    school_info = doc.add_paragraph()
    school_info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    school_run = school_info.add_run('康寧學校財團法人康寧大學資訊管理科')
    school_run.font.size = Pt(12)
    set_chinese_font(school_run, '標楷體')
    
    # 實習期間（可以從學期設定中取得，這裡先留空或使用預設值）
    period_info = doc.add_paragraph()
    period_info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    period_run = period_info.add_run('實習期間：115年2月23日至115年6月26日止')
    period_run.font.size = Pt(12)
    set_chinese_font(period_run, '標楷體')
    
    doc.add_paragraph()  # 空行
    
    # I. 基本單位資訊 - 使用表格格式
    section1_title = doc.add_paragraph()
    section1_run = section1_title.add_run('I. 基本單位資訊')
    section1_run.font.size = Pt(14)
    section1_run.bold = True
    set_chinese_font(section1_run, '標楷體')
    
    # 建立基本資訊表格（2欄，多行）
    basic_info_data = [
        ('編號', data.get('serial_number', '')),
        ('單位名稱', data.get('company_name', '')),
        ('負責人', data.get('person_in_charge', '')),
        ('統一編號', data.get('uniform_number', '')),
        ('聯絡人', data.get('contact_person', '')),
        ('職稱', data.get('contact_title', '')),
        ('聯絡電話', data.get('contact_phone', '')),
        ('傳真', data.get('fax', '')),
        ('地址', data.get('address', '')),
        ('交通說明', data.get('transportation', '')),
        ('E-mail', data.get('email', '')),
        ('單位簡介', data.get('company_intro', ''))
    ]
    
    basic_table = doc.add_table(rows=len(basic_info_data), cols=2)
    basic_table.style = 'Light Grid Accent 1'
    set_table_borders(basic_table)
    
    # 設定欄寬：左欄（標籤）較窄，右欄（內容）較寬
    basic_table.columns[0].width = Inches(1.5)
    basic_table.columns[1].width = Inches(5.5)
    
    for idx, (label, value) in enumerate(basic_info_data):
        # 左欄：標籤
        label_cell = basic_table.rows[idx].cells[0]
        label_cell.text = label
        for paragraph in label_cell.paragraphs:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for run in paragraph.runs:
                set_chinese_font(run, '標楷體')
                run.font.size = Pt(12)
                run.bold = True
        
        # 右欄：值
        value_cell = basic_table.rows[idx].cells[1]
        value_cell.text = value
        for paragraph in value_cell.paragraphs:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for run in paragraph.runs:
                set_chinese_font(run, '標楷體')
                run.font.size = Pt(12)
    
    doc.add_paragraph()  # 空行
    
    # II. 營業項目與企業規模 - 合併為一個區塊
    section2_title = doc.add_paragraph()
    section2_run = section2_title.add_run('II. 營業項目與企業規模')
    section2_run.font.size = Pt(14)
    section2_run.bold = True
    set_chinese_font(section2_run, '標楷體')
    
    # 營業項目表格
    business_table = doc.add_table(rows=1, cols=2)
    business_table.style = 'Light Grid Accent 1'
    set_table_borders(business_table)
    business_table.columns[0].width = Inches(1.5)
    business_table.columns[1].width = Inches(5.5)
    
    # 左欄：標籤
    label_cell = business_table.rows[0].cells[0]
    label_cell.text = '營業項目'
    for paragraph in label_cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in paragraph.runs:
            set_chinese_font(run, '標楷體')
            run.font.size = Pt(12)
            run.bold = True
    
    # 右欄：值
    value_cell = business_table.rows[0].cells[1]
    value_cell.text = data.get("business_scope", "")
    for paragraph in value_cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in paragraph.runs:
            set_chinese_font(run, '標楷體')
            run.font.size = Pt(12)
    
    # 企業規模表格（在同一區塊內）
    scale_table = doc.add_table(rows=1, cols=2)
    scale_table.style = 'Light Grid Accent 1'
    set_table_borders(scale_table)
    scale_table.columns[0].width = Inches(1.5)
    scale_table.columns[1].width = Inches(5.5)
    
    # 左欄：標籤
    label_cell = scale_table.rows[0].cells[0]
    label_cell.text = '企業規模'
    for paragraph in label_cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in paragraph.runs:
            set_chinese_font(run, '標楷體')
            run.font.size = Pt(12)
            run.bold = True
    
    # 右欄：選項
    value_cell = scale_table.rows[0].cells[1]
    scale_options = ['1000人以上', '500-999人', '100-499人', '10-99人', '10以下']
    selected_scale = data.get('company_scale', '')
    scale_text = ''
    for option in scale_options:
        if option == selected_scale:
            scale_text += f'☑ {option}  '
        else:
            scale_text += f'☐ {option}  '
    value_cell.text = scale_text
    for paragraph in value_cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in paragraph.runs:
            set_chinese_font(run, '標楷體')
            run.font.size = Pt(12)
    
    doc.add_paragraph()  # 空行
    
    # III. 職缺明細
    section3_title = doc.add_paragraph()
    section3_run = section3_title.add_run('III. 工作項目')
    section3_run.font.size = Pt(14)
    section3_run.bold = True
    set_chinese_font(section3_run, '標楷體')
    
    jobs = data.get('jobs', [])
    if jobs:
        jobs_table = doc.add_table(rows=len(jobs) + 1, cols=4)
        jobs_table.style = 'Light Grid Accent 1'
        set_table_borders(jobs_table)
        
        # 設定職缺表格欄寬
        jobs_table.columns[0].width = Inches(0.8)  # 工作編號
        jobs_table.columns[1].width = Inches(1.8)    # 工作項目
        jobs_table.columns[2].width = Inches(3.5)   # 需求條件/工作內容
        jobs_table.columns[3].width = Inches(0.9)   # 名額
        
        # 表頭
        header_cells = jobs_table.rows[0].cells
        header_cells[0].text = '工作編號'
        header_cells[1].text = '工作項目'
        header_cells[2].text = '需求條件/工作內容'
        header_cells[3].text = '名額'
        
        for cell in header_cells:
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in paragraph.runs:
                    set_chinese_font(run, '標楷體')
                    run.font.size = Pt(12)
                    run.bold = True
        
        # 職缺資料
        for idx, job in enumerate(jobs, 1):
            row_cells = jobs_table.rows[idx].cells
            row_cells[0].text = str(idx)
            row_cells[1].text = job.get('title', '')
            row_cells[2].text = job.get('description', '')
            row_cells[3].text = str(job.get('slots', 1))
            
            # 設定表格內容字體和對齊
            for cell_idx, cell in enumerate(row_cells):
                for paragraph in cell.paragraphs:
                    # 工作編號和名額置中對齊，其他左對齊
                    if cell_idx == 0 or cell_idx == 3:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    else:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
                    for run in paragraph.runs:
                        set_chinese_font(run, '標楷體')
                        run.font.size = Pt(12)
    
    doc.add_paragraph()  # 空行
    
    # IV. 待遇與來源 - 使用表格格式
    section4_title = doc.add_paragraph()
    section4_run = section4_title.add_run('IV. 待遇與來源')
    section4_run.font.size = Pt(14)
    section4_run.bold = True
    set_chinese_font(section4_run, '標楷體')
    
    compensation_source_table = doc.add_table(rows=2, cols=2)
    compensation_source_table.style = 'Light Grid Accent 1'
    set_table_borders(compensation_source_table)
    compensation_source_table.columns[0].width = Inches(1.5)
    compensation_source_table.columns[1].width = Inches(5.5)
    
    # 待遇行
    comp_label_cell = compensation_source_table.rows[0].cells[0]
    comp_label_cell.text = '待遇'
    for paragraph in comp_label_cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in paragraph.runs:
            set_chinese_font(run, '標楷體')
            run.font.size = Pt(12)
            run.bold = True
    
    comp_value_cell = compensation_source_table.rows[0].cells[1]
    compensation_options = ['月薪', '時薪', '獎金(津貼)', '無']
    compensation_selected = data.get('compensation', [])
    comp_text = ''
    for option in compensation_options:
        if option in compensation_selected:
            comp_text += f'☑ {option}  '
        else:
            comp_text += f'☐ {option}  '
    comp_value_cell.text = comp_text
    for paragraph in comp_value_cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in paragraph.runs:
            set_chinese_font(run, '標楷體')
            run.font.size = Pt(12)
    
    # 來源行
    source_label_cell = compensation_source_table.rows[1].cells[0]
    source_label_cell.text = '來源'
    for paragraph in source_label_cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in paragraph.runs:
            set_chinese_font(run, '標楷體')
            run.font.size = Pt(12)
            run.bold = True
    
    source_value_cell = compensation_source_table.rows[1].cells[1]
    source_options = ['廠商申請', '老師推薦', '學生申請', '其它']
    source_selected = data.get('source', [])
    source_text = ''
    for option in source_options:
        if option in source_selected:
            source_text += f'☑ {option}  '
        else:
            source_text += f'☐ {option}  '
    
    # 如果選擇了「其它」，加上說明
    if '其它' in source_selected:
        other_text = data.get('source_other_text', '')
        if other_text:
            source_text += f'（{other_text}）'
    
    source_value_cell.text = source_text
    for paragraph in source_value_cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in paragraph.runs:
            set_chinese_font(run, '標楷體')
            run.font.size = Pt(12)
    
    return doc


# =========================================================
# 📥 下載公司上傳範本
# =========================================================
@company_bp.route('/download_company_template', methods=['GET'])
def download_company_template():
    try:
        template_file_name = "114學年實習單位基本資料表.docx"
        backend_dir = current_app.root_path
        project_root = os.path.dirname(backend_dir)
        file_path = os.path.join(project_root, 'frontend', 'static', 'examples', template_file_name)

        if not os.path.exists(file_path):
            return jsonify({"success": False, "message": "找不到範本檔案"}), 404

        return send_file(
            file_path,
            as_attachment=True,
            download_name=template_file_name,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "下載範本失敗"}), 500


# =========================================================
# 📤 上傳公司資料（網頁填表，自動生成 Word 檔）
# =========================================================
@company_bp.route('/api/upload_company', methods=['POST'])
def upload_company():
    conn = None
    cursor = None
    file_path = None

    try:
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "請先登入"}), 403

        role = session.get('role')
        if role not in ['teacher', 'director', 'ta', 'vendor']:
           return jsonify({"success": False, "message": "無權限操作此功能"}), 403

        user_id = session['user_id']
        upload_dir = ensure_upload_folder()

        # 判斷是 JSON 資料（新方式）還是表單資料（舊方式，保留向後兼容）
        if request.is_json:
            data = request.get_json()
            company_name = data.get("company_name", "").strip()
            jobs_data = data.get("jobs", [])
        else:
            # 舊方式：表單上傳（向後兼容）
            company_name = request.form.get("company_name", "").strip()
            jobs_data = []
            job_index = 0
            while True:
                job_title = request.form.get(f"job[{job_index}][title]", "").strip()
                slots_str = request.form.get(f"job[{job_index}][slots]", "0").strip()
                if not job_title:
                    break
                try:
                    slots = int(slots_str)
                    if slots <= 0:
                        raise ValueError
                except ValueError:
                    return jsonify({"success": False, "message": f"職缺 #{job_index+1} 名額必須是正整數"}), 400
                jobs_data.append({"title": job_title, "slots": slots})
                job_index += 1

        if not company_name:
            return jsonify({"success": False, "message": "公司名稱為必填欄位"}), 400

        if not jobs_data:
            return jsonify({"success": False, "message": "請至少新增一個職缺"}), 400

        # 如果是 JSON 資料，生成 Word 檔
        if request.is_json:
            # 生成 Word 檔
            doc = generate_company_word_document(data)
            
            # 儲存 Word 檔
            safe_name = secure_filename(f"{company_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx")
            save_path = os.path.join(upload_dir, safe_name)
            doc.save(save_path)
            file_path = os.path.join(UPLOAD_FOLDER, safe_name)
        else:
            # 舊方式：處理上傳的 Word 檔案
            file = request.files.get("company_doc")
            if file and file.filename and allowed_file(file.filename):
                safe_name = secure_filename(f"{company_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                save_path = os.path.join(upload_dir, safe_name)
                file.save(save_path)
                file_path = os.path.join(UPLOAD_FOLDER, safe_name)
            else:
                return jsonify({"success": False, "message": "請上傳有效的 Word 檔案 (.doc 或 .docx)"}), 400

        # 寫入資料庫
        conn = get_db()
        cursor = conn.cursor()

        # 如果是科助，自動填入 advisor_user_id 和 reviewed_by_user_id，並設為已核准狀態
        if role == 'ta':
            advisor_user_id = user_id
            reviewed_by_user_id = user_id
            status = 'approved'
            reviewed_at = datetime.now()
        elif role == 'vendor':
            # 廠商上傳：根據廠商的 teacher_name 找到對應的指導老師
            cursor.execute("SELECT teacher_name FROM users WHERE id = %s", (user_id,))
            vendor_row = cursor.fetchone()
            advisor_user_id = None
            if vendor_row and vendor_row[0]:
                teacher_name = vendor_row[0].strip()
                if teacher_name:
                    cursor.execute("SELECT id FROM users WHERE name = %s AND role IN ('teacher', 'director')", (teacher_name,))
                    teacher_row = cursor.fetchone()
                    if teacher_row:
                        advisor_user_id = teacher_row[0]
            reviewed_by_user_id = None
            status = 'pending'
            reviewed_at = None
        else:
            # 如果是老師或主任，預設上傳教師為指導老師
            if role in ['teacher', 'director']:
                advisor_user_id = user_id
            else:
                advisor_user_id = None
            reviewed_by_user_id = None
            status = 'pending'
            reviewed_at = None

        # 準備公司資料
        if request.is_json:
            company_description = data.get("company_intro", "（詳見附檔）")
            company_location = data.get("address", "")
            contact_person = data.get("contact_person", "")
            contact_title = data.get("contact_title", "")
            contact_email = data.get("email", "")
            contact_phone = data.get("contact_phone", "")
        else:
            company_description = "（詳見附檔）"
            company_location = ""
            contact_person = ""
            contact_title = ""
            contact_email = ""
            contact_phone = ""
        
        cursor.execute("""
            INSERT INTO internship_companies 
            (company_name, uploaded_by_user_id, advisor_user_id, reviewed_by_user_id, status, submitted_at, reviewed_at, company_doc_path, 
             description, location, contact_person, contact_title, contact_email, contact_phone)
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s)
        """, (company_name, user_id, advisor_user_id, reviewed_by_user_id, status, reviewed_at, file_path,
              company_description, company_location, contact_person, contact_title, contact_email, contact_phone))
        company_id = cursor.lastrowid

        # 插入職缺
        job_records = []
        for j in jobs_data:
            job_description = j.get("description", "（詳見附檔）")
            job_records.append((
                company_id,
                j.get("title", ""),
                j.get("slots", 1),
                job_description,
                "",
                "",
                "",
                True
            ))
        cursor.executemany("""
            INSERT INTO internship_jobs 
            (company_id, title, slots, description, period, work_time, remark, is_active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, job_records)

        conn.commit()

        job_count = len(jobs_data)
        
        # 根據角色顯示不同的成功訊息
        if role == 'ta':
            message = f"公司 '{company_name}' ({job_count} 個職缺) 上傳成功，已自動核准。"
        elif role == 'vendor':
            message = f"公司 '{company_name}' ({job_count} 個職缺) 上傳成功，資料已標記為「待科助開放」。"
        else:
            message = f"公司 '{company_name}' ({job_count} 個職缺) 上傳成功，等待審核。"

        response_data = {
            "success": True,
            "message": message,
            "company_id": company_id
        }
        
        # 如果是新方式（JSON），提供下載連結
        if request.is_json and file_path:
            response_data["download_url"] = f"/api/download_company_file/{company_id}"

        return jsonify(response_data)

    except Exception as e:
        traceback.print_exc()
        # 如果發生錯誤，刪除剛剛儲存的檔案
        if file_path:
            project_root = os.path.dirname(current_app.root_path)
            abs_path = os.path.join(project_root, file_path)
            if os.path.exists(abs_path):
                os.remove(abs_path)
        return jsonify({"success": False, "message": f"伺服器錯誤: {e}"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# 📜 查詢使用者上傳紀錄
# =========================================================
@company_bp.route('/api/get_my_companies', methods=['GET'])
def get_my_companies():
    conn = None
    cursor = None
    try:
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "請先登入"}), 403

        user_id = session['user_id']
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                ic.id,
                ic.company_name,
                ic.status,
                ic.company_doc_path AS filepath,
                ic.submitted_at AS upload_time,
                u.role AS uploader_role
            FROM internship_companies ic
            JOIN users u ON ic.uploaded_by_user_id = u.id
            WHERE ic.uploaded_by_user_id = %s
            ORDER BY ic.submitted_at DESC
        """, (user_id,))
        records = cursor.fetchall()

        # === 🕒 加上台灣時區轉換 ===
        from datetime import datetime, timezone, timedelta
        taiwan_tz = timezone(timedelta(hours=8))

        for r in records:
            if isinstance(r.get("upload_time"), datetime):
                # 將 UTC 轉為台灣時間
                r["upload_time"] = r["upload_time"].astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
            else:
                r["upload_time"] = "-"

            r["filename"] = os.path.basename(r["filepath"]) if r["filepath"] else None

        return jsonify({"success": True, "companies": records})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "載入上傳紀錄失敗"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# 📂 下載上傳的公司檔案
# =========================================================
@company_bp.route('/api/download_company_file/<int:file_id>', methods=['GET'])
def download_company_file(file_id):
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT company_doc_path FROM internship_companies WHERE id=%s", (file_id,))
        record = cursor.fetchone()
        if not record or not record["company_doc_path"]:
            return jsonify({"success": False, "message": "找不到檔案"}), 404

        project_root = os.path.dirname(current_app.root_path)
        abs_path = os.path.join(project_root, record["company_doc_path"])
        if not os.path.exists(abs_path):
            return jsonify({"success": False, "message": "檔案不存在"}), 404

        filename = os.path.basename(abs_path)
        return send_file(abs_path, as_attachment=True, download_name=filename)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "下載失敗"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# 🗑️ 刪除公司上傳紀錄
# =========================================================
@company_bp.route('/api/delete_company/<int:company_id>', methods=['DELETE'])
def delete_company(company_id):
    conn = None
    cursor = None
    try:
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "請先登入"}), 403

        user_id = session['user_id']
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 先查資料，確認是否為本人上傳
        cursor.execute("""
            SELECT company_doc_path FROM internship_companies 
            WHERE id=%s AND uploaded_by_user_id=%s
        """, (company_id, user_id))
        record = cursor.fetchone()

        if not record:
            return jsonify({"success": False, "message": "找不到該公司資料或您無權限刪除"}), 404

        # 刪除檔案（如果存在）
        if record["company_doc_path"]:
            project_root = os.path.dirname(current_app.root_path)
            abs_path = os.path.join(project_root, record["company_doc_path"])
            if os.path.exists(abs_path):
                os.remove(abs_path)

        # 刪除相關職缺資料
        cursor.execute("DELETE FROM internship_jobs WHERE company_id=%s", (company_id,))

        # 刪除公司主資料
        cursor.execute("DELETE FROM internship_companies WHERE id=%s", (company_id,))
        conn.commit()

        return jsonify({"success": True, "message": "公司資料已刪除。"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"刪除失敗: {e}"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# API - 取得待審核公司清單
# =========================================================
@company_bp.route("/api/get_pending_companies", methods=["GET"])
def api_get_pending_companies():
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                ic.id,
                u.name AS upload_teacher_name,
                ic.company_name,
                ic.contact_person AS contact_name,
                ic.contact_email,
                ic.submitted_at,
                ic.status
            FROM internship_companies ic
            LEFT JOIN users u ON ic.uploaded_by_user_id = u.id
            WHERE ic.status = 'pending'
            ORDER BY ic.submitted_at DESC
        """)

        companies = cursor.fetchall()

        # === 🕒 台灣時區轉換 & 格式化 ===
        from datetime import timezone, timedelta, datetime
        taiwan_tz = timezone(timedelta(hours=8))

        for r in companies:
            dt = r.get("submitted_at")
            if isinstance(dt, datetime):
                r["submitted_at"] = dt.astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
            else:
                r["submitted_at"] = "-"

        return jsonify({
            "success": True,
            "companies": companies
        })

    except Exception:
        import traceback
        print("❌ 取得待審核公司清單錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# API - 取得已審核公司（歷史紀錄）
# =========================================================
@company_bp.route("/api/get_reviewed_companies", methods=["GET"])
def api_get_reviewed_companies():
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 取得當前學期代碼
        current_semester_code = get_current_semester_code(cursor)

        # 如果沒有設定當前學期，仍然可以顯示公司列表，但無法顯示開放狀態
        if current_semester_code:
            cursor.execute("""
                SELECT 
                    ic.id,
                    u.name AS upload_teacher_name,
                    COALESCE(advisor.name, 
                        CASE 
                            WHEN ic.advisor_user_id IS NULL AND u.role IN ('teacher', 'director') THEN u.name 
                            ELSE NULL 
                        END
                    ) AS advisor_teacher_name,
                    COALESCE(ic.advisor_user_id, 
                        CASE 
                            WHEN u.role IN ('teacher', 'director') THEN ic.uploaded_by_user_id 
                            ELSE NULL 
                        END
                    ) AS advisor_user_id,
                    ic.company_name, 
                    ic.status,
                    ic.submitted_at AS upload_time,
                    ic.reviewed_at,
                    COALESCE(co.is_open, FALSE) AS is_open_current_semester
                FROM internship_companies ic
                LEFT JOIN users u ON ic.uploaded_by_user_id = u.id
                LEFT JOIN users advisor ON ic.advisor_user_id = advisor.id
                LEFT JOIN company_openings co ON ic.id = co.company_id 
                    AND co.semester = %s
                WHERE ic.status = 'approved'
                ORDER BY 
                    CASE WHEN ic.reviewed_at IS NULL THEN 1 ELSE 0 END,
                    ic.reviewed_at DESC,
                    ic.submitted_at DESC
            """, (current_semester_code,))
        else:
            cursor.execute("""
                SELECT 
                    ic.id,
                    u.name AS upload_teacher_name,
                    COALESCE(advisor.name, 
                        CASE 
                            WHEN ic.advisor_user_id IS NULL AND u.role IN ('teacher', 'director') THEN u.name 
                            ELSE NULL 
                        END
                    ) AS advisor_teacher_name,
                    COALESCE(ic.advisor_user_id, 
                        CASE 
                            WHEN u.role IN ('teacher', 'director') THEN ic.uploaded_by_user_id 
                            ELSE NULL 
                        END
                    ) AS advisor_user_id,
                    ic.company_name, 
                    ic.status,
                    ic.submitted_at AS upload_time,
                    ic.reviewed_at,
                    FALSE AS is_open_current_semester
                FROM internship_companies ic
                LEFT JOIN users u ON ic.uploaded_by_user_id = u.id
                LEFT JOIN users advisor ON ic.advisor_user_id = advisor.id
                WHERE ic.status = 'approved'
                ORDER BY 
                    CASE WHEN ic.reviewed_at IS NULL THEN 1 ELSE 0 END,
                    ic.reviewed_at DESC,
                    ic.submitted_at DESC
            """)

        companies = cursor.fetchall()
        
        # 調試：記錄返回的公司狀態分布
        status_count = {}
        for company in companies:
            status = company.get('status', 'unknown')
            status_count[status] = status_count.get(status, 0) + 1
        print(f"📊 已審核公司查詢結果: 總數={len(companies)}, 狀態分布={status_count}")
        
        # 格式化時間
        from datetime import timezone, timedelta
        taiwan_tz = timezone(timedelta(hours=8))
        
        for company in companies:
            if company.get('upload_time') and isinstance(company['upload_time'], datetime):
                company['upload_time'] = company['upload_time'].astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
            else:
                company['upload_time'] = "-"
            
            if company.get('reviewed_at') and isinstance(company['reviewed_at'], datetime):
                company['reviewed_at'] = company['reviewed_at'].astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
            else:
                company['reviewed_at'] = "-"
            
            # 確保 is_open_current_semester 是布林值
            company['is_open_current_semester'] = bool(company.get('is_open_current_semester', False))
        
        return jsonify({"success": True, "companies": companies, "current_semester": current_semester_code})

    except Exception:
        print("❌ 取得已審核公司錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# 🔎 取得公司詳細資料 (包含職缺)
# =========================================================
@company_bp.route('/api/get_company_detail', methods=['GET'])
def get_company_detail():
    conn = None
    cursor = None
    try:
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "請先登入"}), 403

        company_id = request.args.get('company_id', type=int)
        if not company_id:
            return jsonify({"success": False, "message": "缺少 company_id"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 查詢公司主資料
        cursor.execute("""
            SELECT 
                ic.id, ic.company_name, ic.status, ic.description AS company_intro, 
                ic.location AS company_address, ic.contact_person AS contact_name, 
                ic.contact_title, ic.contact_email, ic.contact_phone, 
                ic.reject_reason, ic.submitted_at, ic.reviewed_at, 
                u.name AS upload_teacher_name
            FROM internship_companies ic
            JOIN users u ON ic.uploaded_by_user_id = u.id
            WHERE ic.id = %s
        """, (company_id,))
        company = cursor.fetchone()

        if not company:
            return jsonify({"success": False, "message": "找不到公司資料"}), 404

        # 查詢職缺資料
        cursor.execute("""
            SELECT 
                title AS internship_unit, 
                description AS internship_content, 
                period AS internship_period, 
                work_time AS internship_time, 
                slots AS internship_quota, 
                remark, salary
            FROM internship_jobs
            WHERE company_id = %s
            AND is_active = TRUE
        """, (company_id,))
        jobs = cursor.fetchall()
        company['internship_jobs'] = jobs

        return jsonify({"success": True, "company": company})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"載入詳細資料失敗: {e}"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# 📚 實習 QA - 取得所有問答
# =========================================================
@company_bp.route('/api/qa/list', methods=['GET'])
def qa_list():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, question, answer 
            FROM internship_qa
            ORDER BY sort_order ASC, id DESC
        """)
        data = cursor.fetchall()

        return jsonify({"success": True, "data": data})

    except Exception:
        import traceback
        print("❌ QA 列表錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# ➕ 實習 QA - 新增
# =========================================================
@company_bp.route('/api/qa/add', methods=['POST'])
def qa_add():
    data = request.json

    question = data.get("question", "").strip()
    answer   = data.get("answer", "").strip()
    sort     = data.get("sort_order", 0)

    if not question or not answer:
        return jsonify({"success": False, "message": "問題與答案不得為空"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO internship_qa (question, answer, sort_order)
            VALUES (%s, %s, %s)
        """, (question, answer, sort))

        conn.commit()
        return jsonify({"success": True, "message": "新增成功"})

    except Exception:
        import traceback
        print("❌ QA 新增錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# =========================================================
# ✏️ 實習 QA - 更新
# =========================================================
@company_bp.route('/api/qa/update/<int:qa_id>', methods=['PUT'])
def qa_update(qa_id):
    data = request.json

    question = data.get("question", "").strip()
    answer   = data.get("answer", "").strip()
    sort     = data.get("sort_order")

    if not question or not answer:
        return jsonify({"success": False, "message": "問題與答案不得為空"}), 400

    try:
        sort = int(sort) if str(sort).isdigit() else 0

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE internship_qa
            SET question=%s, answer=%s, sort_order=%s
            WHERE id=%s
        """, (question, answer, sort, qa_id))

        conn.commit()

        if cursor.rowcount == 0:
            return jsonify({"success": False, "message": "找不到該 QA"}), 404

        return jsonify({"success": True, "message": "更新成功"})

    except Exception:
        import traceback
        print("❌ QA 更新錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# 🗑️ 實習 QA - 刪除
# =========================================================
@company_bp.route('/api/qa/delete/<int:qa_id>', methods=['DELETE'])
def qa_delete(qa_id):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM internship_qa WHERE id=%s", (qa_id,))
        conn.commit()

        return jsonify({"success": True, "message": "刪除成功"})

    except Exception:
        import traceback
        print("❌ QA 刪除錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# API - 審核公司
# =========================================================
@company_bp.route("/api/approve_company", methods=["POST"])
def api_approve_company():
    data = request.get_json()
    company_id = data.get("company_id")
    status = data.get("status")

    if not company_id or status not in ['approved', 'rejected']:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT company_name, status FROM internship_companies WHERE id = %s", (company_id,))
        company_row = cursor.fetchone()

        if not company_row:
            return jsonify({"success": False, "message": "查無此公司"}), 404

        company_name, current_status = company_row
        if current_status != 'pending':
            return jsonify({"success": False, "message": f"公司已被審核過（目前狀態為 {current_status}）"}), 400

        # 取得審核者的 user_id
        reviewer_id = session.get('user_id') if 'user_id' in session else None

        cursor.execute("""
            UPDATE internship_companies
            SET status = %s, reviewed_at = %s, reviewed_by_user_id = %s
            WHERE id = %s
        """, (status, datetime.now(), reviewer_id, company_id))
        conn.commit()

        action_text = '核准' if status == 'approved' else '拒絕'
        return jsonify({"success": True, "message": f"公司「{company_name}」已{action_text}"})

    except Exception:
        print("❌ 審核公司錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()

# =========================================================
# API - 設定公司本學期開放狀態
# =========================================================
@company_bp.route("/api/set_company_open_status", methods=["POST"])
def api_set_company_open_status():
    """設定公司在本學期是否開放"""
    data = request.get_json()
    company_id = data.get("company_id")
    is_open = data.get("is_open", False)

    if company_id is None:
        return jsonify({"success": False, "message": "缺少 company_id"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 取得當前學期代碼
        current_semester_code = get_current_semester_code(cursor)
        if not current_semester_code:
            return jsonify({"success": False, "message": "目前沒有設定當前學期"}), 400

        # 檢查公司是否存在且已審核通過
        cursor.execute("SELECT id, company_name, status FROM internship_companies WHERE id = %s", (company_id,))
        company = cursor.fetchone()
        
        if not company:
            return jsonify({"success": False, "message": "找不到該公司"}), 404
        
        if company['status'] != 'approved':
            return jsonify({"success": False, "message": "只有已審核通過的公司才能設定開放狀態"}), 400

        # 檢查是否已存在該公司該學期的記錄
        cursor.execute("""
            SELECT id FROM company_openings 
            WHERE company_id = %s AND semester = %s
        """, (company_id, current_semester_code))
        existing = cursor.fetchone()

        if existing:
            # 更新現有記錄
            cursor.execute("""
                UPDATE company_openings 
                SET is_open = %s, opened_at = %s
                WHERE company_id = %s AND semester = %s
            """, (is_open, datetime.now(), company_id, current_semester_code))
        else:
            # 建立新記錄
            cursor.execute("""
                INSERT INTO company_openings (company_id, semester, is_open, opened_at)
                VALUES (%s, %s, %s, %s)
            """, (company_id, current_semester_code, is_open, datetime.now()))

        conn.commit()
        
        status_text = '開放' if is_open else '關閉'
        return jsonify({
            "success": True, 
            "message": f"公司「{company['company_name']}」已{status_text}",
            "is_open": bool(is_open)
        })

    except Exception as e:
        print("❌ 設定公司開放狀態錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# 🖥️ 上傳公司頁面
# =========================================================
@company_bp.route('/upload_company', methods=['GET'])
def upload_company_form_page():
    return render_template('company/upload_company.html')

# =========================================================
# API - 取得所有指導老師
# =========================================================
@company_bp.route("/api/get_all_teachers", methods=["GET"])
def api_get_all_teachers():
    """取得所有指導老師（teacher 和 director 角色）"""
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT id, name
            FROM users
            WHERE role IN ('teacher', 'director')
            ORDER BY name ASC
        """)
        teachers = cursor.fetchall()
        
        return jsonify({"success": True, "teachers": teachers})
    except Exception:
        print("❌ 取得指導老師列表錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# API - 更新公司指導老師
# =========================================================
@company_bp.route("/api/update_company_advisor", methods=["POST"])
def api_update_company_advisor():
    """更新公司的指導老師"""
    data = request.get_json()
    company_id = data.get("company_id")
    advisor_user_id = data.get("advisor_user_id")  # 可以是 None
    
    if not company_id:
        return jsonify({"success": False, "message": "缺少 company_id"}), 400
    
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 檢查公司是否存在
        cursor.execute("SELECT id, company_name FROM internship_companies WHERE id = %s", (company_id,))
        company = cursor.fetchone()
        if not company:
            return jsonify({"success": False, "message": "找不到該公司"}), 404
        
        # 如果提供了 advisor_user_id，驗證該用戶是老師或主任
        if advisor_user_id:
            cursor.execute("SELECT id, name, role FROM users WHERE id = %s AND role IN ('teacher', 'director')", (advisor_user_id,))
            teacher = cursor.fetchone()
            if not teacher:
                return jsonify({"success": False, "message": "指定的用戶不是有效的指導老師"}), 400
        
        # 更新指導老師
        cursor.execute("""
            UPDATE internship_companies
            SET advisor_user_id = %s
            WHERE id = %s
        """, (advisor_user_id, company_id))
        conn.commit()
        
        # 取得更新後的指導老師名稱
        advisor_name = None
        if advisor_user_id:
            cursor.execute("SELECT name FROM users WHERE id = %s", (advisor_user_id,))
            advisor = cursor.fetchone()
            if advisor:
                advisor_name = advisor['name']
        
        return jsonify({
            "success": True,
            "message": f"公司「{company['company_name']}」的指導老師已更新",
            "advisor_name": advisor_name
        })
    except Exception:
        print("❌ 更新公司指導老師錯誤：", traceback.format_exc())
        conn.rollback()
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# 📥 導出公司審核數據
# =========================================================
@company_bp.route("/api/export_company_reviews", methods=["GET"])
def api_export_company_reviews():
    """導出公司審核數據為SQL文件"""
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 查詢所有已審核的公司
        cursor.execute("""
            SELECT 
                ic.id,
                ic.company_name,
                ic.status,
                ic.reviewed_at,
                ic.reviewed_by_user_id,
                ic.advisor_user_id
            FROM internship_companies ic
            WHERE ic.status IN ('approved', 'rejected')
            ORDER BY ic.id
        """)
        companies = cursor.fetchall()
        
        # 查詢公司開放狀態
        cursor.execute("""
            SELECT 
                co.company_id,
                co.semester,
                co.is_open,
                co.opened_at
            FROM company_openings co
            ORDER BY co.company_id, co.semester
        """)
        openings = cursor.fetchall()
        openings_dict = {}
        for opening in openings:
            company_id = opening['company_id']
            if company_id not in openings_dict:
                openings_dict[company_id] = []
            openings_dict[company_id].append(opening)
        
        # 生成SQL內容
        sql_lines = []
        sql_lines.append("-- ============================================")
        sql_lines.append(f"-- 公司審核數據導出")
        sql_lines.append(f"-- 導出時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        sql_lines.append(f"-- 共 {len(companies)} 家公司")
        sql_lines.append("-- ============================================\n")
        sql_lines.append("START TRANSACTION;\n")
        
        # 更新審核狀態
        sql_lines.append("-- 更新公司審核狀態\n")
        for company in companies:
            company_id = company['id']
            company_name = company['company_name'].replace("'", "''")
            status = company['status']
            reviewed_at = company['reviewed_at']
            reviewed_by_user_id = company['reviewed_by_user_id']
            
            reviewed_at_str = f"'{reviewed_at.strftime('%Y-%m-%d %H:%M:%S')}'" if reviewed_at else "NULL"
            reviewed_by_str = str(reviewed_by_user_id) if reviewed_by_user_id else "NULL"
            
            sql_lines.append(f"-- 公司: {company_name} (ID: {company_id})")
            sql_lines.append(f"UPDATE internship_companies")
            sql_lines.append(f"SET status = '{status}',")
            sql_lines.append(f"    reviewed_at = {reviewed_at_str},")
            sql_lines.append(f"    reviewed_by_user_id = {reviewed_by_str}")
            sql_lines.append(f"WHERE id = {company_id};")
            sql_lines.append("")
        
        # 更新指導老師
        sql_lines.append("-- 更新公司指導老師\n")
        for company in companies:
            if company['advisor_user_id']:
                sql_lines.append(f"UPDATE internship_companies")
                sql_lines.append(f"SET advisor_user_id = {company['advisor_user_id']}")
                sql_lines.append(f"WHERE id = {company['id']};")
                sql_lines.append("")
        
        # 更新開放狀態
        sql_lines.append("-- 更新公司開放狀態\n")
        for company_id, opening_list in openings_dict.items():
            for opening in opening_list:
                semester = opening['semester']
                is_open = 1 if opening['is_open'] else 0
                opened_at = opening['opened_at']
                opened_at_str = f"'{opened_at.strftime('%Y-%m-%d %H:%M:%S')}'" if opened_at else "NOW()"
                
                sql_lines.append(f"INSERT INTO company_openings (company_id, semester, is_open, opened_at)")
                sql_lines.append(f"VALUES ({company_id}, '{semester}', {is_open}, {opened_at_str})")
                sql_lines.append(f"ON DUPLICATE KEY UPDATE")
                sql_lines.append(f"    is_open = {is_open},")
                sql_lines.append(f"    opened_at = {opened_at_str};")
                sql_lines.append("")
        
        sql_lines.append("COMMIT;")
        
        sql_content = '\n'.join(sql_lines)
        
        from flask import Response
        return Response(
            sql_content,
            mimetype='text/plain',
            headers={
                'Content-Disposition': f'attachment; filename=company_reviews_export_{datetime.now().strftime("%Y%m%d")}.sql'
            }
        )
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"導出失敗: {str(e)}"}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# =========================================================
# 🖥️ 審核公司頁面
# =========================================================
@company_bp.route('/approve_company', methods=['GET'])
def approve_company_form_page():
    return render_template('company/approve_company.html')
