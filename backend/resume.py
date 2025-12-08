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
from datetime import datetime, date
from notification import create_notification
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
import io

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
                        (StuID, CourseName, Credits, Grade, SemesterID)
                    VALUES (%s,%s,%s,%s,%s)
                """, (student_id, cname, c.get("credits"), c.get("grade"), semester_id))
            else:
                cursor.execute("""
                    INSERT INTO course_grades
                        (StuID, CourseName, Credits, Grade)
                    VALUES (%s,%s,%s,%s)
                """, (student_id, cname, c.get("credits"), c.get("grade")))

        # -------------------------------------------------------------
        # 3) 儲存 student_certifications
        # -------------------------------------------------------------

        # (1) 拿欄位
        cursor.execute("SHOW COLUMNS FROM student_certifications")
        known_columns = {row["Field"] for row in cursor.fetchall()}

        # (2) 合併兩種來源
        cert_text_rows = data.get("structured_certifications", []) or []
        cert_photo_paths = data.get("cert_photo_paths", []) or []
        cert_photo_names = data.get("cert_names", []) or []
        cert_photo_codes = data.get("cert_codes", []) or []
        cert_photo_issuers = data.get("cert_issuers", []) or []

        cert_rows = []
        
        # 用於去重：記錄已處理的證照（使用 (cert_code, job_category, level) 作為唯一標識）
        # 注意：如果 cert_code 為 NULL，使用 (None, job_category, level) 作為標識
        processed_certs = set()

        # (3) 處理 structured certifications
        # 建立索引映射：將 cert_photo_paths 與 structured_certifications 關聯
        # 假設索引對應（第0個證照的圖片在第0個位置）
        for idx, cert in enumerate(cert_text_rows):
            cert_code = (cert.get("cert_code") or "").strip().upper()
            job_category = (cert.get("job_category") or "").strip()
            level = (cert.get("level") or "").strip()

            # 空資料跳過（不再檢查 custom_cert_name，因為該欄位已刪除）
            if not any([cert_code, job_category, level]):
                continue
            
            # 檢查是否已處理過相同的證照（去重）
            # 優先使用 (job_category, level) 作為唯一標識（因為同一學生的相同職類+級別應該只有一筆記錄）
            # 如果 job_category 和 level 都有值，使用它們作為主要標識
            job_cat = job_category.strip() if job_category else ''
            level_val = level.strip() if level else ''
            
            if job_cat and level_val:
                # 如果 job_category 和 level 都有值，使用它們作為唯一標識（忽略 cert_code 的差異）
                cert_identifier = (job_cat, level_val)
                if cert_identifier in processed_certs:
                    print(f"⚠️ 跳過重複的證照記錄（相同職類+級別）: job_category={job_cat}, level={level_val}, cert_code={cert_code}")
                    continue
                processed_certs.add(cert_identifier)
            # 如果只有 cert_code 有值，使用 cert_code 作為標識
            elif cert_code and cert_code != 'OTHER' and cert_code != '':
                cert_identifier = (cert_code,)
                if cert_identifier in processed_certs:
                    print(f"⚠️ 跳過重複的證照記錄（相同代碼）: cert_code={cert_code}")
                    continue
                processed_certs.add(cert_identifier)
            # 如果都沒有值，跳過（已在前面檢查過）

            row = {"StuID": student_id}

            # 判斷是否為標準發證中心（有 cert_code 且不是 'OTHER'）
            is_standard_authority = cert_code and cert_code != 'OTHER' and cert_code != ''
            
            # 獲取前端傳來的 authority_id（如果有的話）
            frontend_authority_id = cert.get("authority_id")
            if frontend_authority_id:
                try:
                    frontend_authority_id = int(frontend_authority_id) if str(frontend_authority_id).strip() else None
                except (ValueError, TypeError):
                    frontend_authority_id = None
            
            if "cert_code" in known_columns:
                row["cert_code"] = cert_code or None

            # 如果是標準發證中心，從 certificate_codes 表查詢 job_category、level 和 authority_id
            if is_standard_authority:
                try:
                    cursor.execute("""
                        SELECT job_category, level, authority_id 
                        FROM certificate_codes 
                        WHERE code COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci
                        LIMIT 1
                    """, (cert_code,))
                    cert_info = cursor.fetchone()
                    if cert_info:
                        # 使用從 certificate_codes 表查詢的值
                        db_job_category = cert_info.get('job_category', '').strip() if cert_info.get('job_category') else ''
                        db_level = cert_info.get('level', '').strip() if cert_info.get('level') else ''
                        db_authority_id = cert_info.get('authority_id')
                        
                        if "job_category" in known_columns:
                            row["job_category"] = db_job_category if db_job_category else None
                        if "level" in known_columns:
                            row["level"] = db_level if db_level else None
                        
                        # 保存 authority_id（優先使用從 certificate_codes 查詢的，否則使用前端傳來的）
                        if "authority_id" in known_columns:
                            if db_authority_id:
                                row["authority_id"] = int(db_authority_id)
                            elif frontend_authority_id:
                                row["authority_id"] = frontend_authority_id
                            else:
                                row["authority_id"] = None
                        
                        # 標準發證中心不保存 authority_name（custom_cert_name 欄位已刪除）
                        if "authority_name" in known_columns:
                            row["authority_name"] = None
                    else:
                        # 如果查不到，使用前端傳來的值（向後兼容）
                        if "job_category" in known_columns:
                            row["job_category"] = job_category if job_category else None
                        if "level" in known_columns:
                            row["level"] = level if level else None
                except Exception as e:
                    print(f"⚠️ 查詢 certificate_codes 失敗: {e}")
                    # 查詢失敗時，使用前端傳來的值
                    if "job_category" in known_columns:
                        row["job_category"] = job_category if job_category else None
                    if "level" in known_columns:
                        row["level"] = level if level else None
            else:
                # 如果是「其他」發證中心或沒有 cert_code，保存前端傳來的自填資料
                if "authority_name" in known_columns:
                    row["authority_name"] = (cert.get("authority_name") or "").strip() or None
                
                # 「其他」發證中心：如果有前端傳來的 authority_id 則使用，否則設為 NULL
                if "authority_id" in known_columns:
                    row["authority_id"] = frontend_authority_id if frontend_authority_id else None

                # custom_cert_name 欄位已刪除，不再保存

                if "job_category" in known_columns:
                    row["job_category"] = job_category if job_category else None

                if "level" in known_columns:
                    row["level"] = level if level else None

            if "issuer" in known_columns:
                row["issuer"] = (cert.get("issuer") or "").strip() or None

            if "AcquisitionDate" in known_columns:
                row["AcquisitionDate"] = cert.get("acquire_date") or cert.get("acquisition_date") or None

            # 嘗試從 cert_photo_paths 獲取對應的圖片路徑（通過索引匹配）
            cert_path = cert.get("cert_path") or None
            if not cert_path and idx < len(cert_photo_paths):
                cert_path = cert_photo_paths[idx] if cert_photo_paths[idx] else None
            
            if "CertPath" in known_columns:
                # 將路徑轉換為相對路徑格式（使用正斜杠）
                if cert_path:
                    # 將 Windows 路徑格式（反斜杠）轉換為 Web 路徑格式（正斜杠）
                    normalized_path = cert_path.replace("\\", "/")
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

        # (4) 處理上傳證照圖片（舊的圖片上傳方式，向後兼容）
        # 注意：這部分邏輯應該已經被 structured_certifications 取代，但保留以向後兼容
        # 只處理那些在 structured_certifications 中沒有對應圖片路徑的證照，避免重複創建記錄
        processed_paths = set()
        for cert_row in cert_rows:
            if cert_row.get("CertPath"):
                processed_paths.add(cert_row.get("CertPath"))
        
        max_len = max(len(cert_photo_paths), len(cert_photo_codes), len(cert_photo_names), len(cert_photo_issuers))
        for i in range(max_len):
            path = cert_photo_paths[i] if i < len(cert_photo_paths) else None
            if not path:
                continue
            
            # 如果這個圖片路徑已經在 structured_certifications 中處理過，跳過（避免重複）
            if path in processed_paths:
                print(f"⚠️ 跳過已處理的證照圖片: {path}")
                continue

            row = {"StuID": student_id}

            code = cert_photo_codes[i].strip().upper() if i < len(cert_photo_codes) and cert_photo_codes[i] else None
            
            # 檢查是否已處理過相同的證照（去重）
            # 如果有 cert_code，先查詢 job_category 和 level，然後使用 (job_category, level) 作為唯一標識
            if code and code != 'OTHER' and code != '':
                # 先查詢 job_category 和 level（用於去重檢查）
                try:
                    cursor.execute("""
                        SELECT job_category, level 
                        FROM certificate_codes 
                        WHERE code COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci
                        LIMIT 1
                    """, (code,))
                    cert_info = cursor.fetchone()
                    if cert_info:
                        db_job_category = cert_info.get('job_category', '').strip() if cert_info.get('job_category') else ''
                        db_level = cert_info.get('level', '').strip() if cert_info.get('level') else ''
                        # 如果 job_category 和 level 都有值，使用它們作為唯一標識（與第(3)部分一致）
                        if db_job_category and db_level:
                            cert_identifier = (db_job_category, db_level)
                            if cert_identifier in processed_certs:
                                print(f"⚠️ 跳過重複的證照記錄（從圖片上傳，相同職類+級別）: cert_code={code}, job_category={db_job_category}, level={db_level}")
                                continue
                            processed_certs.add(cert_identifier)
                except Exception as e:
                    print(f"⚠️ 查詢 certificate_codes 失敗（去重檢查）: {e}")
            
            if "cert_code" in known_columns:
                row["cert_code"] = code

            # 如果有 cert_code 且不是 'OTHER'，從 certificate_codes 表查詢 job_category、level 和 authority_id
            if code and code != 'OTHER' and code != '':
                try:
                    cursor.execute("""
                        SELECT job_category, level, authority_id 
                        FROM certificate_codes 
                        WHERE code COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci
                        LIMIT 1
                    """, (code,))
                    cert_info = cursor.fetchone()
                    if cert_info:
                        db_job_category = cert_info.get('job_category', '').strip() if cert_info.get('job_category') else ''
                        db_level = cert_info.get('level', '').strip() if cert_info.get('level') else ''
                        db_authority_id = cert_info.get('authority_id')
                        
                        if "job_category" in known_columns:
                            row["job_category"] = db_job_category if db_job_category else None
                        if "level" in known_columns:
                            row["level"] = db_level if db_level else None
                        
                        # 保存 authority_id（如果欄位存在）
                        if "authority_id" in known_columns and db_authority_id:
                            row["authority_id"] = int(db_authority_id)
                        
                        # 標準發證中心（custom_cert_name 欄位已刪除）
                    else:
                        # 如果查不到，不保存 job_category 和 level
                        if "job_category" in known_columns:
                            row["job_category"] = None
                        if "level" in known_columns:
                            row["level"] = None
                except Exception as e:
                    print(f"⚠️ 查詢 certificate_codes 失敗: {e}")
                    if "job_category" in known_columns:
                        row["job_category"] = None
                    if "level" in known_columns:
                        row["level"] = None
            else:
                # 如果是「其他」發證中心或沒有 cert_code，保存自填資料
                # custom_cert_name 欄位已刪除，不再保存
                # 注意：這種舊的上傳方式無法獲取 job_category 和 level，所以設為 NULL
                if "authority_id" in known_columns:
                    row["authority_id"] = None
                if "job_category" in known_columns:
                    row["job_category"] = None
                if "level" in known_columns:
                    row["level"] = None

            if "issuer" in known_columns:
                row["issuer"] = cert_photo_issuers[i] if i < len(cert_photo_issuers) and cert_photo_issuers[i] else None

            if "CertPath" in known_columns:
                # 將路徑轉換為相對路徑格式（使用正斜杠）
                if path:
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

        # (5) 實際寫入資料庫
        # 注意：由於有唯一索引 uk_student_cert_unique (StuID, cert_code, level)，
        # 如果同一學生重複提交相同證照，會觸發唯一索引衝突
        # 這裡使用 DELETE 後 INSERT 的方式，確保不會有重複記錄
        if cert_rows:
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
                    print(f"   記錄內容: {row}")
                    # 不拋出異常，繼續處理下一筆記錄

        # -------------------------------------------------------------
        # 4) 儲存語言能力 student_languageskills
        # -------------------------------------------------------------
        cursor.execute("DELETE FROM student_languageskills WHERE StuID=%s", (student_id,))
        for row in data.get("structured_languages", []):
            if row.get("language") and row.get("level"):
                cursor.execute("""
                    INSERT INTO student_languageskills
                        (StuID, Language, Level, CreatedAt)
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
    # 使用 COLLATE 確保字符集匹配正確
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

        # 處理課程資料（按名稱排序）
        MAX_COURSES = 30
        # 確保課程按名稱排序（使用自然排序，讓「資訊科技進階」排在「資訊科技」之後）
        if grades:
            # 過濾掉空課程名稱
            non_empty_grades = [g for g in grades if g.get('CourseName', '').strip()]
            # 使用更可靠的中文排序方法
            # 使用 locale-aware 排序，如果可用；否則使用 Unicode 排序
            try:
                import locale
                # 嘗試設置中文 locale
                try:
                    locale.setlocale(locale.LC_ALL, 'zh_TW.UTF-8')
                except (locale.Error, OSError):
                    try:
                        locale.setlocale(locale.LC_ALL, 'zh_CN.UTF-8')
                    except (locale.Error, OSError):
                        pass  # 如果設置失敗，使用默認排序
                # 使用 locale.strxfrm 進行排序
                sorted_grades = sorted(non_empty_grades, 
                                     key=lambda x: locale.strxfrm(x.get('CourseName', '').strip()))
            except (ImportError, Exception):
                # 如果 locale 不可用或設置失敗，使用 Unicode 排序（Python 默認排序已支持中文）
                # 確保使用正確的排序鍵：去除首尾空格並使用 Unicode 排序
                # Python 的默認字符串排序已經能夠正確處理中文，例如「資訊科技」會排在「資訊科技進階」之前
                sorted_grades = sorted(non_empty_grades, 
                                     key=lambda x: x.get('CourseName', '').strip())
            # 添加空課程以填充到 MAX_COURSES
            padded_grades = sorted_grades[:MAX_COURSES]
            padded_grades += [{'CourseName': '', 'Credits': ''}] * (MAX_COURSES - len(padded_grades))
        else:
            padded_grades = [{'CourseName': '', 'Credits': ''}] * MAX_COURSES

        context_courses = {}
        NUM_ROWS = 10
        NUM_COLS = 3
        # 改為按列填充，使得相鄰的課程（如「資訊科技」和「資訊科技進階」）能夠垂直排列
        # 填充順序：第1列的所有行，然後第2列的所有行，最後第3列的所有行
        for j in range(NUM_COLS):
            for i in range(NUM_ROWS):
                index = j * NUM_ROWS + i
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
        cert_codes_from_form = student_data.get("cert_codes", [])
        if cert_names_from_form:
            # 重新構建證照列表，使用前端提交的名稱
            certs_with_form_names = []
            for idx, (name, path) in enumerate(zip(cert_names_from_form, cert_photo_paths_from_form)):
                if name and name.strip():
                    # 從原始 certs 中找到對應的證照（優先通過 cert_code 匹配，其次通過索引或路徑匹配）
                    matching_cert = None
                    cert_code = cert_codes_from_form[idx] if idx < len(cert_codes_from_form) else ''
                    
                    # 優先通過 cert_code 匹配（最準確）
                    if cert_code and cert_code.strip() and cert_code.upper() != 'OTHER':
                        for c in certs:
                            # 檢查 certs 中是否有對應的 cert_code（需要從數據庫查詢結果中獲取）
                            # 由於 certs 可能不包含 cert_code，我們通過名稱匹配
                            if c.get("cert_name", "").strip() == name.strip():
                                matching_cert = c
                                break
                    
                    # 如果 cert_code 匹配失敗，嘗試通過索引匹配
                    if not matching_cert and idx < len(certs):
                        matching_cert = certs[idx]
                    
                    # 如果索引匹配失敗，嘗試通過路徑匹配
                    if not matching_cert and path:
                        for c in certs:
                            if c.get("cert_path") == path:
                                matching_cert = c
                                break
                    
                    # 獲取 category（優先從匹配的證照中獲取）
                    category = "other"
                    if matching_cert:
                        category = matching_cert.get("category", "other")
                        print(f"✅ 從匹配的證照獲取 category: name={name}, category={category}")
                    else:
                        print(f"⚠️ 未找到匹配的證照，使用默認 category 'other': name={name}, cert_code={cert_code}")
                    
                    # 使用前端提交的名稱，但保留其他信息（類別、路徑等）
                    cert_item = {
                        "cert_name": name.strip(),  # 使用前端提交的名稱
                        "category": category,
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

        # 根據學生入學年度動態計算缺勤記錄查詢區間
        # 例如：110年度入學 → 1122至1131，111年度入學 → 1132至1141
        try:
            # 從 student_data 中獲取入學年度（已在 submit_and_generate_api 中查詢並設置）
            admission_year = student_data.get('admission_year')
            
            # 如果成功獲取入學年度，計算查詢區間
            if admission_year:
                # 計算：入學年度+2的第2學期 和 入學年度+3的第1學期
                # 例如：110年度入學 → 1122（110+2=112，第2學期）和 1131（110+3=113，第1學期）
                # 例如：111年度入學 → 1132（111+2=113，第2學期）和 1141（111+3=114，第1學期）
                start_semester = f"{admission_year + 2}2"  # 入學年度+2的第2學期
                end_semester = f"{admission_year + 3}1"    # 入學年度+3的第1學期
                
                # 將查詢區間添加到 context，供 Word 模板使用
                # 模板中可以使用 {{ absence_query_range }} 來顯示完整文字
                # 或使用 {{ absence_start_semester }} 和 {{ absence_end_semester }} 分別顯示
                context['absence_query_range'] = f"查詢區間：{start_semester}至{end_semester}學期"
                context['absence_start_semester'] = start_semester
                context['absence_end_semester'] = end_semester
                print(f"✅ 已設置缺勤記錄查詢區間：{context['absence_query_range']} (入學年度: {admission_year})")
            else:
                # 如果無法獲取入學年度，使用預設值或留空
                context['absence_query_range'] = "查詢區間：未設定"
                context['absence_start_semester'] = ""
                context['absence_end_semester'] = ""
                print(f"⚠️ 無法獲取學生入學年度，無法自動計算查詢區間")
        except Exception as e:
            print(f"⚠️ 計算缺勤記錄查詢區間失敗: {e}")
            traceback.print_exc()
            context['absence_query_range'] = "查詢區間：未設定"
            context['absence_start_semester'] = ""
            context['absence_end_semester'] = ""

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
        
        # 處理所有證照（包括沒有圖片的），但優先顯示有圖片的
        # 先過濾出有圖片的證照
        certs_with_photos = []
        certs_without_photos = []
        
        for c in flat_list:
            photo_path = c.get("photo_path", "")
            # 檢查路徑是否存在（處理相對路徑和絕對路徑）
            path_exists = False
            if photo_path:
                # 嘗試多種路徑格式
                if os.path.exists(photo_path):
                    path_exists = True
                else:
                    # 嘗試相對路徑
                    relative_path = photo_path.replace("\\", "/")
                    if os.path.exists(relative_path):
                        c["photo_path"] = relative_path
                        path_exists = True
                    else:
                        # 嘗試從 uploads 目錄開始的相對路徑
                        if relative_path.startswith("uploads/"):
                            abs_path = os.path.abspath(relative_path)
                            if os.path.exists(abs_path):
                                c["photo_path"] = abs_path
                                path_exists = True
            
            if path_exists:
                certs_with_photos.append(c)
            else:
                # 即使沒有圖片，也保留證照名稱
                certs_without_photos.append(c)
        
        # 合併：先顯示有圖片的，再顯示沒有圖片的（但只顯示名稱）
        all_certs_to_display = (certs_with_photos + certs_without_photos)[:max_total]
        total_certs = len(all_certs_to_display)
        
        print(f"📊 證照統計：總共 {len(flat_list)} 張，有圖片 {len(certs_with_photos)} 張，無圖片 {len(certs_without_photos)} 張，將顯示 {total_certs} 張")
        
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
            first_page_certs = all_certs_to_display[:min(8, total_certs)]
            if first_page_certs:
                fill_certificate_photos(context, doc, first_page_certs, start_index=1, max_count=8)
                print(f"✅ 第一頁填充了 {len(first_page_certs)} 張證照")
            
            # 第二頁（9-16）：如果 total_certs > 8 則顯示
            if total_certs > 8:
                context["cert_page_2_block"] = True  # 設置為 True 以顯示區塊
                second_page_certs = all_certs_to_display[8:min(16, total_certs)]
                if second_page_certs:
                    fill_certificate_photos(context, doc, second_page_certs, start_index=9, max_count=8)
                    print(f"✅ 第二頁填充了 {len(second_page_certs)} 張證照")
            
            # 第三頁（17-24）：如果 total_certs > 16 則顯示
            if total_certs > 16:
                context["cert_page_3_block"] = True  # 設置為 True 以顯示區塊
                third_page_certs = all_certs_to_display[16:min(24, total_certs)]
                if third_page_certs:
                    fill_certificate_photos(context, doc, third_page_certs, start_index=17, max_count=8)
                    print(f"✅ 第三頁填充了 {len(third_page_certs)} 張證照")
            
            # 第四頁（25-32）：如果 total_certs > 24 則顯示
            if total_certs > 24:
                context["cert_page_4_block"] = True  # 設置為 True 以顯示區塊
                fourth_page_certs = all_certs_to_display[24:min(32, total_certs)]
                if fourth_page_certs:
                    fill_certificate_photos(context, doc, fourth_page_certs, start_index=25, max_count=8)
                    print(f"✅ 第四頁填充了 {len(fourth_page_certs)} 張證照")

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

        # 1. 儲存個人照片
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

        # 2. 儲存成績單檔案
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

        # 3. 儲存多張證照圖片
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
                # 將絕對路徑轉換為相對路徑（使用正斜杠），用於保存到數據庫
                relative_path = file_path.replace("\\", "/")
                # 確保路徑以相對路徑格式保存（不包含絕對路徑前綴）
                if relative_path.startswith("uploads/"):
                    cert_photo_paths.append(relative_path)
                else:
                    # 如果路徑不是以 uploads/ 開頭，嘗試提取相對路徑部分
                    parts = relative_path.split("/")
                    if "uploads" in parts:
                        idx_uploads = parts.index("uploads")
                        relative_path = "/".join(parts[idx_uploads:])
                        cert_photo_paths.append(relative_path)
                    else:
                        cert_photo_paths.append(relative_path)

        # 4. 處理單張證照圖片（兼容舊版）
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

        if image_path_for_template or certificate_description:
            if cert_photo_paths is None:
                cert_photo_paths = []
            if cert_names is None:
                cert_names = []
            cert_photo_paths.insert(0, image_path_for_template or "")
            cert_names.insert(0, certificate_description or "")

        # 5. 組合缺勤統計
        absence_stats = {}
        
        # 獲取學期範圍參數
        start_semester_id = request.form.get("start_semester_id", None)
        end_semester_id = request.form.get("end_semester_id", None)
        
        # 構建查詢條件
        where_conditions = ["user_id = %s"]
        query_params = [user_id]
        
        # 如果有學期範圍，添加學期篩選
        if start_semester_id and end_semester_id:
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

        # 處理前端傳來的 JSON 統計值 (作為備用)
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
        
        context.update(absence_stats)

        # 6. 處理缺勤佐證圖片 (上傳與儲存)
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

        # 嘗試從 JSON 找圖片路徑
        if not absence_image_path:
            try:
                ar_json = request.form.get("absence_records_json", None)
                if ar_json:
                    ar_list = json.loads(ar_json)
                    for rec in reversed(ar_list):
                        img = rec.get("image_filename") or rec.get("image_path")
                        if img:
                            absence_image_path = img
                            break
            except Exception as e:
                print("⚠️ 嘗試讀取 absence_records_json 失敗:", e)

        # 嘗試從資料庫找最新的圖片路徑
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

        # 7. 更新缺勤記錄的圖片路徑到資料庫
        try:
            # (A) 處理個別記錄的圖片
            absence_records_with_images_json = request.form.get("absence_records_with_images", None)
            if absence_records_with_images_json:
                try:
                    records_with_images = json.loads(absence_records_with_images_json)
                    for record_info in records_with_images:
                        record_id = record_info.get("record_id")
                        if not record_id: continue
                        
                        image_key = f"proof_image_{record_id}"
                        uploaded_image = request.files.get(image_key)
                        
                        if uploaded_image and uploaded_image.filename:
                            try:
                                os.makedirs(ABSENCE_PROOF_FOLDER, exist_ok=True)
                                ext = os.path.splitext(secure_filename(uploaded_image.filename))[1] or ".png"
                                fname = f"{user_id}_record_{record_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
                                save_path = os.path.join(ABSENCE_PROOF_FOLDER, fname)
                                uploaded_image.save(save_path)
                                
                                cursor.execute("""
                                    UPDATE absence_records SET image_path = %s, updated_at = NOW()
                                    WHERE id = %s AND user_id = %s
                                """, (save_path, record_id, user_id))
                            except Exception as e:
                                print(f"⚠️ 更新缺勤記錄 {record_id} 的佐證圖片失敗: {e}")
                except Exception as e:
                    print(f"⚠️ 解析 absence_records_with_images 失敗: {e}")
            
            # (B) 處理整體佐證圖片 (更新到符合條件的缺勤記錄)
            # ================== 修正重點開始 ==================
            if absence_image_path:
                semester_id = request.form.get("semester_id", None)
                start_semester_id = request.form.get("start_semester_id", None)
                end_semester_id = request.form.get("end_semester_id", None)

                try:
                    cursor.execute("SHOW COLUMNS FROM absence_records LIKE 'semester_id'")
                    has_semester_id = cursor.fetchone() is not None
                    
                    if has_semester_id:
                        # 情況 1: 單一學期 (舊版)
                        if semester_id:
                            cursor.execute("""
                                UPDATE absence_records 
                                SET image_path = %s, updated_at = NOW()
                                WHERE user_id = %s AND semester_id = %s 
                                AND (image_path IS NULL OR image_path = '')
                            """, (absence_image_path, user_id, semester_id))
                            
                        # 情況 2: 學期範圍 (新版)
                        elif start_semester_id and end_semester_id:
                            cursor.execute("""
                                SELECT id FROM semesters 
                                WHERE code >= (SELECT code FROM semesters WHERE id = %s)
                                AND code <= (SELECT code FROM semesters WHERE id = %s)
                            """, (start_semester_id, end_semester_id))
                            sem_rows = cursor.fetchall()
                            sem_ids = [r['id'] for r in sem_rows]
                            
                            if sem_ids:
                                placeholders = ','.join(['%s'] * len(sem_ids))
                                cursor.execute(f"""
                                    UPDATE absence_records 
                                    SET image_path = %s, updated_at = NOW()
                                    WHERE user_id = %s AND semester_id IN ({placeholders})
                                    AND (image_path IS NULL OR image_path = '')
                                """, (absence_image_path, user_id, *sem_ids))

                        # 情況 3: 未指定 (更新該生所有無圖記錄)
                        else:
                            cursor.execute("""
                                UPDATE absence_records 
                                SET image_path = %s, updated_at = NOW()
                                WHERE user_id = %s 
                                AND (image_path IS NULL OR image_path = '')
                            """, (absence_image_path, user_id))
                    else:
                        # 無 semester_id 欄位
                        cursor.execute("""
                            UPDATE absence_records 
                            SET image_path = %s, updated_at = NOW()
                            WHERE user_id = %s 
                            AND (image_path IS NULL OR image_path = '')
                        """, (absence_image_path, user_id))
                    
                    print(f"✅ 已將整體佐證圖片更新到缺勤記錄 (路徑: {absence_image_path})")
                except Exception as e:
                    print(f"⚠️ 更新整體佐證圖片失敗: {e}")
                    traceback.print_exc()
            # ================== 修正重點結束 ==================

        except Exception as e:
            print(f"⚠️ 處理缺勤記錄圖片失敗: {e}")
            traceback.print_exc()

        # 8. 取得學生 ID
        cursor.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        result = cursor.fetchone()
        if not result:
            return jsonify({"success": False, "message": "找不到使用者"}), 404
        student_id = result['username']

        # 9. 處理課程 Grade
        for c in courses:
            c['grade'] = c.get('grade', '')

        # 10. 解析證照資料
        structured_certifications = []
        # (讀取各個 list ...)
        cert_names_text = request.form.getlist('cert_name[]')
        cert_types = request.form.getlist('cert_type[]')
        cert_codes_text = request.form.getlist('cert_code[]')
        cert_issuers_text = request.form.getlist('cert_issuer[]')
        cert_authority_ids = request.form.getlist('cert_authority[]')
        cert_authority_names = request.form.getlist('cert_authority_name[]')
        cert_job_categories = request.form.getlist('cert_job_category[]')
        cert_levels = request.form.getlist('cert_level[]')
        cert_other_job_categories = request.form.getlist('cert_other_job_category[]')
        cert_other_levels = request.form.getlist('cert_other_level[]')
        cert_acquisition_dates = request.form.getlist('cert_acquisition_date[]')

        max_len = max(len(cert_names_text), len(cert_codes_text), len(cert_levels), len(cert_job_categories))
        
        for i in range(max_len):
            n = cert_names_text[i] if i < len(cert_names_text) else ''
            t = cert_types[i] if i < len(cert_types) else 'other'
            code = cert_codes_text[i] if i < len(cert_codes_text) else ''
            issuer = cert_issuers_text[i] if i < len(cert_issuers_text) else ''
            authority_id = cert_authority_ids[i] if i < len(cert_authority_ids) else ''
            authority_name = cert_authority_names[i] if i < len(cert_authority_names) else ''
            job_category = cert_job_categories[i] if i < len(cert_job_categories) else ''
            level = cert_levels[i] if i < len(cert_levels) else ''
            other_job_category = cert_other_job_categories[i] if i < len(cert_other_job_categories) else ''
            other_level = cert_other_levels[i] if i < len(cert_other_levels) else ''
            acquisition_date = cert_acquisition_dates[i] if i < len(cert_acquisition_dates) else ''
            
            if code.strip().upper() == 'OTHER':
                job_category = other_job_category
                level = other_level
            
            # 檢查有效性
            if not (job_category.strip() and level.strip()) and not n.strip() and not code.strip():
                continue
            
            # 決定名稱
            final_cert_name = f"{job_category.strip()}{level.strip()}" if (job_category.strip() and level.strip()) else n.strip()
            final_cert_code = code.strip().upper() if code.strip() else 'OTHER'
            
            cert_path = None
            if i < len(cert_photo_paths) and cert_photo_paths[i]:
                cert_path = cert_photo_paths[i]
            
            structured_certifications.append({
                "name": final_cert_name,
                "type": t.strip() if t else "other",
                "code": final_cert_code,
                "authority_id": authority_id.strip() if authority_id.strip() and authority_id.strip() != 'OTHER' else None,
                "authority_name": authority_name.strip() if authority_id.strip() == 'OTHER' else '',
                "job_category": job_category.strip() if job_category.strip() else '',
                "level": level.strip() if level.strip() else '',
                "acquisition_date": acquisition_date.strip() if acquisition_date.strip() else None,
                "issuer": issuer.strip() if issuer else "",
                "cert_path": cert_path
            })

        # 11. 解析語言能力
        structured_languages = []
        lang_mapping = {'lang_en_level': '英語', 'lang_tw_level': '台語', 'lang_jp_level': '日語', 'lang_hk_level': '客語'}
        for form_field, lang_name in lang_mapping.items():
            level = request.form.get(form_field, '').strip()
            if level:
                structured_languages.append({"language": lang_name, "level": level})

        # 12. 儲存結構化資料
        cert_codes = request.form.getlist('cert_code[]')
        cert_issuers = request.form.getlist('cert_issuer[]')
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
            "cert_codes": cert_codes,
            "cert_issuers": cert_issuers
        }

        context.update(data)
        context.update(structured_data)

        if not save_structured_data(cursor, student_id, structured_data, semester_id=semester_id):
            conn.rollback()
            return jsonify({"success": False, "message": "資料儲存失敗"}), 500

        # 13. 更新成績單路徑到資料庫
        if transcript_path:
            try:
                cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'ProofImage'")
                has_proof_image = cursor.fetchone() is not None
                if has_proof_image:
                    cursor.execute("""
                        UPDATE course_grades SET ProofImage = %s WHERE StuID = %s
                    """, (transcript_path, student_id))
                else:
                    cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'transcript_path'")
                    if cursor.fetchone():
                        cursor.execute("""
                            UPDATE course_grades SET transcript_path = %s WHERE StuID = %s
                        """, (transcript_path, student_id))
            except Exception as e:
                print("⚠️ 更新 course_grades.ProofImage 失敗:", e)

        # 14. 查詢學生入學年度（用於動態計算缺勤記錄查詢區間）
        admission_year = None
        try:
            cursor.execute("""
                SELECT c.admission_year
                FROM users u
                LEFT JOIN classes c ON u.class_id = c.id
                WHERE u.id = %s
            """, (user_id,))
            student_class_info = cursor.fetchone()
            if student_class_info and student_class_info.get('admission_year'):
                admission_year = student_class_info['admission_year']
                # 處理不同格式的 admission_year
                if isinstance(admission_year, str):
                    # 如果是4位數（如1122），提取前3位作為年度
                    if len(admission_year) >= 4 and admission_year[:3].isdigit():
                        admission_year = int(admission_year[:3])
                    elif admission_year.isdigit():
                        admission_year = int(admission_year)
                elif isinstance(admission_year, int):
                    # 如果是4位數（如1122），提取前3位作為年度
                    if admission_year >= 1000:
                        admission_year = admission_year // 10
                print(f"✅ 獲取學生入學年度: {admission_year} (user_id: {user_id})")
        except Exception as e:
            print(f"⚠️ 查詢學生入學年度失敗: {e}")
            traceback.print_exc()

        # 15. 生成 Word 文件
        student_data_for_doc = get_student_info_for_doc(cursor, student_id, semester_id=semester_id)
        student_data_for_doc["info"]["PhotoPath"] = photo_path
        student_data_for_doc["info"]["ConductScoreNumeric"] = data.get("conduct_score_numeric")
        student_data_for_doc["cert_photo_paths"] = cert_photo_paths
        student_data_for_doc["cert_names"] = cert_names
        student_data_for_doc["cert_codes"] = cert_codes
        
        # 將入學年度添加到 student_data_for_doc，供 generate_application_form_docx 使用
        if admission_year:
            student_data_for_doc["admission_year"] = admission_year
        
        # 優先使用 DB 中的缺勤圖片
        absence_proof_from_db = student_data_for_doc.get("Absence_Proof_Path")
        student_data_for_doc.update(context)
        
        if absence_proof_from_db:
            student_data_for_doc["Absence_Proof_Path"] = absence_proof_from_db

        filename = f"{student_id}_履歷_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        save_path = os.path.join(UPLOAD_FOLDER, filename)

        if not generate_application_form_docx(student_data_for_doc, save_path):
            conn.rollback()
            return jsonify({"success": False, "message": "文件生成失敗"}), 500

        # 16. 寫入 Resumes 表
        # 將絕對路徑轉換為相對路徑（統一使用正斜杠）
        filepath_for_db = save_path.replace('\\', '/')
        # 確保是相對路徑格式（如果包含絕對路徑前綴，提取相對部分）
        if os.path.isabs(filepath_for_db):
            # 獲取當前工作目錄，然後計算相對路徑
            abs_upload_folder = os.path.abspath(UPLOAD_FOLDER)
            if filepath_for_db.startswith(abs_upload_folder.replace('\\', '/')):
                filepath_for_db = filepath_for_db.replace(abs_upload_folder.replace('\\', '/'), UPLOAD_FOLDER)
            else:
                # 如果無法計算相對路徑，嘗試提取 uploads/ 之後的部分
                parts = filepath_for_db.split('/')
                if 'uploads' in parts:
                    idx_uploads = parts.index('uploads')
                    filepath_for_db = '/'.join(parts[idx_uploads:])
        
        # 獲取文件大小
        file_size = 0
        try:
            if os.path.exists(save_path):
                file_size = os.path.getsize(save_path)
        except Exception as e:
            print(f"⚠️ 獲取文件大小失敗: {e}")
        
        # status 應該使用 'uploaded'（符合數據庫 enum 定義：'uploaded','approved','rejected'）
        print(f"📝 準備插入履歷記錄:")
        print(f"   user_id={user_id}")
        print(f"   filepath={filepath_for_db}")
        print(f"   original_filename={filename}")
        print(f"   status=uploaded")
        print(f"   semester_id={semester_id}")
        print(f"   filesize={file_size}")
        
        cursor.execute("""
            INSERT INTO resumes
            (user_id, filepath, original_filename, status, semester_id, filesize, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """, (user_id, filepath_for_db, filename, 'uploaded', semester_id, file_size))
        
        resume_id = cursor.lastrowid
        print(f"✅ 履歷記錄已成功插入資料庫 (ID: {resume_id})")
        
        # 驗證插入的資料
        cursor.execute("SELECT filepath, status FROM resumes WHERE id = %s", (resume_id,))
        inserted_resume = cursor.fetchone()
        if inserted_resume:
            print(f"✅ 驗證：資料庫中的 filepath = {inserted_resume.get('filepath')}")
            print(f"✅ 驗證：資料庫中的 status = {inserted_resume.get('status')}")
        else:
            print(f"⚠️ 警告：無法驗證插入的資料")

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
            SELECT filepath, original_filename, user_id, status 
            FROM resumes 
            WHERE id = %s
        """, (resume_id,))
        resume = cursor.fetchone()

        if not resume:
            return jsonify({"success": False, "message": "找不到履歷"}), 404

        # 權限檢查
        session_user_id = session['user_id']
        session_role = session['role']

        # vendor 特殊處理：檢查該履歷狀態是否為 'approved'
        if session_role == "vendor":
            if resume.get('status') != 'approved':
                return jsonify({"success": False, "message": "無權限：只能下載已通過審核的履歷"}), 403
        else:
            # 其他角色使用原有的權限檢查
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
    print(f"🔍 request.content_type: {request.content_type}")
    print(f"🔍 request.is_json: {request.is_json}")
    
    if 'proof_image' in request.files:
        proof_image = request.files['proof_image']
        print(f"🔍 proof_image 對象: {proof_image}")
        print(f"🔍 proof_image.filename: {proof_image.filename if proof_image else 'None'}")
        print(f"🔍 proof_image.content_type: {proof_image.content_type if proof_image else 'None'}")
        
        # 檢查文件是否存在且有效（不僅檢查 filename，也檢查文件大小）
        # 注意：即使 filename 為空，也要檢查文件內容（可能是瀏覽器兼容性問題）
        if proof_image:
            # 檢查文件是否有內容（通過檢查 content_length 或嘗試讀取）
            file_has_content = False
            if hasattr(proof_image, 'content_length') and proof_image.content_length:
                file_has_content = proof_image.content_length > 0
            elif proof_image.filename and len(proof_image.filename.strip()) > 0:
                file_has_content = True
            else:
                # 嘗試讀取文件內容來判斷
                try:
                    proof_image.seek(0)
                    content = proof_image.read(1)
                    proof_image.seek(0)  # 重置指針
                    file_has_content = len(content) > 0
                except:
                    file_has_content = False
            
            if file_has_content:
                try:
                    # 確保目錄存在
                    os.makedirs(ABSENCE_PROOF_FOLDER, exist_ok=True)
                    # 確保檔名安全，並加上 user_id 和時間戳以避免重複
                    original_filename = proof_image.filename or f"proof_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
                    # 如果沒有副檔名，根據 content_type 添加
                    if '.' not in original_filename:
                        ext_map = {
                            'image/jpeg': '.jpg',
                            'image/jpg': '.jpg',
                            'image/png': '.png',
                            'image/gif': '.gif'
                        }
                        ext = ext_map.get(proof_image.content_type, '.jpg')
                        original_filename = original_filename + ext
                    
                    filename = secure_filename(f"{user_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{original_filename}")
                    save_path = os.path.join(ABSENCE_PROOF_FOLDER, filename)
                    # 將路徑中的反斜杠轉換為正斜杠（統一格式）
                    save_path = save_path.replace('\\', '/')
                    proof_image.save(save_path)
                    # 確保保存到資料庫的路徑是相對路徑（統一格式）
                    # 如果 save_path 是絕對路徑，提取相對路徑部分
                    if os.path.isabs(save_path):
                        # 獲取當前工作目錄，然後計算相對路徑
                        abs_absence_folder = os.path.abspath(ABSENCE_PROOF_FOLDER)
                        if save_path.startswith(abs_absence_folder):
                            image_path = save_path.replace(abs_absence_folder, ABSENCE_PROOF_FOLDER).replace('\\', '/')
                        else:
                            # 如果無法計算相對路徑，使用原始路徑
                            image_path = save_path.replace('\\', '/')
                    else:
                        image_path = save_path  # 已經是相對路徑
                    print(f"✅ 缺勤佐證圖片已保存: {save_path}")
                    print(f"✅ 儲存到資料庫的路徑: {image_path}")
                    print(f"✅ 文件大小: {os.path.getsize(save_path) if os.path.exists(save_path) else 'N/A'} bytes")
                    print(f"✅ 文件是否存在: {os.path.exists(save_path)}")
                except Exception as e:
                    print(f"⚠️ 儲存缺勤佐證圖片失敗: {e}")
                    traceback.print_exc()
                    # 即使圖片保存失敗，也繼續處理其他資料（image_path 保持為 None）
            else:
                print(f"⚠️ proof_image 文件內容為空: filename={proof_image.filename if proof_image else 'None'}, content_length={getattr(proof_image, 'content_length', 'N/A')}")
        else:
            print(f"⚠️ proof_image 對象為 None")
    else:
        print(f"⚠️ request.files 中沒有 'proof_image' 鍵")
        print(f"🔍 可用的文件鍵: {list(request.files.keys())}")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 插入缺勤紀錄到 absence_records 表格
        print(f"📝 準備插入缺勤紀錄:")
        print(f"   user_id={user_id}")
        print(f"   absence_date={absence_date}")
        print(f"   absence_type={absence_type}")
        print(f"   duration_units={duration_units}")
        print(f"   reason={reason}")
        print(f"   image_path={image_path}")
        print(f"   image_path type={type(image_path)}")
        print(f"   image_path is None={image_path is None}")
        
        # 檢查 absence_records 表是否有 semester_id 欄位
        cursor.execute("SHOW COLUMNS FROM absence_records LIKE 'semester_id'")
        has_semester_id = cursor.fetchone() is not None
        
        # 根據 absence_date 計算 semester_id（如果表有該欄位）
        semester_id = None
        if has_semester_id:
            try:
                from datetime import datetime as dt
                absence_dt = dt.strptime(absence_date, '%Y-%m-%d')
                # 查詢包含該日期的學期
                cursor.execute("""
                    SELECT id FROM semesters 
                    WHERE start_date <= %s AND end_date >= %s
                    LIMIT 1
                """, (absence_date, absence_date))
                semester_row = cursor.fetchone()
                if semester_row:
                    semester_id = semester_row['id']
                    print(f"   semester_id={semester_id} (根據日期 {absence_date} 計算)")
            except Exception as e:
                print(f"⚠️ 計算 semester_id 失敗: {e}")
        
        if has_semester_id and semester_id:
            cursor.execute("""
                INSERT INTO absence_records 
                (user_id, absence_date, absence_type, duration_units, reason, image_path, semester_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (user_id, absence_date, absence_type, duration_units, reason, image_path, semester_id))
        else:
            cursor.execute("""
                INSERT INTO absence_records 
                (user_id, absence_date, absence_type, duration_units, reason, image_path)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (user_id, absence_date, absence_type, duration_units, reason, image_path))
        
        conn.commit()
        record_id = cursor.lastrowid
        print(f"✅ 缺勤紀錄已成功插入資料庫 (ID: {record_id})")
        print(f"✅ image_path 已保存: {image_path}")
        
        # 驗證插入的資料
        cursor.execute("SELECT image_path FROM absence_records WHERE id = %s", (record_id,))
        inserted_record = cursor.fetchone()
        if inserted_record:
            print(f"✅ 驗證：資料庫中的 image_path = {inserted_record.get('image_path')}")
        else:
            print(f"⚠️ 警告：無法驗證插入的資料")

        return jsonify({"success": True, "message": "缺勤紀錄提交成功！"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"資料庫操作失敗: {str(e)}"}), 500

    finally:
        cursor.close()
        conn.close()

# -------------------------
# 缺勤預設學期範圍 API
# -------------------------
@resume_bp.route('/api/absence/default_range', methods=['GET'])
def get_absence_default_range():
    """取得缺勤預設學期範圍（支持按入學年度查詢）"""
    admission_year = request.args.get('admission_year', None)
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        if admission_year:
            # 如果指定了入學年度，查詢該年度的設定
            # 先檢查表是否有 admission_year 欄位
            cursor.execute("SHOW COLUMNS FROM absence_default_semester_range LIKE 'admission_year'")
            has_admission_year = cursor.fetchone() is not None
            
            if has_admission_year:
                cursor.execute("""
                    SELECT start_semester_code, end_semester_code, admission_year
                    FROM absence_default_semester_range
                    WHERE admission_year = %s
                    ORDER BY id DESC
                    LIMIT 1
                """, (admission_year,))
            else:
                # 如果表沒有 admission_year 欄位，使用舊邏輯（向後兼容）
                cursor.execute("""
                    SELECT start_semester_code, end_semester_code
                    FROM absence_default_semester_range
                    ORDER BY id DESC
                    LIMIT 1
                """)
        else:
            # 沒有指定入學年度，返回所有設定（用於管理頁面）
            cursor.execute("SHOW COLUMNS FROM absence_default_semester_range LIKE 'admission_year'")
            has_admission_year = cursor.fetchone() is not None
            
            if has_admission_year:
                cursor.execute("""
                    SELECT id, start_semester_code, end_semester_code, admission_year, created_at, updated_at
                    FROM absence_default_semester_range
                    ORDER BY admission_year DESC, id DESC
                """)
                results = cursor.fetchall()
                return jsonify({
                    "success": True,
                    "ranges": results
                })
            else:
                # 向後兼容：返回單一設定
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
                "defaultStart": result['start_semester_code'],
                "defaultEnd": result['end_semester_code']
            })
        else:
            # 如果沒有設定，返回空值
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

@resume_bp.route('/api/absence/default_range', methods=['POST'])
def update_absence_default_range():
    """更新缺勤預設學期範圍（後台用，支持按入學年度設定）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    start_code = data.get('start', '').strip()
    end_code = data.get('end', '').strip()
    admission_year = data.get('admission_year', None)  # 可選：入學年度
    range_id = data.get('id', None)  # 可選：要更新的記錄ID
    
    if not start_code or not end_code:
        return jsonify({"success": False, "message": "請提供開始和結束學期代碼"}), 400
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 檢查表是否有 admission_year 欄位
        cursor.execute("SHOW COLUMNS FROM absence_default_semester_range LIKE 'admission_year'")
        has_admission_year = cursor.fetchone() is not None
        
        if has_admission_year and admission_year:
            # 如果有 admission_year 欄位且提供了入學年度
            if range_id:
                # 更新指定ID的記錄
                cursor.execute("""
                    UPDATE absence_default_semester_range
                    SET start_semester_code = %s, end_semester_code = %s, admission_year = %s, updated_at = NOW()
                    WHERE id = %s
                """, (start_code, end_code, admission_year, range_id))
            else:
                # 檢查是否已存在該入學年度的記錄
                cursor.execute("""
                    SELECT id FROM absence_default_semester_range 
                    WHERE admission_year = %s
                    LIMIT 1
                """, (admission_year,))
                exists = cursor.fetchone()
                
                if exists:
                    # 更新現有記錄
                    cursor.execute("""
                        UPDATE absence_default_semester_range
                        SET start_semester_code = %s, end_semester_code = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (start_code, end_code, exists['id']))
                else:
                    # 插入新記錄
                    cursor.execute("""
                        INSERT INTO absence_default_semester_range (start_semester_code, end_semester_code, admission_year, created_at, updated_at)
                        VALUES (%s, %s, %s, NOW(), NOW())
                    """, (start_code, end_code, admission_year))
        else:
            # 向後兼容：沒有 admission_year 欄位或沒有提供入學年度
            if range_id:
                cursor.execute("""
                    UPDATE absence_default_semester_range
                    SET start_semester_code = %s, end_semester_code = %s
                    WHERE id = %s
                """, (start_code, end_code, range_id))
            else:
                cursor.execute("SELECT id FROM absence_default_semester_range LIMIT 1")
                exists = cursor.fetchone()
                
                if exists:
                    cursor.execute("""
                        UPDATE absence_default_semester_range
                        SET start_semester_code = %s, end_semester_code = %s
                        WHERE id = %s
                    """, (start_code, end_code, exists['id']))
                else:
                    cursor.execute("""
                        INSERT INTO absence_default_semester_range (start_semester_code, end_semester_code)
                        VALUES (%s, %s)
                    """, (start_code, end_code))
        
        conn.commit()
        return jsonify({
            "success": True,
            "message": "預設學期範圍已更新",
            "defaultStart": start_code,
            "defaultEnd": end_code
        })
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": f"更新預設學期範圍失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

@resume_bp.route('/api/absence/default_range/<int:range_id>', methods=['DELETE'])
def delete_absence_default_range(range_id):
    """刪除指定ID的缺勤預設學期範圍"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("DELETE FROM absence_default_semester_range WHERE id = %s", (range_id,))
        conn.commit()
        
        return jsonify({
            "success": True,
            "message": "預設學期範圍已刪除"
        })
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": f"刪除預設學期範圍失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

@resume_bp.route('/api/absence/admission_years', methods=['GET'])
def get_admission_years():
    """獲取所有入學年度列表（用於管理頁面）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 從 classes 表獲取所有不重複的入學年度
        cursor.execute("""
            SELECT DISTINCT admission_year
            FROM classes
            WHERE admission_year IS NOT NULL
            ORDER BY admission_year DESC
        """)
        years = cursor.fetchall()
        
        # 處理入學年度格式（可能是3位數或4位數）
        admission_years = []
        for year in years:
            admission_year = year['admission_year']
            if admission_year:
                # 如果是4位數（如1122），提取前3位
                if isinstance(admission_year, int) and admission_year >= 1000:
                    admission_year = admission_year // 10
                elif isinstance(admission_year, str) and len(admission_year) >= 4:
                    admission_year = int(admission_year[:3])
                admission_years.append(admission_year)
        
        # 去重並排序
        admission_years = sorted(list(set(admission_years)), reverse=True)
        
        return jsonify({
            "success": True,
            "admission_years": admission_years
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"取得入學年度列表失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------
# 獲取學生可用的學期列表（根據預設範圍和入學年度過濾）
# -------------------------
@resume_bp.route('/api/absence/available_semesters', methods=['GET'])
def get_available_semesters_for_student():
    """獲取學生可用的學期列表（根據預設範圍和入學年度過濾）"""
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    user_id = session.get('user_id')
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 1. 獲取學生的入學年度
        cursor.execute("""
            SELECT c.admission_year
            FROM users u
            LEFT JOIN classes c ON u.class_id = c.id
            WHERE u.id = %s
        """, (user_id,))
        student_info = cursor.fetchone()
        
        admission_year = None
        if student_info and student_info.get('admission_year'):
            admission_year = student_info['admission_year']
            # 如果 admission_year 是字符串，嘗試轉換
            if isinstance(admission_year, str):
                # 如果是4位數（如1122），提取前3位作為年度
                if len(admission_year) >= 4 and admission_year[:3].isdigit():
                    admission_year = int(admission_year[:3])
                elif admission_year.isdigit():
                    admission_year = int(admission_year)
            elif isinstance(admission_year, int):
                # 如果是4位數（如1122），提取前3位作為年度
                if admission_year >= 1000:
                    admission_year = admission_year // 10
        
        # 2. 獲取預設學期範圍（根據入學年度）
        # 先檢查表是否有 admission_year 欄位
        cursor.execute("SHOW COLUMNS FROM absence_default_semester_range LIKE 'admission_year'")
        has_admission_year = cursor.fetchone() is not None
        
        if has_admission_year and admission_year:
            # 如果有 admission_year 欄位且獲取到入學年度，查詢該年度的設定
            cursor.execute("""
                SELECT start_semester_code, end_semester_code
                FROM absence_default_semester_range
                WHERE admission_year = %s
                ORDER BY id DESC
                LIMIT 1
            """, (admission_year,))
        else:
            # 向後兼容：查詢所有設定（取最新的）
            cursor.execute("""
                SELECT start_semester_code, end_semester_code
                FROM absence_default_semester_range
                ORDER BY id DESC
                LIMIT 1
            """)
        default_range = cursor.fetchone()
        
        if not default_range or not default_range.get('start_semester_code') or not default_range.get('end_semester_code'):
            # 如果沒有設定預設範圍，返回空列表
            return jsonify({
                "success": True,
                "semesters": [],
                "message": "尚未設定預設學期範圍"
            })
        
        start_code = default_range['start_semester_code']
        end_code = default_range['end_semester_code']
        
        # 3. 獲取所有在預設範圍內的學期
        cursor.execute("""
            SELECT id, code, start_date, end_date, is_active, created_at
            FROM semesters
            WHERE code >= %s AND code <= %s
            ORDER BY code ASC
        """, (start_code, end_code))
        all_semesters = cursor.fetchall()
        
        # 4. 根據入學年度過濾學期
        filtered_semesters = []
        if admission_year:
            # 110年度入學的學生應該只顯示：
            # - 1122（入學年度+2的第2學期）
            # - 1131（入學年度+3的第1學期）
            # 這些是實習相關的學期
            
            target_semester_codes = [
                f"{admission_year + 2}2",  # 入學年度+2的第2學期（如1122）
                f"{admission_year + 3}1"   # 入學年度+3的第1學期（如1131）
            ]
            
            for semester in all_semesters:
                semester_code = semester['code']
                if semester_code in target_semester_codes:
                    filtered_semesters.append(semester)
        else:
            # 如果無法獲取入學年度，只根據預設範圍過濾（不進行入學年度過濾）
            filtered_semesters = all_semesters
        
        # 格式化日期
        for s in filtered_semesters:
            if isinstance(s.get('start_date'), datetime):
                s['start_date'] = s['start_date'].strftime("%Y-%m-%d")
            if isinstance(s.get('end_date'), datetime):
                s['end_date'] = s['end_date'].strftime("%Y-%m-%d")
            if isinstance(s.get('created_at'), datetime):
                s['created_at'] = s['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        return jsonify({
            "success": True,
            "semesters": filtered_semesters
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"取得可用學期列表失敗: {str(e)}"}), 500
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
# 下載已修習專業核心科目Excel模板
# -------------------------
@resume_bp.route('/api/download_course_template', methods=['GET'])
def download_course_template():
    """下載已修習專業核心科目Excel模板"""
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    try:
        # 使用現有的模板文件
        template_path = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'static', 'examples', '已修習專業核心科目範本.xlsx')
        template_path = os.path.abspath(template_path)
        
        if not os.path.exists(template_path):
            return jsonify({"success": False, "message": "模板文件不存在"}), 404
        
        return send_file(
            template_path,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='已修習專業核心科目範本.xlsx'
        )
    except Exception as e:
        print("❌ 下載模板錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": "下載模板失敗"}), 500

# -------------------------
# 上傳並解析已修習專業核心科目Excel
# -------------------------
@resume_bp.route('/api/upload_course_excel', methods=['POST'])
def upload_course_excel():
    """上傳並解析已修習專業核心科目Excel文件"""
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({"success": False, "message": "未授權"}), 403
    
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "未找到上傳文件"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "文件名稱不能為空"}), 400
    
    # 檢查文件格式
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({"success": False, "message": "只支援Excel文件(.xlsx, .xls)"}), 400
    
    try:
        # 讀取Excel文件（不使用data_only，這樣可以獲取原始格式）
        file_content = file.read()
        wb = load_workbook(io.BytesIO(file_content), data_only=False)
        ws = wb.active
        
        # 也創建一個data_only版本用於讀取公式計算結果（D欄的修課狀態）
        wb_data = load_workbook(io.BytesIO(file_content), data_only=True)
        ws_data = wb_data.active
        
        def get_cell_value(cell, data_cell=None):
            """獲取單元格值，處理日期格式問題"""
            if cell is None:
                return None
            
            value = cell.value
            if value is None:
                return None
            
            # 檢查是否是日期類型
            if isinstance(value, datetime):
                # 如果是日期，嘗試從原始格式恢復
                # 檢查number_format來判斷原始格式
                number_format = cell.number_format
                # 如果是日期格式（包含d、m、y等），嘗試恢復
                if any(char in str(number_format).lower() for char in ['d', 'm', 'y']):
                    # 嘗試轉換為 mm/dd 格式
                    try:
                        month = value.month
                        day = value.day
                        # 如果月份和日期相同（如2/2、3/3），返回分數格式
                        if month == day:
                            return f"{month}/{day}"
                        else:
                            return f"{month}/{day}"
                    except:
                        pass
                # 返回日期字符串表示
                return value.strftime('%Y-%m-%d %H:%M:%S')
            
            # 如果是數字，但格式看起來像是分數（檢查number_format）
            if isinstance(value, (int, float)):
                number_format = str(cell.number_format or '')
                # 如果格式中包含分數符號，嘗試恢復
                if '/' in number_format:
                    # 嘗試從日期恢復（如果月份和日期相同）
                    try:
                        if isinstance(value, float) and 1 <= int(value) <= 12:
                            # 可能是日期序列號，嘗試轉換
                            from openpyxl.utils.datetime import from_excel
                            date_val = from_excel(value)
                            if date_val.month == date_val.day:
                                return f"{date_val.month}/{date_val.day}"
                    except:
                        pass
            
            return value
        
        courses = []
        # 從第2行開始讀取（第1行是標題）
        for row_idx in range(2, ws.max_row + 1):
            # A欄：課程名稱，B欄：學分數，C欄：成績，D欄：修課狀態
            cell_name = ws.cell(row=row_idx, column=1)
            cell_credits = ws.cell(row=row_idx, column=2)
            cell_grade = ws.cell(row=row_idx, column=3)
            cell_status = ws_data.cell(row=row_idx, column=4)  # 使用data_only版本讀取公式結果
            
            course_name = get_cell_value(cell_name)
            credits_raw = cell_credits.value  # 直接獲取原始值，不使用get_cell_value（因為需要特殊處理學分數）
            grade = get_cell_value(ws.cell(row=row_idx, column=3))
            status = get_cell_value(cell_status) if cell_status.value is not None else None
            
            # 如果課程名稱為空，跳過這一行
            if not course_name or str(course_name).strip() == '':
                continue
            
            # 轉換為字符串並清理
            course_name = str(course_name).strip()
            
            # 處理學分數：特別處理日期格式
            credits_str = ''
            if credits_raw is not None:
                # 如果是datetime對象（Excel將"2/2"識別為日期）
                if isinstance(credits_raw, datetime):
                    month = credits_raw.month
                    day = credits_raw.day
                    # 恢復為分數格式（如"2/2"、"3/3"）
                    credits_str = f"{month}/{day}"
                # 如果是日期格式的字符串（如"2025-01-01 00:00:00"）
                elif isinstance(credits_raw, str):
                    credits_str = credits_raw.strip()
                    # 檢查是否是日期格式字符串
                    if ('2025-' in credits_str or '2024-' in credits_str or '2026-' in credits_str) and ('-' in credits_str):
                        try:
                            # 嘗試解析日期
                            date_part = credits_str.split()[0] if ' ' in credits_str else credits_str
                            date_obj = datetime.strptime(date_part, '%Y-%m-%d')
                            month = date_obj.month
                            day = date_obj.day
                            # 恢復為分數格式
                            credits_str = f"{month}/{day}"
                        except:
                            # 解析失敗，使用format_credits格式化
                            credits_str = format_credits(credits_str)
                    else:
                        # 不是日期格式，使用format_credits格式化
                        credits_str = format_credits(credits_str)
                else:
                    # 其他類型（數字等），格式化後轉換為字符串
                    credits_str = format_credits(credits_raw)
            
            # 保留原始學分數（B欄的值），不管是否未修課
            original_credits_str = credits_str
            
            # 處理成績：轉換為字符串
            grade_str = str(grade).strip() if grade else ''
            
            # 判斷是否未修課（D欄為0或者C欄為空）
            is_not_taken = False
            if status is not None:
                # 如果是數字，判斷是否為0
                try:
                    status_num = float(status)
                    is_not_taken = (status_num == 0)
                except (ValueError, TypeError):
                    # 如果是字符串，檢查是否為"0"
                    is_not_taken = (str(status).strip() == '0')
            elif not grade_str:  # 如果C欄為空，也視為未修課
                is_not_taken = True
            
            # 如果未修課，顯示學分數為0，但保留原始學分數
            display_credits = '0' if is_not_taken else original_credits_str
            
            courses.append({
                'name': course_name,
                'credits': original_credits_str,  # 保留原始學分數，前端會根據isNotTaken決定顯示值
                'grade': grade_str,
                'isNotTaken': is_not_taken
            })
        
        # 寫入course_grades表
        student_id = session.get('user_id')
        if not student_id:
            return jsonify({"success": False, "message": "無法取得學生ID"}), 400
        
        # 取得學號（username）
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT username FROM users WHERE id = %s", (student_id,))
            user = cursor.fetchone()
            if not user:
                return jsonify({"success": False, "message": "找不到學生資料"}), 400
            
            student_number = user['username']
            
            # 檢查是否有SemesterID欄位
            cursor.execute("SHOW COLUMNS FROM course_grades LIKE 'SemesterID'")
            has_semester_id = cursor.fetchone() is not None
            
            # 取得當前學期ID（如果有）
            semester_id = None
            if has_semester_id:
                semester_id = get_current_semester_id(cursor)
            
            # 刪除該學生的舊資料
            if has_semester_id and semester_id:
                cursor.execute(
                    "DELETE FROM course_grades WHERE StuID=%s AND IFNULL(SemesterID,'')=%s",
                    (student_number, semester_id)
                )
            else:
                cursor.execute("DELETE FROM course_grades WHERE StuID=%s", (student_number,))
            
            # 重新插入Excel的成績
            insert_count = 0
            seen_courses = set()
            for course in courses:
                course_name = course['name'].strip()
                if not course_name or course_name in seen_courses:
                    continue
                seen_courses.add(course_name)
                
                # 如果未修課，學分數設為0，成績為空
                credits = '0' if course.get('isNotTaken', False) else course.get('credits', '')
                grade = '' if course.get('isNotTaken', False) else course.get('grade', '')
                
                if has_semester_id and semester_id:
                    cursor.execute("""
                        INSERT INTO course_grades (StuID, CourseName, Credits, Grade, SemesterID)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (student_number, course_name, credits, grade, semester_id))
                else:
                    cursor.execute("""
                        INSERT INTO course_grades (StuID, CourseName, Credits, Grade)
                        VALUES (%s, %s, %s, %s)
                    """, (student_number, course_name, credits, grade))
                insert_count += 1
            
            conn.commit()
            
            return jsonify({
                "success": True,
                "courses": courses,
                "count": insert_count,
                "message": f"成功匯入 {insert_count} 門課程資料並寫入資料庫"
            })
        except Exception as e:
            conn.rollback()
            print("❌ 寫入course_grades錯誤:", e)
            traceback.print_exc()
            return jsonify({"success": False, "message": f"寫入資料庫失敗: {str(e)}"}), 500
        finally:
            cursor.close()
            conn.close()
    except Exception as e:
        print("❌ 解析Excel錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": f"解析Excel失敗: {str(e)}"}), 500

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
            # 2. 指導老師綁定的學生（通過 teacher_student_relations）
            # 3. 選擇了該老師作為指導老師的公司的學生（通過 student_preferences 和 internship_companies）
        if role in ["teacher", "class_teacher"]:
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
                    r.created_at,
                    COALESCE(
                        (SELECT ic3.company_name 
                         FROM student_preferences sp3
                         JOIN internship_companies ic3 ON sp3.company_id = ic3.id
                         WHERE sp3.student_id = u.id 
                         AND ic3.advisor_user_id = %s
                         ORDER BY sp3.preference_order ASC
                         LIMIT 1),
                        ''
                    ) AS company_name   
                FROM resumes r
                JOIN users u ON r.user_id = u.id
                LEFT JOIN classes c ON u.class_id = c.id
                WHERE EXISTS (
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
                    SELECT 1
                    FROM student_preferences sp
                    JOIN internship_companies ic2 ON sp.company_id = ic2.id
                    WHERE sp.student_id = u.id AND ic2.advisor_user_id = %s
                )
                ORDER BY c.name, u.name
            """
            sql_params = (user_id, user_id, user_id, user_id, user_id, user_id)

            cursor.execute(sql_query, sql_params)
            resumes = cursor.fetchall()

            if not resumes:
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

# -------------------------
# 提供上傳文件的訪問
# -------------------------
@resume_bp.route('/uploads/<path:filename>')
def serve_uploaded_file(filename):
    """提供上傳文件的訪問"""
    try:
        # 構建文件完整路徑
        file_path = os.path.join('uploads', filename)
        # 確保路徑安全（防止路徑遍歷攻擊）
        if not os.path.abspath(file_path).startswith(os.path.abspath('uploads')):
            return jsonify({"success": False, "message": "無效的路徑"}), 403
        
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return send_file(file_path)
        else:
            return jsonify({"success": False, "message": "文件不存在"}), 404
    except Exception as e:
        print(f"❌ 提供上傳文件失敗: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "message": "文件訪問失敗"}), 500

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
        # 創建上傳目錄
        upload_dir = os.path.join('uploads', 'standard_courses')
        os.makedirs(upload_dir, exist_ok=True)
        
        # 生成安全的文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_filename = secure_filename(file.filename)
        filename = f"{timestamp}_{safe_filename}"
        file_path = os.path.join(upload_dir, filename)
        
        # 保存文件
        file.seek(0)  # 重置文件指針
        abs_file_path = os.path.abspath(file_path)
        os.makedirs(os.path.dirname(abs_file_path), exist_ok=True)
        with open(abs_file_path, 'wb') as f:
            f.write(file_content)
        
        # 數據庫中的相對路徑
        db_file_path = file_path.replace('\\', '/')
        
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
        
        # 處理相對路徑
        if not os.path.isabs(file_path):
            abs_file_path = os.path.abspath(file_path)
        else:
            abs_file_path = file_path
        
        if not os.path.exists(abs_file_path):
            return jsonify({"success": False, "message": "文件不存在"}), 404
        
        # 獲取原始文件名（從路徑中提取）
        original_filename = os.path.basename(file_path)
        # 如果文件名包含時間戳，嘗試提取原始文件名
        if '_' in original_filename:
            parts = original_filename.split('_', 1)
            if len(parts) > 1:
                original_filename = parts[1]
        
        return send_file(abs_file_path, as_attachment=True, download_name=original_filename)
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
# 科助上傳標準課程頁面
# -------------------------
@resume_bp.route('/ta/upload_standard_courses')
def upload_standard_courses_page():
    if 'user_id' not in session or session.get('role') != 'ta':
        return redirect('/login')
    return render_template('ta/upload_standard_courses.html')