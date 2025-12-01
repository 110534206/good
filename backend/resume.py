from flask import Blueprint, request, jsonify, session, send_file, render_template, redirect
from werkzeug.utils import secure_filename
from config import get_db
from semester import get_current_semester_id
from docxtpl import DocxTemplate, InlineImage
from docx.shared import Inches
import os
import traceback
import json
import re
from datetime import datetime
from notification import create_notification

# 添加圖片驗證函數
def is_valid_image_file(file_path):
    """
    驗證圖片文件是否有效
    返回 True 如果文件是有效的圖片，否則返回 False
    """
    if not file_path or not os.path.exists(file_path):
        return False
    
    try:
        from PIL import Image
        # 嘗試打開並驗證圖片
        with Image.open(file_path) as img:
            img.verify()  # 驗證圖片是否損壞
        
        # verify() 後需要重新打開圖片（因為 verify 會關閉文件）
        with Image.open(file_path) as img:
            # 檢查圖片格式是否被支持
            if img.format not in ['JPEG', 'PNG', 'GIF', 'BMP', 'TIFF']:
                print(f"⚠️ 不支持的圖片格式: {img.format} (路徑: {file_path})")
                return False
        return True
    except ImportError:
        # 如果 PIL 未安裝，跳過驗證（向後兼容）
        print("⚠️ PIL/Pillow 未安裝，跳過圖片驗證")
        return True  # 返回 True 讓程序繼續運行
    except Exception as e:
        print(f"⚠️ 圖片驗證失敗 {file_path}: {e}")
        return False

# 安全地創建 InlineImage 對象
def safe_create_inline_image(doc, file_path, width, description=""):
    """
    安全地創建 InlineImage 對象，如果失敗則返回 None
    """
    if not file_path or not os.path.exists(file_path):
        return None
    
    # 先驗證圖片
    if not is_valid_image_file(file_path):
        print(f"⚠️ {description}圖片無效或損壞，跳過: {file_path}")
        return None
    
    try:
        abs_path = os.path.abspath(file_path)
        image_obj = InlineImage(doc, abs_path, width=width)
        return image_obj
    except Exception as e:
        print(f"⚠️ {description}圖片載入錯誤 (路徑: {file_path}): {e}")
        traceback.print_exc()
        return None


resume_bp = Blueprint("resume_bp", __name__)

# 上傳資料夾設定
UPLOAD_FOLDER = "uploads/resumes"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 缺勤佐證圖片資料夾設定
ABSENCE_PROOF_FOLDER = "uploads/absence_proofs"
os.makedirs(ABSENCE_PROOF_FOLDER, exist_ok=True)

def score_to_grade(score):
    # 若已經是等第，直接回傳
    if str(score).strip() in ['優', '甲', '乙', '丙', '丁']:
        return str(score).strip()

    # 若是分數才做數字轉換
    try:
        score = int(str(score).strip())
    except (ValueError, TypeError):
        return '丁'

    if score >= 90:
        return '優'
    elif score >= 80:
        return '甲'
    elif score >= 70:
        return '乙'
    elif score >= 60:
        return '丙'
    else:
        return '丁'

# -------------------------
# 語文能力複選框處理輔助函式 (未使用，但保留)
# -------------------------
def generate_language_marks(level):
    marks = {'Jing': '□', 'Zhong': '□', 'Lue': '□'}
    level_map = {'精通': 'Jing', '中等': 'Zhong', '略懂': 'Lue'}
    level_key = level_map.get(level)
    if level_key in marks:
        marks[level_key] = '■'
    return marks

# -------------------------
# Helper / 權限管理
# -------------------------
def get_user_by_username(cursor, username):
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    return cursor.fetchone()

def get_user_by_id(cursor, user_id):
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    return cursor.fetchone()

def get_director_department(cursor, user_id):
    """
    取得主任所屬 department（透過 classes_teacher -> classes.department）
    若管理多個班級，只回傳第一個有 department 的值（可擴充回傳 list）
    """
    cursor.execute("""
        SELECT DISTINCT c.department
        FROM classes c
        JOIN classes_teacher ct ON ct.class_id = c.id
        WHERE ct.teacher_id = %s
        LIMIT 1
    """, (user_id,))
    r = cursor.fetchone()
    return r['department'] if r and r.get('department') else None

def teacher_manages_class(cursor, teacher_id, class_id):
    cursor.execute("""
        SELECT 1 FROM classes_teacher
        WHERE teacher_id = %s AND class_id = %s
        LIMIT 1
    """, (teacher_id, class_id))
    return cursor.fetchone() is not None

def can_access_target_resume(cursor, session_user_id, session_role, target_user_id):
    # admin 可以
    if session_role == "admin":
        return True

    # student 只能自己
    if session_role == "student":
        return session_user_id == target_user_id

    # ta 可以讀所有
    if session_role == "ta":
        return True

    # 取得 target student's class_id
    cursor.execute("SELECT class_id FROM users WHERE id = %s", (target_user_id,))
    u = cursor.fetchone()
    if not u:
        return False
    target_class_id = u.get('class_id')

    if session_role == "teacher":
        return teacher_manages_class(cursor, session_user_id, target_class_id)

    if session_role == "class_teacher":
        return teacher_manages_class(cursor, session_user_id, target_class_id)

    if session_role == "director":
        director_dept = get_director_department(cursor, session_user_id)
        if not director_dept:
            return False
        cursor.execute("SELECT department FROM classes WHERE id = %s", (target_class_id,))
        cd = cursor.fetchone()
        if not cd:
            return False
        return cd.get('department') == director_dept

    return False

def require_login():
    return 'user_id' in session and 'role' in session

# -------------------------
# 處理學生證照（查詢 → 分類 → 填入模板）
# -------------------------
def load_student_certifications(cursor, student_id):
    """
    回傳該學生所有證照完整資訊
    """
    sql = """
        SELECT
            cc.name AS cert_name,
            cc.category AS cert_category,
            CONCAT(cc.name, ' (', ca.name, ')') AS full_name,
            sc.CertPath AS cert_path,
            sc.AcquisitionDate AS acquire_date
        FROM student_certifications sc
        JOIN certificate_codes cc ON sc.cert_code = cc.code
        JOIN cert_authorities ca ON cc.authority_id = ca.id
        WHERE sc.StuID = %s
        ORDER BY sc.AcquisitionDate DESC, sc.id ASC
    """
    cursor.execute(sql, (student_id,))
    rows = cursor.fetchall()
    # 轉為 Python dict（cursor.fetchall() 已返回字典，因為使用了 dictionary=True）
    results = []
    for r in rows:
        if r:  # 確保 r 不是 None
            results.append({
                "cert_name": r.get('cert_name', '') or '',
                "category": r.get('cert_category', 'other'),        # labor / intl / local / other
                "full_name": r.get('full_name', '') or '',       # 表格區使用 → 例: 電腦軟體乙級 (勞動部)
                "cert_path": r.get('cert_path', '') or '',       # 圖片路徑
                "acquire_date": r.get('acquire_date', '') or '',    # 日期
            })
    return results

def categorize_certifications(cert_list):
    """
    分類證照 → 放到四種類別
    """
    labor = []
    international = []
    local = []
    other = []
    for c in cert_list:
        item = {
            "table_name": c.get("cert_name", ""),     # 表格區顯示名稱（只顯示證照名稱，不含發證中心）
            "photo_name": c.get("cert_name", ""),     # 圖片下方名稱
            "photo_path": c.get("cert_path", ""),     # 圖片路徑
            "date": c.get("acquire_date", ""),        # 日期
        }
        category = c.get("category", "other")
        if category == "labor":
            labor.append(item)
        elif category == "intl":
            international.append(item)
        elif category == "local":
            local.append(item)
        else:
            other.append(item)
    return labor, international, local, other

def fill_certificates_to_doc(context, prefix, items, max_count):
    """
    填入 Word 模板（表格區）
    prefix 例如: LaborCerts_  → LaborCerts_1, LaborCerts_2 …
    """
    for i in range(1, max_count + 1):
        if i <= len(items):
            context[f"{prefix}{i}"] = items[i-1].get("table_name", "")
        else:
            context[f"{prefix}{i}"] = ""

def fill_certificate_photos(context, doc, items, start_index, max_count=8):
    """
    圖片區（依順序放，不分類）
    start_index → 從第幾張開始，例如 1、9、17、25
    max_count → 最多填充幾張（實際填充的數量可能少於此值）
    """
    image_size = Inches(3.0)
    actual_count = min(len(items), max_count)
    
    # 填充實際有的證照
    for idx, item in enumerate(items[:max_count], start=start_index):
        photo_path = item.get("photo_path", "")
        photo_name = item.get("photo_name", "")
        
        if photo_path:
            image_obj = safe_create_inline_image(doc, photo_path, image_size, "證照")
            context[f"CertPhotoImages_{idx}"] = image_obj if image_obj else ""
        else:
            context[f"CertPhotoImages_{idx}"] = ""
        
        context[f"CertPhotoName_{idx}"] = photo_name
    
    # 清空本頁未使用的格子（如果實際數量少於 max_count）
    if actual_count < max_count:
        for idx in range(start_index + actual_count, start_index + max_count):
            context[f"CertPhotoImages_{idx}"] = ""
            context[f"CertPhotoName_{idx}"] = ""

# -------------------------
# 儲存結構化資料
# -------------------------
def save_structured_data(cursor, student_id, data, semester_id=None):
    try:
        # 1) 儲存 Student_Info (基本資料)
        cursor.execute("""
            INSERT INTO Student_Info (StuID, StuName, BirthDate, Gender, Phone, Email, Address, ConductScore, Autobiography, PhotoPath, UpdatedAt)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON DUPLICATE KEY UPDATE 
                StuName=VALUES(StuName), BirthDate=VALUES(BirthDate), Gender=VALUES(Gender),
                Phone=VALUES(Phone), Email=VALUES(Email), Address=VALUES(Address),
                ConductScore=VALUES(ConductScore), Autobiography=VALUES(Autobiography),
                PhotoPath=VALUES(PhotoPath), UpdatedAt=NOW()
        """, (
            student_id, data.get('name'), data.get('birth_date'), data.get('gender'),
            data.get('phone'), data.get('email'), data.get('address'),
            data.get('conduct_score'), data.get('autobiography'), data.get('photo_path')
        ))
        print(f"🔍 保存學生資料: name={data.get('name')}, birth_date={data.get('birth_date')}, gender={data.get('gender')}")

        # 2) 儲存課程 (先刪除同學同學期的課程，再插入)
        # 檢查表是否有 SemesterID 列
        try:
            cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'SemesterID'")
            has_semester_id = cursor.fetchone() is not None
        except:
            has_semester_id = False
        
        if semester_id is None:
            # 若沒有 semester_id，仍刪除所有該 StuID 的課程（保守處理）
            cursor.execute("DELETE FROM course_grades WHERE StuID=%s", (student_id,))
        else:
            if has_semester_id:
                cursor.execute("DELETE FROM course_grades WHERE StuID=%s AND IFNULL(SemesterID, '')=%s", (student_id, semester_id))
            else:
                # 如果表沒有 SemesterID 列，只根據 StuID 刪除
                cursor.execute("DELETE FROM course_grades WHERE StuID=%s", (student_id,))

        seen_course_names = set()
        unique_courses = []

        for c in data.get('courses', []):
            course_name = (c.get('name') or '').strip()
            if course_name and course_name not in seen_course_names:
                unique_courses.append(c)
                seen_course_names.add(course_name)
            elif course_name:
                # 重複課程，跳過
                print(f"⚠️ 偵測到重複課程名稱並已跳過: {course_name}")

        for c in unique_courses:
            # 支援 semester_id 儲存（如果表有 SemesterID 列）
            if semester_id is not None and has_semester_id:
                cursor.execute("""
                    REPLACE INTO course_grades (StuID, CourseName, Credits, Grade, SemesterID)
                    VALUES (%s,%s,%s,%s,%s)
                """, (student_id, c.get('name'), c.get('credits'), c.get('grade'), semester_id))
            else:
                cursor.execute("""
                    INSERT INTO course_grades (StuID, CourseName, Credits, Grade)
                    VALUES (%s,%s,%s,%s)
                """, (student_id, c.get('name'), c.get('credits'), c.get('grade')))

        # 3) 儲存證照（整合：文本 + 圖片皆放 student_certifications）
        # 為簡潔處理：刪除該學生既有證照（提交履歷時，視為更新整份證照清單）
        cursor.execute("DELETE FROM student_certifications WHERE StuID=%s", (student_id,))

        # 檢查 student_certifications 表的實際列結構
        try:
            cursor.execute("SHOW COLUMNS FROM student_certifications")
            columns_info = cursor.fetchall()
            # 確保完全讀取所有結果，避免 "Unread result found" 錯誤
            if columns_info:
                column_names = [col['Field'] for col in columns_info]
            else:
                column_names = []
            
            has_cert_code = 'cert_code' in column_names
            has_cert_name = 'CertName' in column_names
            has_cert_type = 'CertType' in column_names
            has_cert_path = 'CertPath' in column_names
            has_acquisition_date = 'AcquisitionDate' in column_names
            has_issuing_body = 'IssuingBody' in column_names
            has_authority_name = 'authority_name' in column_names
            has_custom_cert_name = 'custom_cert_name' in column_names
            has_issuer = 'issuer' in column_names
            has_job_category = 'job_category' in column_names
            has_level = 'level' in column_names
        except:
            # 如果查詢失敗，假設所有列都不存在（保守處理）
            has_cert_code = False
            has_cert_name = False
            has_cert_type = False
            has_cert_path = False
            has_acquisition_date = False
            has_issuing_body = False

        # 3a) 插入文本證照 (structured_certifications)
        # 現在需要保存 cert_code, authority_name, custom_cert_name, issuer, AcquisitionDate
        structured_certs = data.get('structured_certifications', [])
        # 獲取證照圖片路徑列表（與 structured_certs 對應，按順序）
        cert_photo_paths = data.get('cert_photo_paths', [])
        print(f"🔍 證照記錄數: {len(structured_certs)}, 證照圖片數: {len(cert_photo_paths)}")
        
        for idx, cert in enumerate(structured_certs):
            cert_code = cert.get('code', '').strip().upper()
            name = cert.get('name', '').strip()
            ctype = cert.get('type', 'other')
            acquire_date = cert.get('acquisition_date') or cert.get('acquire_date')
            issuer = cert.get('issuer', '').strip()
            authority_id = cert.get('authority_id')
            authority_name = cert.get('authority_name', '').strip()
            custom_cert_name = cert.get('custom_cert_name', '').strip()
            job_category = cert.get('job_category', '').strip()
            level = cert.get('level', '').strip()
            
            # 檢查是否為空記錄：必須至少有職類+級別，或者有證照名稱/代碼/自填名稱
            has_job_category_and_level = job_category and level and job_category.strip() and level.strip()
            has_cert_name_value = name and name.strip()  # 改為 has_cert_name_value 避免與 has_cert_name 變數衝突
            has_cert_code = cert_code and cert_code.strip()
            has_custom_name = custom_cert_name and custom_cert_name.strip()
            
            # 如果沒有任何有效數據，跳過（不保存空記錄）
            if not has_job_category_and_level and not has_cert_name_value and not has_cert_code and not has_custom_name:
                print(f"⚠️ 跳過空的證照記錄 (save_structured_data, 索引 {idx}): job_category={job_category}, level={level}, name={name}, code={cert_code}, custom_name={custom_cert_name}")
                continue
            
            # 如果 authority_id 不是 'OTHER' 且不是 None，從數據庫獲取發證中心名稱
            final_authority_name = authority_name
            if authority_id and str(authority_id).strip() and str(authority_id).strip() != 'OTHER':
                try:
                    cursor.execute("SELECT name FROM cert_authorities WHERE id = %s", (int(authority_id),))
                    auth_result = cursor.fetchone()
                    if auth_result:
                        final_authority_name = auth_result.get('name', '')
                except Exception as e:
                    print(f"⚠️ 獲取發證中心名稱失敗: {e}")
                    # 如果獲取失敗，使用 authority_name（如果有的話）
            
            # 如果 cert_code 不是 'OTHER' 且不為空，從 certificate_codes 表獲取 job_category 和 level
            final_job_category = job_category
            final_level = level
            if cert_code and cert_code.strip() and cert_code.strip().upper() != 'OTHER':
                try:
                    # 確保之前的查詢結果已完全讀取
                    cursor.fetchall() if cursor.with_rows else None
                    cursor.execute("SELECT job_category, level FROM certificate_codes WHERE code = %s", (cert_code,))
                    cert_result = cursor.fetchone()
                    if cert_result:
                        # 從 certificate_codes 表獲取的 job_category 和 level 優先
                        if cert_result.get('job_category'):
                            final_job_category = cert_result.get('job_category', '').strip()
                        if cert_result.get('level'):
                            final_level = cert_result.get('level', '').strip()
                except Exception as e:
                    print(f"⚠️ 從 certificate_codes 獲取 job_category 和 level 失敗: {e}")
                    # 如果獲取失敗，使用表單傳入的值（如果有的話）
            
            # 獲取對應的證照圖片路徑（如果有的話）
            cert_photo_path = None
            if idx < len(cert_photo_paths) and cert_photo_paths[idx]:
                cert_photo_path = cert_photo_paths[idx]
            
            # 根據實際存在的列動態構建 SQL
            columns = ['StuID']
            values = [student_id]
            
            if has_cert_code:
                # 如果 cert_code 為空但 custom_cert_name 有值，使用 'OTHER'
                final_cert_code = cert_code if cert_code else ('OTHER' if custom_cert_name else '')
                if final_cert_code:
                    columns.append('cert_code')
                    values.append(final_cert_code)
            
            if has_authority_name and final_authority_name:
                columns.append('authority_name')
                values.append(final_authority_name)
            
            if has_custom_cert_name and custom_cert_name:
                columns.append('custom_cert_name')
                values.append(custom_cert_name)
            
            if has_cert_name:  # has_cert_name 是檢查列是否存在的變數
                # 優先使用 custom_cert_name，否則使用 name
                final_name = custom_cert_name if custom_cert_name else name
                if final_name:
                    columns.append('CertName')
                    values.append(final_name)
            
            if has_cert_type:
                columns.append('CertType')
                values.append(ctype)
            
            if has_cert_path:
                columns.append('CertPath')
                values.append(cert_photo_path)  # 使用對應的圖片路徑，如果沒有則為 None
            
            if has_acquisition_date and acquire_date:
                columns.append('AcquisitionDate')
                values.append(acquire_date)
            
            if has_issuer and issuer:
                columns.append('issuer')
                values.append(issuer)
            
            # 使用最終確定的 job_category 和 level
            # 邏輯：
            # - 如果 cert_code 不是 'OTHER'，從 certificate_codes 表獲取並保存到 student_certifications（連動）
            # - 如果 cert_code 是 'OTHER'，使用表單輸入的值並保存到 student_certifications（用戶自填）
            if has_job_category:
                # 無論是從 certificate_codes 獲取還是表單輸入，都保存到 student_certifications
                if final_job_category:
                    columns.append('job_category')
                    values.append(final_job_category)
                else:
                    # 如果沒有值，也保存空字串（確保列存在）
                    columns.append('job_category')
                    values.append('')
            
            if has_level:
                # 無論是從 certificate_codes 獲取還是表單輸入，都保存到 student_certifications
                if final_level:
                    columns.append('level')
                    values.append(final_level)
                else:
                    # 如果沒有值，也保存空字串（確保列存在）
                    columns.append('level')
                    values.append('')
            
            columns.append('CreatedAt')
            
            if len(columns) > 1:  # 至少要有 StuID 和 CreatedAt
                placeholders = ', '.join(['%s'] * (len(columns) - 1)) + ', NOW()'
                columns_str = ', '.join(columns[:-1])  # 排除 CreatedAt，因為用 NOW()
                sql = f"INSERT INTO student_certifications ({columns_str}, CreatedAt) VALUES ({placeholders})"
                try:
                    cursor.execute(sql, tuple(values))  # values 不包含 CreatedAt 的值
                except Exception as e:
                    # 如果出現 "Unread result found" 錯誤，先清空未讀取的結果
                    if "Unread result" in str(e):
                        try:
                            cursor.fetchall()
                        except:
                            pass
                        # 重新執行插入
                        cursor.execute(sql, tuple(values))
                    else:
                        raise

        # 3b) 插入上傳的證照圖片
        cert_photo_paths = data.get('cert_photo_paths') or []
        cert_names = data.get('cert_names') or []
        cert_codes = data.get('cert_codes') or []  # 新增：證照代碼列表
        cert_issuers = data.get('cert_issuers') or []  # 新增：發證人列表
        # 四個陣列可能長度不同，取最大
        max_len = max(len(cert_photo_paths), len(cert_names), len(cert_codes), len(cert_issuers))
        for i in range(max_len):
            path = cert_photo_paths[i] if i < len(cert_photo_paths) else None
            name = cert_names[i] if i < len(cert_names) else ''
            cert_code = cert_codes[i].strip().upper() if i < len(cert_codes) and cert_codes[i] else None
            issuer = cert_issuers[i].strip() if i < len(cert_issuers) and cert_issuers[i] else None
            
            if not path and not name:
                continue
            
            # 根據實際存在的列動態構建 SQL
            columns = ['StuID']
            values = [student_id]
            
            if has_cert_code and cert_code:
                columns.append('cert_code')
                values.append(cert_code)
            
            if has_cert_name:
                columns.append('CertName')
                values.append(name or None)
            
            if has_cert_type:
                columns.append('CertType')
                values.append('photo')
            
            if has_cert_path:
                columns.append('CertPath')
                values.append(path or None)
            
            if has_issuing_body:
                columns.append('IssuingBody')
                values.append(issuer or None)
            
            if has_issuing_body:
                columns.append('IssuingBody')
                values.append(issuer or None)
            
            columns.append('CreatedAt')
            
            if len(columns) > 1:  # 至少要有 StuID 和 CreatedAt
                placeholders = ', '.join(['%s'] * (len(columns) - 1)) + ', NOW()'
                columns_str = ', '.join(columns[:-1])  # 排除 CreatedAt，因為用 NOW()
                sql = f"INSERT INTO student_certifications ({columns_str}, CreatedAt) VALUES ({placeholders})"
                cursor.execute(sql, tuple(values))  # values 不包含 CreatedAt 的值

        # 4) 儲存語文能力（student_languageskills 表）
        cursor.execute("DELETE FROM student_languageskills WHERE StuID=%s", (student_id,))
        for lang_skill in data.get('structured_languages', []):
            if lang_skill.get('language') and lang_skill.get('level'):
                cursor.execute("""
                    INSERT INTO student_languageskills (StuID, Language, Level, CreatedAt)
                    VALUES (%s, %s, %s, NOW())
                """, (student_id, lang_skill['language'], lang_skill['level']))

        return True
    except Exception as e:
        print("❌ 儲存結構化資料錯誤:", e)
        traceback.print_exc()
        return False

# -------------------------
# 取回學生資料 (for 生成履歷)
# -------------------------
def get_student_info_for_doc(cursor, student_id, semester_id=None):
    data = {}
    cursor.execute("SELECT * FROM Student_Info WHERE StuID=%s", (student_id,))
    data['info'] = cursor.fetchone() or {}

    # 檢查表是否有 SemesterID、ProofImage 和 transcript_path 列
    try:
        cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'SemesterID'")
        has_semester_id = cursor.fetchone() is not None
        cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'ProofImage'")
        has_proof_image = cursor.fetchone() is not None
        cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'transcript_path'")
        has_transcript_path = cursor.fetchone() is not None
    except:
        has_semester_id = False
        has_proof_image = False
        has_transcript_path = False
    
    # 優先使用 ProofImage 欄位，如果沒有則使用 transcript_path（兼容舊結構）
    transcript_field = 'ProofImage' if has_proof_image else ('transcript_path' if has_transcript_path else None)
    
    if semester_id is not None and has_semester_id:
        if transcript_field:
            cursor.execute(f"""
                SELECT CourseName, Credits, Grade, IFNULL({transcript_field}, '') AS transcript_path, SemesterID
                FROM course_grades
                WHERE StuID=%s AND SemesterID=%s
            """, (student_id, semester_id))
        else:
            cursor.execute("""
                SELECT CourseName, Credits, Grade, SemesterID
                FROM course_grades
                WHERE StuID=%s AND SemesterID=%s
            """, (student_id, semester_id))
    else:
        if transcript_field:
            cursor.execute(f"""
                SELECT CourseName, Credits, Grade, IFNULL({transcript_field}, '') AS transcript_path
                FROM course_grades
                WHERE StuID=%s
            """, (student_id,))
        else:
            cursor.execute("""
                SELECT CourseName, Credits, Grade
                FROM course_grades
                WHERE StuID=%s
            """, (student_id,))

    grades_rows = cursor.fetchall() or []
    # Extract transcript_path: prefer the one with SemesterID == semester_id, else latest non-empty
    data['grades'] = grades_rows
    data['transcript_path'] = ''

    # Try to find a transcript_path from grades_rows
    for row in grades_rows:
        tp = row.get('transcript_path')
        if tp:
            data['transcript_path'] = tp
            break

    # 證照 - 使用新的查詢方式（JOIN certificate_codes 和 cert_authorities）
    # 先嘗試使用新的 JOIN 查詢（有 cert_code 的記錄）
    cursor.execute("""
        SELECT
            cc.name AS cert_name,
            cc.category AS cert_category,
            CONCAT(cc.name, ' (', ca.name, ')') AS full_name,
            sc.CertPath AS cert_path,
            sc.AcquisitionDate AS acquire_date
        FROM student_certifications sc
        LEFT JOIN certificate_codes cc ON sc.cert_code = cc.code
        LEFT JOIN cert_authorities ca ON cc.authority_id = ca.id
        WHERE sc.StuID = %s
        ORDER BY sc.AcquisitionDate DESC, sc.id ASC
    """, (student_id,))
    cert_rows = cursor.fetchall() or []
    
    # 轉換為統一格式
    certifications = []
    for row in cert_rows:
        # 如果有 JOIN 結果，使用 JOIN 的資料
        if row.get('cert_name'):
            certifications.append({
                "cert_name": row.get('cert_name', ''),
                "category": row.get('cert_category', 'other'),
                "full_name": row.get('full_name', ''),
                "cert_path": row.get('cert_path', ''),
                "acquire_date": row.get('acquire_date', ''),
            })
        else:
            # 兼容舊資料：沒有 cert_code 的記錄，使用原始欄位
            certifications.append({
                "cert_name": row.get('CertName', ''),
                "category": row.get('CertType', 'other'),
                "full_name": row.get('CertName', ''),
                "cert_path": row.get('CertPhotoPath', ''),
                "acquire_date": row.get('AcquisitionDate', ''),
            })
    
    data['certifications'] = certifications

    # 語文能力（student_languageskills 表）
    cursor.execute("SELECT Language, Level FROM student_languageskills WHERE StuID=%s", (student_id,))
    data['languages'] = cursor.fetchall() or []

    # 缺勤佐證圖片：從 absence_records 表獲取最新的 image_path
    # 需要先獲取 user_id（通過 StuID 從 users 表查找）
    try:
        cursor.execute("SELECT id FROM users WHERE username=%s", (student_id,))
        user_row = cursor.fetchone()
        if user_row:
            user_id = user_row.get('id')
            print(f"🔍 查找缺勤佐證圖片: student_id={student_id}, user_id={user_id}")
            # 嘗試使用 created_at 排序，如果沒有該欄位則使用 id
            try:
                cursor.execute("""
                    SELECT image_path, created_at
                    FROM absence_records
                    WHERE user_id = %s AND image_path IS NOT NULL AND image_path != ''
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (user_id,))
            except:
                # 如果 created_at 欄位不存在，使用 id 排序
                cursor.execute("""
                    SELECT image_path
                    FROM absence_records
                    WHERE user_id = %s AND image_path IS NOT NULL AND image_path != ''
                    ORDER BY id DESC
                    LIMIT 1
                """, (user_id,))
            absence_row = cursor.fetchone()
            if absence_row:
                image_path = absence_row.get('image_path')
                data['Absence_Proof_Path'] = image_path
                print(f"✅ 找到缺勤佐證圖片路徑: {image_path}")
            else:
                print(f"⚠️ 未找到缺勤佐證圖片路徑 (user_id={user_id})")
        else:
            print(f"⚠️ 找不到用戶: student_id={student_id}")
    except Exception as e:
        print(f"⚠️ 獲取缺勤佐證圖片路徑失敗: {e}")
        traceback.print_exc()
        # 不影響其他數據的返回

    return data

# -------------------------
# Word 生成邏輯
# -------------------------
def generate_application_form_docx(student_data, output_path):
    try:
        base_dir = os.path.dirname(__file__)
        template_path = os.path.abspath(os.path.join(base_dir, "..", "frontend", "static", "examples", "實習履歷(空白).docx"))
        if not os.path.exists(template_path):
            print("❌ 找不到模板：", template_path)
            return False

        doc = DocxTemplate(template_path)
        info = student_data.get("info", {})
        grades = student_data.get("grades", [])
        certs = student_data.get("certifications", [])

        # 格式化出生日期
        def fmt_date(val):
            if hasattr(val, 'strftime'):
                return val.strftime("%Y-%m-%d")
            if isinstance(val, str) and len(val) >= 10:
                return val.split("T")[0]
            return ""

        bdate = fmt_date(info.get("BirthDate"))
        year, month, day = ("", "", "")
        if bdate:
            try:
                year, month, day = bdate.split("-")
            except:
                pass

        # 照片
        photo_path = info.get("PhotoPath")
        image_obj = safe_create_inline_image(doc, photo_path, Inches(1.2), "照片")

        # 處理課程資料（保留原邏輯）
        MAX_COURSES = 30
        padded_grades = grades[:MAX_COURSES]
        padded_grades += [{'CourseName': '', 'Credits': ''}] * (MAX_COURSES - len(padded_grades))

        context_courses = {}
        NUM_ROWS = 10
        NUM_COLS = 3
        for i in range(NUM_ROWS):
            for j in range(NUM_COLS):
                index = i * NUM_COLS + j
                if index < MAX_COURSES:
                    course = padded_grades[index]
                    row_num = i + 1
                    col_num = j + 1
                    context_courses[f'CourseName_{row_num}_{col_num}'] = course.get('CourseName', '')
                    context_courses[f'Credits_{row_num}_{col_num}'] = course.get('Credits', '')

        # 插入成績單圖片：嘗試從 student_data['transcript_path']（由 get_student_info_for_doc 提供）
        transcript_path = student_data.get("transcript_path") or info.get("TranscriptPath") or ''
        transcript_obj = safe_create_inline_image(doc, transcript_path, Inches(6.0), "成績單")

        # 缺勤佐證圖片
        absence_proof_path = student_data.get("Absence_Proof_Path")
        absence_proof_obj = safe_create_inline_image(doc, absence_proof_path, Inches(6.0), "缺勤佐證")

        # 操行等級
        conduct_score = info.get('ConductScore', '')
        conduct_marks = {k: '□' for k in ['C_You', 'C_Jia', 'C_Yi', 'C_Bing', 'C_Ding']}
        mapping = {'優': 'C_You', '甲': 'C_Jia', '乙': 'C_Yi', '丙': 'C_Bing', '丁': 'C_Ding'}
        if conduct_score in mapping:
            conduct_marks[mapping[conduct_score]] = '■'

        # 證照分類 - 使用新的分類邏輯
        # certs 已經從 get_student_info_for_doc 返回，格式統一
        # 優先使用前端提交的證照名稱（如果有的話）
        # 這樣可以確保只顯示用戶實際選擇的證照，而不是數據庫中所有相關記錄
        cert_names_from_form = student_data.get("cert_names", [])
        cert_photo_paths_from_form = student_data.get("cert_photo_paths", [])
        
        # 如果有前端提交的證照名稱，使用它們來覆蓋數據庫查詢結果
        if cert_names_from_form:
            # 重新構建證照列表，使用前端提交的名稱
            certs_with_form_names = []
            for idx, (name, path) in enumerate(zip(cert_names_from_form, cert_photo_paths_from_form)):
                if name and name.strip():
                    # 從原始 certs 中找到對應的證照（通過索引或路徑匹配）
                    matching_cert = None
                    if idx < len(certs):
                        matching_cert = certs[idx]
                    elif path:
                        # 通過路徑匹配
                        for c in certs:
                            if c.get("cert_path") == path:
                                matching_cert = c
                                break
                    
                    # 使用前端提交的名稱，但保留其他信息（類別、路徑等）
                    cert_item = {
                        "cert_name": name.strip(),  # 使用前端提交的名稱
                        "category": matching_cert.get("category", "other") if matching_cert else "other",
                        "cert_path": path if path else (matching_cert.get("cert_path", "") if matching_cert else ""),
                        "acquire_date": matching_cert.get("acquire_date", "") if matching_cert else "",
                    }
                    certs_with_form_names.append(cert_item)
            
            # 如果有匹配的證照，使用新的列表；否則使用原始列表
            if certs_with_form_names:
                certs = certs_with_form_names
        
        # 分類證照
        labor_list, intl_list, local_list, other_list = categorize_certifications(certs)

        def pad_list(lst, length=5):
            lst = lst[:length]
            lst += [''] * (length - len(lst))
            return lst

        # 建 context
        # 處理自傳：移除多餘的換行符，避免產生空白行
        autobiography = info.get('Autobiography', '').strip()
        if autobiography:
            # 將多個連續換行符替換為單個換行符，移除開頭和結尾的換行符
            autobiography = re.sub(r'\n{3,}', '\n\n', autobiography)
            autobiography = autobiography.strip('\n')
        
        context = {
            'StuID': info.get('StuID', ''),
            'StuName': info.get('StuName', ''),
            'BirthYear': year, 'BirthMonth': month, 'BirthDay': day,
            'Gender': info.get('Gender', ''),
            'Phone': info.get('Phone', ''),
            'Email': info.get('Email', ''),
            'Address': info.get('Address', ''),
            'ConductScoreNumeric': info.get('ConductScoreNumeric', ''),
            'ConductScore': conduct_score,
            'Autobiography': autobiography,  # 使用處理過的自傳
            'Image_1': image_obj,
            'transcript_path': transcript_obj,
            'Absence_Proof_Image': absence_proof_obj if absence_proof_obj else "（查無佐證圖片）"
        }
        
        # 清空可能出現在"缺勤記錄"標題上方的空變數
        # 如果模板中有這些變數但值為空，設為 None 以避免顯示空白行
        # 常見的可能變數名
        empty_vars_to_clear = [
            'empty_line_1', 'empty_line_2', 'empty_line_3',
            'blank_line_1', 'blank_line_2', 'blank_line_3',
            'spacer_1', 'spacer_2', 'spacer_3',
            'extra_line_1', 'extra_line_2', 'extra_line_3',
            'blank_1', 'blank_2', 'blank_3',
        ]
        for var in empty_vars_to_clear:
            context[var] = None  # 設為 None 而不是空字符串，Jinja2 會跳過 None 值

        # 加入缺勤統計
        # 只填充這8個標準字段，確保沒有多餘的空白行
        absence_fields = ['曠課', '遲到', '事假', '病假', '生理假', '公假', '喪假', '總計']
        for t in absence_fields:
            key = f"absence_{t}_units"
            # 從 student_data 中獲取缺勤統計數據
            value = student_data.get(key, "0 節")
            context[key] = value
            # 調試輸出
            if value == "0 節" and t != "總計":
                print(f"⚠️ 缺勤統計 {key} 未找到，使用預設值: {value}")
            else:
                print(f"✅ 缺勤統計 {key} = {value}")
        
        # 如果模板中有額外的行（例如第9、10、11行），將它們設為空字符串
        # 常見的額外變數名可能是：absence_row_9, absence_row_10, absence_row_11 等
        # 或者：absence_9_units, absence_10_units, absence_11_units 等
        # 清空可能的額外行變數
        for i in range(9, 12):  # 第9、10、11行
            # 嘗試多種可能的變數名格式
            possible_keys = [
                f"absence_row_{i}",
                f"absence_{i}_units",
                f"absence_row_{i}_units",
                f"absence_item_{i}",
                f"absence_type_{i}",
            ]
            for key in possible_keys:
                context[key] = ""
        
        # 清空可能存在的其他缺勤類型變數（防止模板中有額外的空白行）
        # 例如：absence_其他_units, absence_其他1_units 等
        # 只保留標準的8個字段，其他都設為空字符串
        standard_keys = [f"absence_{t}_units" for t in absence_fields]
        for key in list(context.keys()):
            if key.startswith("absence_") and key.endswith("_units"):
                if key not in standard_keys:
                    context[key] = ""  # 清空非標準字段

        # 加入操行等級勾選
        context.update(conduct_marks)

        # 加入課程資料
        context.update(context_courses)

        # 加入證照文字清單 - 使用新的填充函數
        fill_certificates_to_doc(context, "LaborCerts_", labor_list, 5)
        fill_certificates_to_doc(context, "IntlCerts_", intl_list, 5)
        fill_certificates_to_doc(context, "LocalCerts_", local_list, 5)
        fill_certificates_to_doc(context, "OtherCerts_", other_list, 5)

        # 證照圖片（不分類，依順序塞）- 使用新的填充函數
        # 將四類組裝成一個大 list（圖片不分類）
        flat_list = labor_list + intl_list + local_list + other_list
        
        # 分頁顯示證照圖片：每頁8張，最多32張（4頁）
        # 使用區塊變數控制頁面顯示/隱藏
        certs_per_page = 8
        max_total = 32  # 最多32張（4頁）
        
        # 只處理實際有圖片的證照（最多32張）
        certs_with_photos = [c for c in flat_list if c.get("photo_path") and os.path.exists(c.get("photo_path", ""))]
        certs_to_display = certs_with_photos[:max_total]
        total_certs = len(certs_to_display)
        
        # 初始化所有證照圖片和名稱為空
        for idx in range(1, 33):
            context[f"CertPhotoImages_{idx}"] = ""
            context[f"CertPhotoName_{idx}"] = ""
        
        # 初始化所有頁面區塊為 False（不顯示）
        # 使用布林值控制頁面顯示，模板中使用 {% if cert_page_2_block %} ... {% endif %}
        context["cert_page_2_block"] = False
        context["cert_page_3_block"] = False
        context["cert_page_4_block"] = False
        
        if total_certs > 0:
            # 第一頁（1-8）：總是填充（如果有證照）
            first_page_certs = certs_to_display[:min(8, total_certs)]
            if first_page_certs:
                fill_certificate_photos(context, doc, first_page_certs, start_index=1, max_count=8)
            
            # 第二頁（9-16）：如果 total_certs > 8 則顯示
            if total_certs > 8:
                context["cert_page_2_block"] = True  # 設置為 True 以顯示區塊
                second_page_certs = certs_to_display[8:min(16, total_certs)]
                if second_page_certs:
                    fill_certificate_photos(context, doc, second_page_certs, start_index=9, max_count=8)
            
            # 第三頁（17-24）：如果 total_certs > 16 則顯示
            if total_certs > 16:
                context["cert_page_3_block"] = True  # 設置為 True 以顯示區塊
                third_page_certs = certs_to_display[16:min(24, total_certs)]
                if third_page_certs:
                    fill_certificate_photos(context, doc, third_page_certs, start_index=17, max_count=8)
            
            # 第四頁（25-32）：如果 total_certs > 24 則顯示
            if total_certs > 24:
                context["cert_page_4_block"] = True  # 設置為 True 以顯示區塊
                fourth_page_certs = certs_to_display[24:min(32, total_certs)]
                if fourth_page_certs:
                    fill_certificate_photos(context, doc, fourth_page_certs, start_index=25, max_count=8)

        # 語文能力
        lang_context = {}
        lang_codes = ['En', 'Jp', 'Tw', 'Hk']
        level_codes = ['Jing', 'Zhong', 'Lue']
        for code in lang_codes:
            for level_code in level_codes:
                lang_context[f'{code}_{level_code}'] = '□'

        lang_code_map = {'英語': 'En', '日語': 'Jp', '台語': 'Tw', '客語': 'Hk'}
        level_code_map = {'精通': 'Jing', '中等': 'Zhong', '略懂': 'Lue'}
        
        # 獲取已選擇的語言列表
        selected_languages = set()
        for lang_skill in student_data.get('languages', []):
            lang = lang_skill.get('Language')
            level = lang_skill.get('Level')
            lang_code = lang_code_map.get(lang)
            level_code = level_code_map.get(level)
            if lang_code and level_code:
                key = f'{lang_code}_{level_code}'
                if key in lang_context:
                    lang_context[key] = '■'
                    selected_languages.add(lang_code)
        
        # 對於未選擇的語言，自動設置為「略懂」
        all_languages = {'En': '英語', 'Jp': '日語', 'Tw': '台語', 'Hk': '客語'}
        for lang_code, lang_name in all_languages.items():
            if lang_code not in selected_languages:
                # 設置為「略懂」
                lue_key = f'{lang_code}_Lue'
                if lue_key in lang_context:
                    lang_context[lue_key] = '■'
                    print(f"📝 未選擇的語言 {lang_name} 自動設置為「略懂」")

        context.update(lang_context)
        
        # 在渲染前，清理所有可能導致空白行的空變數
        # 將所有空字符串變數設為 None，這樣 Jinja2 在模板中會跳過它們
        # 但保留重要的變數（如數字、圖片等）
        for key in list(context.keys()):
            value = context[key]
            # 如果是空字符串，設為 None（但保留重要的變數）
            if isinstance(value, str) and value.strip() == '':
                # 檢查是否為重要變數（不應設為 None）
                important_vars = ['StuID', 'StuName', 'Gender', 'Phone', 'Email', 'Address', 
                                 'ConductScore', 'ConductScoreNumeric', 'BirthYear', 'BirthMonth', 'BirthDay']
                if key not in important_vars:
                    # 對於可能出現在"缺勤記錄"標題上方的變數，設為 None
                    # 這樣模板中如果使用 {% if variable %} 就不會顯示空白行
                    if any(key.startswith(prefix) for prefix in ['empty_', 'blank_', 'spacer_', 'extra_']):
                        context[key] = None
                    # 或者，如果變數名包含 "line" 或 "row"，也可能是空白行變數
                    elif 'line' in key.lower() or 'row' in key.lower():
                        context[key] = None

        # 在渲染前，清理所有無效的圖片對象（將 InlineImage 對象替換為 None 或空字符串）
        # 這樣可以避免在模板渲染時出現 UnrecognizedImageError
        for key, value in list(context.items()):
            if isinstance(value, InlineImage):
                # 檢查 InlineImage 對象是否有效
                # 如果圖片路徑不存在或無效，替換為空字符串
                try:
                    # 嘗試訪問 image_descriptor 來檢查圖片是否有效
                    if hasattr(value, 'image_descriptor'):
                        img_path = value.image_descriptor
                        if not os.path.exists(img_path) or not is_valid_image_file(img_path):
                            print(f"⚠️ 清理無效的圖片對象: {key} (路徑: {img_path})")
                            context[key] = ""
                except:
                    # 如果無法檢查，為了安全起見，保留原值
                    pass
        
        # 渲染與儲存
        try:
            doc.render(context)
            doc.save(output_path)
            print(f"✅ 履歷文件已生成: {output_path}")
            return True
        except Exception as render_error:
            # 如果渲染時仍然出現錯誤，嘗試再次清理所有圖片對象
            error_msg = str(render_error)
            error_type = type(render_error).__name__
            if "UnrecognizedImageError" in error_type or "image" in error_msg.lower():
                print(f"⚠️ 渲染時出現圖片錯誤，嘗試清理所有圖片對象後重試...")
                for key, value in list(context.items()):
                    if isinstance(value, InlineImage):
                        print(f"⚠️ 移除可能有問題的圖片對象: {key}")
                        context[key] = ""
                try:
                    doc.render(context)
                    doc.save(output_path)
                    print(f"✅ 履歷文件已生成（跳過無效圖片）: {output_path}")
                    return True
                except Exception as retry_error:
                    print(f"❌ 重試後仍然失敗: {retry_error}")
                    raise
            else:
                raise

    except Exception as e:
        print("❌ 生成 Word 檔錯誤:", e)
        traceback.print_exc()
        return False

# -------------------------
# API: 取得所有發證中心列表
# -------------------------
@resume_bp.route('/api/get_cert_authorities', methods=['GET'])
def get_cert_authorities():
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT id, name FROM cert_authorities ORDER BY name")
        authorities = cursor.fetchall()
        
        return jsonify({
            "success": True,
            "authorities": authorities
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# -------------------------
# API: 根據發證中心ID取得該中心的證照列表
# -------------------------
@resume_bp.route('/api/get_certificates_by_authority', methods=['GET'])
def get_certificates_by_authority():
    conn = None
    cursor = None
    try:
        authority_id = request.args.get('authority_id')
        if not authority_id:
            return jsonify({"success": False, "message": "缺少 authority_id 參數"}), 400
        
        try:
            authority_id = int(authority_id)
        except ValueError:
            return jsonify({"success": False, "message": "authority_id 必須是數字"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT code, name, category 
            FROM certificate_codes 
            WHERE authority_id = %s 
            ORDER BY name
        """, (authority_id,))
        certificates = cursor.fetchall()
        
        return jsonify({
            "success": True,
            "certificates": certificates
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# -------------------------
# API: 根據發證中心ID取得該中心的職類和級別列表
# -------------------------
@resume_bp.route('/api/get_job_categories_and_levels', methods=['GET'])
def get_job_categories_and_levels():
    conn = None
    cursor = None
    try:
        authority_id = request.args.get('authority_id')
        if not authority_id:
            return jsonify({"success": False, "message": "缺少 authority_id 參數"}), 400
        
        try:
            authority_id = int(authority_id)
        except ValueError:
            return jsonify({"success": False, "message": "authority_id 必須是數字"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 取得該發證中心的所有證照
        # 優先使用 certificate_codes 表的 job_category 和 level 字段（如果存在）
        # 否則從 name 字段解析職類和級別
        cursor.execute("""
            SELECT code, name, 
                   COALESCE(job_category, '') AS job_category,
                   COALESCE(level, '') AS level
            FROM certificate_codes 
            WHERE authority_id = %s 
            ORDER BY COALESCE(job_category, name), COALESCE(level, '')
        """, (authority_id,))
        certificates = cursor.fetchall()
        
        # 解析職類和級別
        import re
        job_categories = set()  # 使用 set 避免重複
        job_category_levels = {}  # {職類: [級別列表]}
        
        level_pattern = re.compile(r'(甲級|乙級|丙級|丁級|甲|乙|丙|丁)')
        
        for cert in certificates:
            # 優先使用 certificate_codes 表的 job_category 和 level 字段
            job_category = cert.get('job_category', '').strip()
            level = cert.get('level', '').strip()
            
            if job_category and level:
                # 如果 certificate_codes 表有 job_category 和 level，直接使用
                job_categories.add(job_category)
                if job_category not in job_category_levels:
                    job_category_levels[job_category] = set()
                job_category_levels[job_category].add(level)
            else:
                # 否則從 name 字段解析職類和級別（向後兼容）
                cert_name = cert.get('name', '').strip()
                if not cert_name:
                    continue
                
                # 嘗試從證照名稱中解析職類和級別
                match = level_pattern.search(cert_name)
                if match:
                    parsed_level = match.group(1)
                    # 將簡寫轉換為完整形式
                    level_map = {'甲': '甲級', '乙': '乙級', '丙': '丙級', '丁': '丁級'}
                    full_level = level_map.get(parsed_level, parsed_level)
                    
                    # 提取職類（移除級別後的部分）
                    parsed_job_category = level_pattern.sub('', cert_name).strip()
                    
                    if parsed_job_category:
                        job_categories.add(parsed_job_category)
                        if parsed_job_category not in job_category_levels:
                            job_category_levels[parsed_job_category] = set()
                        job_category_levels[parsed_job_category].add(full_level)
        
        # 轉換為列表並排序
        job_categories_list = sorted(list(job_categories))
        # 將級別集合轉換為排序列表
        for job_category in job_category_levels:
            job_category_levels[job_category] = sorted(list(job_category_levels[job_category]))
        
        return jsonify({
            "success": True,
            "job_categories": job_categories_list,
            "job_category_levels": job_category_levels  # {職類: [級別列表]}
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# -------------------------
# API: 根據證照代碼查詢名稱和類別
# -------------------------
@resume_bp.route('/api/get_certificate_info', methods=['GET'])
def get_certificate_info():
    conn = None
    cursor = None
    try:
        cert_code = request.args.get('code')
        if not cert_code:
            return jsonify({"success": False, "message": "缺少證照代碼 (code) 參數"}), 400

        cert_code = cert_code.strip().upper()

        conn = get_db()
        cursor = conn.cursor(dictionary=True) 

        # ❗ 查詢所有匹配的記錄
        sql_query = "SELECT name, category FROM certificate_codes WHERE code = %s"
        cursor.execute(sql_query, (cert_code,))
        
        # ❗ 使用 fetchall() 獲取所有結果
        results = cursor.fetchall()

        if results:
            # 找到資料，返回一個結果列表
            return jsonify({
                "success": True,
                # ❗ 返回的 info 是一個包含多個 {name, category} 的列表
                "info": results,
                "count": len(results)
            })
        else:
            # 查無此代碼
            return jsonify({
                "success": False,
                "message": f"查無代碼: {cert_code}"
            })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# -------------------------
# API：提交並生成履歷
# -------------------------
@resume_bp.route('/api/submit_and_generate', methods=['POST'])
def submit_and_generate_api():
    context = {}
    conn = None
    cursor = None

    try:
        if session.get('role') != 'student' or not session.get('user_id'):
            return jsonify({"success": False, "message": "只有學生可以提交"}), 403

        user_id = session['user_id']
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        data = request.form.to_dict()
        courses = json.loads(data.get('courses', '[]'))
        photo = request.files.get('photo')
        transcript_file = request.files.get('transcript_file')
        cert_files = request.files.getlist('cert_photos[]')
        cert_names = request.form.getlist('cert_names[]')

        ALLOWED_IMAGE_MIMES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']

        # 儲存照片
        photo_path = None
        if photo and photo.filename:
            if photo.mimetype not in ALLOWED_IMAGE_MIMES:
                return jsonify({"success": False, "message": f"照片檔案格式錯誤 ({photo.mimetype})"}), 400
            filename = secure_filename(photo.filename)
            photo_dir = os.path.join(UPLOAD_FOLDER, "photos")
            os.makedirs(photo_dir, exist_ok=True)
            ext = os.path.splitext(filename)[1]
            new_filename = f"{user_id}_photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            photo_path = os.path.join(photo_dir, new_filename)
            photo.save(photo_path)

        # 儲存成績單檔案（先儲存檔案，再 update 到 course_grades 的 transcript_path）
        transcript_path = None
        if transcript_file and transcript_file.filename:
            if transcript_file.mimetype not in ALLOWED_IMAGE_MIMES:
                return jsonify({"success": False, "message": f"成績單檔案格式錯誤 ({transcript_file.mimetype})"}), 400
            filename = secure_filename(transcript_file.filename)
            transcript_dir = os.path.join(UPLOAD_FOLDER, "transcripts")
            os.makedirs(transcript_dir, exist_ok=True)
            ext = os.path.splitext(filename)[1]
            new_filename = f"{user_id}_transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            transcript_path = os.path.join(transcript_dir, new_filename)
            transcript_file.save(transcript_path)

        # 儲存多張證照
        cert_photo_paths = []
        if cert_files:
            cert_dir = os.path.join(UPLOAD_FOLDER, "cert_photos")
            os.makedirs(cert_dir, exist_ok=True)

        for idx, file in enumerate(cert_files, start=1):
            if file and file.filename:
                if file.mimetype not in ALLOWED_IMAGE_MIMES:
                    print(f"⚠️ 證照檔案格式錯誤已跳過: {file.filename} ({file.mimetype})")
                    continue
                ext = os.path.splitext(secure_filename(file.filename))[1]
                new_filename = f"{user_id}_cert_{idx}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
                file_path = os.path.join(cert_dir, new_filename)
                file.save(file_path)
                cert_photo_paths.append(file_path)

        # 處理單張證照圖片（certificate_image + certificate_description）
        certificate_image_file = request.files.get('certificate_image')
        certificate_description = request.form.get('certificate_description', '')
        image_path_for_template = None
        if certificate_image_file and certificate_image_file.filename != '' and 'user_id' in session:
            try:
                cert_folder = os.path.join(UPLOAD_FOLDER, 'certificates')
                os.makedirs(cert_folder, exist_ok=True)
                filename = secure_filename(certificate_image_file.filename)
                file_extension = os.path.splitext(filename)[1] or '.png'
                unique_filename = f"{session['user_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}{file_extension}"
                image_save_path = os.path.join(cert_folder, unique_filename)
                certificate_image_file.save(image_save_path)
                image_path_for_template = image_save_path
            except Exception as e:
                print(f"❌ 儲存單一證照圖片失敗: {e}")
                traceback.print_exc()
                image_path_for_template = None

        if image_path_for_template or certificate_description:
            if cert_photo_paths is None:
                cert_photo_paths = []
            if cert_names is None:
                cert_names = []
            cert_photo_paths.insert(0, image_path_for_template or "")
            cert_names.insert(0, certificate_description or "")

        # 組合缺勤統計（支援學期範圍篩選）
        absence_stats = {}
        
        # 獲取學期範圍參數
        start_semester_id = request.form.get("start_semester_id", None)
        end_semester_id = request.form.get("end_semester_id", None)
        
        # 構建查詢條件
        where_conditions = ["user_id = %s"]
        query_params = [user_id]
        
        # 如果有學期範圍，添加學期篩選
        if start_semester_id and end_semester_id:
            # 獲取所有在範圍內的學期ID
            cursor.execute("""
                SELECT id FROM semesters 
                WHERE code >= (SELECT code FROM semesters WHERE id = %s)
                AND code <= (SELECT code FROM semesters WHERE id = %s)
                ORDER BY code
            """, (start_semester_id, end_semester_id))
            semester_ids_in_range = [row['id'] for row in cursor.fetchall()]
            if semester_ids_in_range:
                placeholders = ','.join(['%s'] * len(semester_ids_in_range))
                where_conditions.append(f"semester_id IN ({placeholders})")
                query_params.extend(semester_ids_in_range)
        
        where_clause = " AND ".join(where_conditions)
        
        cursor.execute(f"""
            SELECT 
                absence_type, 
                SUM(duration_units) AS total_units 
            FROM absence_records
            WHERE {where_clause}
            GROUP BY absence_type
        """, tuple(query_params))
        results = cursor.fetchall()
        all_types = ["曠課", "遲到", "事假", "病假", "生理假", "公假", "喪假"]
        db_stats = {t: 0 for t in all_types}
        for row in results:
            typ = row.get('absence_type')
            if typ in db_stats:
                try:
                    db_stats[typ] = int(row.get('total_units') or 0)
                except Exception:
                    db_stats[typ] = 0
        for t in all_types:
            absence_stats[f"absence_{t}_units"] = f"{db_stats.get(t,0)} 節"

        incoming_stats_json = request.form.get("absence_stats_json", None)
        if incoming_stats_json:
            try:
                incoming = json.loads(incoming_stats_json)
                for t in all_types:
                    val = incoming.get(t)
                    if val is not None:
                        try:
                            val_int = int(val)
                        except Exception:
                            try:
                                val_int = int(str(val).replace("節","").strip())
                            except Exception:
                                val_int = db_stats.get(t, 0)
                        absence_stats[f"absence_{t}_units"] = f"{val_int} 節"
            except Exception as e:
                print("⚠️ 無法解析 absence_stats_json，忽略前端傳入值:", e)

        total = 0
        for t in all_types:
            v = absence_stats.get(f"absence_{t}_units", "0 節")
            try:
                total += int(str(v).replace("節","").strip())
            except Exception:
                pass
        absence_stats["absence_總計_units"] = f"{total} 節"
        
        # 調試輸出：確認缺勤統計數據
        print("📊 缺勤統計數據:", absence_stats)
        
        context.update(absence_stats)
        
        # 調試輸出：確認 context 中的缺勤統計數據
        print("📊 context 中的缺勤統計數據:", {k: v for k, v in context.items() if k.startswith("absence_")})

        # 處理並儲存缺勤佐證圖片（與你原邏輯一致）
        absence_image_path = None
        try:
            uploaded_proof = request.files.get('proof_image') or request.files.get('absence_proof')
            if uploaded_proof and uploaded_proof.filename:
                if uploaded_proof.mimetype in ALLOWED_IMAGE_MIMES:
                    os.makedirs(ABSENCE_PROOF_FOLDER, exist_ok=True)
                    ext = os.path.splitext(secure_filename(uploaded_proof.filename))[1] or ".png"
                    fname = f"{user_id}_absence_proof_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
                    savep = os.path.join(ABSENCE_PROOF_FOLDER, fname)
                    uploaded_proof.save(savep)
                    absence_image_path = savep
                else:
                    print(f"⚠️ 上傳的缺勤佐證圖片格式不支援: {uploaded_proof.mimetype}")
        except Exception as e:
            print("⚠️ 儲存上傳的缺勤佐證圖片失敗:", e)
            traceback.print_exc()

        if not absence_image_path:
            try:
                ar_json = request.form.get("absence_records_json", None)
                if ar_json:
                    try:
                        ar_list = json.loads(ar_json)
                        for rec in reversed(ar_list):
                            img = rec.get("image_filename") or rec.get("image_path")
                            if img:
                                absence_image_path = img
                                break
                    except Exception as e:
                        print("⚠️ 解析 absence_records_json 失敗:", e)
            except Exception as e:
                print("⚠️ 嘗試讀取 absence_records_json 失敗:", e)

        if not absence_image_path:
            try:
                cursor.execute("""
                    SELECT image_path
                    FROM absence_records
                    WHERE user_id = %s AND image_path IS NOT NULL AND image_path != ''
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (user_id,))
                row = cursor.fetchone()
                if row:
                    absence_image_path = row.get('image_path')
            except Exception as e:
                print(f"Error fetching latest absence proof path from DB: {e}")

        context['Absence_Proof_Path'] = absence_image_path

        # 更新缺勤記錄的佐證圖片（從資料庫讀取的記錄，只需更新圖片）
        try:
            # 1. 處理個別記錄的佐證圖片上傳
            absence_records_with_images_json = request.form.get("absence_records_with_images", None)
            if absence_records_with_images_json:
                try:
                    records_with_images = json.loads(absence_records_with_images_json)
                    print(f"📝 準備更新 {len(records_with_images)} 筆缺勤記錄的佐證圖片")
                    
                    for record_info in records_with_images:
                        record_id = record_info.get("record_id")
                        if not record_id:
                            continue
                        
                        # 獲取對應的圖片文件
                        image_key = f"proof_image_{record_id}"
                        uploaded_image = request.files.get(image_key)
                        
                        if uploaded_image and uploaded_image.filename:
                            try:
                                # 保存圖片
                                os.makedirs(ABSENCE_PROOF_FOLDER, exist_ok=True)
                                ext = os.path.splitext(secure_filename(uploaded_image.filename))[1] or ".png"
                                fname = f"{user_id}_record_{record_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
                                save_path = os.path.join(ABSENCE_PROOF_FOLDER, fname)
                                uploaded_image.save(save_path)
                                
                                # 更新資料庫中對應記錄的 image_path
                                cursor.execute("""
                                    UPDATE absence_records 
                                    SET image_path = %s, updated_at = NOW()
                                    WHERE id = %s AND user_id = %s
                                """, (save_path, record_id, user_id))
                                
                                print(f"✅ 已更新缺勤記錄 {record_id} 的佐證圖片: {save_path}")
                            except Exception as e:
                                print(f"⚠️ 更新缺勤記錄 {record_id} 的佐證圖片失敗: {e}")
                                traceback.print_exc()
                    
                    conn.commit()
                    print(f"✅ 所有缺勤記錄的佐證圖片已成功更新")
                except Exception as e:
                    print(f"⚠️ 解析 absence_records_with_images 失敗: {e}")
                    traceback.print_exc()
            
            # 2. 如果有整體佐證圖片，更新到該學期所有沒有圖片的記錄
            if absence_image_path:
                semester_id = request.form.get("semester_id", None)
                if semester_id:
                    try:
                        # 檢查是否有 semester_id 欄位
                        cursor.execute("SHOW COLUMNS FROM absence_records LIKE 'semester_id'")
                        has_semester_id = cursor.fetchone() is not None
                        
                        if has_semester_id:
                            # 更新該學期所有沒有圖片的記錄
                            cursor.execute("""
                                UPDATE absence_records 
                                SET image_path = %s, updated_at = NOW()
                                WHERE user_id = %s AND semester_id = %s 
                                AND (image_path IS NULL OR image_path = '')
                            """, (absence_image_path, user_id, semester_id))
                        else:
                            # 如果沒有 semester_id 欄位，更新所有沒有圖片的記錄
                            cursor.execute("""
                                UPDATE absence_records 
                                SET image_path = %s, updated_at = NOW()
                                WHERE user_id = %s 
                                AND (image_path IS NULL OR image_path = '')
                            """, (absence_image_path, user_id))
                        
                        conn.commit()
                        print(f"✅ 已將整體佐證圖片更新到缺勤記錄")
                    except Exception as e:
                        print(f"⚠️ 更新整體佐證圖片失敗: {e}")
                        traceback.print_exc()
        except Exception as e:
            print(f"⚠️ 處理缺勤記錄圖片失敗: {e}")
            traceback.print_exc()

        # 查學生學號 (username)
        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        result = cursor.fetchone()
        if not result:
            return jsonify({"success": False, "message": "找不到使用者"}), 404
        student_id = result['username']

        # 確保 courses 中的 grade 欄位存在
        for c in courses:
            c['grade'] = c.get('grade', '')

        # 解析文本證照資料（非圖片）
        structured_certifications = []
        cert_names_text = request.form.getlist('cert_name[]')
        cert_types = request.form.getlist('cert_type[]')
        cert_codes_text = request.form.getlist('cert_code[]')
        cert_issuers_text = request.form.getlist('cert_issuer[]')
        cert_authority_ids = request.form.getlist('cert_authority[]')
        cert_authority_names = request.form.getlist('cert_authority_name[]')
        cert_custom_names = request.form.getlist('cert_custom_name[]')
        cert_job_categories = request.form.getlist('cert_job_category[]')
        cert_levels = request.form.getlist('cert_level[]')
        cert_other_job_categories = request.form.getlist('cert_other_job_category[]')
        cert_other_levels = request.form.getlist('cert_other_level[]')
        cert_acquisition_dates = request.form.getlist('cert_acquisition_date[]')

        # 使用最長列表的長度來確保所有數據都被處理
        max_len = max(len(cert_names_text), len(cert_types), len(cert_codes_text), 
                     len(cert_issuers_text), len(cert_authority_ids), len(cert_authority_names),
                     len(cert_custom_names), len(cert_job_categories), len(cert_levels),
                     len(cert_other_job_categories), len(cert_other_levels),
                     len(cert_acquisition_dates))
        
        for i in range(max_len):
            n = cert_names_text[i] if i < len(cert_names_text) else ''
            t = cert_types[i] if i < len(cert_types) else 'other'
            code = cert_codes_text[i] if i < len(cert_codes_text) else ''
            issuer = cert_issuers_text[i] if i < len(cert_issuers_text) else ''
            authority_id = cert_authority_ids[i] if i < len(cert_authority_ids) else ''
            authority_name = cert_authority_names[i] if i < len(cert_authority_names) else ''
            custom_cert_name = cert_custom_names[i] if i < len(cert_custom_names) else ''
            job_category = cert_job_categories[i] if i < len(cert_job_categories) else ''
            level = cert_levels[i] if i < len(cert_levels) else ''
            other_job_category = cert_other_job_categories[i] if i < len(cert_other_job_categories) else ''
            other_level = cert_other_levels[i] if i < len(cert_other_levels) else ''
            acquisition_date = cert_acquisition_dates[i] if i < len(cert_acquisition_dates) else ''
            
            # 如果 cert_code 是 'OTHER'，使用 other_job_category 和 other_level
            if code.strip().upper() == 'OTHER':
                job_category = other_job_category
                level = other_level
            
            # 檢查是否為空記錄：必須至少有職類+級別，或者有證照名稱/代碼/自填名稱
            has_job_category_and_level = job_category.strip() and level.strip()
            has_cert_name = n.strip() or custom_cert_name.strip()
            has_cert_code = code.strip()
            
            # 如果沒有任何有效數據，跳過（不保存空記錄）
            if not has_job_category_and_level and not has_cert_name and not has_cert_code:
                print(f"⚠️ 跳過空的證照記錄 (索引 {i}): job_category='{job_category}', level='{level}', name='{n}', code='{code}', custom_name='{custom_cert_name}'")
                continue
            
            # 如果只有職類或只有級別（不完整），也跳過
            if (job_category.strip() and not level.strip()) or (not job_category.strip() and level.strip()):
                print(f"⚠️ 跳過不完整的證照記錄 (索引 {i}): job_category='{job_category}', level='{level}' (必須同時有職類和級別)")
                continue
            
            # 確定最終使用的證照名稱（職類+級別優先）
            if has_job_category_and_level:
                # 如果有職類和級別，組合為完整名稱
                final_cert_name = f"{job_category.strip()}{level.strip()}"
            elif custom_cert_name.strip():
                final_cert_name = custom_cert_name.strip()
            else:
                final_cert_name = n.strip()
            
            # 確定最終使用的證照代碼
            final_cert_code = code.strip().upper() if code.strip() else ('OTHER' if custom_cert_name.strip() else '')
            
            structured_certifications.append({
                "name": final_cert_name,
                "type": t.strip() if t else "other",
                "code": final_cert_code,
                "authority_id": authority_id.strip() if authority_id.strip() and authority_id.strip() != 'OTHER' else None,
                "authority_name": authority_name.strip() if authority_id.strip() == 'OTHER' else '',
                "custom_cert_name": custom_cert_name.strip() if custom_cert_name.strip() else '',
                "job_category": job_category.strip() if job_category.strip() else '',
                "level": level.strip() if level.strip() else '',
                "acquisition_date": acquisition_date.strip() if acquisition_date.strip() else None,
                "issuer": issuer.strip() if issuer else ""
            })

        # 解析語言能力資料
        structured_languages = []
        # 前端使用 lang_en_level, lang_tw_level, lang_jp_level, lang_hk_level
        lang_mapping = {
            'lang_en_level': '英語',
            'lang_tw_level': '台語',
            'lang_jp_level': '日語',
            'lang_hk_level': '客語'
        }
        
        for form_field, lang_name in lang_mapping.items():
            level = request.form.get(form_field, '').strip()
            if level:  # 如果有選擇等級
                structured_languages.append({
                    "language": lang_name,
                    "level": level
                })

        # 收集證照代碼和發證人（從前端表單）
        cert_codes = request.form.getlist('cert_code[]')
        cert_issuers = request.form.getlist('cert_issuer[]')  # 新增：發證人列表
        
        # 建立結構化資料（傳入 save_structured_data）
        semester_id = get_current_semester_id(cursor)
        structured_data = {
            "name": data.get("name"),
            "birth_date": data.get("birth_date"),
            "gender": data.get("gender"),
            "phone": data.get("phone"),
            "email": data.get("email"),
            "address": data.get("address"),
            "conduct_score": score_to_grade(data.get("conduct_score")),
            "autobiography": data.get("autobiography"),
            "courses": courses,
            "photo_path": photo_path,
            "structured_certifications": structured_certifications,
            "structured_languages": structured_languages,
            "cert_photo_paths": cert_photo_paths,
            "cert_names": cert_names,
            "cert_codes": cert_codes,  # 新增：證照代碼列表
            "cert_issuers": cert_issuers  # 新增：發證人列表
        }

        # 將表單數據和結構化數據也加入 context (以便套版)
        context.update(data)
        context.update(structured_data)

        # 儲存結構化資料（包含 language / Certs / course_grades）
        if not save_structured_data(cursor, student_id, structured_data, semester_id=semester_id):
            conn.rollback()
            return jsonify({"success": False, "message": "資料儲存失敗"}), 500

        # 將成績單圖片路徑更新到 course_grades 表的 ProofImage 欄位
        if transcript_path:
            try:
                # 檢查表是否有 SemesterID 和 ProofImage 列
                cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'SemesterID'")
                has_semester_id = cursor.fetchone() is not None
                cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'ProofImage'")
                has_proof_image = cursor.fetchone() is not None
                
                if has_proof_image:
                    if has_semester_id and semester_id:
                        # 嘗試 update 同學該學期的 course_grades（若沒有，插入一筆佔位紀錄）
                        cursor.execute("""
                            UPDATE course_grades
                            SET ProofImage = %s
                            WHERE StuID = %s AND SemesterID = %s
                        """, (transcript_path, student_id, semester_id))
                        if cursor.rowcount == 0:
                            # 沒有更新到任何列，插入一筆僅含 ProofImage 的占位
                            cursor.execute("""
                                INSERT INTO course_grades (StuID, CourseName, Credits, Grade, SemesterID, ProofImage)
                                VALUES (%s, %s, %s, %s, %s, %s)
                            """, (student_id, '', 0, '', semester_id, transcript_path))
                    else:
                        # 沒有 SemesterID 列，只根據 StuID 更新
                        cursor.execute("""
                            UPDATE course_grades
                            SET ProofImage = %s
                            WHERE StuID = %s
                            LIMIT 1
                        """, (transcript_path, student_id))
                else:
                    # 如果沒有 ProofImage 列，嘗試使用 transcript_path（兼容舊結構）
                    cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'transcript_path'")
                    has_transcript_path = cursor.fetchone() is not None
                    if has_transcript_path:
                        if has_semester_id and semester_id:
                            cursor.execute("""
                                UPDATE course_grades
                                SET transcript_path = %s
                                WHERE StuID = %s AND SemesterID = %s
                            """, (transcript_path, student_id, semester_id))
                        else:
                            cursor.execute("""
                                UPDATE course_grades
                                SET transcript_path = %s
                                WHERE StuID = %s
                                LIMIT 1
                            """, (transcript_path, student_id))
            except Exception as e:
                print("⚠️ 更新 course_grades.ProofImage 失敗:", e)
                traceback.print_exc()

        # 生成 Word 文件
        student_data_for_doc = get_student_info_for_doc(cursor, student_id, semester_id=semester_id)
        # PhotoPath & ConductScoreNumeric
        student_data_for_doc["info"]["PhotoPath"] = photo_path
        student_data_for_doc["info"]["ConductScoreNumeric"] = data.get("conduct_score_numeric")
        # 傳遞證照圖片與名稱清單（generate 會自行從 certs 讀）
        student_data_for_doc["cert_photo_paths"] = cert_photo_paths
        student_data_for_doc["cert_names"] = cert_names
        # 合併 context（包含缺勤統計數據）
        student_data_for_doc.update(context)
        
        # 調試輸出：確認 student_data_for_doc 中的缺勤統計數據
        absence_keys_in_doc = {k: v for k, v in student_data_for_doc.items() if k.startswith("absence_")}
        print("📊 student_data_for_doc 中的缺勤統計數據:", absence_keys_in_doc)

        filename = f"{student_id}_履歷_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        save_path = os.path.join(UPLOAD_FOLDER, filename)

        if not generate_application_form_docx(student_data_for_doc, save_path):
            conn.rollback()
            return jsonify({"success": False, "message": "文件生成失敗"}), 500

        # 寫入 resumes
        cursor.execute("""
            INSERT INTO resumes
            (user_id, filepath, original_filename, status, semester_id, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
        """, (
            user_id,
            save_path,
            filename,
            'uploaded',  # 使用資料庫 enum 定義的狀態值
            semester_id
        ))

        conn.commit()
        return jsonify({
            "success": True,
            "message": "履歷已成功提交並生成文件",
            "file_path": save_path,
            "filename": filename
        })

    except Exception as e:
        print("❌ submit_and_generate_api 發生錯誤:", e)
        traceback.print_exc()
        if conn:
            conn.rollback()
        return jsonify({"success": False, "message": f"系統錯誤: {str(e)}"}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# -------------------------
# 下載履歷
# -------------------------
@resume_bp.route('/api/download_resume/<int:resume_id>', methods=['GET'])
def download_resume(resume_id):
    if not require_login():
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 取得履歷資料
        cursor.execute("""
            SELECT filepath, original_filename, user_id 
            FROM resumes 
            WHERE id = %s
        """, (resume_id,))
        resume = cursor.fetchone()

        if not resume:
            return jsonify({"success": False, "message": "找不到履歷"}), 404

        # 權限檢查
        session_user_id = session['user_id']
        session_role = session['role']

        if not can_access_target_resume(cursor, session_user_id, session_role, resume['user_id']):
            return jsonify({"success": False, "message": "無權限"}), 403

        # 統一路徑格式
        file_path = os.path.normpath(resume['filepath'])

        if not os.path.exists(file_path):
            print(f"[DEBUG] File not found: {file_path}")  # 方便除錯
            return jsonify({"success": False, "message": "檔案不存在"}), 404

        # 安全下載
        return send_file(file_path, as_attachment=True, download_name=resume['original_filename'])

    finally:
        cursor.close()
        conn.close()

# -------------------------
# 下載成績單
# -------------------------
@resume_bp.route("/api/download_transcript/<int:resume_id>")
def download_transcript(resume_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        # 先從 resumes 表取得 user_id
        cursor.execute("SELECT user_id FROM resumes WHERE id=%s", (resume_id,))
        resume_result = cursor.fetchone()
        
        if not resume_result:
            return jsonify({"success": False, "message": "找不到履歷"}), 404
        
        user_id = resume_result['user_id']
        
        # 權限檢查
        if not can_access_target_resume(cursor, session.get('user_id'), session.get('role'), user_id):
            return jsonify({"success": False, "message": "無權限"}), 403
        
        # 從 users 表取得學號（StuID）
        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        user_result = cursor.fetchone()
        if not user_result:
            return jsonify({"success": False, "message": "找不到學生"}), 404
        
        student_id = user_result['username']
        
        # 從 course_grades 表讀取 ProofImage（優先）或 transcript_path（兼容）
        cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'ProofImage'")
        has_proof_image = cursor.fetchone() is not None
        cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'transcript_path'")
        has_transcript_path = cursor.fetchone() is not None
        
        transcript_path = None
        if has_proof_image:
            cursor.execute("""
                SELECT ProofImage 
                FROM course_grades 
                WHERE StuID=%s AND ProofImage IS NOT NULL AND ProofImage != ''
                ORDER BY id DESC
                LIMIT 1
            """, (student_id,))
            result = cursor.fetchone()
            if result and result.get('ProofImage'):
                transcript_path = result['ProofImage']
        
        if not transcript_path and has_transcript_path:
            cursor.execute("""
                SELECT transcript_path 
                FROM course_grades 
                WHERE StuID=%s AND transcript_path IS NOT NULL AND transcript_path != ''
                ORDER BY id DESC
                LIMIT 1
            """, (student_id,))
            result = cursor.fetchone()
            if result and result.get('transcript_path'):
                transcript_path = result['transcript_path']
        
        if not transcript_path:
            return jsonify({"success": False, "message": "找不到成績單"}), 404
        
        if not os.path.exists(transcript_path):
            return jsonify({"success": False, "message": "檔案不存在"}), 404

        # 嘗試推斷檔名，如果找不到則使用預設名
        download_name = os.path.basename(transcript_path)
        if not download_name or not os.path.splitext(download_name)[1]:
            download_name = f"transcript_{resume_id}.jpg" # 預設檔名
            
        return send_file(transcript_path, as_attachment=True, download_name=download_name)
    finally:
        cursor.close()
        db.close()

# -------------------------
#  缺勤統計查詢（按學期）
# -------------------------
@resume_bp.route('/api/get_absence_stats', methods=['GET'])
def get_absence_stats():
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    user_id = session['user_id']
    semester_id = request.args.get('semester_id', None)  # 可選：指定單一學期ID（向後兼容）
    start_semester_id = request.args.get('start_semester_id', None)  # 可選：開始學期ID
    end_semester_id = request.args.get('end_semester_id', None)  # 可選：結束學期ID
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 檢查 absence_records 表是否有 semester_id 欄位
        cursor.execute("SHOW COLUMNS FROM absence_records LIKE 'semester_id'")
        has_semester_id = cursor.fetchone() is not None
        
        # 查詢並計算各類別缺勤總節數（按學期分組）
        if has_semester_id:
            # 優先使用學期範圍篩選
            if start_semester_id and end_semester_id:
                # 學期範圍篩選：獲取所有在範圍內的學期ID
                cursor.execute("""
                    SELECT id FROM semesters 
                    WHERE code >= (SELECT code FROM semesters WHERE id = %s)
                    AND code <= (SELECT code FROM semesters WHERE id = %s)
                    ORDER BY code
                """, (start_semester_id, end_semester_id))
                semester_ids_in_range = [row['id'] for row in cursor.fetchall()]
                if semester_ids_in_range:
                    placeholders = ','.join(['%s'] * len(semester_ids_in_range))
                    cursor.execute(f"""
                        SELECT 
                            ar.absence_type, 
                            SUM(ar.duration_units) AS total_units
                        FROM absence_records ar
                        LEFT JOIN semesters s ON ar.semester_id = s.id
                        WHERE ar.user_id = %s AND ar.semester_id IN ({placeholders})
                        GROUP BY ar.absence_type
                    """, (user_id, *semester_ids_in_range))
                else:
                    cursor.execute("""
                        SELECT 
                            ar.absence_type, 
                            SUM(ar.duration_units) AS total_units
                        FROM absence_records ar
                        WHERE ar.user_id = %s AND 1=0
                        GROUP BY ar.absence_type
                    """, (user_id,))
            elif semester_id:
                # 單一學期查詢（向後兼容）
                cursor.execute("""
                    SELECT 
                        ar.absence_type, 
                        SUM(ar.duration_units) AS total_units,
                        s.code AS semester_code,
                        s.id AS semester_id
                    FROM absence_records ar
                    LEFT JOIN semesters s ON ar.semester_id = s.id
                    WHERE ar.user_id = %s AND ar.semester_id = %s
                    GROUP BY ar.absence_type, s.code, s.id
                """, (user_id, semester_id))
        elif has_semester_id:
            # 如果有 semester_id 欄位但未指定學期，查詢當前學期
            current_semester_id = get_current_semester_id(cursor)
            if current_semester_id:
                cursor.execute("""
                    SELECT 
                        ar.absence_type, 
                        SUM(ar.duration_units) AS total_units,
                        s.code AS semester_code,
                        s.id AS semester_id
                    FROM absence_records ar
                    LEFT JOIN semesters s ON ar.semester_id = s.id
                    WHERE ar.user_id = %s AND ar.semester_id = %s
                    GROUP BY ar.absence_type, s.code, s.id
                """, (user_id, current_semester_id))
            else:
                # 沒有當前學期，查詢所有學期
                cursor.execute("""
                    SELECT 
                        ar.absence_type, 
                        SUM(ar.duration_units) AS total_units,
                        s.code AS semester_code,
                        s.id AS semester_id
                    FROM absence_records ar
                    LEFT JOIN semesters s ON ar.semester_id = s.id
                    WHERE ar.user_id = %s
                    GROUP BY ar.absence_type, s.code, s.id
                """, (user_id,))
        else:
            # 沒有 semester_id 欄位，查詢所有缺勤記錄
            cursor.execute("""
                SELECT 
                    absence_type, 
                    SUM(duration_units) AS total_units 
                FROM absence_records
                WHERE user_id = %s
                GROUP BY absence_type
            """, (user_id,))
        
        results = cursor.fetchall()
        
        # 將結果轉換為前端需要的字典格式 (例如: {"曠課": 5, "事假": 10, ...})
        stats = {}
        for row in results:
            # 確保 total_units 轉換為整數
            stats[row['absence_type']] = int(row['total_units'])

        return jsonify({"success": True, "stats": stats})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
#  獲取學生學期出勤記錄（詳細列表）
# -------------------------
@resume_bp.route('/api/get_semester_absence_records', methods=['GET'])
def get_semester_absence_records():
    """獲取學生的學期出勤記錄，用於自動填充表單"""
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    user_id = session['user_id']
    semester_id = request.args.get('semester_id', None)  # 可選：指定單一學期ID（向後兼容）
    start_semester_id = request.args.get('start_semester_id', None)  # 可選：開始學期ID
    end_semester_id = request.args.get('end_semester_id', None)  # 可選：結束學期ID
    start_date = request.args.get('start_date', None)  # 可選：開始日期（向後兼容）
    end_date = request.args.get('end_date', None)  # 可選：結束日期（向後兼容）
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 檢查 absence_records 表是否有 semester_id 欄位
        cursor.execute("SHOW COLUMNS FROM absence_records LIKE 'semester_id'")
        has_semester_id = cursor.fetchone() is not None
        
        # 構建 WHERE 條件和參數
        where_conditions = ["ar.user_id = %s"]
        query_params = [user_id]
        
        # 優先使用學期範圍篩選
        if has_semester_id:
            if start_semester_id and end_semester_id:
                # 學期範圍篩選：需要獲取學期代碼來比較
                cursor.execute("SELECT code FROM semesters WHERE id IN (%s, %s)", (start_semester_id, end_semester_id))
                semester_codes = {row['code']: None for row in cursor.fetchall()}
                if len(semester_codes) == 2:
                    # 獲取所有在範圍內的學期ID
                    cursor.execute("""
                        SELECT id FROM semesters 
                        WHERE code >= (SELECT code FROM semesters WHERE id = %s)
                        AND code <= (SELECT code FROM semesters WHERE id = %s)
                        ORDER BY code
                    """, (start_semester_id, end_semester_id))
                    semester_ids_in_range = [row['id'] for row in cursor.fetchall()]
                    if semester_ids_in_range:
                        placeholders = ','.join(['%s'] * len(semester_ids_in_range))
                        where_conditions.append(f"ar.semester_id IN ({placeholders})")
                        query_params.extend(semester_ids_in_range)
            elif semester_id:
                # 單一學期篩選（向後兼容）
                where_conditions.append("ar.semester_id = %s")
                query_params.append(semester_id)
        
        # 添加日期範圍篩選（向後兼容，但優先使用學期範圍）
        if not (start_semester_id and end_semester_id):
            if start_date:
                where_conditions.append("ar.absence_date >= %s")
                query_params.append(start_date)
            if end_date:
                where_conditions.append("ar.absence_date <= %s")
                query_params.append(end_date)
        
        where_clause = " AND ".join(where_conditions)
        
        # 查詢缺勤記錄
        if has_semester_id:
            # 如果有 semester_id 欄位，使用 JOIN 查詢
            query = f"""
                SELECT 
                    ar.id,
                    ar.absence_date,
                    ar.absence_type,
                    ar.duration_units,
                    ar.reason,
                    ar.image_path,
                    ar.created_at,
                    s.code AS semester_code,
                    s.id AS semester_id,
                    u.username AS student_id,
                    u.name AS student_name
                FROM absence_records ar
                LEFT JOIN semesters s ON ar.semester_id = s.id
                LEFT JOIN users u ON ar.user_id = u.id
                WHERE {where_clause}
                ORDER BY ar.absence_date DESC, ar.created_at DESC
            """
            cursor.execute(query, tuple(query_params))
        else:
            # 沒有 semester_id 欄位，不使用 JOIN
            query = f"""
                SELECT 
                    ar.id,
                    ar.absence_date,
                    ar.absence_type,
                    ar.duration_units,
                    ar.reason,
                    ar.image_path,
                    ar.created_at,
                    NULL AS semester_code,
                    NULL AS semester_id,
                    u.username AS student_id,
                    u.name AS student_name
                FROM absence_records ar
                LEFT JOIN users u ON ar.user_id = u.id
                WHERE {where_clause}
                ORDER BY ar.absence_date DESC, ar.created_at DESC
            """
            cursor.execute(query, tuple(query_params))
        
        records = cursor.fetchall()
        
        # 格式化日期
        for record in records:
            if record.get('absence_date'):
                absence_date = record['absence_date']
                if isinstance(absence_date, datetime):
                    record['absence_date'] = absence_date.strftime("%Y-%m-%d")
                elif isinstance(absence_date, str):
                    # 如果是字符串，嘗試解析並格式化
                    try:
                        # 先嘗試提取 YYYY-MM-DD 格式
                        date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', absence_date)
                        if date_match:
                            # 如果找到 YYYY-MM-DD 格式，直接使用
                            record['absence_date'] = date_match.group(0)
                        else:
                            # 嘗試解析各種日期格式
                            if 'T' in absence_date:
                                # ISO 格式: 2024-03-27T00:00:00
                                date_str = absence_date.split('T')[0]
                                record['absence_date'] = date_str
                            elif 'GMT' in absence_date or 'UTC' in absence_date:
                                # GMT 格式: Sat, 29 Nov 2025 00:00:00 GMT
                                # 使用正則表達式提取日期部分
                                date_match = re.search(r'(\w{3}),\s+(\d{1,2})\s+(\w{3})\s+(\d{4})', absence_date)
                                if date_match:
                                    # 轉換月份名稱
                                    month_map = {
                                        'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
                                        'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
                                        'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'
                                    }
                                    day = date_match.group(2).zfill(2)
                                    month = month_map.get(date_match.group(3), '01')
                                    year = date_match.group(4)
                                    record['absence_date'] = f"{year}-{month}-{day}"
                                else:
                                    # 嘗試使用 datetime 解析
                                    try:
                                        date_obj = datetime.strptime(absence_date.split(',')[1].strip().split()[0], "%d %b %Y")
                                        record['absence_date'] = date_obj.strftime("%Y-%m-%d")
                                    except:
                                        print(f"⚠️ 無法解析日期格式: {absence_date}")
                            else:
                                # 嘗試標準格式
                                date_obj = datetime.strptime(absence_date.split()[0], "%Y-%m-%d")
                                record['absence_date'] = date_obj.strftime("%Y-%m-%d")
                    except (ValueError, AttributeError, IndexError) as e:
                        print(f"⚠️ 無法解析日期格式: {absence_date}, 錯誤: {e}")
            if record.get('created_at'):
                if isinstance(record['created_at'], datetime):
                    record['created_at'] = record['created_at'].strftime("%Y-%m-%d %H:%M:%S")
                elif isinstance(record['created_at'], str):
                    try:
                        if 'T' in record['created_at']:
                            date_obj = datetime.fromisoformat(record['created_at'].replace('Z', '+00:00'))
                            record['created_at'] = date_obj.strftime("%Y-%m-%d %H:%M:%S")
                    except (ValueError, AttributeError):
                        pass
        
        # 計算統計數據
        stats = {}
        for record in records:
            absence_type = record.get('absence_type')
            duration_units = record.get('duration_units', 0)
            if absence_type:
                stats[absence_type] = stats.get(absence_type, 0) + int(duration_units)
        
        return jsonify({
            "success": True, 
            "records": records,
            "stats": stats,
            "semester_id": semester_id
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
#  缺勤紀錄提交
# -------------------------
@resume_bp.route('/api/submit_absence_record', methods=['POST'])
def submit_absence_record():
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    user_id = session['user_id']
    
    # 取得前端傳來的所有欄位
    absence_date = request.form.get('absence_date')
    absence_type = request.form.get('absence_type')
    duration_units = request.form.get('duration_units')
    reason = request.form.get('reason')

    if not all([absence_date, absence_type, duration_units, reason]):
        return jsonify({"success": False, "message": "日期、類型、節數、事由皆為必填欄位"}), 400

    try:
        duration_units = int(duration_units)
        if duration_units <= 0:
            return jsonify({"success": False, "message": "節數必須為正整數"}), 400
    except ValueError:
        return jsonify({"success": False, "message": "節數格式錯誤"}), 400

    image_path = None
    # 處理佐證圖片上傳
    print(f"🔍 檢查上傳的文件: request.files.keys() = {list(request.files.keys())}")
    
    if 'proof_image' in request.files:
        proof_image = request.files['proof_image']
        print(f"🔍 proof_image 對象: {proof_image}")
        print(f"🔍 proof_image.filename: {proof_image.filename if proof_image else 'None'}")
        print(f"🔍 proof_image.content_type: {proof_image.content_type if proof_image else 'None'}")
        
        # 檢查文件是否存在且有效（不僅檢查 filename，也檢查文件大小）
        if proof_image and proof_image.filename and len(proof_image.filename.strip()) > 0:
            try:
                # 確保目錄存在
                os.makedirs(ABSENCE_PROOF_FOLDER, exist_ok=True)
                # 確保檔名安全，並加上 user_id 和時間戳以避免重複
                original_filename = proof_image.filename
                filename = secure_filename(f"{user_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{original_filename}")
                save_path = os.path.join(ABSENCE_PROOF_FOLDER, filename)
                proof_image.save(save_path)
                image_path = save_path # 儲存到資料庫的路徑
                print(f"✅ 缺勤佐證圖片已保存: {image_path}")
                print(f"✅ 文件大小: {os.path.getsize(save_path) if os.path.exists(save_path) else 'N/A'} bytes")
            except Exception as e:
                print(f"⚠️ 儲存缺勤佐證圖片失敗: {e}")
                traceback.print_exc()
                # 即使圖片保存失敗，也繼續處理其他資料（image_path 保持為 None）
        else:
            print(f"⚠️ proof_image 無效: proof_image={proof_image}, filename={proof_image.filename if proof_image else 'None'}")
    else:
        print(f"⚠️ request.files 中沒有 'proof_image' 鍵")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 插入缺勤紀錄到 absence_records 表格
        print(f"📝 準備插入缺勤紀錄: user_id={user_id}, date={absence_date}, type={absence_type}, image_path={image_path}")
        cursor.execute("""
            INSERT INTO absence_records 
            (user_id, absence_date, absence_type, duration_units, reason, image_path)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, absence_date, absence_type, duration_units, reason, image_path))
        
        conn.commit()
        print(f"✅ 缺勤紀錄已成功插入資料庫，image_path={image_path}")

        return jsonify({"success": True, "message": "缺勤紀錄提交成功！"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"資料庫操作失敗: {str(e)}"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# 查詢學生履歷列表
# -------------------------
@resume_bp.route('/api/get_my_resumes', methods=['GET'])
def get_my_resumes():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT r.id, r.original_filename, r.status, r.comment, r.note, r.created_at AS upload_time
            FROM resumes r
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
        """, (session['user_id'],))
        resumes = cursor.fetchall()
        for r in resumes:
            if isinstance(r.get('upload_time'), datetime):
                r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "resumes": resumes})
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API：取得已提交履歷的完整資料（用於頁面刷新後恢復表單）
# -------------------------
@resume_bp.route('/api/get_resume_data', methods=['GET'])
def get_resume_data():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 檢查是否有已提交的履歷
        cursor.execute("""
            SELECT id FROM resumes 
            WHERE user_id = %s 
            ORDER BY created_at DESC 
            LIMIT 1
        """, (user_id,))
        resume = cursor.fetchone()
        
        if not resume:
            return jsonify({"success": False, "message": "沒有已提交的履歷"}), 404

        # 獲取學生的 username（學號），因為 Student_Info 表的 StuID 存儲的是 username
        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        user_result = cursor.fetchone()
        if not user_result:
            return jsonify({"success": False, "message": "找不到使用者"}), 404
        student_id = user_result['username']

        # 1. 獲取基本資料 (Student_Info)
        cursor.execute("SELECT * FROM Student_Info WHERE StuID=%s", (student_id,))
        student_info = cursor.fetchone() or {}
        
        # 2. 獲取課程資料 (course_grades)
        cursor.execute("""
            SELECT CourseName AS name, Credits AS credits, Grade AS grade
            FROM course_grades
            WHERE StuID=%s
            ORDER BY CourseName
        """, (student_id,))
        courses = cursor.fetchall() or []
        
        # 3. 取得證照資料 (student_certifications) - 只取最新的記錄
        # 邏輯：
        # 1. 如果學生有選擇發證中心（cert_code 存在且不是 'OTHER'），則從 certificate_codes 表獲取 job_category 和 level
        # 2. 如果學生沒有選擇發證中心（cert_code 為 'OTHER' 或 NULL），則使用 student_certifications 表中的 job_category、level 或 custom_cert_name
        
        # 先檢查 student_certifications 表是否有 job_category 和 level 列
        cursor.execute("SHOW COLUMNS FROM student_certifications LIKE 'job_category'")
        has_job_category_col = cursor.fetchone() is not None
        cursor.execute("SHOW COLUMNS FROM student_certifications LIKE 'level'")
        has_level_col = cursor.fetchone() is not None
        
        # 根據列是否存在動態構建 SQL
        if has_job_category_col and has_level_col:
            # 如果列存在，使用完整的查詢
            # 邏輯：
            # - 如果 cert_code 不是 'OTHER'，使用 certificate_codes 表的 job_category 和 level（連動）
            # - 如果 cert_code 是 'OTHER' 或 NULL，使用 student_certifications 表的 job_category 和 level（用戶自填）
            sql = """
                SELECT
                    sc.CertPath,
                    sc.AcquisitionDate,
                    sc.cert_code, 
                    sc.issuer,
                    sc.authority_name,
                    sc.custom_cert_name,
                    -- 如果 cert_code 不是 'OTHER'，使用 certificate_codes 的值（連動）
                    -- 如果 cert_code 是 'OTHER' 或 NULL，使用 student_certifications 的值（用戶自填）
                    CASE 
                        WHEN sc.cert_code IS NOT NULL AND BINARY sc.cert_code != BINARY 'OTHER' AND sc.cert_code != ''
                        THEN COALESCE(cc.job_category, '')
                        ELSE COALESCE(sc.job_category, '')
                    END AS job_category,
                    CASE 
                        WHEN sc.cert_code IS NOT NULL AND BINARY sc.cert_code != BINARY 'OTHER' AND sc.cert_code != ''
                        THEN COALESCE(cc.level, '')
                        ELSE COALESCE(sc.level, '')
                    END AS level,
                    sc.CreatedAt,
                    sc.id,
                    -- 組合證照名稱：優先使用 job_category + level，否則使用 custom_cert_name
                    CASE 
                        WHEN (CASE 
                                WHEN sc.cert_code IS NOT NULL AND BINARY sc.cert_code != BINARY 'OTHER' AND sc.cert_code != ''
                                THEN COALESCE(cc.job_category, '')
                                ELSE COALESCE(sc.job_category, '')
                              END) != '' 
                              AND (CASE 
                                WHEN sc.cert_code IS NOT NULL AND BINARY sc.cert_code != BINARY 'OTHER' AND sc.cert_code != ''
                                THEN COALESCE(cc.level, '')
                                ELSE COALESCE(sc.level, '')
                              END) != ''
                        THEN CONCAT(
                            CASE 
                                WHEN sc.cert_code IS NOT NULL AND BINARY sc.cert_code != BINARY 'OTHER' AND sc.cert_code != ''
                                THEN COALESCE(cc.job_category, '')
                                ELSE COALESCE(sc.job_category, '')
                            END,
                            CASE 
                                WHEN sc.cert_code IS NOT NULL AND BINARY sc.cert_code != BINARY 'OTHER' AND sc.cert_code != ''
                                THEN COALESCE(cc.level, '')
                                ELSE COALESCE(sc.level, '')
                            END
                        )
                        WHEN sc.custom_cert_name IS NOT NULL AND sc.custom_cert_name != ''
                        THEN sc.custom_cert_name
                        ELSE ''
                    END AS CertName,
                    -- 獲取 authority_id（從 certificate_codes 或 cert_authorities）
                    COALESCE(ca.id, NULL) AS authority_id,
                    -- IssuingBody 使用 authority_name（如果有的話）
                    COALESCE(ca.name, sc.authority_name, 'N/A') AS IssuingBody,
                    -- CertType 從 certificate_codes 獲取
                    COALESCE(cc.category, 'other') AS CertType
                 FROM student_certifications sc
                 LEFT JOIN certificate_codes cc ON sc.cert_code = cc.code AND sc.cert_code IS NOT NULL AND BINARY sc.cert_code != BINARY 'OTHER'
                 LEFT JOIN cert_authorities ca ON cc.authority_id = ca.id
                 WHERE sc.StuID = %s
                 ORDER BY sc.id DESC
            """
        else:
            # 如果列不存在，只從 certificate_codes 表獲取 job_category 和 level
            sql = """
                SELECT
                    sc.CertPath,
                    sc.AcquisitionDate,
                    sc.cert_code, 
                    sc.issuer,
                    sc.authority_name,
                    sc.custom_cert_name,
                    -- 只從 certificate_codes 表獲取 job_category 和 level（如果列不存在）
                    COALESCE(cc.job_category, '') AS job_category,
                    COALESCE(cc.level, '') AS level,
                    sc.CreatedAt,
                    sc.id,
                    -- 組合證照名稱：優先使用 job_category + level，否則使用 custom_cert_name
                    CASE 
                        WHEN (COALESCE(cc.job_category, '') IS NOT NULL 
                              AND COALESCE(cc.job_category, '') != '' 
                              AND COALESCE(cc.level, '') IS NOT NULL 
                              AND COALESCE(cc.level, '') != '') 
                        THEN CONCAT(COALESCE(cc.job_category, ''), COALESCE(cc.level, ''))
                        WHEN sc.custom_cert_name IS NOT NULL AND sc.custom_cert_name != ''
                        THEN sc.custom_cert_name
                        ELSE ''
                    END AS CertName,
                    -- 獲取 authority_id（從 certificate_codes 或 cert_authorities）
                    COALESCE(ca.id, NULL) AS authority_id,
                    -- IssuingBody 使用 authority_name（如果有的話）
                    COALESCE(ca.name, sc.authority_name, 'N/A') AS IssuingBody,
                    -- CertType 從 certificate_codes 獲取
                    COALESCE(cc.category, 'other') AS CertType
                 FROM student_certifications sc
                 LEFT JOIN certificate_codes cc ON sc.cert_code = cc.code AND sc.cert_code IS NOT NULL AND BINARY sc.cert_code != BINARY 'OTHER'
                 LEFT JOIN cert_authorities ca ON cc.authority_id = ca.id
                 WHERE sc.StuID = %s
                 ORDER BY sc.id DESC
            """
        
        cursor.execute(sql, (student_id,)) 
        all_certifications = cursor.fetchall() or []
        
        # 只取最新一批的證照
        # 方法：按 CreatedAt 分組，如果 CreatedAt 相同（或為 NULL），則按 id 分組
        # 取 id 最大的那一批（假設同一批次插入的記錄 id 是連續的）
        certifications = []
        if all_certifications:
            # 獲取最新的 CreatedAt 時間和 id
            latest_created_at = all_certifications[0].get('CreatedAt')
            latest_id = all_certifications[0].get('id')
            
            if latest_created_at:
                # 如果有 CreatedAt，只保留與最新時間相同的記錄
                certifications = [cert for cert in all_certifications 
                                if cert.get('CreatedAt') == latest_created_at]
            else:
                # 如果 CreatedAt 為 NULL，則按 id 分組
                # 找出與最大 id 相近的記錄（假設同一批次的 id 是連續的）
                # 取 id 最大的記錄，然後找出所有 id 在合理範圍內的記錄（id 差距在 50 以內）
                max_id = latest_id
                certifications = [cert for cert in all_certifications 
                                if cert.get('id') and cert.get('id') >= (max_id - 50)]
            
            # 過濾掉空的證照記錄（必須至少有職類+級別，或者有證照名稱/代碼/自填名稱）
            # 優先檢查 job_category + level（這是新的主要方式）
            certifications = [cert for cert in certifications 
                            if ((cert.get('job_category') and cert.get('level') and 
                                 cert.get('job_category').strip() and cert.get('level').strip()) or
                                (cert.get('CertName') and cert.get('CertName').strip()) or 
                                (cert.get('cert_code') and cert.get('cert_code').strip() and cert.get('cert_code').strip() != 'OTHER') or 
                                (cert.get('custom_cert_name') and cert.get('custom_cert_name').strip()))]
            
            # 再次過濾：確保職類和級別要麼都有，要麼都沒有（不能只有一個）
            certifications = [cert for cert in certifications 
                            if not ((cert.get('job_category') and cert.get('job_category').strip() and 
                                    not (cert.get('level') and cert.get('level').strip())) or
                                   (cert.get('level') and cert.get('level').strip() and 
                                    not (cert.get('job_category') and cert.get('job_category').strip())))]
            
            # 如果需要 authority_id，可以通過 cert_code 查詢（但用戶說不需要，所以這裡先跳過）
            # 如果需要，可以在這裡添加查詢邏輯
            
            print(f"🔍 總證照記錄數: {len(all_certifications)}, 最新批次記錄數: {len(certifications)}, 過濾後記錄數: {len(certifications)}, 最新ID: {latest_id}, 最新CreatedAt: {latest_created_at}")
        
        # 4. 獲取語言能力 (student_languageskills)
        cursor.execute("""
            SELECT Language AS language, Level AS level
            FROM student_languageskills
            WHERE StuID=%s
            ORDER BY Language
        """, (student_id,))
        languages = cursor.fetchall() or []
        
        # 格式化日期
        birth_date = student_info.get('BirthDate')
        if birth_date:
            if isinstance(birth_date, datetime):
                birth_date = birth_date.strftime("%Y-%m-%d")
            elif isinstance(birth_date, str):
                try:
                    # 嘗試解析並格式化
                    dt = datetime.strptime(birth_date, "%Y-%m-%d")
                    birth_date = dt.strftime("%Y-%m-%d")
                except:
                    pass
        
        # 格式化證照日期並映射字段名（前端期望的字段名）
        formatted_certs = []
        for cert in certifications:
            cert_copy = {
                'id': cert.get('id'),
                'cert_code': cert.get('cert_code', ''),
                'cert_path': cert.get('CertPath', ''),
                'name': cert.get('CertName', ''),  # 由 job_category + level 組合，或 custom_cert_name
                'job_category': cert.get('job_category', ''),
                'level': cert.get('level', ''),
                'custom_cert_name': cert.get('custom_cert_name', ''),
                'authority_name': cert.get('authority_name', ''),
                'issuer': cert.get('issuer', ''),
                'authority_id': cert.get('authority_id'),
                'IssuingBody': cert.get('IssuingBody', ''),
                'CertType': cert.get('CertType', 'other')
            }
            
            # 格式化取得日期
            acquire_date = cert.get('AcquisitionDate')
            if acquire_date:
                if isinstance(acquire_date, datetime):
                    cert_copy['acquire_date'] = acquire_date.strftime("%Y-%m-%d")
                elif isinstance(acquire_date, str):
                    try:
                        dt = datetime.strptime(acquire_date, "%Y-%m-%d")
                        cert_copy['acquire_date'] = dt.strftime("%Y-%m-%d")
                    except:
                        # 嘗試其他日期格式
                        try:
                            from dateutil import parser
                            dt = parser.parse(acquire_date)
                            cert_copy['acquire_date'] = dt.strftime("%Y-%m-%d")
                        except:
                            cert_copy['acquire_date'] = acquire_date
                else:
                    cert_copy['acquire_date'] = str(acquire_date)
            else:
                cert_copy['acquire_date'] = ''
            
            formatted_certs.append(cert_copy)
        
        return jsonify({
            "success": True,
            "data": {
                "student_info": {
                    "name": student_info.get('StuName', ''),
                    "birth_date": birth_date or '',
                    "gender": student_info.get('Gender', ''),
                    "phone": student_info.get('Phone', ''),
                    "email": student_info.get('Email', ''),
                    "address": student_info.get('Address', ''),
                    "conduct_score": student_info.get('ConductScore', ''),
                    "autobiography": student_info.get('Autobiography', ''),
                    "photo_path": student_info.get('PhotoPath', '')
                },
                "courses": courses,
                "certifications": formatted_certs,
                "languages": languages
            }
        })
    except Exception as e:
        print("❌ 取得履歷資料錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"取得履歷資料失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API：取得標準核心科目
# -------------------------
@resume_bp.route('/api/get_standard_courses', methods=['GET'])
def get_standard_courses():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT course_name AS name, credits 
            FROM standard_courses 
            WHERE is_active = 1 
            ORDER BY order_index
        """)
        courses = cursor.fetchall()
        return jsonify({"success": True, "courses": courses})
    except Exception as e:
        print("❌ 取得標準核心科目錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": "取得標準核心科目失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 儲存學生個人模板
# -------------------------
@resume_bp.route('/api/save_personal_template', methods=['POST'])
def save_personal_template():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        data = request.get_json()
        courses_json = json.dumps(data.get('courses', []), ensure_ascii=False)
        cursor.execute("""
            INSERT INTO templates (template_type, content, display_name, is_active, uploaded_by, uploaded_at)
    VALUES (%s, %s, %s, %s, %s, NOW())
    ON DUPLICATE KEY UPDATE content=VALUES(content), display_name=VALUES(display_name), updated_at=NOW()
""", ('student_custom', courses_json, data.get('display_name', '我的模板'), 1, session['user_id']))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        print("❌ 儲存模板錯誤:", e)
        return jsonify({"success": False, "message": "儲存失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 載入學生個人模板
# -------------------------
@resume_bp.route('/api/load_personal_template', methods=['GET'])
def load_personal_template():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 1️⃣ 抓標準課程
        cursor.execute("""
            SELECT course_name AS name, credits 
            FROM standard_courses 
            WHERE is_active = 1 
            ORDER BY order_index
        """)
        standard_courses = cursor.fetchall()
        standard_count = len(standard_courses)
        
        # 【新增】建立標準課程的 (name, credits) 集合，用於內容比對
        standard_set = {(c['name'], c['credits']) for c in standard_courses}

        # 2️⃣ 嘗試抓學生個人模板
        cursor.execute("""
            SELECT content FROM templates
            WHERE uploaded_by=%s AND template_type='student_custom'
            ORDER BY uploaded_at DESC LIMIT 1
        """, (session['user_id'],))
        row = cursor.fetchone()

        if not row:
            # 沒模板 → 回傳標準課程
            return jsonify({
                "success": True,
                "courses": standard_courses,
                "needs_update": False,
                "source": "standard"
            })

        # 3️⃣ 解析模板內容
        try:
            student_courses = json.loads(row['content'])
        except Exception:
            student_courses = []
        
        student_count = len(student_courses)
        
        # 【新增】建立學生課程的 (name, credits) 集合，用於內容比對
        student_set = {(c.get('name'), c.get('credits')) for c in student_courses}

        # 4️⃣ 檢查是否有新增或內容變更
        # needs_update = student_count < standard_count
        # 【修改】若標準課程數量增加 OR 兩個課程內容集合不相等，則視為需要更新
        needs_update = (student_count < standard_count) or (student_set != standard_set)

        # 回傳資料
        return jsonify({
            "success": True,
            "courses": student_courses,
            "needs_update": needs_update,
            "source": "student" if not needs_update else "student_outdated"
        })
    except Exception as e:
        print("❌ 載入模板錯誤:", e)
        return jsonify({"success": False, "message": "載入模板失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API - 取得班導 / 主任 履歷 (支援多班級 & 全系)（讀取）
# -------------------------
@resume_bp.route("/api/get_class_resumes", methods=["GET"])
def get_class_resumes():
    # 驗證登入
    if not require_login():
        return jsonify({"success": False, "message": "未授權"}), 403

    user_id = session['user_id']
    role = session['role']
    mode = request.args.get('mode', '').strip().lower()

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        resumes = []  # 初始化結果列表
        sql_query = ""
        sql_params = tuple()

        print(f"🔍 [DEBUG] get_class_resumes called - user_id: {user_id}, role: {role}")

        # ------------------------------------------------------------------
        # 1. 班導 / 教師 (role == "teacher" or "class_teacher")
        # ------------------------------------------------------------------
        if role in ["teacher", "class_teacher"]:
            sql_query = """
                SELECT 
                    r.id,
                    u.name AS student_name,
                    u.username AS student_number,
                    c.name AS class_name,
                    c.department,
                    r.original_filename,
                    r.filepath,
                    r.status,
                    r.comment,
                    r.note,
                    r.created_at
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                JOIN classes_teacher ct ON ct.class_id = c.id
                WHERE ct.teacher_id = %s
                ORDER BY c.name, u.name
            """
            sql_params = (user_id,)

            cursor.execute(sql_query, sql_params)
            resumes = cursor.fetchall()

            if not resumes:
                print(f"⚠️ [DEBUG] Teacher/class_teacher user {user_id} has no assigned classes.")
                resumes = []

        # ------------------------------------------------------------------
        # 2. 主任 (role == "director")
        # ------------------------------------------------------------------
        elif role == "director":
            # director 根據 mode 控制可見範圍：
            # - mode=director → 同科系全部
            # - 其他/預設 → 僅自己帶的班級（班導模式）
            if mode == "director":
                # 取得主任所屬科系（使用 helper）
                department = get_director_department(cursor, user_id)

                if not department:
                    # 沒有設定科系 → 不顯示任何資料，以免越權
                    resumes = []
                    sql_query = ""
                    sql_params = tuple()
                else:
                    sql_query = """
                        SELECT 
                            r.id,
                            u.name AS student_name,
                            u.username AS student_number,
                            c.name AS class_name,
                            c.department,
                            r.original_filename,
                            r.filepath,
                            r.status,
                            r.comment,
                            r.note,
                            r.created_at
                        FROM resumes r
                        JOIN users u ON r.user_id = u.id
                        JOIN classes c ON u.class_id = c.id
                        WHERE c.department = %s
                        ORDER BY c.name, u.name
                    """
                    sql_params = (department,)
            else:
                # homeroom/預設：僅看自己帶的班級
                sql_query = """
                    SELECT 
                        r.id,
                        u.name AS student_name,
                        u.username AS student_number,
                        c.name AS class_name,
                        c.department,
                        r.original_filename,
                        r.filepath,
                        r.status,
                        r.comment,
                        r.note,
                        r.created_at
                    FROM resumes r
                    JOIN users u ON r.user_id = u.id
                    LEFT JOIN classes c ON u.class_id = c.id
                    JOIN classes_teacher ct ON ct.class_id = c.id
                    WHERE ct.teacher_id = %s
                    ORDER BY c.name, u.name
                """
                sql_params = (user_id,)

            # 執行 SQL 查詢 (主任邏輯在上面已完成查詢或準備好查詢字串)
            if sql_query:
                cursor.execute(sql_query, sql_params)
                resumes = cursor.fetchall()

        # ------------------------------------------------------------------
        # 3. TA 或 Admin (role == "ta" or "admin")
        # ------------------------------------------------------------------
        elif role in ["ta", "admin"]:
            sql_query = """
                SELECT 
                    r.id,
                    u.name AS student_name,
                    u.username AS student_number,
                    c.name AS class_name,
                    c.department,
                    r.original_filename,
                    r.filepath,
                    r.status,
                    r.comment,
                    r.note,
                    r.created_at
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                ORDER BY c.name, u.name
            """
            cursor.execute(sql_query, tuple())
            resumes = cursor.fetchall()

        # ------------------------------------------------------------------
        # 4. Vendor (role == "vendor")
        # ------------------------------------------------------------------
        elif role == "vendor":
            # Vendor 可以看到選擇了他們上傳的公司的學生履歷
            # 或者被錄取到他們公司的學生履歷
            sql_query = """
                SELECT DISTINCT
                    r.id,
                    u.name AS student_name,
                    u.username AS student_number,
                    c.name AS class_name,
                    c.department,
                    r.original_filename,
                    r.filepath,
                    r.status,
                    r.comment,
                    r.note,
                    r.created_at
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                WHERE EXISTS (
                    -- 學生選擇了該 vendor 上傳的公司
                    SELECT 1 FROM student_preferences sp
                    JOIN internship_companies ic ON sp.company_id = ic.id
                    WHERE sp.student_id = u.id
                    AND ic.uploaded_by_user_id = %s
                ) OR EXISTS (
                    -- 學生被錄取到該 vendor 的公司
                    SELECT 1 FROM internship_experiences ie
                    JOIN internship_companies ic ON ie.company_id = ic.id
                    WHERE ie.user_id = u.id
                    AND ic.uploaded_by_user_id = %s
                )
                ORDER BY c.name, u.name
            """
            cursor.execute(sql_query, (user_id, user_id))
            resumes = cursor.fetchall()

        else:
            return jsonify({"success": False, "message": "無效的角色或權限"}), 403

        # 格式化日期時間並統一字段名稱
        for r in resumes:
            if isinstance(r.get('created_at'), datetime):
                r['created_at'] = r['created_at'].strftime("%Y-%m-%d %H:%M:%S")
            # 統一字段名稱，確保前端能正確訪問
            if 'student_name' in r:
                r['name'] = r['student_name']
            if 'student_number' in r:
                r['username'] = r['student_number']
            if 'class_name' in r:
                r['className'] = r['class_name']
            if 'created_at' in r:
                r['upload_time'] = r['created_at']

        print(f"✅ [DEBUG] Returning {len(resumes)} resumes for role {role}")
        return jsonify({"success": True, "resumes": resumes})

    except Exception:
        print("❌ 取得班級履歷資料錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# 審核履歷 (退件/完成)
# -------------------------
@resume_bp.route('/api/review_resume/<int:resume_id>', methods=['POST'])
def review_resume(resume_id):
    user_id = session.get('user_id')
    user_role = session.get('role')

    # 1. 權限檢查
    ALLOWED_ROLES = ['teacher', 'admin', 'class_teacher']
    if not user_id or user_role not in ALLOWED_ROLES:
        return jsonify({"success": False, "message": "未授權或無權限"}), 403

    data = request.get_json()
    status = data.get('status')
    comment = data.get('comment') # 老師留言

    if status not in ['approved', 'rejected']:
        return jsonify({"success": False, "message": "無效的狀態碼"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 2. 查詢履歷並取得學生Email和姓名
        cursor.execute("""
            SELECT 
                r.user_id, r.original_filename, r.status AS old_status, r.comment,
                u.email AS student_email, u.name AS student_name
            FROM resumes r
            JOIN users u ON r.user_id = u.id
            WHERE r.id=%s
        """, (resume_id,))
        resume_data = cursor.fetchone()

        if not resume_data:
            return jsonify({"success": False, "message": "找不到履歷"}), 404

        student_user_id = resume_data['user_id']
        student_email = resume_data['student_email'] 
        student_name = resume_data['student_name']  
        old_status = resume_data['old_status']


        # 3. 更新履歷狀態 (使用您確認的 reviewed_by 和 reviewed_at)
        cursor.execute("""
            UPDATE resumes SET 
                status=%s, 
                comment=%s, 
                reviewed_by=%s,    
                reviewed_at=NOW()  
            WHERE id=%s
        """, (status, comment, user_id, resume_id))
        
        # 4. 取得審核者姓名
        cursor.execute("SELECT name FROM users WHERE id = %s", (user_id,))
        reviewer = cursor.fetchone()
        reviewer_name = reviewer['name'] if reviewer else "審核老師"

        # 5. 處理 Email 寄送與通知 (僅在狀態改變時處理)
        from email_service import send_resume_rejection_email, send_resume_approval_email
        if old_status != status:
            # =============== 退件 ===============
            if status == 'rejected':
                email_success, email_message, log_id = send_resume_rejection_email(
                    student_email, student_name, reviewer_name, comment or "無"
                )
                print(f"📧 履歷退件 Email: {email_success}, {email_message}, Log ID: {log_id}")

                # 🎯 建立退件通知（改成 create_notification）
                notification_content = (
                    f"您的履歷已被 {reviewer_name} 老師退件。\n\n"
                    f"退件原因：{comment if comment else '請查看老師留言'}\n\n"
                    f"請根據老師的建議修改後重新上傳。"
                )

                create_notification(
                    user_id=student_user_id,
                    title="履歷退件通知",
                    message=notification_content
                )

            # =============== 通過 ===============
            elif status == 'approved':
                email_success, email_message, log_id = send_resume_approval_email(
                    student_email, student_name, reviewer_name
                )
                print(f"📧 履歷通過 Email: {email_success}, {email_message}, Log ID: {log_id}")

                # 🎯 建立通過通知（改成 create_notification）
                notification_content = (
                    f"恭喜您！您的履歷已由 {reviewer_name} 老師審核通過。\n"
                    f"您可以繼續後續的實習申請流程。"
                )

                create_notification(
                    user_id=student_user_id,
                    title="履歷審核通過通知",
                    message=notification_content
                )

        conn.commit()

        return jsonify({"success": True, "message": "履歷審核狀態更新成功"})

    except Exception as e:
        conn.rollback()
        traceback.print_exc() 
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}，請檢查後台日誌"}), 500

    finally:
        cursor.close()
        conn.close()
        
# -------------------------
# API - 更新履歷欄位（comment, note）（含權限檢查）
# -------------------------
@resume_bp.route('/api/update_resume_field', methods=['POST'])
def update_resume_field():
    try:
        if not require_login():
            return jsonify({"success": False, "message": "未授權"}), 403

        data = request.get_json() or {}
        resume_id = data.get('resume_id')
        field = data.get('field')
        value = (data.get('value') or '').strip()

        allowed_fields = {
            "comment": "comment",
            "note": "note"
        }

        try:
            resume_id = int(resume_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "resume_id 必須是數字"}), 400

        if field not in allowed_fields:
            return jsonify({"success": False, "message": "參數錯誤"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 先找出 resume 的 owner
        cursor.execute("SELECT user_id FROM resumes WHERE id = %s", (resume_id,))
        r = cursor.fetchone()
        if not r:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        owner_id = r['user_id']

        # 取得使用者角色與 id
        role = session.get('role')
        user_id = session['user_id']

        if role == "class_teacher":
            if not teacher_manages_class(cursor, user_id, get_user_by_id(cursor, owner_id)['class_id']):
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "沒有權限修改該履歷"}), 403

        elif role == "director":
            director_dept = get_director_department(cursor, user_id)
            cursor.execute("SELECT c.department FROM classes c JOIN users u ON u.class_id = c.id WHERE u.id = %s", (owner_id,))
            target_dept_row = cursor.fetchone()
            if not director_dept or not target_dept_row or director_dept != target_dept_row.get('department'):
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "沒有權限修改該履歷"}), 403

        elif role == "admin":
            pass  # admin 可以

        elif role == "student":
            # 學生只能修改自己的履歷，且只能修改 note 欄位
            if user_id != owner_id:
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "學生只能修改自己的履歷"}), 403
            if field != "note":
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "學生只能修改備註欄位"}), 403

        else:
            # ta 或其他角色不可修改
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "角色無權限修改"}), 403

        # 更新欄位
        sql = f"UPDATE resumes SET {allowed_fields[field]} = %s, updated_at = NOW() WHERE id = %s"
        cursor.execute(sql, (value, resume_id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"success": True, "field": field, "resume_id": resume_id})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500


# -------------------------
# API - 查詢履歷狀態
# -------------------------
@resume_bp.route('/api/resume_status', methods=['GET'])
def resume_status():
    resume_id = request.args.get('resume_id')
    if not resume_id:
        return jsonify({"success": False, "message": "缺少 resume_id"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT status FROM resumes WHERE id = %s", (resume_id,))
        resume = cursor.fetchone()
        cursor.close()
        conn.close()

        if not resume:
            return jsonify({"success": False, "message": "找不到該履歷"}), 404

        return jsonify({"success": True, "status": resume['status']})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500


# -------------------------
# API - 查詢所有學生履歷（根據 username，含讀取權限檢查）
# -------------------------
@resume_bp.route('/api/get_student_resumes', methods=['GET'])
def get_student_resumes():
    if not require_login():
        return jsonify({"success": False, "message": "未授權"}), 403

    username = request.args.get('username')
    if not username:
        return jsonify({"success": False, "message": "缺少 username"}), 400

    user_id = session['user_id']
    role = session['role']

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT u.id AS student_id, u.class_id, c.department
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            WHERE u.username = %s
        """, (username,))
        student = cursor.fetchone()
        if not student:
            return jsonify({"success": False, "message": "找不到學生"}), 404

        # 權限判斷（讀取）
        if role == "teacher":
            if not teacher_manages_class(cursor, user_id, student['class_id']):
                return jsonify({"success": False, "message": "沒有權限查看該學生履歷"}), 403

        elif role == "director":
            director_dept = get_director_department(cursor, user_id)
            if not director_dept or director_dept != student.get('department'):
                return jsonify({"success": False, "message": "沒有權限查看該學生履歷"}), 403

        elif role == "ta":
            pass  # TA 可讀全部（如需限制可在此修改）

        elif role == "admin":
            pass

        else:
            return jsonify({"success": False, "message": "角色無權限"}), 403

        # 取得該學生履歷
        cursor.execute("""
            SELECT r.id, r.original_filename, r.status, r.comment, r.note, r.created_at AS upload_time
            FROM resumes r
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
        """, (student['student_id'],))
        resumes = cursor.fetchall()

        for r in resumes:
            if isinstance(r.get('upload_time'), datetime):
                r['upload_time'] = r['upload_time'].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"success": True, "resumes": resumes})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# 頁面路由
# -------------------------
@resume_bp.route('/upload_resume')
def upload_resume_page():
    return render_template('resume/upload_resume.html')

@resume_bp.route('/review_resume')
def review_resume_page():
    # 檢查登入狀態
    if not require_login():
        return redirect('/login')
    
    # 根據角色返回對應的模板
    role = session.get('role', '')
    
    # 老師、班導、主任、TA、管理員、廠商使用審核頁面
    if role in ['teacher', 'class_teacher', 'director', 'ta', 'admin', 'vendor']:
        return render_template('user_shared/review_resumes.html')
    
    # 其他角色使用一般查看頁面
    return render_template('resume/review_resume.html')

@resume_bp.route('/ai_edit_resume')
def ai_edit_resume_page():
    return render_template('resume/ai_edit_resume.html')