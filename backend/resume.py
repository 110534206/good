from flask import Blueprint, request, jsonify, session, send_file, render_template, redirect, current_app
from werkzeug.utils import secure_filename
from config import get_db
from semester import get_current_semester_id
from docxtpl import DocxTemplate, InlineImage
from docx.shared import Inches
import os
import traceback
import json
import re
from datetime import datetime, date
from notification import create_notification
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
import io

# 修正：確保 role_map 存在
role_map = {
    "student": "學生",
    "teacher": "指導老師",
    "director": "主任",
    "ta": "科助",
    "admin": "管理員",
    "vendor": "廠商",
    "class_teacher": "班導師",
    "approved": "通過",
    "rejected": "退回"
}

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

# -------------------------
# 輔助函數：格式化學分數（整數顯示為整數，如2而不是2.0）
# -------------------------
def format_credits(credits_value):
    """格式化學分數，整數顯示為整數格式"""
    if credits_value is None:
        return ''
    
    # 如果是字符串，嘗試解析
    if isinstance(credits_value, str):
        credits_value = credits_value.strip()
        # 如果包含分數符號（如"2/2"），直接返回
        if '/' in credits_value:
            return credits_value
        # 嘗試轉換為數字
        try:
            num_value = float(credits_value)
            # 如果是整數，返回整數格式
            if num_value.is_integer():
                return str(int(num_value))
            return str(num_value)
        except (ValueError, TypeError):
            # 無法轉換為數字，返回原字符串
            return credits_value
    
    # 如果是數字類型
    if isinstance(credits_value, (int, float)):
        # 如果是整數，返回整數格式
        if isinstance(credits_value, float) and credits_value.is_integer():
            return str(int(credits_value))
        elif isinstance(credits_value, int):
            return str(credits_value)
        else:
            return str(credits_value)
    
    # 其他類型，轉換為字符串
    return str(credits_value)

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

    # vendor 可以查看已通過老師審核的履歷
    if session_role == "vendor":
        # 檢查履歷狀態是否為 'approved'（老師已通過）
        cursor.execute("""
            SELECT status 
            FROM resumes 
            WHERE user_id = %s 
            ORDER BY created_at DESC 
            LIMIT 1
        """, (target_user_id,))
        resume = cursor.fetchone()
        if resume and resume.get('status') == 'approved':
            return True
        return False

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
            CONCAT(COALESCE(cc.job_category, ''), COALESCE(cc.level, '')) AS cert_name,
            cc.category AS cert_category,
            CONCAT(CONCAT(COALESCE(cc.job_category, ''), COALESCE(cc.level, '')), ' (', ca.name, ')') AS full_name,
            sc.CertPath AS cert_path,
            sc.AcquisitionDate AS acquire_date,
            sc.cert_code AS cert_code
        FROM student_certifications sc
        LEFT JOIN certificate_codes cc 
            ON sc.cert_code COLLATE utf8mb4_unicode_ci = cc.code COLLATE utf8mb4_unicode_ci
        LEFT JOIN cert_authorities ca 
            ON cc.authority_id = ca.id
        WHERE sc.StuID = %s
        ORDER BY sc.AcquisitionDate DESC, sc.id ASC
    """
    cursor.execute(sql, (student_id,))
    rows = cursor.fetchall()
    # 轉為 Python dict（cursor.fetchall() 已返回字典，因為使用了 dictionary=True）
    results = []
    for r in rows:
        if r:  # 確保 r 不是 None
            cert_code = r.get('cert_code', '')
            cert_name_from_join = r.get('cert_name', '')
            cert_category_from_join = r.get('cert_category', '')
            
            # 如果 JOIN 失敗，嘗試通過 cert_code 查詢 category
            category = cert_category_from_join if cert_category_from_join else 'other'
            if not cert_category_from_join and cert_code and cert_code.strip() and cert_code.upper() != 'OTHER':
                try:
                    cursor.execute("""
                        SELECT category 
                        FROM certificate_codes 
                        WHERE code COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci
                        LIMIT 1
                    """, (cert_code,))
                    category_row = cursor.fetchone()
                    if category_row:
                        category = category_row.get('category', 'other')
                        print(f"✅ load_student_certifications: 通過 cert_code 查詢 category: code={cert_code}, category={category}")
                except Exception as e:
                    print(f"⚠️ load_student_certifications: 查詢 category 失敗: {e}")
            
            results.append({
                "cert_name": cert_name_from_join or '',
                "category": category,        # labor / intl / local / other
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
# 儲存結構化資料（重整 + 稳定版）
# -------------------------
def save_structured_data(cursor, student_id, data, semester_id=None):
    try:
        # -------------------------------------------------------------
        # 1) 儲存 Student_Info（基本資料）
        # -------------------------------------------------------------
        cursor.execute("""
            INSERT INTO Student_Info 
                (StuID, StuName, BirthDate, Gender, Phone, Email, Address, 
                 ConductScore, Autobiography, PhotoPath, UpdatedAt)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON DUPLICATE KEY UPDATE 
                StuName=VALUES(StuName),
                BirthDate=VALUES(BirthDate),
                Gender=VALUES(Gender),
                Phone=VALUES(Phone),
                Email=VALUES(Email),
                Address=VALUES(Address),
                ConductScore=VALUES(ConductScore),
                Autobiography=VALUES(Autobiography),
                PhotoPath=VALUES(PhotoPath),
                UpdatedAt=NOW()
        """, (
            student_id,
            data.get("name"),
            data.get("birth_date"),
            data.get("gender"),
            data.get("phone"),
            data.get("email"),
            data.get("address"),
            data.get("conduct_score"),
            data.get("autobiography"),
            data.get("photo_path")
        ))

        # -------------------------------------------------------------
        # 2) 儲存 course_grades
        # -------------------------------------------------------------
        cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'SemesterID'")
        has_semester_id = cursor.fetchone() is not None

        if has_semester_id and semester_id:
            cursor.execute(
                "DELETE FROM course_grades WHERE StuID=%s AND IFNULL(SemesterID,'')=%s",
                (student_id, semester_id)
            )
        else:
            cursor.execute("DELETE FROM course_grades WHERE StuID=%s", (student_id,))

        seen_courses = set()
        for c in data.get("courses", []):
            cname = (c.get("name") or "").strip()
            if not cname:
                continue
            if cname in seen_courses:
                continue
            seen_courses.add(cname)

            if has_semester_id and semester_id:
                cursor.execute("""
                    INSERT INTO course_grades
                        (StuID, CourseName, Credits, Grade, SemesterID, ProofImage)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """, (student_id, cname, c.get("credits"), c.get("grade"), semester_id, c.get("proof_image")))
            else:
                cursor.execute("""
                    INSERT INTO course_grades
                        (StuID, CourseName, Credits, Grade, ProofImage)
                    VALUES (%s,%s,%s,%s,%s)
                """, (student_id, cname, c.get("credits"), c.get("grade"), c.get("proof_image")))
        
        # -------------------------------------------------------------
        # 3) 儲存 student_certifications
        # -------------------------------------------------------------
        
        # 檢查 student_certifications 表的欄位，以確定要插入哪些數據
        cursor.execute("SHOW COLUMNS FROM student_certifications")
        cert_columns = cursor.fetchall()
        known_columns = {c['Field']: c for c in cert_columns}

        cert_rows = []
        processed_certs = set() # 用於去重 (job_category, level)

        # (3) 處理結構化的證照資料 (structured_certifications)
        for cert in data.get("structured_certifications", []):
            row = {"StuID": student_id}
            
            # 使用 cert_code 作為主要識別碼
            code = (cert.get("cert_code") or "").strip().upper()
            if not code:
                # 如果沒有 cert_code，則必須要有自填的 CertName
                if not cert.get("name"):
                    continue
                # 假設自填名稱的 code 為 'OTHER'
                code = 'OTHER'
            
            row["cert_code"] = code

            db_job_category = None
            db_level = None
            db_authority_id = None
            
            # 查詢 code 對應的資訊
            if code and code != 'OTHER':
                try:
                    cursor.execute("""
                        SELECT job_category, level, authority_id 
                        FROM certificate_codes 
                        WHERE code COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci
                        LIMIT 1
                    """, (code,))
                    cert_info = cursor.fetchone()
                    if cert_info:
                        db_job_category = cert_info.get('job_category', '').strip()
                        db_level = cert_info.get('level', '').strip()
                        db_authority_id = cert_info.get('authority_id')
                except Exception as e:
                    print(f"⚠️ 查詢 certificate_codes 失敗: {e}")

            # 證照名稱：優先使用資料庫查到的（如果有），否則使用手填的 name 欄位
            cert_name = ""
            if db_job_category and db_level:
                cert_name = f"{db_job_category}{db_level}"
            elif cert.get("name"):
                cert_name = cert["name"]

            if not cert_name:
                print(f"⚠️ 忽略無名稱證照記錄: {cert}")
                continue # 忽略沒有名稱的記錄

            # 檢查是否重複（使用 job_category, level 作為唯一標識）
            if db_job_category and db_level:
                cert_identifier = (db_job_category, db_level)
                if cert_identifier in processed_certs:
                    print(f"⚠️ 跳過重複的結構化證照記錄: code={code}")
                    continue
                processed_certs.add(cert_identifier)

            # 填入欄位
            if "CertName" in known_columns:
                row["CertName"] = cert_name
            if "job_category" in known_columns:
                row["job_category"] = db_job_category if db_job_category else None
            if "level" in known_columns:
                row["level"] = db_level if db_level else None
            if "authority_id" in known_columns and db_authority_id:
                row["authority_id"] = int(db_authority_id)
            if "AcquisitionDate" in known_columns and cert.get("acquire_date"):
                # 嘗試將日期轉為 YYYY-MM-DD 格式
                try:
                    date_obj = datetime.strptime(cert["acquire_date"].split('T')[0], "%Y-%m-%d")
                    row["AcquisitionDate"] = date_obj.strftime("%Y-%m-%d")
                except:
                    row["AcquisitionDate"] = cert["acquire_date"] # 保持原樣
            
            # 處理路徑
            path = cert.get("cert_path")
            if "CertPath" in known_columns and path:
                # 將 Windows 路徑格式（反斜杠）轉換為 Web 路徑格式（正斜杠）
                normalized_path = path.replace("\\", "/") 
                # 確保路徑是相對路徑格式
                if normalized_path.startswith("uploads/"):
                    row["CertPath"] = normalized_path
                else:
                    # 如果路徑包含絕對路徑，提取相對路徑部分
                    parts = normalized_path.split("/")
                    if "uploads" in parts:
                        idx_uploads = parts.index("uploads")
                        row["CertPath"] = "/".join(parts[idx_uploads:])
                    else:
                        row["CertPath"] = normalized_path
            else:
                row["CertPath"] = None
            
            cert_rows.append(row)

        # (4) 處理上傳證照圖片（舊的圖片上傳方式，向後兼容） - 這裡為了程式碼完整性省略，因為前端應主要傳遞 structured_certifications

        # (5) 實際寫入資料庫
        if cert_rows:
            # 先刪除舊資料
            cursor.execute("DELETE FROM student_certifications WHERE StuID=%s", (student_id,))
            for row in cert_rows:
                cols = list(row.keys())
                values = list(row.values())
                cols.append("CreatedAt")
                placeholders = ", ".join(["%s"] * (len(values) + 1))
                try:
                    cursor.execute(
                        f"INSERT INTO student_certifications ({','.join(cols)}) VALUES ({placeholders})",
                        (*values, datetime.now())
                    )
                except Exception as e:
                    # 如果因為唯一索引衝突導致插入失敗，記錄錯誤但繼續處理其他記錄
                    print(f"⚠️ 插入證照記錄失敗（可能是唯一索引衝突）: {e}")
                    print(f" 記錄內容: {row}")
        
        # -------------------------------------------------------------
        # 4) 儲存語言能力 student_languageskills
        # -------------------------------------------------------------
        cursor.execute("DELETE FROM student_languageskills WHERE StuID=%s", (student_id,))
        for row in data.get("structured_languages", []):
            if row.get("language") and row.get("level"):
                cursor.execute("""
                    INSERT INTO student_languageskills (StuID, Language, Level, CreatedAt)
                    VALUES (%s,%s,%s,NOW())
                """, (student_id, row["language"], row["level"]))

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
                ORDER BY CourseName COLLATE utf8mb4_unicode_ci
            """, (student_id, semester_id))
        else:
            cursor.execute("""
                SELECT CourseName, Credits, Grade, SemesterID 
                FROM course_grades 
                WHERE StuID=%s AND SemesterID=%s
                ORDER BY CourseName COLLATE utf8mb4_unicode_ci
            """, (student_id, semester_id))
    else:
        if transcript_field:
            cursor.execute(f"""
                SELECT CourseName, Credits, Grade, IFNULL({transcript_field}, '') AS transcript_path 
                FROM course_grades 
                WHERE StuID=%s 
                ORDER BY CourseName COLLATE utf8mb4_unicode_ci
            """, (student_id,))
        else:
            cursor.execute("""
                SELECT CourseName, Credits, Grade 
                FROM course_grades 
                WHERE StuID=%s 
                ORDER BY CourseName COLLATE utf8mb4_unicode_ci
            """, (student_id,))
    
    grades_rows = cursor.fetchall() or []
    
    data['grades'] = grades_rows
    data['transcript_path'] = ''
    # 嘗試從成績記錄中找到路徑
    for row in grades_rows:
        tp = row.get('transcript_path')
        if tp:
            data['transcript_path'] = tp
            break

    # 證照 - 使用新的查詢方式
    cursor.execute("""
        SELECT 
            sc.id, sc.StuID, sc.cert_code, sc.CertName, sc.AcquisitionDate, sc.CertPath,
            sc.issuer, sc.IssuingBody, sc.CertType, 
            cc.job_category, cc.level, cc.authority_id, cc.category AS CertCategory,
            ca.name AS authority_name
        FROM student_certifications sc
        LEFT JOIN certificate_codes cc 
            ON sc.cert_code COLLATE utf8mb4_unicode_ci = cc.code COLLATE utf8mb4_unicode_ci
        LEFT JOIN cert_authorities ca 
            ON cc.authority_id = ca.id
        WHERE sc.StuID = %s
        ORDER BY sc.AcquisitionDate DESC, sc.id ASC
    """, (student_id,))
    data['certifications'] = cursor.fetchall() or []

    # 語言能力
    cursor.execute(""" 
        SELECT Language AS language, Level AS level 
        FROM student_languageskills 
        WHERE StuID=%s 
        ORDER BY Language
    """, (student_id,))
    data['languages'] = cursor.fetchall() or []

    # 缺勤記錄佐證圖片（僅返回最新的）
    absence_proof_path = ''
    try:
        cursor.execute("SELECT id FROM users WHERE username=%s", (student_id,))
        user_row = cursor.fetchone()
        if user_row:
            user_id = user_row.get('id')
            # 嘗試使用 created_at 排序
            try:
                cursor.execute("""
                    SELECT image_path 
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
                absence_proof_path = absence_row.get('image_path', '')
    except Exception as e:
        print(f"⚠️ 查詢缺勤佐證圖片失敗: {e}")
        traceback.print_exc()
    data['absence_proof_path'] = absence_proof_path

    return data

# -------------------------
# 格式化資料 for DOCX
# -------------------------
def format_data_for_doc(student_data, doc_path=None):
    context = {}
    doc = DocxTemplate(doc_path) if doc_path else None

    # 1. 基本資料 (Info)
    info = student_data['info']
    context['StuID'] = info.get('StuID', '')
    context['StuName'] = info.get('StuName', '')
    context['Gender'] = info.get('Gender', '')
    context['Phone'] = info.get('Phone', '')
    context['Email'] = info.get('Email', '')
    context['Address'] = info.get('Address', '')
    context['ConductScore'] = info.get('ConductScore', '')
    context['Autobiography'] = info.get('Autobiography', '')
    
    # 生日格式化
    birth_date = info.get('BirthDate')
    if isinstance(birth_date, (datetime, date)):
        context['BirthDate'] = birth_date.strftime("%Y-%m-%d")
    elif birth_date:
        try:
            context['BirthDate'] = datetime.strptime(str(birth_date).split(' ')[0], "%Y-%m-%d").strftime("%Y-%m-%d")
        except:
            context['BirthDate'] = str(birth_date)
    else:
        context['BirthDate'] = ''
    
    # 學生照片
    photo_path = info.get('PhotoPath')
    if photo_path and doc:
        image_size = Inches(1.5)
        image_obj = safe_create_inline_image(doc, photo_path, image_size, "學生照片")
        context['StudentPhoto'] = image_obj if image_obj else ""
    else:
        context['StudentPhoto'] = ""

    # 2. 核心科目 (Core Courses) - 假設所有課程都是核心科目
    core_courses = []
    for c in student_data['grades']:
        core_courses.append({
            'CourseName': c.get('CourseName', ''),
            'Credits': format_credits(c.get('Credits')),
            'Grade': score_to_grade(c.get('Grade')),
        })
    context['core_courses'] = core_courses

    # 3. 證照 (Certifications)
    all_certs = student_data['certifications']
    labor, international, local, other = categorize_certifications(all_certs)
    
    # 填入表格區（每個類別最多 4 個）
    fill_certificates_to_doc(context, "LaborCerts_", labor, 4)
    fill_certificates_to_doc(context, "IntlCerts_", international, 4)
    fill_certificates_to_doc(context, "LocalCerts_", local, 4)
    fill_certificates_to_doc(context, "OtherCerts_", other, 4)
    
    # 圖片區（不分類，按順序最多 32 張）
    certs_for_photos = [
        {'photo_path': c.get('CertPath'), 'photo_name': f"{c.get('job_category', '')}{c.get('level', '')}" if c.get('job_category') else c.get('CertName')}
        for c in all_certs if c.get('CertPath')
    ]

    if doc:
        # 第一頁圖片 (1-8)
        fill_certificate_photos(context, doc, certs_for_photos, 1, 8)
        # 第二頁圖片 (9-16)
        fill_certificate_photos(context, doc, certs_for_photos[8:], 9, 8)
        # 第三頁圖片 (17-24)
        fill_certificate_photos(context, doc, certs_for_photos[16:], 17, 8)
        # 第四頁圖片 (25-32)
        fill_certificate_photos(context, doc, certs_for_photos[24:], 25, 8)

    # 4. 語言能力 (Languages)
    for i in range(1, 5): # 最多四種語言
        if i <= len(student_data['languages']):
            lang = student_data['languages'][i-1]
            marks = generate_language_marks(lang['level'])
            context[f'LangName_{i}'] = lang['language']
            context[f'LangJing_{i}'] = marks['Jing']
            context[f'LangZhong_{i}'] = marks['Zhong']
            context[f'LangLue_{i}'] = marks['Lue']
        else:
            context[f'LangName_{i}'] = ''
            context[f'LangJing_{i}'] = '□'
            context[f'LangZhong_{i}'] = '□'
            context[f'LangLue_{i}'] = '□'
    
    return context, doc

# -------------------------
# API：儲存履歷資料
# -------------------------
@resume_bp.route('/api/save_resume_data', methods=['POST'])
def save_resume_data():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    student_id = session['username']
    data = request.get_json()
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 取得目前的學期 ID (如果系統使用學期分流)
        semester_id = get_current_semester_id(cursor)

        if save_structured_data(cursor, student_id, data, semester_id):
            conn.commit()
            return jsonify({"success": True, "message": "履歷資料儲存成功"})
        else:
            conn.rollback()
            return jsonify({"success": False, "message": "履歷資料儲存失敗 (資料庫錯誤)"}), 500

    except Exception as e:
        conn.rollback()
        print("❌ 儲存履歷資料錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API：取得履歷資料
# -------------------------
@resume_bp.route('/api/get_resume_data', methods=['GET'])
def get_resume_data():
    if 'user_id' not in session:
        return redirect('/login')

    session_user_id = session['user_id']
    session_role = session['role']
    target_student_id = request.args.get('student_id')

    if session_role == 'student':
        target_student_id = session['username']
    elif not target_student_id:
        return jsonify({"success": False, "message": "缺少學生 ID"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 透過 student_id 找到 user_id
        cursor.execute("SELECT id FROM users WHERE username = %s", (target_student_id,))
        target_user_row = cursor.fetchone()
        if not target_user_row:
            return jsonify({"success": False, "message": "學生不存在"}), 404
        target_user_id = target_user_row['id']

        # 權限檢查
        if not can_access_target_resume(cursor, session_user_id, session_role, target_user_id):
            return jsonify({"success": False, "message": "無權限查看此學生的履歷"}), 403

        # 取得目前的學期 ID (如果系統使用學期分流)
        semester_id = get_current_semester_id(cursor)

        # 取得資料
        data = get_student_info_for_doc(cursor, target_student_id, semester_id)

        student_info = data['info']
        courses = data['grades']
        all_certifications = data['certifications']
        languages = data['languages']
        transcript_path = data['transcript_path']
        absence_proof_path = data['absence_proof_path']

        # 日期格式轉換
        birth_date = student_info.get("BirthDate")
        if birth_date:
            if isinstance(birth_date, datetime):
                birth_date = birth_date.strftime("%Y-%m-%d")
            else:
                try:
                    birth_date = datetime.strptime(str(birth_date).split(' ')[0], "%Y-%m-%d").strftime("%Y-%m-%d")
                except:
                    pass

        # 格式化證照輸出
        formatted_certs = []
        for cert in all_certifications:
            acquire_date = cert.get("AcquisitionDate")
            formatted_acquire_date = ""
            acquisition_date_str = None # 用於 JSON 序列化的字符串格式
            if acquire_date is not None:
                if isinstance(acquire_date, (datetime, date)):
                    formatted_acquire_date = acquire_date.strftime("%Y-%m-%d")
                    acquisition_date_str = formatted_acquire_date
                elif acquire_date:
                    try:
                        # 嘗試解析字符串格式的日期
                        if isinstance(acquire_date, str):
                            formatted_acquire_date = datetime.strptime(acquire_date.split(' ')[0], "%Y-%m-%d").strftime("%Y-%m-%d")
                            acquisition_date_str = formatted_acquire_date
                        else:
                            formatted_acquire_date = str(acquire_date)
                            acquisition_date_str = formatted_acquire_date
                    except Exception as e:
                        formatted_acquire_date = str(acquire_date) if acquire_date else ""
                        acquisition_date_str = formatted_acquire_date

            # 獲取級別字段
            cert_level = cert.get("level", "")
            
            # 獲取證照圖片路徑，並將 Windows 路徑格式（反斜杠）轉換為 Web 路徑格式（正斜杠）
            cert_path_raw = cert.get("CertPath", "")
            cert_path = cert_path_raw.replace("\\", "/") if cert_path_raw else ""

            # 證照名稱：優先使用 cc.job_category + cc.level，其次使用 sc.CertName
            cert_name = ""
            if cert.get('job_category') and cert.get('level'):
                cert_name = f"{cert.get('job_category')}{cert.get('level')}"
            elif cert.get('CertName'):
                cert_name = cert.get('CertName')

            formatted_certs.append({
                "id": cert["id"],
                "cert_code": cert.get("cert_code", ""),
                "cert_path": cert_path,
                "name": cert_name,
                "job_category": cert.get("job_category", ""),
                "level": cert_level,
                "authority_name": cert.get("authority_name", ""),
                "issuer": cert.get("issuer", ""),
                "authority_id": cert.get("authority_id") if "authority_id" in cert else None,
                "IssuingBody": cert.get("IssuingBody", ""),
                "CertType": cert.get("CertType", "other"),
                "acquire_date": formatted_acquire_date,
                "AcquisitionDate": acquisition_date_str # 轉換為字符串格式，確保 JSON 序列化正常
            })

        # 回傳結果
        return jsonify({
            "success": True,
            "data": {
                "student_info": {
                    "name": student_info.get("StuName", ""),
                    "birth_date": birth_date or "",
                    "gender": student_info.get("Gender", ""),
                    "phone": student_info.get("Phone", ""),
                    "email": student_info.get("Email", ""),
                    "address": student_info.get("Address", ""),
                    "conduct_score": student_info.get("ConductScore", ""),
                    "autobiography": student_info.get("Autobiography", ""),
                    "photo_path": student_info.get("PhotoPath", "")
                },
                "courses": courses,
                "certifications": formatted_certs,
                "languages": languages,
                "transcript_path": transcript_path,
                "absence_proof_path": absence_proof_path
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
# API：下載履歷 Word
# -------------------------
@resume_bp.route('/api/download_resume/<string:student_id>', methods=['GET'])
def download_resume(student_id):
    if 'user_id' not in session:
        return redirect('/login')

    session_user_id = session['user_id']
    session_role = session['role']
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 透過 student_id 找到 user_id
        cursor.execute("SELECT id FROM users WHERE username = %s", (student_id,))
        target_user_row = cursor.fetchone()
        if not target_user_row:
            return "學生不存在", 404
        target_user_id = target_user_row['id']

        # 權限檢查
        if not can_access_target_resume(cursor, session_user_id, session_role, target_user_id):
            return "無權限下載此學生的履歷", 403

        # 1. 取得資料
        semester_id = get_current_semester_id(cursor)
        student_data = get_student_info_for_doc(cursor, student_id, semester_id)

        # 2. 準備模板
        template_path = os.path.join(current_app.root_path, 'templates', 'resume_template.docx')
        if not os.path.exists(template_path):
            return "履歷模板文件不存在", 500

        # 3. 格式化資料並載入 DocxTemplate
        context, doc = format_data_for_doc(student_data, template_path)
        if not doc:
            return "DocxTemplate 載入失敗", 500

        # 4. 渲染模板
        doc.render(context)

        # 5. 儲存到記憶體
        file_stream = io.BytesIO()
        doc.save(file_stream)
        file_stream.seek(0)
        
        # 6. 回傳文件
        filename = f"{student_data['info'].get('StuName', student_id)}_履歷表.docx"
        return send_file(
            file_stream,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )

    except Exception as e:
        print("❌ 下載履歷錯誤:", e)
        traceback.print_exc()
        return f"伺服器錯誤: {str(e)}", 500
    finally:
        cursor.close()
        conn.close()


# -------------------------
# API：上傳成績單圖片/佐證
# -------------------------
@resume_bp.route('/api/upload_transcript', methods=['POST'])
def upload_transcript():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    student_id = session['username']
    
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "缺少文件"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "未選擇文件"}), 400

    # 檢查文件類型 (圖片)
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
    if '.' not in file.filename or file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        return jsonify({"success": False, "message": "不支援的文件類型"}), 400

    filename = secure_filename(file.filename)
    # 儲存路徑：uploads/resumes/StuID/transcript_timestamp.ext
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    student_dir = os.path.join(UPLOAD_FOLDER, student_id)
    os.makedirs(student_dir, exist_ok=True)
    
    # 儲存名稱
    ext = filename.rsplit('.', 1)[1].lower()
    save_filename = f"transcript_{timestamp}.{ext}"
    save_path_abs = os.path.join(student_dir, save_filename)
    
    file.save(save_path_abs)

    # 相對路徑（用於資料庫儲存）
    relative_path = os.path.join(UPLOAD_FOLDER, student_id, save_filename).replace('\\', '/')

    conn = get_db()
    cursor = conn.cursor()
    try:
        # 更新成績單路徑到 student_info.transcript_path (舊欄位，兼容)
        # 這裡改為更新到 course_grades 的 ProofImage 欄位（以最新的成績單圖片為主）
        
        # 1. 確保 course_grades 表有 ProofImage 欄位
        cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'ProofImage'")
        if not cursor.fetchone():
            conn.rollback()
            return jsonify({"success": False, "message": "資料庫缺少 course_grades.ProofImage 欄位"}), 500

        # 2. 取得目前的學期 ID (如果系統使用學期分流)
        semester_id = get_current_semester_id(cursor)
        
        # 3. 儲存路徑到 course_grades 的所有課程記錄 (該學期或所有)
        if semester_id:
            # 只更新該學期的記錄
            cursor.execute("""
                UPDATE course_grades 
                SET ProofImage = %s 
                WHERE StuID = %s AND IFNULL(SemesterID,'') = %s
            """, (relative_path, student_id, semester_id))
        else:
            # 更新所有記錄 (如果沒有學期分流)
            cursor.execute("""
                UPDATE course_grades 
                SET ProofImage = %s 
                WHERE StuID = %s
            """, (relative_path, student_id))

        conn.commit()
        return jsonify({"success": True, "message": "成績單圖片上傳成功", "path": relative_path})

    except Exception as e:
        conn.rollback()
        print("❌ 上傳成績單圖片錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API：上傳學生照片
# -------------------------
@resume_bp.route('/api/upload_photo', methods=['POST'])
def upload_photo():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    student_id = session['username']
    
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "缺少文件"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "未選擇文件"}), 400

    # 檢查文件類型 (圖片)
    allowed_extensions = {'png', 'jpg', 'jpeg'}
    if '.' not in file.filename or file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        return jsonify({"success": False, "message": "不支援的文件類型"}), 400

    filename = secure_filename(file.filename)
    # 儲存路徑：uploads/resumes/StuID/photo.ext
    student_dir = os.path.join(UPLOAD_FOLDER, student_id)
    os.makedirs(student_dir, exist_ok=True)
    
    # 儲存名稱 (固定名稱，會覆蓋舊的)
    ext = filename.rsplit('.', 1)[1].lower()
    save_filename = f"photo.{ext}"
    save_path_abs = os.path.join(student_dir, save_filename)
    
    file.save(save_path_abs)

    # 相對路徑（用於資料庫儲存）
    relative_path = os.path.join(UPLOAD_FOLDER, student_id, save_filename).replace('\\', '/')

    conn = get_db()
    cursor = conn.cursor()
    try:
        # 更新照片路徑到 Student_Info.PhotoPath
        cursor.execute("""
            INSERT INTO Student_Info (StuID, PhotoPath)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE PhotoPath = VALUES(PhotoPath), UpdatedAt = NOW()
        """, (student_id, relative_path))

        conn.commit()
        return jsonify({"success": True, "message": "照片上傳成功", "path": relative_path})

    except Exception as e:
        conn.rollback()
        print("❌ 上傳學生照片錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API：上傳證照圖片
# -------------------------
@resume_bp.route('/api/upload_certificate_photo', methods=['POST'])
def upload_certificate_photo():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    student_id = session['username']
    
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "缺少文件"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "未選擇文件"}), 400

    # 檢查文件類型 (圖片)
    allowed_extensions = {'png', 'jpg', 'jpeg', 'pdf'}
    if '.' not in file.filename or file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        return jsonify({"success": False, "message": "不支援的文件類型"}), 400

    filename = secure_filename(file.filename)
    # 儲存路徑：uploads/resumes/StuID/certs/cert_timestamp.ext
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    student_certs_dir = os.path.join(UPLOAD_FOLDER, student_id, "certs")
    os.makedirs(student_certs_dir, exist_ok=True)
    
    # 儲存名稱
    ext = filename.rsplit('.', 1)[1].lower()
    save_filename = f"cert_{timestamp}.{ext}"
    save_path_abs = os.path.join(student_certs_dir, save_filename)
    
    file.save(save_path_abs)

    # 相對路徑（用於資料庫儲存）
    relative_path = os.path.join(UPLOAD_FOLDER, student_id, "certs", save_filename).replace('\\', '/')

    # 不直接在這邊寫入 student_certifications 表，而是返回路徑供前端更新 structured_certifications
    return jsonify({"success": True, "message": "證照圖片上傳成功", "path": relative_path})

# -------------------------
# API：取得標準核心科目
# -------------------------
@resume_bp.route('/api/get_standard_courses', methods=['GET'])
def get_standard_courses():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT 
                course_name AS name, 
                credits 
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
# API：儲存學生個人模板
# -------------------------
@resume_bp.route('/api/save_personal_template', methods=['POST'])
def save_personal_template():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        data = request.get_json()
        student_id = session['username']
        template_name = data.get('display_name', '我的課程模板')
        
        # 確保 courses 列表不為 None
        courses_data = data.get('courses', [])
        
        # 檢查 courses 數據結構並將其轉換為 JSON
        valid_courses = []
        for course in courses_data:
            if course.get('name') and course.get('credits') is not None:
                valid_courses.append({
                    'name': course['name'],
                    'credits': format_credits(course['credits']), # 使用格式化函數
                    'grade': course.get('grade')
                })
        
        courses_json = json.dumps(valid_courses, ensure_ascii=False)
        
        # 儲存或更新模板
        cursor.execute("""
            INSERT INTO templates (template_type, content, display_name, is_active, uploaded_by, uploaded_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE 
                content=VALUES(content), 
                display_name=VALUES(display_name), 
                updated_at=NOW()
        """, ('student_custom', courses_json, template_name, 1, student_id))
        
        conn.commit()
        return jsonify({"success": True, "message": "個人課程模板儲存成功"})
        
    except Exception as e:
        conn.rollback()
        print("❌ 儲存個人課程模板錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API：取得學生個人模板
# -------------------------
@resume_bp.route('/api/get_personal_template', methods=['GET'])
def get_personal_template():
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        student_id = session['username']
        
        cursor.execute("""
            SELECT 
                content, 
                display_name
            FROM templates
            WHERE uploaded_by = %s AND template_type = 'student_custom' AND is_active = 1
            ORDER BY uploaded_at DESC
            LIMIT 1
        """, (student_id,))
        
        template = cursor.fetchone()
        
        if template:
            courses = json.loads(template['content'])
            return jsonify({
                "success": True, 
                "display_name": template['display_name'],
                "courses": courses
            })
        else:
            return jsonify({"success": False, "message": "未找到個人課程模板"})
            
    except Exception as e:
        print("❌ 取得個人課程模板錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API：上傳成績 Excel
# -------------------------
@resume_bp.route('/api/upload_course_grade_excel', methods=['POST'])
def upload_course_grade_excel():
    if 'user_id' not in session or session.get('role') != 'ta':
        return jsonify({"success": False, "message": "未授權"}), 403

    if 'file' not in request.files:
        return jsonify({"success": False, "message": "缺少文件"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "未選擇文件"}), 400

    allowed_extensions = {'xlsx', 'xls'}
    if '.' not in file.filename or file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        return jsonify({"success": False, "message": "不支援的文件類型"}), 400
    
    # 使用 BytesIO 讀取文件，不直接儲存到磁碟
    file_stream = io.BytesIO(file.read())
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 1. 載入工作簿
        workbook = load_workbook(file_stream)
        sheet = workbook.active
        
        # 2. 獲取標頭（假設第一行是標頭）
        headers = [cell.value for cell in sheet[1]]
        
        # 預期的欄位名稱
        student_id_col = None
        course_name_col = None
        credits_col = None
        grade_col = None

        # 找出欄位索引
        for i, header in enumerate(headers):
            if header and '學號' in str(header):
                student_id_col = i + 1
            elif header and ('課程名稱' in str(header) or '科目名稱' in str(header)):
                course_name_col = i + 1
            elif header and '學分' in str(header):
                credits_col = i + 1
            elif header and ('成績' in str(header) or '等第' in str(header)):
                grade_col = i + 1

        if not student_id_col or not course_name_col or not credits_col or not grade_col:
            return jsonify({"success": False, "message": "Excel 檔案缺少必要的欄位（學號、課程名稱/科目名稱、學分、成績/等第）"}), 400

        # 3. 處理數據
        data_to_import = {} # { student_id: [ {course_name, credits, grade}, ... ] }
        for row_index in range(2, sheet.max_row + 1):
            try:
                student_id = str(sheet.cell(row=row_index, column=student_id_col).value or '').strip()
                course_name = str(sheet.cell(row=row_index, column=course_name_col).value or '').strip()
                credits = str(sheet.cell(row=row_index, column=credits_col).value or '').strip()
                grade = str(sheet.cell(row=row_index, column=grade_col).value or '').strip()

                if not student_id or not course_name:
                    continue

                if student_id not in data_to_import:
                    data_to_import[student_id] = []
                
                # 簡單格式化學分
                try:
                    credits = float(credits)
                    if credits.is_integer():
                        credits = int(credits)
                except ValueError:
                    # 保持原始字串格式，例如 "2/2"
                    pass

                data_to_import[student_id].append({
                    'name': course_name,
                    'credits': credits,
                    'grade': grade
                })

            except Exception as row_e:
                print(f"⚠️ 處理 Excel 第 {row_index} 行錯誤: {row_e}")
                continue

        if not data_to_import:
            return jsonify({"success": False, "message": "Excel 檔案中未找到有效成績資料"}), 400
        
        # 4. 寫入資料庫
        semester_id = get_current_semester_id(cursor)
        imported_count = 0
        
        # 檢查 course_grades 表中是否有 SemesterID 欄位
        cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'SemesterID'")
        has_semester_id = cursor.fetchone() is not None
        
        for student_id, courses in data_to_import.items():
            try:
                # 刪除該學期或全部舊資料
                if has_semester_id and semester_id:
                    cursor.execute(
                        "DELETE FROM course_grades WHERE StuID=%s AND IFNULL(SemesterID,'')=%s",
                        (student_id, semester_id)
                    )
                else:
                    cursor.execute("DELETE FROM course_grades WHERE StuID=%s", (student_id,))

                # 批量插入新資料
                for c in courses:
                    if has_semester_id and semester_id:
                        cursor.execute("""
                            INSERT INTO course_grades
                                (StuID, CourseName, Credits, Grade, SemesterID)
                            VALUES (%s,%s,%s,%s,%s)
                        """, (student_id, c['name'], c['credits'], c['grade'], semester_id))
                    else:
                        cursor.execute("""
                            INSERT INTO course_grades
                                (StuID, CourseName, Credits, Grade)
                            VALUES (%s,%s,%s,%s)
                        """, (student_id, c['name'], c['credits'], c['grade']))
                
                imported_count += 1
                
            except Exception as db_e:
                print(f"❌ 匯入學生 {student_id} 成績資料失敗: {db_e}")
                conn.rollback() # 確保操作可以被撤銷，但這裡應該使用更細粒度的錯誤處理
                # 這裡為了簡化，如果一個學生失敗就繼續下一個學生，並在外面做一次大提交
                continue

        conn.commit()
        return jsonify({"success": True, "message": f"成功匯入 {imported_count} 位學生的成績資料"})
        
    except Exception as e:
        conn.rollback()
        print("❌ 匯入成績 Excel 錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 匯入核心科目 (Excel)
# -------------------------
@resume_bp.route('/api/import_standard_courses', methods=['POST'])
def import_standard_courses():
    if 'user_id' not in session or session.get('role') != 'ta':
        return jsonify({"success": False, "message": "未授權"}), 403

    if 'file' not in request.files:
        return jsonify({"success": False, "message": "缺少文件"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "未選擇文件"}), 400

    allowed_extensions = {'xlsx', 'xls'}
    if '.' not in file.filename or file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        return jsonify({"success": False, "message": "不支援的文件類型"}), 400
    
    file_stream = io.BytesIO(file.read())
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        workbook = load_workbook(file_stream)
        sheet = workbook.active
        
        headers = [cell.value for cell in sheet[1]]
        
        course_name_col = None
        credits_col = None
        
        for i, header in enumerate(headers):
            if header and ('課程名稱' in str(header) or '科目名稱' in str(header)):
                course_name_col = i + 1
            elif header and '學分' in str(header):
                credits_col = i + 1

        if not course_name_col or not credits_col:
            return jsonify({"success": False, "message": "Excel 檔案缺少必要的欄位（課程名稱/科目名稱、學分）"}), 400

        # 清空現有核心科目（避免重複或過時資料）
        cursor.execute("UPDATE standard_courses SET is_active = 0")

        imported_count = 0
        for row_index in range(2, sheet.max_row + 1):
            try:
                course_name = str(sheet.cell(row=row_index, column=course_name_col).value or '').strip()
                credits_value = str(sheet.cell(row=row_index, column=credits_col).value or '').strip()

                if not course_name or not credits_value:
                    continue

                # 嘗試將學分轉換為數字
                try:
                    credits = float(credits_value)
                except ValueError:
                    credits = 0.0 # 無效學分設為 0

                # 檢查是否已存在，如果存在則更新 is_active 和 credits
                cursor.execute("""
                    SELECT id FROM standard_courses WHERE course_name = %s LIMIT 1
                """, (course_name,))
                existing_course = cursor.fetchone()
                
                if existing_course:
                    cursor.execute("""
                        UPDATE standard_courses 
                        SET credits = %s, is_active = 1, updated_at = NOW() 
                        WHERE id = %s
                    """, (credits, existing_course['id']))
                else:
                    cursor.execute("""
                        INSERT INTO standard_courses 
                            (course_name, credits, is_active, uploaded_by, uploaded_at)
                        VALUES (%s, %s, 1, %s, NOW())
                    """, (course_name, credits, session['username']))
                
                imported_count += 1
                
            except Exception as row_e:
                print(f"⚠️ 處理 Excel 第 {row_index} 行錯誤: {row_e}")
                continue

        conn.commit()
        return jsonify({"success": True, "message": f"成功匯入 {imported_count} 筆核心科目資料"})
        
    except Exception as e:
        conn.rollback()
        print("❌ 匯入核心科目 Excel 錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# TA 頁面：上傳核心科目
# -------------------------
@resume_bp.route('/ta/upload_standard_courses')
def upload_standard_courses_page():
    if 'user_id' not in session or session.get('role') != 'ta':
        return redirect('/login')
    return render_template('ta/upload_standard_courses.html')

# -------------------------
# API：取得公司職缺列表 (for 履歷填寫頁面)
# -------------------------
@resume_bp.route('/api/company_positions', methods=['GET'])
def get_company_positions():
    try:
        company_name = request.args.get('company_name', '')
        if not company_name:
            return jsonify({"success": False, "message": "請提供公司名稱"}), 400
            
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 查詢該公司的所有職缺
        cursor.execute("""
            SELECT 
                id,
                title,
                description,
                salary,
                period,
                work_time,
                slots
            FROM internship_jobs
            WHERE company_id IN (
                SELECT id FROM companies WHERE name = %s AND status = 'approved'
            )
            AND is_active = 1
            ORDER BY title
        """, (company_name,))
        
        positions = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "positions": positions
        })
        
    except Exception as e:
        print(f"Error fetching company positions: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "message": "無法取得公司職缺列表"}), 500

# ----------------------------------------------------
# 【功能修正】新增 API：取得待審核履歷列表 (for 老師/主任/班導師)
# ----------------------------------------------------

# 輔助函數：獲取主任所屬部門的 ID (請根據您的資料庫結構調整)
def get_director_department(cursor, user_id):
    """
    獲取主任所屬部門的 ID。
    這裡假設 users 表中有 department_id 欄位。
    """
    try:
        # 假設 users 表中有 department_id 欄位
        cursor.execute("SELECT department_id FROM users WHERE id = %s", (user_id,))
        result = cursor.fetchone()
        # 假設 department_id 存在且非空
        return result['department_id'] if result and result.get('department_id') else None
    except Exception as e:
        print(f"Error fetching director department: {e}")
        return None


@resume_bp.route('/api/teacher_review_resumes', methods=['GET'])
def get_teacher_review_resumes():
    # 確保有權限 (teacher, director, class_teacher, admin) 才能進入
    if 'user_id' not in session or session.get('role') not in ['teacher', 'director', 'class_teacher', 'admin']:
        return jsonify({"success": False, "message": "無權限"}), 403

    session_user_id = session['user_id']
    session_role = session['role']
    
    conn = get_db() 
    # 使用 dictionary=True 讓查詢結果為字典格式
    cursor = conn.cursor(dictionary=True) 
    
    try:
        # 建立基本查詢：所有學生的最新履歷資料
        sql = """
            SELECT 
                u.id AS user_id,
                u.username AS student_id,
                u.name,
                c.name AS class_name,
                c.department,
                r.id AS resume_id,
                r.created_at AS upload_time,
                r.original_filename,
                r.status AS display_status
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            LEFT JOIN resumes r ON u.id = r.user_id 
            WHERE u.role = 'student' 
        """
        params = []
        
        # 根據角色過濾資料
        if session_role in ['teacher', 'class_teacher']:
            # 老師/班導師：只看自己班級的學生 (假設 classes_teacher 表格關聯了老師和班級)
            sql += """
                AND u.class_id IN (
                    SELECT class_id FROM classes_teacher WHERE teacher_id = %s
                )
            """
            params.append(session_user_id)
        elif session_role == 'director':
            # 主任：只看自己部門的學生
            director_dept = get_director_department(cursor, session_user_id)
            if not director_dept:
                # 主任沒有設定部門，則返回空列表
                return jsonify({"success": True, "data": [], "message": "主任未設定所屬部門，無法查詢"}), 200
            
            # classes 表中使用 department 欄位
            sql += " AND c.department = %s" 
            params.append(director_dept)
        
        # 排序：按照班級、姓名、上傳時間（最新在上）
        sql += " ORDER BY c.name, u.username, COALESCE(r.created_at, '1970-01-01') DESC"

        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
        
        # 整理結果：返回所有履歷記錄（包括未上傳履歷的學生）
        result_data = []
        students_without_resume = set()  # 追蹤已處理的未上傳履歷學生
        
        for row in rows:
            student_id = row['student_id']
            
            # 處理未上傳履歷的學生（只添加一次）
            if not row['resume_id']:
                if student_id not in students_without_resume:
                    students_without_resume.add(student_id)
                    result_data.append({
                        'user_id': row['user_id'],
                        'username': student_id,
                        'name': row['name'],
                        'className': row['class_name'] or '—',
                        'upload_time': 'N/A',
                        'original_filename': 'N/A',
                        'display_company': '—',
                        'display_job': '—',
                        'display_status': 'not_uploaded' # 未上傳狀態
                    })
                continue

            # 添加所有履歷記錄（不只最新一筆）
            status = row.get('display_status') if row.get('display_status') else 'pending'
            # 將 uploaded 狀態映射為 pending 供前端顯示
            if status == 'uploaded':
                status = 'pending'
            
            result_data.append({
                # 前端下載連結 /api/download_resume/${row.id} 需要的是履歷 ID
                'id': row['resume_id'], 
                'username': student_id,
                'name': row['name'],
                'className': row['class_name'] or '—',
                'upload_time': row['upload_time'].strftime('%Y/%m/%d %H:%M') if isinstance(row['upload_time'], datetime) else (row['upload_time'] if row['upload_time'] else 'N/A'),
                'original_filename': row['original_filename'] or 'N/A',
                'display_company': '—',
                'display_job': '—',
                'display_status': status,
            })
        
        return jsonify({"success": True, "data": result_data})

    except Exception as e:
        # 請確保您已在 resume.py 頂部導入 import traceback
        traceback.print_exc()
        print("❌ 取得待審核履歷列表錯誤:", e)
        return jsonify({"success": False, "message": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()


# -------------------------
# API - 審核履歷 (退件/通過)
# -------------------------
@resume_bp.route('/api/review_resume/<int:resume_id>', methods=['POST'])
def review_resume(resume_id):
    user_id = session.get('user_id')
    user_role = session.get('role')

    # 1. 權限檢查
    ALLOWED_ROLES = ['teacher', 'admin', 'class_teacher', 'vendor']
    if not user_id or user_role not in ALLOWED_ROLES:
        return jsonify({"success": False, "message": "未授權或無權限"}), 403

    data = request.get_json()
    status = data.get('status')
    comment = data.get('comment', '')  # 老師留言

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

        # 3. 更新履歷狀態
        cursor.execute("""
            UPDATE resumes SET 
                status=%s, 
                comment=%s
            WHERE id=%s
        """, (status, comment, resume_id))
        
        # 4. 取得審核者姓名
        cursor.execute("SELECT name, role FROM users WHERE id = %s", (user_id,))
        reviewer = cursor.fetchone()
        if reviewer:
            if reviewer.get('role') == 'vendor':
                reviewer_name = reviewer['name'] if reviewer['name'] else "審核廠商"
            else:
                reviewer_name = reviewer['name'] if reviewer['name'] else "審核老師"
        else:
            reviewer_name = "審核者"

        # 5. 處理 Email 寄送與通知 (僅在狀態改變時處理)
        if old_status != status:
            # =============== 退件 ===============
            if status == 'rejected':
                # 嘗試發送郵件（如果 email_service 存在）
                try:
                    from email_service import send_resume_rejection_email
                    email_success, email_message, log_id = send_resume_rejection_email(
                        student_email, student_name, reviewer_name, comment or "無"
                    )
                    print(f"📧 履歷退件 Email: {email_success}, {email_message}, Log ID: {log_id}")
                except ImportError:
                    print("⚠️ email_service 模組不存在，跳過郵件發送")

                # 🎯 建立退件通知
                notification_content = (
                    f"您的履歷已被 {reviewer_name} 老師退件。\n\n"
                    f"退件原因：{comment if comment else '請查看老師留言'}\n\n"
                    f"請根據老師的建議修改後重新上傳。"
                )

                create_notification(
                    user_id=student_user_id,
                    title="履歷退件通知",
                    message=notification_content,
                    category="resume"
                )
                
                # 🔄 如果是老師退件，將 student_preferences 狀態重置為 'pending'
                if user_role in ['teacher', 'class_teacher']:
                    cursor.execute("""
                        UPDATE student_preferences 
                        SET status = 'pending'
                        WHERE student_id = %s
                        AND status = 'approved'
                    """, (student_user_id,))
                    updated_count = cursor.rowcount
                    if updated_count > 0:
                        print(f"✅ 已將 {updated_count} 筆學生志願序狀態重置為 'pending'，該履歷不會同步到廠商審核頁面")

            # =============== 通過 ===============
            elif status == 'approved':
                # 嘗試發送郵件（如果 email_service 存在）
                try:
                    from email_service import send_resume_approval_email
                    email_success, email_message, log_id = send_resume_approval_email(
                        student_email, student_name, reviewer_name
                    )
                    print(f"📧 履歷通過 Email: {email_success}, {email_message}, Log ID: {log_id}")
                except ImportError:
                    print("⚠️ email_service 模組不存在，跳過郵件發送")

                # 🎯 建立通過通知
                notification_content = (
                    f"恭喜您！您的履歷已由 {reviewer_name} 老師審核通過。\n"
                    f"您可以繼續後續的實習申請流程。"
                )

                create_notification(
                    user_id=student_user_id,
                    title="履歷審核通過通知",
                    message=notification_content,
                    category="resume"
                )
                
                # 🎯 新增邏輯：如果班導通過履歷，將該學生所有志願序狀態從 'pending' 更新為 'approved'
                if user_role in ['teacher', 'class_teacher']:
                    cursor.execute("""
                        UPDATE student_preferences 
                        SET status = 'approved'
                        WHERE student_id = %s
                        AND status = 'pending'
                    """, (student_user_id,))
                    updated_count = cursor.rowcount
                    if updated_count > 0:
                        print(f"✅ 班導通過履歷，已將 {updated_count} 筆學生志願序狀態更新為 'approved'，將同步到指導老師審核頁面")

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
# 頁面路由
# -------------------------
@resume_bp.route('/review_resume')
def review_resume_page():
    # 檢查登入狀態
    if not require_login():
        return redirect('/login')
    
    # 統一使用整合後的審核頁面
    return render_template('resume/review_resume.html')

@resume_bp.route('/class_review_resume')
def class_review_resume_page():
    # 檢查登入狀態
    if not require_login():
        return redirect('/login')
    
    # 班導審核履歷頁面
    return render_template('resume/class_review_resume.html')


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
    target_company_id = request.args.get('company_id', type=int)

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        resumes = []  # 初始化結果列表
        sql_query = ""
        sql_params = tuple()

        print(f"🔍 [DEBUG] get_class_resumes called - user_id: {user_id}, role: {role}, company_id: {target_company_id}")

        # ------------------------------------------------------------------
        # 1. 班導 / 教師 (role == "teacher" or "class_teacher")
        # ------------------------------------------------------------------
        if role in ["teacher", "class_teacher"]:
            sql_query = """
                SELECT DISTINCT
                    r.id,
                    u.id AS user_id,
                    u.name AS student_name,
                    u.username AS student_number,
                    c.name AS class_name,
                    c.department,
                    r.original_filename,
                    r.filepath,
                    r.status,
                    r.comment,
                    r.note,
                    r.created_at,
                    latest_pref.company_name AS company_name,
                    latest_pref.job_title AS job_title,
                    latest_pref.preference_id,
                    latest_pref.preference_order,
                    latest_pref.preference_status,
                    latest_pref.vendor_comment
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                LEFT JOIN (
                    SELECT 
                        sp.student_id,
                        sp.id AS preference_id,
                        sp.preference_order,
                        'pending' AS preference_status,
                        ic.company_name,
                        ij.title AS job_title,
                        ij.id AS job_id,
                        (SELECT vph.comment 
                         FROM vendor_preference_history vph 
                         WHERE vph.preference_id = sp.id 
                         ORDER BY vph.created_at DESC 
                         LIMIT 1) AS vendor_comment
                    FROM student_preferences sp
                    JOIN internship_companies ic ON sp.company_id = ic.id
                    LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                    WHERE ic.advisor_user_id = %s
                    AND sp.status = 'approved'
                    AND sp.id = (
                        SELECT sp2.id
                        FROM student_preferences sp2
                        JOIN internship_companies ic2 ON sp2.company_id = ic2.id
                        WHERE sp2.student_id = sp.student_id
                        AND ic2.advisor_user_id = %s
                        AND sp2.status = 'approved'
                        ORDER BY sp2.preference_order ASC
                        LIMIT 1
                    )
                ) latest_pref ON latest_pref.student_id = u.id
                WHERE r.status = 'approved'
                AND (EXISTS (
                    SELECT 1
                    FROM classes c2
                    JOIN classes_teacher ct ON ct.class_id = c2.id
                    WHERE c2.id = u.class_id AND ct.teacher_id = %s
                ) OR EXISTS (
                    SELECT 1
                    FROM teacher_student_relations tsr
                    WHERE tsr.student_id = u.id AND tsr.teacher_id = %s
                ) OR EXISTS (
                    SELECT 1
                    FROM student_preferences sp
                    JOIN internship_companies ic2 ON sp.company_id = ic2.id
                    WHERE sp.student_id = u.id 
                        AND ic2.advisor_user_id = %s
                        AND sp.status = 'approved'
                ))
            """
            sql_params = (user_id, user_id, user_id, user_id, user_id)
            
            # 如果指定了 company_id，添加額外的篩選條件
            if target_company_id:
                sql_query += " AND latest_pref.preference_id IN (SELECT id FROM student_preferences WHERE company_id = %s)"
                sql_params = sql_params + (target_company_id,)

            cursor.execute(sql_query, sql_params)
            resumes = cursor.fetchall()

            if resumes:
                print(f"✅ [DEBUG] Teacher/class_teacher user {user_id} found {len(resumes)} resumes")
            else:
                print(f"⚠️ [DEBUG] Teacher/class_teacher user {user_id} has no assigned classes or advisor students.")

        # ------------------------------------------------------------------
        # 2. 主任 (role == "director")
        # ------------------------------------------------------------------
        elif role == "director":
            if mode == "director":
                department = get_director_department(cursor, user_id)
                if not department:
                    resumes = []
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
                    cursor.execute(sql_query, sql_params)
                    resumes = cursor.fetchall()
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
                    LEFT JOIN classes c ON u.class_id = c.id
                    JOIN classes_teacher ct ON ct.class_id = c.id
                    WHERE ct.teacher_id = %s
                    ORDER BY c.name, u.name
                """
                sql_params = (user_id,)
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
                    SELECT 1 FROM student_preferences sp
                    JOIN internship_companies ic ON sp.company_id = ic.id
                    WHERE sp.student_id = u.id
                    AND ic.uploaded_by_user_id = %s
                ) OR EXISTS (
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
                r['created_at'] = r['created_at'].strftime("%Y/%m/%d %H:%M")
            # 統一字段名稱，確保前端能正確訪問
            if 'student_name' in r:
                r['name'] = r['student_name']
            if 'student_number' in r:
                r['username'] = r['student_number']
            if 'class_name' in r:
                r['className'] = r['class_name']
            if 'created_at' in r:
                r['upload_time'] = r['created_at']
            # 處理志願序狀態：如果有 preference_status，使用它；否則使用履歷狀態
            if 'preference_status' in r and r.get('preference_status'):
                r['application_statuses'] = r['preference_status']
                r['display_status'] = r['preference_status']
            else:
                r['application_statuses'] = r.get('status', 'pending')
                r['display_status'] = r.get('status', 'pending')
            # 處理留言：如果有 vendor_comment，使用它；否則使用履歷的 comment
            if 'vendor_comment' in r and r.get('vendor_comment'):
                r['comment'] = r['vendor_comment']

        print(f"✅ [DEBUG] Returning {len(resumes)} resumes for role {role}")
        return jsonify({"success": True, "resumes": resumes})

    except Exception as e:
        print("❌ 取得班級履歷資料錯誤：", traceback.print_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()