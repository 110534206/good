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
            -- 1. 證照名稱：優先使用代碼表(cc)，若無則使用資料表(sc)的手填欄位
            CASE 
                WHEN cc.code IS NOT NULL THEN CONCAT(COALESCE(cc.job_category, ''), COALESCE(cc.level, ''))
                ELSE CONCAT(COALESCE(sc.job_category, ''), COALESCE(sc.level, ''))
            END AS cert_name,
            
            -- 2. 類別：若無則歸類為 other
            COALESCE(cc.category, 'other') AS cert_category,
            
            -- 3. 完整顯示名稱 (含發證中心)
            CASE 
                WHEN cc.code IS NOT NULL THEN CONCAT(COALESCE(cc.job_category, ''), COALESCE(cc.level, ''), ' (', COALESCE(ca.name, ''), ')')
                ELSE CONCAT(COALESCE(sc.job_category, ''), COALESCE(sc.level, ''), ' (', COALESCE(IFNULL(ca_sc.name, sc.authority_name), '自填'), ')')
            END AS full_name,
            
            sc.CertPath AS cert_path,
            sc.AcquisitionDate AS acquire_date,
            sc.cert_code AS cert_code
        FROM student_certifications sc
        -- 關聯1: 嘗試透過代碼關聯標準代碼表
        LEFT JOIN certificate_codes cc 
            ON sc.cert_code COLLATE utf8mb4_unicode_ci = cc.code COLLATE utf8mb4_unicode_ci
        -- 關聯2: 透過代碼表找到發證中心 (正常情況)
        LEFT JOIN cert_authorities ca 
            ON cc.authority_id = ca.id
        -- 關聯3: 若代碼關聯失敗，嘗試直接透過 sc.authority_id 關聯發證中心 (補救情況)
        LEFT JOIN cert_authorities ca_sc 
            ON sc.authority_id = ca_sc.id
        WHERE sc.StuID = %s
        ORDER BY sc.AcquisitionDate DESC, sc.id ASC
    """, (student_id,))
    
    cert_rows = cursor.fetchall() or []
    
  # 轉換為統一格式
    certifications = []
    for row in cert_rows:
        cert_code = row.get('cert_code', '')
        cert_name_from_join = row.get('cert_name', '')
        cert_category_from_join = row.get('cert_category', '')
        
        # 預設分類
        category = cert_category_from_join if cert_category_from_join else 'other'

        # =========================================================================
        # 🔥 新增補救邏輯：若分類為 'other'，嘗試用「證照名稱」去資料庫反查正確分類
        # 解決手動輸入正確名稱 (如: 電腦軟體設計乙級) 卻被歸類在「其他」的問題
        # =========================================================================
        if category == 'other':
            # 決定要用來查詢的名稱 (優先使用 SQL 組合出來的名稱，若無則用舊欄位)
            search_name = cert_name_from_join or row.get('CertName', '')
            
            if search_name:
                try:
                    # 使用 CONCAT 模擬資料庫中的名稱格式進行比對
                    cursor.execute("""
                        SELECT category 
                        FROM certificate_codes 
                        WHERE CONCAT(COALESCE(job_category, ''), COALESCE(level, '')) = %s
                        LIMIT 1
                    """, (search_name,))
                    match_row = cursor.fetchone()
                    
                    if match_row and match_row.get('category'):
                        category = match_row['category']
                        print(f"✅ (DOC補救) 成功透過名稱 '{search_name}' 修正分類為: {category}")
                except Exception as e:
                    print(f"⚠️ (DOC補救) 名稱反查失敗: {e}")
        # =========================================================================

        # 組合資料並加入列表
        if cert_name_from_join:
            # 來自新的 SQL 邏輯
            certifications.append({
                "cert_name": cert_name_from_join,
                "category": category, # 使用修正後的 category
                "full_name": row.get('full_name', ''),
                "cert_path": row.get('cert_path', ''),
                "acquire_date": row.get('acquire_date', ''),
            })
            print(f"✅ 證照 JOIN 成功: code={cert_code}, name={cert_name_from_join}, category={category}")
        else:
            # 舊資料兼容邏輯 (若 SQL JOIN 沒產出名稱)
            certifications.append({
                "cert_name": row.get('CertName', ''),
                "category": category, # 使用修正後的 category
                "full_name": row.get('CertName', ''),
                "cert_path": row.get('CertPhotoPath', '') or row.get('cert_path', ''),
                "acquire_date": row.get('AcquisitionDate', '') or row.get('acquire_date', ''),
            })
            print(f"⚠️ 證照 JOIN 失敗，使用回退邏輯: code={cert_code}, category={category}")
    
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
        
        # 檢查是否有 name 欄位（向後兼容）
        cursor.execute("SHOW COLUMNS FROM certificate_codes LIKE 'name'")
        has_name_column = cursor.fetchone() is not None
        
        if has_name_column:
            name_select = "name"
            order_by = "name"
        else:
            # 如果沒有 name 欄位，使用 job_category 和 level 組合
            name_select = "CONCAT(COALESCE(job_category, ''), COALESCE(level, '')) AS name"
            order_by = "COALESCE(job_category, ''), COALESCE(level, '')"
        
        cursor.execute(f"""
            SELECT code, {name_select}, category 
            FROM certificate_codes 
            WHERE authority_id = %s 
            ORDER BY {order_by}
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
# API：取得缺勤可用的學期列表
# -------------------------
@resume_bp.route('/api/absence/available_semesters', methods=['GET'])
def get_absence_available_semesters():
    """取得缺勤可用的學期列表（根據預設範圍過濾）"""
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取學生入學年度（從username前3碼）
        user_id = session['user_id']
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        user_result = cursor.fetchone()
        
        admission_year = None
        if user_result and user_result.get('username'):
            username = user_result['username']
            if len(username) >= 3:
                try:
                    admission_year = int(username[:3])
                except ValueError:
                    pass
        
        # 檢查並獲取預設學期範圍
        cursor.execute("SHOW TABLES LIKE 'absence_default_semester_range'")
        table_exists = cursor.fetchone() is not None
        
        start_semester_code = None
        end_semester_code = None
        
        if table_exists:
            # 檢查表是否有 admission_year 欄位
            cursor.execute("SHOW COLUMNS FROM absence_default_semester_range LIKE 'admission_year'")
            has_admission_year = cursor.fetchone() is not None
            
            if has_admission_year and admission_year:
                cursor.execute("""
                    SELECT start_semester_code, end_semester_code
                    FROM absence_default_semester_range
                    WHERE admission_year = %s
                    ORDER BY id DESC
                    LIMIT 1
                """, (admission_year,))
            else:
                cursor.execute("""
                    SELECT start_semester_code, end_semester_code
                    FROM absence_default_semester_range
                    ORDER BY id DESC
                    LIMIT 1
                """)
            
            range_result = cursor.fetchone()
            if range_result:
                start_semester_code = range_result.get('start_semester_code')
                end_semester_code = range_result.get('end_semester_code')
        
        # 查詢學期列表
        if start_semester_code and end_semester_code:
            # 根據預設範圍過濾學期
            cursor.execute("""
                SELECT id, code, start_date, end_date, is_active
                FROM semesters
                WHERE code >= %s AND code <= %s
                ORDER BY code ASC
            """, (start_semester_code, end_semester_code))
        else:
            # 如果沒有預設範圍，返回所有學期
            cursor.execute("""
                SELECT id, code, start_date, end_date, is_active
                FROM semesters
                ORDER BY code DESC
            """)
        
        semesters = cursor.fetchall()
        
        # 格式化日期
        for s in semesters:
            if isinstance(s.get('start_date'), datetime):
                s['start_date'] = s['start_date'].strftime("%Y-%m-%d")
            if isinstance(s.get('end_date'), datetime):
                s['end_date'] = s['end_date'].strftime("%Y-%m-%d")
            if isinstance(s.get('created_at'), datetime):
                s['created_at'] = s['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        return jsonify({
            "success": True,
            "semesters": semesters
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"取得學期列表失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API：取得缺勤預設學期範圍
# -------------------------
@resume_bp.route('/api/absence/default_range', methods=['GET'])
def get_absence_default_range():
    """取得缺勤預設學期範圍"""
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 檢查表是否存在
        cursor.execute("SHOW TABLES LIKE 'absence_default_semester_range'")
        table_exists = cursor.fetchone() is not None
        
        if not table_exists:
            return jsonify({
                "success": True,
                "defaultStart": "",
                "defaultEnd": ""
            })
        
        # 獲取學生入學年度（從username前3碼）
        user_id = session['user_id']
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        user_result = cursor.fetchone()
        
        admission_year = None
        if user_result and user_result.get('username'):
            username = user_result['username']
            if len(username) >= 3:
                try:
                    admission_year = int(username[:3])
                except ValueError:
                    pass
        
        # 先檢查表是否有 admission_year 欄位
        cursor.execute("SHOW COLUMNS FROM absence_default_semester_range LIKE 'admission_year'")
        has_admission_year = cursor.fetchone() is not None
        
        if has_admission_year and admission_year:
            cursor.execute("""
                SELECT start_semester_code, end_semester_code
                FROM absence_default_semester_range
                WHERE admission_year = %s
                ORDER BY id DESC
                LIMIT 1
            """, (admission_year,))
        else:
            # 如果沒有 admission_year 欄位或沒有入學年度，使用舊邏輯
            cursor.execute("""
                SELECT start_semester_code, end_semester_code
                FROM absence_default_semester_range
                ORDER BY id DESC
                LIMIT 1
            """)
        
        result = cursor.fetchone()
        
        if result:
            return jsonify({
                "success": True,
                "defaultStart": result.get('start_semester_code', ''),
                "defaultEnd": result.get('end_semester_code', '')
            })
        else:
            return jsonify({
                "success": True,
                "defaultStart": "",
                "defaultEnd": ""
            })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"取得預設學期範圍失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# API：設定/保存缺勤預設學期範圍 (新增 POST 請求)
# -------------------------
@resume_bp.route('/api/absence/default_range', methods=['POST'])
def set_absence_default_range():
    """設定/保存缺勤預設學期範圍"""
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401
    
    # 1. 從前端獲取 JSON 資料
    data = request.get_json()
    if not data or 'defaultStart' not in data or 'defaultEnd' not in data:
        return jsonify({"success": False, "message": "缺少必要的參數"}), 400

    start_semester_code = data['defaultStart']
    end_semester_code = data['defaultEnd']
    
    # 2. 獲取 admission_year 邏輯（可選，如果你的 POST 請求也需要這個）
    # 為了簡化，我們先假設 POST 只需要保存設定。
    # 更好的做法是，如果 POST 請求中傳遞了 admission_year，就用它。
    
    # 這裡你需要寫入資料庫的邏輯：
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 3. 執行 SQL 寫入/更新資料庫
        # 假設你的表設計是，每次設定都是新增一筆記錄（如果沒有 admission_year 欄位）
        # 如果你有 admission_year 欄位，你需要執行 UPDATE 或 INSERT ... ON DUPLICATE KEY UPDATE
        
        # 這裡以簡化的 INSERT 為例：
        cursor.execute("""
            INSERT INTO absence_default_semester_range 
            (start_semester_code, end_semester_code) 
            VALUES (%s, %s)
        """, (start_semester_code, end_semester_code))
        
        conn.commit()
        return jsonify({"success": True, "message": "預設學期範圍已保存"}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"保存預設學期範圍失敗: {str(e)}"}), 500
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
        # 使用 job_category 和 level 組合生成 name，如果沒有則使用 name 字段（向後兼容）
        sql_query = """
            SELECT 
                COALESCE(CONCAT(job_category, level), name) AS name, 
                category 
            FROM certificate_codes 
            WHERE code = %s
        """
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
        # 使用 certificate_codes 表的 job_category 和 level 字段組合生成 name
        # 檢查是否有 name 欄位（向後兼容）
        cursor.execute("SHOW COLUMNS FROM certificate_codes LIKE 'name'")
        has_name_column = cursor.fetchone() is not None
        
        if has_name_column:
            # 如果還有 name 欄位，使用 COALESCE 向後兼容
            name_select = "COALESCE(CONCAT(job_category, level), name) AS name"
            order_by = "COALESCE(job_category, name), COALESCE(level, '')"
        else:
            # 如果沒有 name 欄位，直接使用 CONCAT
            name_select = "CONCAT(COALESCE(job_category, ''), COALESCE(level, '')) AS name"
            order_by = "COALESCE(job_category, ''), COALESCE(level, '')"
        
        cursor.execute(f"""
            SELECT code, 
                   {name_select},
                   COALESCE(job_category, '') AS job_category,
                   COALESCE(level, '') AS level
            FROM certificate_codes 
            WHERE authority_id = %s 
            ORDER BY {order_by}
        """, (authority_id,))
        certificates = cursor.fetchall()
        
        # 解析職類和級別
        import re
        job_categories = set()  # 使用 set 避免重複
        job_category_levels = {}  # {職類: [級別列表]}
        
        level_pattern = re.compile(r'(甲級|乙級|丙級|丁級|甲|乙|丙|丁)')
        
        print(f"🔍 查詢發證中心 {authority_id} 的證照，共 {len(certificates)} 筆")
        
        for cert in certificates:
            # 優先使用 certificate_codes 表的 job_category 和 level 字段
            job_category = cert.get('job_category', '').strip()
            level = cert.get('level', '').strip()
            cert_name = cert.get('name', '').strip()
            
            # 情況1: job_category 和 level 都有值，直接使用
            if job_category and level:
                job_categories.add(job_category)
                if job_category not in job_category_levels:
                    job_category_levels[job_category] = set()
                job_category_levels[job_category].add(level)
                print(f"  ✅ 使用欄位值: 職類={job_category}, 級別={level}")
            # 情況2: 只有 job_category 有值（即使沒有 level 也顯示職類）
            elif job_category:
                job_categories.add(job_category)
                if job_category not in job_category_levels:
                    job_category_levels[job_category] = set()
                # 嘗試從 name 解析 level（如果有的話）
                if not level and cert_name:
                    match = level_pattern.search(cert_name)
                    if match:
                        parsed_level = match.group(1)
                        level_map = {'甲': '甲級', '乙': '乙級', '丙': '丙級', '丁': '丁級'}
                        full_level = level_map.get(parsed_level, parsed_level)
                        job_category_levels[job_category].add(full_level)
                        print(f"  ✅ 職類有值，從名稱解析級別: 職類={job_category}, 級別={full_level}")
                    else:
                        print(f"  ✅ 職類有值，無級別: 職類={job_category}")
                elif level:
                    job_category_levels[job_category].add(level)
                    print(f"  ✅ 職類和級別都有值: 職類={job_category}, 級別={level}")
                else:
                    print(f"  ✅ 職類有值，無級別: 職類={job_category}")
            # 情況3: 只有 level 有值，嘗試從 name 解析 job_category
            elif level and not job_category and cert_name:
                # 從名稱中移除級別，剩下的作為職類
                parsed_job_category = level_pattern.sub('', cert_name).strip()
                if parsed_job_category:
                    job_categories.add(parsed_job_category)
                    if parsed_job_category not in job_category_levels:
                        job_category_levels[parsed_job_category] = set()
                    job_category_levels[parsed_job_category].add(level)
                    print(f"  ✅ 級別有值，從名稱解析職類: 職類={parsed_job_category}, 級別={level}")
            # 情況4: 都沒有值，從 name 字段解析職類和級別（向後兼容）
            elif cert_name:
                match = level_pattern.search(cert_name)
                if match:
                    parsed_level = match.group(1)
                    level_map = {'甲': '甲級', '乙': '乙級', '丙': '丙級', '丁': '丁級'}
                    full_level = level_map.get(parsed_level, parsed_level)
                    
                    # 提取職類（移除級別後的部分）
                    parsed_job_category = level_pattern.sub('', cert_name).strip()
                    
                    if parsed_job_category:
                        job_categories.add(parsed_job_category)
                        if parsed_job_category not in job_category_levels:
                            job_category_levels[parsed_job_category] = set()
                        job_category_levels[parsed_job_category].add(full_level)
                        print(f"  ✅ 從名稱解析: 職類={parsed_job_category}, 級別={full_level}")
                else:
                    # 如果無法解析級別，但名稱不為空，將整個名稱作為職類（無級別）
                    job_categories.add(cert_name)
                    if cert_name not in job_category_levels:
                        job_category_levels[cert_name] = set()
                    print(f"  ✅ 從名稱解析（無級別）: 職類={cert_name}")
            else:
                print(f"  ⚠️ 跳過無效證照記錄: code={cert.get('code')}, name={cert_name}")
        
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
        # ===== 1. 檢查是否有已提交履歷 =====
        cursor.execute("""
            SELECT id FROM resumes 
            WHERE user_id = %s 
            ORDER BY created_at DESC 
            LIMIT 1
        """, (user_id,))
        resume = cursor.fetchone()

        if not resume:
            return jsonify({"success": False, "message": "沒有已提交的履歷"}), 404

        # ===== 2. 抓 StudentID（學號）=====
        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        user_result = cursor.fetchone()
        if not user_result:
            return jsonify({"success": False, "message": "找不到使用者"}), 404

        student_id = user_result["username"]

        # ===== 3. 基本資料 =====
        cursor.execute("SELECT * FROM Student_Info WHERE StuID=%s", (student_id,))
        student_info = cursor.fetchone() or {}

        # ===== 4. 課程資料 =====
        # 檢查是否有 ProofImage 欄位
        cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'ProofImage'")
        has_proof_image = cursor.fetchone() is not None
        
        if has_proof_image:
            cursor.execute("""
                SELECT CourseName AS name, Credits AS credits, Grade AS grade, ProofImage AS transcript_path
                FROM course_grades
                WHERE StuID=%s
                ORDER BY CourseName
            """, (student_id,))
        else:
            cursor.execute("""
                SELECT CourseName AS name, Credits AS credits, Grade AS grade
                FROM course_grades
                WHERE StuID=%s
                ORDER BY CourseName
            """, (student_id,))
        courses = cursor.fetchall() or []
        
        # 提取成績單路徑（從 ProofImage 欄位）
        transcript_path = ''
        for course in courses:
            tp = course.get('transcript_path')
            if tp:
                transcript_path = tp
                break

        # ===== 5. 證照資料 — 單一 SQL，不再三段重複 =====

        sql_cert = """
            SELECT
                sc.id,
                sc.CertPath,
                sc.AcquisitionDate,
                sc.cert_code,
                sc.issuer,
                sc.authority_name,
                sc.job_category AS sc_job_category,
                sc.CreatedAt,
                
                -- 發證中心ID：優先使用 sc.authority_id（如果存在），否則從 certificate_codes 獲取
                COALESCE(
                    sc.authority_id,
                    CASE 
                        WHEN sc.cert_code IS NOT NULL 
                             AND BINARY sc.cert_code != BINARY 'OTHER'
                             AND sc.cert_code != ''
                        THEN cc.authority_id
                        ELSE NULL
                    END
                ) AS authority_id,

                -- 職類：若 cert_code 有值且不是 OTHER → 取 certificate_codes
                CASE 
                    WHEN sc.cert_code IS NOT NULL 
                         AND BINARY sc.cert_code != BINARY 'OTHER'
                         AND sc.cert_code != ''
                    THEN COALESCE(cc.job_category, '')
                    ELSE COALESCE(sc.job_category, '')
                END AS job_category,

                -- 等級
                CASE 
                    WHEN sc.cert_code IS NOT NULL 
                         AND BINARY sc.cert_code != BINARY 'OTHER'
                         AND sc.cert_code != ''
                    THEN COALESCE(cc.level, '')
                    ELSE COALESCE(sc.level, '')
                END AS level,

                -- 組合證照名稱
                CASE 
                    WHEN (
                        CASE 
                            WHEN sc.cert_code IS NOT NULL 
                                 AND BINARY sc.cert_code != BINARY 'OTHER'
                                 AND sc.cert_code != ''
                            THEN cc.job_category
                            ELSE sc.job_category
                        END
                    ) IS NOT NULL
                    AND (
                        CASE 
                            WHEN sc.cert_code IS NOT NULL 
                                 AND BINARY sc.cert_code != BINARY 'OTHER'
                                 AND sc.cert_code != ''
                            THEN cc.level
                            ELSE sc.level
                        END
                    ) IS NOT NULL
                    AND (
                        CASE 
                            WHEN sc.cert_code IS NOT NULL 
                                 AND BINARY sc.cert_code != BINARY 'OTHER'
                                 AND sc.cert_code != ''
                            THEN cc.job_category
                            ELSE sc.job_category
                        END
                    ) != ''
                    AND (
                        CASE 
                            WHEN sc.cert_code IS NOT NULL 
                                 AND BINARY sc.cert_code != BINARY 'OTHER'
                                 AND sc.cert_code != ''
                            THEN cc.level
                            ELSE sc.level
                        END
                    ) != ''
                THEN CONCAT(
                    CASE 
                        WHEN sc.cert_code IS NOT NULL 
                             AND BINARY sc.cert_code != BINARY 'OTHER'
                             AND sc.cert_code != ''
                        THEN cc.job_category
                        ELSE sc.job_category
                    END,
                    CASE 
                        WHEN sc.cert_code IS NOT NULL 
                             AND BINARY sc.cert_code != BINARY 'OTHER'
                             AND sc.cert_code != ''
                        THEN cc.level
                        ELSE sc.level
                    END
                )
                ELSE ''
                END AS CertName,

                -- 發證中心名稱：優先使用 sc.authority_id 關聯的 cert_authorities，否則使用從 certificate_codes 獲取的 authority_id，最後使用 authority_name
                COALESCE(
                    ca_from_sc.name,
                    ca.name, 
                    sc.authority_name, 
                    'N/A'
                ) AS IssuingBody,
                COALESCE(cc.category, 'other') AS CertType
            FROM student_certifications sc
            LEFT JOIN certificate_codes cc 
                ON sc.cert_code COLLATE utf8mb4_unicode_ci = cc.code COLLATE utf8mb4_unicode_ci
            LEFT JOIN cert_authorities ca 
                ON cc.authority_id = ca.id
            LEFT JOIN cert_authorities ca_from_sc 
                ON sc.authority_id = ca_from_sc.id
            WHERE sc.StuID = %s
            ORDER BY sc.id DESC
        """

        cursor.execute(sql_cert, (student_id,))
        all_certifications = cursor.fetchall() or []
        
        # 調試：打印查詢結果，確認 level 字段
        print(f"🔍 查詢證照資料: 共 {len(all_certifications)} 筆")
        for idx, cert in enumerate(all_certifications[:3]):  # 只打印前3筆
            print(f"  證照 {idx+1}: id={cert.get('id')}, cert_code={cert.get('cert_code')}, job_category={cert.get('job_category')}, level={cert.get('level')}, authority_id={cert.get('authority_id')}")

        # ===== 6. 取最新一批證照 =====

        certifications = []
        if all_certifications:
            latest_created_at = all_certifications[0]["CreatedAt"]
            latest_id = all_certifications[0]["id"]

            if latest_created_at:
                certifications = [
                    c for c in all_certifications
                    if c["CreatedAt"] == latest_created_at
                ]
            else:
                max_id = latest_id
                certifications = [
                    c for c in all_certifications
                    if c["id"] >= (max_id - 50)
                ]

            # 過濾空白資料
            certifications = [
                c for c in certifications
                if (
                    (c["job_category"] and c["level"]) or
                    (c["CertName"]) or
                    (c["cert_code"] and c["cert_code"] != "OTHER")
                )
            ]

        # ===== 7. 語言能力 =====

        cursor.execute("""
            SELECT Language AS language, Level AS level
            FROM student_languageskills
            WHERE StuID=%s
            ORDER BY Language
        """, (student_id,))
        languages = cursor.fetchall() or []
        
        # ===== 7.5 缺勤記錄佐證圖片 =====
        # 從 absence_records 表獲取最新的 image_path
        absence_proof_path = ''
        try:
            cursor.execute("SELECT id FROM users WHERE username=%s", (student_id,))
            user_row = cursor.fetchone()
            if user_row:
                user_id = user_row.get('id')
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
                    absence_proof_path = absence_row.get('image_path', '')
                    print(f"🔍 找到缺勤佐證圖片: {absence_proof_path}")
        except Exception as e:
            print(f"⚠️ 查詢缺勤佐證圖片失敗: {e}")
            traceback.print_exc()

        # ===== 8. 日期格式轉換 =====
        birth_date = student_info.get("BirthDate")
        if birth_date:
            if isinstance(birth_date, datetime):
                birth_date = birth_date.strftime("%Y-%m-%d")
            else:
                try:
                    birth_date = datetime.strptime(birth_date, "%Y-%m-%d").strftime("%Y-%m-%d")
                except:
                    pass

        # ===== 9. 格式化證照輸出 =====
        formatted_certs = []
        for cert in certifications:
            acquire_date = cert.get("AcquisitionDate")
            formatted_acquire_date = ""
            acquisition_date_str = None  # 用於 JSON 序列化的字符串格式
            
            if acquire_date is not None:
                if isinstance(acquire_date, datetime):
                    formatted_acquire_date = acquire_date.strftime("%Y-%m-%d")
                    acquisition_date_str = formatted_acquire_date
                elif isinstance(acquire_date, date):
                    formatted_acquire_date = acquire_date.strftime("%Y-%m-%d")
                    acquisition_date_str = formatted_acquire_date
                elif acquire_date:
                    try:
                        # 嘗試解析字符串格式的日期
                        if isinstance(acquire_date, str):
                            formatted_acquire_date = datetime.strptime(acquire_date, "%Y-%m-%d").strftime("%Y-%m-%d")
                            acquisition_date_str = formatted_acquire_date
                        else:
                            formatted_acquire_date = str(acquire_date)
                            acquisition_date_str = formatted_acquire_date
                    except Exception as e:
                        print(f"⚠️ 日期格式化失敗: {acquire_date}, 錯誤: {e}")
                        formatted_acquire_date = str(acquire_date) if acquire_date else ""
                        acquisition_date_str = formatted_acquire_date
            
            # 獲取級別字段（SQL 返回的字段名是 level）
            cert_level = cert.get("level", "")
            print(f"🔍 證照資料處理: id={cert.get('id')}, AcquisitionDate={acquire_date}, formatted={formatted_acquire_date}, level={cert_level}, job_category={cert.get('job_category', '')}")
            
            # 獲取證照圖片路徑，並將 Windows 路徑格式（反斜杠）轉換為 Web 路徑格式（正斜杠）
            cert_path_raw = cert.get("CertPath", "")
            cert_path = cert_path_raw.replace("\\", "/") if cert_path_raw else ""
            
            formatted_certs.append({
                "id": cert["id"],
                "cert_code": cert.get("cert_code", ""),
                "cert_path": cert_path,
                "name": cert.get("CertName", ""),
                "job_category": cert.get("job_category", ""),
                "level": cert_level,  # 修正：SQL 返回的字段名是 level，不是 CertLevel
                "authority_name": cert.get("authority_name", ""),
                "issuer": cert.get("issuer", ""),
                "authority_id": cert.get("authority_id") if "authority_id" in cert else None,
                "IssuingBody": cert.get("IssuingBody", ""),
                "CertType": cert.get("CertType", "other"),
                "acquire_date": formatted_acquire_date,
                "AcquisitionDate": acquisition_date_str  # 轉換為字符串格式，確保 JSON 序列化正常
            })

        # ===== 10. 回傳結果 =====
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
            # 合併查詢：班導的學生履歷 + 指導老師綁定公司的學生履歷
            # 使用 UNION 合併三種情況：
            # 1. 班導的學生（通過 classes_teacher）
            # 2. 指導老師綁定的學生（從 teacher_student_relations）
            # 3. 選擇了該老師作為指導老師的公司的學生（通過 student_preferences 和 internship_companies）
            #    重點：學生的履歷會根據填寫的志願序，傳給選擇公司的指導老師
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
                               SELECT 
                            sp.student_id,
                            sp.id AS preference_id,
                            sp.preference_order,
                            'pending' AS preference_status,  -- 指導老師看到的初始狀態為待審核
                            ic.company_name,
                            ij.title AS job_title,
                            (SELECT vph.comment 
                             FROM vendor_preference_history vph 
                             WHERE vph.preference_id = sp.id 
                             ORDER BY vph.created_at DESC 
                             LIMIT 1) AS vendor_comment
                        FROM student_preferences sp
                        JOIN internship_companies ic ON sp.company_id = ic.id
                        LEFT JOIN internship_jobs ij ON sp.job_id = ij.id
                        WHERE ic.advisor_user_id = %s
                        AND sp.status = 'approved'  -- 只顯示班導已審核通過的志願序
                        AND sp.id = (
                            -- 獲取該學生選擇該老師管理的公司中，preference_order 最小的志願序
                            SELECT sp2.id
                            FROM student_preferences sp2
                            JOIN internship_companies ic2 ON sp2.company_id = ic2.id
                            WHERE sp2.student_id = sp.student_id
                            AND ic2.advisor_user_id = %s
                            AND sp2.status = 'approved'  -- 只考慮班導已審核通過的志願序
                            ORDER BY sp2.preference_order ASC
                            LIMIT 1
                        )
                    ) latest_pref ON latest_pref.student_id = u.id
                    WHERE r.status = 'approved'  -- 只顯示班導已審核通過的履歷
                    AND (EXISTS (
                    -- 情況1：班導的學生
                    SELECT 1
                    FROM classes c2
                    JOIN classes_teacher ct ON ct.class_id = c2.id
                    WHERE c2.id = u.class_id AND ct.teacher_id = %s
                ) OR EXISTS (
                    -- 情況2：指導老師綁定的學生（從 teacher_student_relations）
                    SELECT 1
                    FROM teacher_student_relations tsr
                    WHERE tsr.student_id = u.id AND tsr.teacher_id = %s
                ) OR EXISTS (
                    -- 情況3：選擇了該老師作為指導老師的公司的學生
                    -- 重點：學生的履歷會根據填寫的志願序，傳給選擇公司的指導老師
                    -- 只有班導已審核通過的志願序和履歷，指導老師才能看到

                    SELECT 1
                    FROM student_preferences sp
                    JOIN internship_companies ic2 ON sp.company_id = ic2.id
                    WHERE sp.student_id = u.id 
                        AND ic2.advisor_user_id = %s
                        AND sp.status = 'approved'  -- 只顯示班導已審核通過的志願序
                    ))
                ORDER BY c.name, u.name
            """
            sql_params = (user_id, user_id, user_id, user_id, user_id)

            cursor.execute(sql_query, sql_params)
            resumes = cursor.fetchall()

            # 調試：記錄查詢結果
            if resumes:
                print(f"✅ [DEBUG] Teacher/class_teacher user {user_id} found {len(resumes)} resumes")
                # 統計有多少履歷是通過「選擇了該老師管理的公司」這個條件出現的
                company_based_count = sum(1 for r in resumes if r.get('company_name'))
                print(f"📊 [DEBUG] {company_based_count} resumes are from students who selected companies managed by this teacher")
                        # 統計顯示的公司和職缺
                companies_shown = set()
                jobs_shown = set()
                for r in resumes:
                    if r.get('company_name'):
                        companies_shown.add(r.get('company_name'))
                    if r.get('job_title'):
                        jobs_shown.add(r.get('job_title'))
                print(f"📊 [DEBUG] Companies shown: {len(companies_shown)} - {sorted(companies_shown)}")
                print(f"📊 [DEBUG] Jobs shown: {len(jobs_shown)} - {sorted(jobs_shown)}")
            else:
                print(f"⚠️ [DEBUG] Teacher/class_teacher user {user_id} has no assigned classes or advisor students.")
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
            # 處理留言：如果有 vendor_comment，使用它；否則使用履歷的 comment
            if 'vendor_comment' in r and r.get('vendor_comment'):
                r['comment'] = r['vendor_comment']      

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
    ALLOWED_ROLES = ['teacher', 'admin', 'class_teacher', 'vendor']
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
                    message=notification_content,
                    category="resume"
                )
             # 🔄 如果是老師退件，將 student_preferences 狀態重置為 'pending'，避免同步到廠商審核頁面
                if user_role in ['teacher', 'class_teacher']:
                    # 將該學生所有志願序的狀態重置為 'pending'，這樣就不會顯示在廠商審核頁面
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
                    message=notification_content,
                    category="resume"
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
                c.class_name,
                c.department_id,  -- 假設 classes 表中有 department_id 欄位
                r.id AS resume_id,
                r.upload_time,
                r.original_filename,
                r.display_company,
                r.display_job,
                r.display_status
            FROM users u
            JOIN classes c ON u.class_id = c.id
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
            director_dept_id = get_director_department(cursor, session_user_id)
            if not director_dept_id:
                # 主任沒有設定部門，則返回空列表
                return jsonify({"success": True, "data": [], "message": "主任未設定所屬部門，無法查詢"}), 200
            
            # 假設 classes 表中有 department_id 欄位
            sql += " AND c.department_id = %s" 
            params.append(director_dept_id)
        
        # 排序：按照班級、姓名、上傳時間（最新在上）
        sql += " ORDER BY c.class_name, u.username, r.upload_time DESC"

        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
        
        # 整理結果：確保每個學生只顯示最新的履歷記錄
        latest_resumes = {}
        for row in rows:
            student_id = row['student_id']
            
            # 處理未上傳履歷的學生
            if not row['resume_id']:
                if student_id not in latest_resumes:
                    latest_resumes[student_id] = {
                        'user_id': row['user_id'],
                        'username': student_id,
                        'name': row['name'],
                        'class_name': row['class_name'],
                        'upload_time': 'N/A',
                        'original_filename': 'N/A',
                        'display_company': 'N/A',
                        'display_job': 'N/A',
                        'display_status': 'not_uploaded' # 未上傳狀態
                    }
                continue

            # 只保留該學生的最新一筆履歷記錄 (根據 resume_id，因為 SQL 排序了)
            if student_id not in latest_resumes or row['resume_id'] > latest_resumes[student_id].get('resume_id', 0):
                status = row.get('display_status') if row.get('display_status') else 'pending'
                
                latest_resumes[student_id] = {
                    # 前端下載連結 /api/download_resume/${row.id} 需要的是履歷 ID
                    'id': row['resume_id'], 
                    'username': student_id,
                    'name': row['name'],
                    'class_name': row['class_name'],
                    'upload_time': row['upload_time'].strftime('%Y-%m-%d %H:%M:%S') if row['upload_time'] else 'N/A',
                    'original_filename': row['original_filename'],
                    'display_company': row['display_company'] or '—',
                    'display_job': row['display_job'] or '—',
                    'display_status': status,
                }
        
        # 將字典的值轉換為列表
        result_data = list(latest_resumes.values())
        
        return jsonify({"success": True, "data": result_data})

    except Exception as e:
        # 請確保您已在 resume.py 頂部導入 import traceback
        # traceback.print_exc()
        print("❌ 取得待審核履歷列表錯誤:", e)
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
# 科助上傳標準課程Excel（預覽）
# -------------------------
@resume_bp.route('/api/ta/preview_standard_courses', methods=['POST'])
def preview_standard_courses():
    """科助預覽標準課程Excel文件"""
    if 'user_id' not in session or session.get('role') != 'ta':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "未找到上傳文件"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "文件名稱不能為空"}), 400
    
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({"success": False, "message": "只支援Excel文件(.xlsx, .xls)"}), 400
    
    try:
        file_content = file.read()
        wb = load_workbook(io.BytesIO(file_content), data_only=False)
        ws = wb.active
        
        def get_cell_value(cell):
            """獲取單元格值，處理日期格式問題"""
            if cell is None or cell.value is None:
                return None
            value = cell.value
            if isinstance(value, datetime):
                month = value.month
                day = value.day
                return f"{month}/{day}"
            return value
        
        courses = []
        for row_idx in range(2, ws.max_row + 1):
            cell_name = ws.cell(row=row_idx, column=1)
            cell_credits = ws.cell(row=row_idx, column=2)
            
            course_name = get_cell_value(cell_name)
            credits_raw = cell_credits.value
            
            if not course_name or str(course_name).strip() == '':
                continue
            
            course_name = str(course_name).strip()
            
            # 處理學分數
            credits_str = ''
            if credits_raw is not None:
                if isinstance(credits_raw, datetime):
                    month = credits_raw.month
                    day = credits_raw.day
                    credits_str = f"{month}/{day}"
                elif isinstance(credits_raw, str):
                    credits_str = credits_raw.strip()
                    if ('2025-' in credits_str or '2024-' in credits_str or '2026-' in credits_str) and ('-' in credits_str):
                        try:
                            date_part = credits_str.split()[0] if ' ' in credits_str else credits_str
                            date_obj = datetime.strptime(date_part, '%Y-%m-%d')
                            month = date_obj.month
                            day = date_obj.day
                            credits_str = f"{month}/{day}"
                        except:
                            # 解析失敗，使用format_credits格式化
                            credits_str = format_credits(credits_str)
                    else:
                        # 不是日期格式，使用format_credits格式化
                        credits_str = format_credits(credits_str)
                else:
                    credits_str = format_credits(credits_raw)
            
            courses.append({
                'name': course_name,
                'credits': credits_str
            })
        
        return jsonify({
            "success": True,
            "courses": courses,
            "message": f"成功解析 {len(courses)} 門課程"
        })
    except Exception as e:
        print("❌ 預覽Excel錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"解析Excel失敗: {str(e)}"}), 500

# -------------------------
# 科助上傳標準課程Excel（寫入資料庫）
# -------------------------
@resume_bp.route('/api/ta/upload_standard_courses', methods=['POST'])
def upload_standard_courses():
    """科助上傳標準課程Excel並寫入standard_courses表"""
    if 'user_id' not in session or session.get('role') != 'ta':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "未找到上傳文件"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "文件名稱不能為空"}), 400
    
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({"success": False, "message": "只支援Excel文件(.xlsx, .xls)"}), 400
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        file_content = file.read()
        wb = load_workbook(io.BytesIO(file_content), data_only=False)
        ws = wb.active
        
        def get_cell_value(cell):
            if cell is None or cell.value is None:
                return None
            value = cell.value
            if isinstance(value, datetime):
                month = value.month
                day = value.day
                return f"{month}/{day}"
            return value
        
        courses = []
        for row_idx in range(2, ws.max_row + 1):
            cell_name = ws.cell(row=row_idx, column=1)
            cell_credits = ws.cell(row=row_idx, column=2)
            
            course_name = get_cell_value(cell_name)
            credits_raw = cell_credits.value
            
            if not course_name or str(course_name).strip() == '':
                continue
            
            course_name = str(course_name).strip()
            
            # 處理學分數
            credits_str = ''
            if credits_raw is not None:
                if isinstance(credits_raw, datetime):
                    month = credits_raw.month
                    day = credits_raw.day
                    credits_str = f"{month}/{day}"
                elif isinstance(credits_raw, str):
                    credits_str = credits_raw.strip()
                    if ('2025-' in credits_str or '2024-' in credits_str or '2026-' in credits_str) and ('-' in credits_str):
                        try:
                            date_part = credits_str.split()[0] if ' ' in credits_str else credits_str
                            date_obj = datetime.strptime(date_part, '%Y-%m-%d')
                            month = date_obj.month
                            day = date_obj.day
                            credits_str = f"{month}/{day}"
                        except:
                            # 解析失敗，使用format_credits格式化
                            credits_str = format_credits(credits_str)
                    else:
                        # 不是日期格式，使用format_credits格式化
                        credits_str = format_credits(credits_str)
                else:
                    credits_str = format_credits(credits_raw)
            
            courses.append({
                'name': course_name,
                'credits': credits_str
            })
        
        if len(courses) == 0:
            return jsonify({"success": False, "message": "Excel文件中沒有找到課程資料"}), 400
        
        # 保存上傳的Excel文件
        # 獲取項目根目錄（backend的父目錄）
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        upload_base_dir = os.path.join(project_root, 'uploads', 'standard_courses')
        os.makedirs(upload_base_dir, exist_ok=True)
        
        print(f"📁 項目根目錄: {project_root}")
        print(f"📁 上傳目錄: {upload_base_dir}")
        
        # 生成安全的文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # 先從原始文件名提取擴展名
        original_filename = file.filename if file.filename else 'upload.xlsx'
        original_ext = os.path.splitext(original_filename)[1].lower()
        if not original_ext or original_ext not in ['.xlsx', '.xls']:
            original_ext = '.xlsx'  # 默認使用 .xlsx
        
        # 處理文件名：移除擴展名，使用secure_filename處理，然後重新添加擴展名
        filename_without_ext = os.path.splitext(original_filename)[0]
        if not filename_without_ext or filename_without_ext.strip() == '':
            filename_without_ext = 'upload'
        
        safe_basename = secure_filename(filename_without_ext)
        if not safe_basename or safe_basename.strip() == '':
            safe_basename = 'upload'
        
        # 確保最終文件名包含擴展名
        safe_filename = safe_basename + original_ext
        filename = f"{timestamp}_{safe_filename}"
        
        # 完整的絕對路徑（用於保存文件）
        abs_file_path = os.path.join(upload_base_dir, filename)
        
        # 相對路徑（用於存儲到數據庫）
        db_file_path = os.path.join('uploads', 'standard_courses', filename).replace('\\', '/')
        
        print(f"📝 文件上傳信息:")
        print(f"  - 原始文件名: {original_filename}")
        print(f"  - 提取的擴展名: {original_ext}")
        print(f"  - 安全的文件名: {safe_filename}")
        print(f"  - 最終文件名: {filename}")
        print(f"  - 絕對保存路徑: {abs_file_path}")
        print(f"  - 數據庫路徑: {db_file_path}")
        
        # 保存文件
        file.seek(0)  # 重置文件指針
        os.makedirs(os.path.dirname(abs_file_path), exist_ok=True)
        with open(abs_file_path, 'wb') as f:
            f.write(file_content)
        
        print(f"✅ 文件已保存到: {abs_file_path}")
        # 驗證文件是否真的保存成功
        if os.path.exists(abs_file_path):
            file_size = os.path.getsize(abs_file_path)
            print(f"✅ 文件保存成功，大小: {file_size} bytes")
        else:
            print(f"❌ 警告：文件保存後無法找到！")
        
        # 檢查並創建 uploaded_course_templates 表（如果不存在）
        cursor.execute("SHOW TABLES LIKE 'uploaded_course_templates'")
        has_template_table = cursor.fetchone() is not None
        
        if not has_template_table:
            # 創建 uploaded_course_templates 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS uploaded_course_templates (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    file_path VARCHAR(500) NOT NULL,
                    uploaded_by INT NULL,
                    uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_uploaded_at (uploaded_at),
                    INDEX idx_file_path (file_path)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            print("✅ 已創建 uploaded_course_templates 表")
        
        # 先將舊資料標記為非活躍（不直接刪除，保留歷史）
        cursor.execute("UPDATE standard_courses SET is_active = 0")
        
        # 重新插入Excel中的課程（不包含文件路徑）
        insert_count = 0
        for idx, course in enumerate(courses, 1):
            try:
                cursor.execute("""
                    INSERT INTO standard_courses (course_name, credits, order_index, is_active, created_at)
                    VALUES (%s, %s, %s, 1, NOW())
                """, (course['name'], course['credits'], idx))
                insert_count += 1
            except Exception as e:
                print(f"⚠️ 插入課程失敗: {course['name']}, 錯誤: {e}")
                # 繼續插入其他課程，不中斷
                continue
        
        # 將文件路徑保存到 uploaded_course_templates 表
        template_id = None
        try:
            cursor.execute("""
                INSERT INTO uploaded_course_templates (file_path, uploaded_by, uploaded_at)
                VALUES (%s, %s, NOW())
            """, (db_file_path, session['user_id']))
            cursor.execute("SELECT LAST_INSERT_ID() as id")
            result = cursor.fetchone()
            if result:
                template_id = result['id']
            print(f"✅ 已保存文件路徑到 uploaded_course_templates 表，ID: {template_id}, 文件路徑: {db_file_path}, 課程數: {insert_count}")
        except Exception as e:
            print(f"⚠️ 保存文件路徑失敗: {e}")
            traceback.print_exc()
        
        print(f"✅ 已插入 {insert_count} 門課程到 standard_courses 表")
        
        # 確保事務提交
        try:
            conn.commit()
            print(f"✅ 成功更新 standard_courses 表，插入 {insert_count} 門課程")
            print(f"✅ 文件已保存到: {abs_file_path}")
            
            # 驗證更新是否成功
            cursor.execute("SELECT COUNT(*) as count FROM standard_courses WHERE is_active = 1")
            verify_result = cursor.fetchone()
            active_count = verify_result['count'] if verify_result else 0
            print(f"✅ 驗證：standard_courses 表中 is_active=1 的記錄數: {active_count}")
            
            # 驗證文件路徑是否正確保存到 uploaded_course_templates 表
            if template_id:
                cursor.execute("SELECT * FROM uploaded_course_templates WHERE id = %s", (template_id,))
                verify_template = cursor.fetchone()
                if verify_template:
                    print(f"✅ 驗證：文件路徑已保存到 uploaded_course_templates 表，ID: {template_id}, 文件路徑: {verify_template.get('file_path', 'N/A')}")
                else:
                    print(f"⚠️ 警告：uploaded_course_templates 表記錄ID {template_id} 未找到")
            
            return jsonify({
                "success": True,
                "count": insert_count,
                "message": f"成功上傳 {insert_count} 門課程",
                "file_path": db_file_path
            })
        except Exception as commit_error:
            conn.rollback()
            print(f"❌ 提交事務失敗: {commit_error}")
            traceback.print_exc()
            raise commit_error
    except Exception as e:
        conn.rollback()
        print("❌ 上傳標準課程錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"上傳失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 科助取得標準課程上傳歷史
# -------------------------
@resume_bp.route('/api/ta/get_standard_courses_history', methods=['GET'])
def get_standard_courses_history():
    """取得標準課程上傳歷史記錄"""
    if 'user_id' not in session or session.get('role') != 'ta':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 檢查 uploaded_course_templates 表是否存在
        cursor.execute("SHOW TABLES LIKE 'uploaded_course_templates'")
        has_template_table = cursor.fetchone() is not None
        
        if has_template_table:
            # 從 uploaded_course_templates 表獲取歷史記錄
            # 並從 standard_courses 表計算每次上傳的課程數量（根據上傳日期匹配）
            cursor.execute("""
                SELECT 
                    t.id,
                    t.file_path,
                    t.uploaded_by,
                    t.uploaded_at,
                    COALESCE(COUNT(DISTINCT s.id), 0) as course_count
                FROM uploaded_course_templates t
                LEFT JOIN standard_courses s ON DATE(s.created_at) = DATE(t.uploaded_at)
                    AND s.is_active = 1
                GROUP BY t.id, t.file_path, t.uploaded_by, t.uploaded_at
                ORDER BY t.uploaded_at DESC
                LIMIT 20
            """)
            history = cursor.fetchall()
            # 調試：打印查詢結果
            print(f"🔍 從 uploaded_course_templates 表查詢到 {len(history)} 筆歷史記錄")
            for record in history:
                print(f"  - ID: {record.get('id')}, 文件路徑: {record.get('file_path', 'NULL')}, 課程數: {record.get('course_count', 0)}")
        else:
            # 如果表不存在，返回空列表
            print("⚠️ uploaded_course_templates 表不存在")
            history = []
        
        return jsonify({
            "success": True,
            "history": history
        })
    except Exception as e:
        print("❌ 取得上傳歷史錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"取得歷史記錄失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 科助下載標準課程Excel文件
# -------------------------
@resume_bp.route('/api/ta/download_standard_course_file/<int:history_id>', methods=['GET'])
def download_standard_course_file(history_id):
    """下載上傳的Excel文件（從uploaded_course_templates表）"""
    if 'user_id' not in session or session.get('role') != 'ta':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 從 uploaded_course_templates 表獲取文件路徑
        cursor.execute("""
            SELECT file_path 
            FROM uploaded_course_templates 
            WHERE id = %s
        """, (history_id,))
        record = cursor.fetchone()
        
        if not record or not record.get('file_path'):
            return jsonify({"success": False, "message": "找不到文件"}), 404
        
        file_path = record.get('file_path')
        
        # 處理相對路徑 - 從項目根目錄開始
        if not os.path.isabs(file_path):
            # 獲取項目根目錄（backend的父目錄）
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            abs_file_path = os.path.join(project_root, file_path)
        else:
            abs_file_path = file_path
        
        # 標準化路徑分隔符
        # abs_file_path = os.path.normpath(abs_file_path)
        
        print(f"🔍 嘗試下載文件: {abs_file_path}")
        
        # 檢查文件是否存在，如果不存在，嘗試多種方式查找
        if not os.path.exists(abs_file_path):
            print(f"⚠️ 文件不存在，嘗試查找相似文件...")
            
            # 方法1：嘗試添加 .xlsx 擴展名
            abs_file_path_xlsx = abs_file_path + '.xlsx'
            abs_file_path_xls = abs_file_path + '.xls'
            
            if os.path.exists(abs_file_path_xlsx):
                print(f"✅ 找到文件（添加.xlsx後）: {abs_file_path_xlsx}")
                abs_file_path = abs_file_path_xlsx
            elif os.path.exists(abs_file_path_xls):
                print(f"✅ 找到文件（添加.xls後）: {abs_file_path_xls}")
                abs_file_path = abs_file_path_xls
            else:
                # 方法2：在目錄中查找以該文件名開頭的文件
                file_dir = os.path.dirname(abs_file_path)
                file_basename = os.path.basename(abs_file_path)
                
                if os.path.isdir(file_dir):
                    print(f"🔍 在目錄中搜索: {file_dir}, 文件名前綴: {file_basename}")
                    try:
                        files_in_dir = os.listdir(file_dir)
                        print(f"📁 目錄中的文件: {files_in_dir}")
                        
                        # 查找以該文件名開頭的Excel文件
                        matching_files = [f for f in files_in_dir 
                                        if f.startswith(file_basename) 
                                        and (f.lower().endswith('.xlsx') or f.lower().endswith('.xls'))]
                        
                        if matching_files:
                            # 找到匹配的文件，使用第一個
                            found_file = matching_files[0]
                            abs_file_path = os.path.join(file_dir, found_file)
                            print(f"✅ 找到匹配文件: {abs_file_path}")
                        else:
                            # 方法3：查找所有Excel文件，看是否有相似的時間戳
                            excel_files = [f for f in files_in_dir 
                                         if f.lower().endswith('.xlsx') or f.lower().endswith('.xls')]
                            print(f"📊 目錄中的Excel文件: {excel_files}")
                            
                            # 嘗試提取時間戳部分進行匹配
                            if file_basename and '_' in file_basename:
                                timestamp_part = file_basename.split('_')[0] + '_' + file_basename.split('_')[1] if len(file_basename.split('_')) >= 2 else file_basename
                                matching_by_timestamp = [f for f in excel_files if timestamp_part in f]
                                
                                if matching_by_timestamp:
                                    abs_file_path = os.path.join(file_dir, matching_by_timestamp[0])
                                    print(f"✅ 根據時間戳找到文件: {abs_file_path}")
                                else:
                                    print(f"❌ 無法找到匹配的文件")
                                    print(f"❌ 嘗試過: {abs_file_path}")
                                    print(f"❌ 嘗試過: {abs_file_path_xlsx}")
                                    print(f"❌ 嘗試過: {abs_file_path_xls}")
                                    return jsonify({"success": False, "message": f"文件不存在: {os.path.basename(file_path)}"}), 404
                            else:
                                print(f"❌ 無法找到匹配的文件")
                                print(f"❌ 嘗試過: {abs_file_path}")
                                print(f"❌ 嘗試過: {abs_file_path_xlsx}")
                                print(f"❌ 嘗試過: {abs_file_path_xls}")
                                return jsonify({"success": False, "message": f"文件不存在: {os.path.basename(file_path)}"}), 404
                    except Exception as e:
                        print(f"❌ 搜索文件時發生錯誤: {e}")
                        return jsonify({"success": False, "message": f"搜索文件失敗: {str(e)}"}), 500
                else:
                    print(f"❌ 目錄不存在: {file_dir}")
                    return jsonify({"success": False, "message": f"目錄不存在: {file_dir}"}), 404
        
        # 獲取原始文件名（從路徑中提取）
        original_filename = os.path.basename(file_path)
        # 如果文件名包含時間戳，嘗試提取原始文件名
        if '_' in original_filename and original_filename[0].isdigit():
            # 檢查是否是時間戳格式 (YYYYMMDD_HHMMSS_)
            parts = original_filename.split('_', 2)
            if len(parts) >= 3 and len(parts[0]) == 8 and len(parts[1]) == 6:
                original_filename = '_'.join(parts[2:])  # 保留後面的部分
        
        # 確保文件名有正確的擴展名（從實際文件路徑獲取）
        actual_filename = os.path.basename(abs_file_path)
        if actual_filename.lower().endswith('.xlsx'):
            ext = '.xlsx'
        elif actual_filename.lower().endswith('.xls'):
            ext = '.xls'
        else:
            ext = '.xlsx'  # 默認使用 .xlsx
        
        # 如果原始文件名沒有擴展名，添加擴展名
        if not original_filename.lower().endswith(('.xlsx', '.xls')):
            original_filename = original_filename + ext
        elif not original_filename.lower().endswith(ext):
            # 如果擴展名不匹配，使用實際文件的擴展名
            original_filename = os.path.splitext(original_filename)[0] + ext
        
        # 設置正確的MIME類型
        if original_filename.lower().endswith('.xlsx'):
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        elif original_filename.lower().endswith('.xls'):
            mimetype = 'application/vnd.ms-excel'
        else:
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        
        print(f"✅ 下載文件: {abs_file_path}, 文件名: {original_filename}, MIME: {mimetype}")
        return send_file(abs_file_path, as_attachment=True, download_name=original_filename, mimetype=mimetype)
    except Exception as e:
        print(f"❌ 下載文件錯誤: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "message": f"下載失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 科助刪除標準課程上傳記錄
# -------------------------
@resume_bp.route('/api/ta/delete_standard_course_history/<int:history_id>', methods=['DELETE'])
def delete_standard_course_history(history_id):
    """刪除上傳歷史記錄及對應的文件（從uploaded_course_templates表）"""
    if 'user_id' not in session or session.get('role') != 'ta':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 從 uploaded_course_templates 表獲取文件路徑
        cursor.execute("""
            SELECT file_path
            FROM uploaded_course_templates 
            WHERE id = %s
        """, (history_id,))
        record = cursor.fetchone()
        
        if not record:
            return jsonify({"success": False, "message": "找不到記錄"}), 404
        
        file_path = record.get('file_path')
        
        # 刪除文件（如果存在）
        if file_path:
            abs_file_path = os.path.abspath(file_path)
            if os.path.exists(abs_file_path):
                try:
                    os.remove(abs_file_path)
                    print(f"✅ 已刪除文件: {abs_file_path}")
                except Exception as e:
                    print(f"⚠️ 刪除文件失敗: {e}")
        
        # 刪除 uploaded_course_templates 表中的記錄
        cursor.execute("DELETE FROM uploaded_course_templates WHERE id = %s", (history_id,))
        conn.commit()
        
        print(f"✅ 已刪除 uploaded_course_templates 表記錄，ID: {history_id}")
        
        return jsonify({
            "success": True,
            "message": "已成功刪除記錄"
        })
    except Exception as e:
        conn.rollback()
        print(f"❌ 刪除記錄錯誤: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "message": f"刪除失敗: {str(e)}"}), 500
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
    
     # 統一使用整合後的審核頁面
    return render_template('resume/review_resume.html')

@resume_bp.route('/ai_edit_resume')
def ai_edit_resume_page():
    return render_template('resume/ai_edit_resume.html')
