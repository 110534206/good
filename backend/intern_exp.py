from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for
from config import get_db
import traceback
from datetime import datetime, timezone, timedelta
from email_service import send_email, send_interview_email, send_admission_email
from notification import create_notification

def get_taiwan_time():
    """取得目前的台灣時間 (UTC+8)"""
    return datetime.utcnow() + timedelta(hours=8)

intern_exp_bp = Blueprint('intern_exp_bp', __name__, url_prefix='/intern_experience')

# --------------------
# Helpers
# --------------------
def require_login():
    return 'user_id' in session

def to_minguo(year):
    """輸入可為西元年或民國年，若是西元(>1911)則轉民國；若已是民國(<2000)則直接回傳。"""
    try:
        y = int(year)
        if y > 1911:
            return y - 1911
        return y
    except Exception:
        return None

def to_gregorian_if_needed(year):
    """若傳入為民國（例如 110），回傳西元；若是西元則回傳原本值。方便內部需要西元時使用（此專案多數不需要）。"""
    try:
        y = int(year)
        if y < 2000:
            return y + 1911
        return y
    except Exception:
        return None

# --------------------
# 頁面：整合列表 + 新增（前端 HTML）
# --------------------
@intern_exp_bp.route('/')
def page_intern_exp():
    if not require_login():
        return redirect(url_for('auth_bp.login'))

    # 新增邏輯：從 URL 取得 company_id 參數
    company_id = request.args.get('company_id')

    # 將 company_id 與目前登入者資訊傳遞給前端範本
    return render_template(
        'user_shared/intern_experience.html',
        initial_company_id=company_id,
        user_id=session.get('user_id')
    )

# --------------------
# API：公司清單（下拉用） - 從資料表抓取所有公司資料
# --------------------
@intern_exp_bp.route('/api/companies', methods=['GET'])
def get_companies():
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        # 從 internship_companies 資料表抓取所有公司資料（不限狀態）
        # 移除狀態限制，顯示資料表中的所有公司
        cursor.execute("""
            SELECT id, company_name, status 
            FROM internship_companies 
            ORDER BY company_name ASC
        """)
        companies = cursor.fetchall()
        
        # 確保回傳資料格式正確
        if not companies:
            companies = []
        
        return jsonify({
            "success": True, 
            "data": companies,
            "count": len(companies)
        })
    except Exception as e:
        print(f"抓取公司資料時發生錯誤: {traceback.format_exc()}")
        return jsonify({
            "success": False, 
            "message": f"無法載入公司資料: {str(e)}"
        }), 500

# --------------------
# API：取得某公司職缺
# --------------------
@intern_exp_bp.route('/api/jobs/<int:company_id>', methods=['GET'])
def get_jobs_by_company(company_id):
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, title FROM internship_jobs WHERE company_id = %s ORDER BY title", (company_id,))
        jobs = cursor.fetchall()
        return jsonify({"success": True, "data": jobs})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500

# --------------------
# API：心得列表（搜尋、年份篩選）- 顯示所有公開心得
# 移除 is_public 限制以確保所有使用者都能看到心得（避免資料庫欄位類型問題）
# --------------------
@intern_exp_bp.route('/api/list', methods=['GET'])
def get_experience_list():
    try:
        keyword = request.args.get('keyword', '').strip()
        year = request.args.get('year', '').strip()  # 前端會傳民國年
        company_id = request.args.get('company_id', '').strip()
        my_experiences = request.args.get('my_experiences', '').strip()  # 'true' 表示只顯示自己的心得
        include_unapproved = request.args.get('include_unapproved', '').strip()  # 'true'：包含未公開心得（給老師審核用）
        db = get_db()
        cursor = db.cursor(dictionary=True)
        
        # 取得目前登入的使用者 ID（如果有的話）
        current_user_id = session.get('user_id') if 'user_id' in session else None
        current_role = session.get('role')

        # 顯示心得列表
        # 使用 LEFT JOIN 確保即使使用者或公司資料不存在也能顯示心得
        query = """
            SELECT ie.id, ie.year, ie.content, ie.rating, ie.created_at, ie.is_public,
                   u.id AS author_id, COALESCE(u.name, '未知使用者') AS author, 
                   c.id AS company_id, COALESCE(c.company_name, '未填寫公司') AS company_name,
                   j.id AS job_id, j.title AS job_title, j.salary AS job_salary
            FROM internship_experiences ie
            LEFT JOIN users u ON ie.user_id = u.id
            LEFT JOIN internship_companies c ON ie.company_id = c.id
            LEFT JOIN internship_jobs j ON ie.job_id = j.id
            WHERE 1=1
        """
        params = []

        # 過濾掉「已錄取」的自動記錄（實習錄取不應顯示在實習心得中）
        query += " AND (ie.content IS NULL OR ie.content != '已錄取')"

        # 如果要求只顯示自己的心得
        if my_experiences.lower() == 'true' and current_user_id:
            # 顯示自己的所有心得（包括未公開的）
            query += " AND ie.user_id = %s"
            params.append(current_user_id)
        else:
            # 一般情況：只顯示已公開心得
            # 若 include_unapproved=true 且為老師/主任/班導，則顯示所有心得（供審核使用）
            if not (include_unapproved.lower() == 'true' and current_role in ['teacher', 'director', 'class_teacher']):
                query += " AND ie.is_public = 1"
            else:
                # 審核模式：只顯示該指導老師負責的廠商的心得
                if include_unapproved.lower() == 'true' and current_role in ['teacher', 'director', 'class_teacher'] and current_user_id:
                    query += " AND c.advisor_user_id = %s"
                    params.append(current_user_id)

        if keyword:
            query += " AND c.company_name LIKE %s"
            params.append(f"%{keyword}%")

        if year:
            # year 前端傳民國年（如110），資料庫也儲存為民國年
            query += " AND ie.year = %s"
            params.append(year)

        if company_id:
            query += " AND ie.company_id = %s"
            params.append(company_id)

        query += " ORDER BY ie.created_at DESC"

        cursor.execute(query, params)
        experiences = cursor.fetchall()

        taiwan_tz = timezone(timedelta(hours=8))

        # 確保 year 為 int（並以民國年輸出）
        for e in experiences:
            try:
                e['year'] = int(e['year']) if e.get('year') is not None else None
            except:
                e['year'] = None

            created_at = e.get('created_at')
            if isinstance(created_at, datetime):
                e['created_at'] = created_at.astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
            elif created_at:
                try:
                    parsed = datetime.fromisoformat(str(created_at))
                    e['created_at'] = parsed.astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
                except:
                    e['created_at'] = str(created_at)

        # 記錄查詢結果數量（用於除錯）
        client_ip = request.remote_addr
        print(f"[心得列表] IP={client_ip}, 查詢條件: keyword={keyword}, year={year}, company_id={company_id}, 結果數量: {len(experiences)}")
        
        # 如果沒有資料，記錄更多資訊
        if len(experiences) == 0:
            # 檢查資料庫中是否有任何心得
            cursor.execute("SELECT COUNT(*) as total FROM internship_experiences")
            total_count = cursor.fetchone()
            print(f"[心得列表] 資料庫中心得總數: {total_count.get('total', 0) if total_count else 0}")
        
        return jsonify({
            "success": True, 
            "data": experiences,
            "count": len(experiences),
            "debug": {
                "keyword": keyword,
                "year": year,
                "company_id": company_id,
                "client_ip": client_ip
            }
        })
    except Exception as e:
        error_msg = f"取得心得列表時發生錯誤: {str(e)}"
        print(f"[錯誤] {error_msg}")
        print(traceback.format_exc())
        return jsonify({
            "success": False, 
            "message": error_msg
        }), 500

# --------------------
# API：查看單篇心得
# --------------------
@intern_exp_bp.route('/api/view/<int:exp_id>', methods=['GET'])
def view_experience(exp_id):
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT ie.id, ie.year, ie.content, ie.rating, ie.created_at, ie.user_id,
                   u.name AS author,
                   c.id AS company_id, c.company_name,
                   j.id AS job_id, j.title AS job_title, j.salary AS job_salary
            FROM internship_experiences ie
            JOIN users u ON ie.user_id = u.id
            LEFT JOIN internship_companies c ON ie.company_id = c.id
            LEFT JOIN internship_jobs j ON ie.job_id = j.id
            WHERE ie.id = %s
        """, (exp_id,))
        exp = cursor.fetchone()
        if exp:
            try:
                exp['year'] = int(exp['year']) if exp.get('year') is not None else None
            except:
                exp['year'] = None

            taiwan_tz = timezone(timedelta(hours=8))
            created_at = exp.get('created_at')
            if isinstance(created_at, datetime):
                exp['created_at'] = created_at.astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
            elif created_at:
                try:
                    parsed = datetime.fromisoformat(str(created_at))
                    exp['created_at'] = parsed.astimezone(taiwan_tz).strftime("%Y-%m-%d %H:%M")
                except:
                    exp['created_at'] = str(created_at)

        return jsonify({"success": True, "data": exp})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500

# --------------------
# API：新增心得（接收 company_id, job_id, year, rating, content）
#       - year 可為西元或民國，會儲存為民國年
# --------------------
@intern_exp_bp.route('/api/add', methods=['POST'])
def add_experience():
    try:
        if not require_login():
            return jsonify({"success": False, "message": "請先登入"}), 403

        user_id = session['user_id']
        data = request.get_json() or {}
        company_id = data.get('company_id') or None
        job_id = data.get('job_id') or None
        year_raw = data.get('year')
        content = data.get('content') or ''
        rating = data.get('rating') or None

        # 轉換 year（若用者傳西元或民國皆接受，統一儲存為民國年）
        year = None
        if year_raw is not None and year_raw != '':
            try:
                year = to_minguo(int(year_raw))
            except:
                year = None

        db = get_db()
        cursor = db.cursor(dictionary=True)

        # 新增心得時預設為未公開（待指導老師審核後才公開）
        cursor.execute("""
            INSERT INTO internship_experiences
                (user_id, company_id, job_id, year, content, rating, is_public, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, 0, NOW())
        """, (user_id, company_id, job_id, year, content, rating))

        # 獲取該公司的指導老師資訊
        advisor_id = None
        advisor_name = None
        if company_id:
            cursor.execute("""
                SELECT advisor_user_id 
                FROM internship_companies 
                WHERE id = %s
            """, (company_id,))
            company = cursor.fetchone()
            if company and company.get('advisor_user_id'):
                advisor_id = company['advisor_user_id']
                cursor.execute("""
                    SELECT id, name 
                    FROM users 
                    WHERE id = %s AND role IN ('teacher', 'director', 'class_teacher')
                """, (advisor_id,))
                advisor = cursor.fetchone()
                if advisor:
                    advisor_name = advisor.get('name')

        db.commit()
        return jsonify({
            "success": True, 
            "message": "心得已新增",
            "advisor_id": advisor_id,
            "advisor_name": advisor_name
        })
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500

# --------------------
# API：刪除心得（只能刪自己的）
# --------------------
@intern_exp_bp.route('/api/delete/<int:exp_id>', methods=['DELETE'])
def delete_experience(exp_id):
    try:
        if not require_login():
            return jsonify({"success": False, "message": "請先登入"}), 403

        user_id = session['user_id']
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT user_id FROM internship_experiences WHERE id = %s", (exp_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"success": False, "message": "心得不存在"}), 404

        # row may be tuple (cursor default) or dict; handle both
        owner_id = row[0] if not isinstance(row, dict) else row.get('user_id')
        if owner_id != user_id:
            return jsonify({"success": False, "message": "不能刪除他人的心得"}), 403
        cursor.execute("DELETE FROM internship_experiences WHERE id = %s", (exp_id,))
        db.commit()
        return jsonify({"success": True, "message": "已刪除"})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500

# --------------------
# API：老師審核通過心得（設定為公開）
# --------------------
@intern_exp_bp.route('/api/approve/<int:exp_id>', methods=['POST'])
def approve_experience(exp_id):
    try:
        if not require_login():
            return jsonify({"success": False, "message": "請先登入"}), 403

        # 僅限指導老師 / 主任 / 班導使用
        if session.get('role') not in ['teacher', 'director', 'class_teacher']:
            return jsonify({"success": False, "message": "未授權"}), 403

        db = get_db()
        cursor = db.cursor(dictionary=True)
        
        # 先獲取心得和學生資訊（在更新之前）
        cursor.execute("""
            SELECT ie.user_id, ie.company_id, c.company_name, u.name AS student_name, u.email AS student_email
            FROM internship_experiences ie
            LEFT JOIN users u ON ie.user_id = u.id
            LEFT JOIN internship_companies c ON ie.company_id = c.id
            WHERE ie.id = %s
        """, (exp_id,))
        exp_info = cursor.fetchone()
        
        if not exp_info:
            return jsonify({"success": False, "message": "心得不存在"}), 404
        
        student_id = exp_info.get('user_id')
        student_name = exp_info.get('student_name') or '同學'
        student_email = exp_info.get('student_email')
        company_name = exp_info.get('company_name') or '實習公司'
        
        # 更新心得狀態
        cursor.execute("UPDATE internship_experiences SET is_public = 1 WHERE id = %s", (exp_id,))
        db.commit()

        if cursor.rowcount == 0:
            return jsonify({"success": False, "message": "心得不存在"}), 404

        # 獲取審核老師資訊
        reviewer_id = session.get('user_id')
        cursor.execute("SELECT name FROM users WHERE id = %s", (reviewer_id,))
        reviewer_info = cursor.fetchone()
        reviewer_name = reviewer_info.get('name', '指導老師') if reviewer_info else '指導老師'
        
        # 創建公告並發送通知給學生
        if student_id:
            # 創建公告
            ann_title = "實習心得審核通過通知"
            ann_content = f"""
親愛的 {student_name} 同學：

您好！

您的實習心得（{company_name}）已通過審核，現在已公開顯示在您的「所有心得」頁面中。

審核老師：{reviewer_name}

請登入系統查看您的實習心得。

如有任何疑問，請聯絡您的班導師或系統管理員。

--
智慧實習平台
"""
            now = get_taiwan_time()
            # 設定公告時間（立即開始，30天後結束）
            start_time = now.strftime('%Y-%m-%d %H:%M:%S')
            end_time = (now + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute("""
                INSERT INTO announcement (title, content, start_time, end_time, is_published, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (ann_title, ann_content, start_time, end_time, 1, now))
            ann_id = cursor.lastrowid
            db.commit()
            
            # 為該學生創建通知，指向該公告（使用同一個資料庫連接）
            link_url = f"/view_announcement/{ann_id}"
            notification_title = "實習心得審核通過通知"
            notification_message = ann_content[:200] if len(ann_content) > 200 else ann_content
            try:
                cursor.execute("""
                    INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
                    VALUES (%s, %s, %s, %s, %s, 0, NOW())
                """, (student_id, notification_title, notification_message, "company", link_url))
                db.commit()
                print(f"[成功] 為學生 {student_id} 創建通知成功，公告ID: {ann_id}, 通知標題: {notification_title}")
            except Exception as e:
                print(f"[錯誤] 創建通知時發生異常: {traceback.format_exc()}")
                db.rollback()
        
        # 發送Email給學生
        if student_email:
            email_subject = "【智慧實習平台】實習心得審核通過通知"
            email_content = f"""
親愛的 {student_name} 同學：

您好！

您的實習心得（{company_name}）已通過審核，現在已公開顯示在您的「所有心得」頁面中。

審核老師：{reviewer_name}

請登入系統查看您的實習心得。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習平台
"""
            send_email(student_email, email_subject, email_content, related_user_id=student_id)

        return jsonify({"success": True, "message": "已審核通過，學生心得頁面將顯示此筆心得"})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500

# --------------------
# API：老師退件心得（刪除心得）
# --------------------
@intern_exp_bp.route('/api/reject/<int:exp_id>', methods=['POST'])
def reject_experience(exp_id):
    try:
        if not require_login():
            return jsonify({"success": False, "message": "請先登入"}), 403

        # 僅限指導老師 / 主任 / 班導使用
        if session.get('role') not in ['teacher', 'director', 'class_teacher']:
            return jsonify({"success": False, "message": "未授權"}), 403

        db = get_db()
        cursor = db.cursor(dictionary=True)
        
        # 先獲取心得和學生資訊（在刪除之前）
        cursor.execute("""
            SELECT ie.user_id, ie.company_id, c.company_name, u.name AS student_name, u.email AS student_email
            FROM internship_experiences ie
            LEFT JOIN users u ON ie.user_id = u.id
            LEFT JOIN internship_companies c ON ie.company_id = c.id
            WHERE ie.id = %s
        """, (exp_id,))
        exp_info = cursor.fetchone()
        
        if not exp_info:
            return jsonify({"success": False, "message": "心得不存在"}), 404
        
        student_id = exp_info.get('user_id')
        student_name = exp_info.get('student_name') or '同學'
        student_email = exp_info.get('student_email')
        company_name = exp_info.get('company_name') or '實習公司'
        
        # 獲取審核老師資訊
        reviewer_id = session.get('user_id')
        cursor.execute("SELECT name FROM users WHERE id = %s", (reviewer_id,))
        reviewer_info = cursor.fetchone()
        reviewer_name = reviewer_info.get('name', '指導老師') if reviewer_info else '指導老師'
        
        # 刪除心得
        cursor.execute("DELETE FROM internship_experiences WHERE id = %s", (exp_id,))
        db.commit()

        # 發送通知給學生
        if student_id:
            notification_title = "實習心得退件通知"
            notification_message = f"您的實習心得（{company_name}）已被退件，該心得已從系統中移除。請重新提交心得。"
            link_url = "/intern_experience"
            create_notification(student_id, notification_title, notification_message, category="company", link_url=link_url)
        
        # 發送Email給學生
        if student_email:
            email_subject = "【智慧實習平台】實習心得退件通知"
            email_content = f"""
親愛的 {student_name} 同學：

您好！

很抱歉，您的實習心得（{company_name}）已被退件，該心得已從系統中移除。

審核老師：{reviewer_name}

請重新登入系統，檢查您的實習心得內容，並重新提交。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習平台
"""
            send_email(student_email, email_subject, email_content, related_user_id=student_id)

        return jsonify({"success": True, "message": "已退件，該心得已從系統中移除"})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500

# --------------------
# API：測試 Email 發送
# --------------------
@intern_exp_bp.route('/api/test_email', methods=['POST'])
def test_email():
    """測試 Email 發送功能"""
    try:
        if not require_login():
            return jsonify({"success": False, "message": "請先登入"}), 403
        
        data = request.get_json(silent=True) or {}
        recipient_email = data.get('recipient_email', '').strip()
        
        if not recipient_email:
            return jsonify({"success": False, "message": "請輸入收件人 Email"}), 400
        
        if '@' not in recipient_email:
            return jsonify({"success": False, "message": "Email 格式不正確"}), 400
        
        # 發送測試郵件
        subject = "【智慧實習平台】Email 發送測試"
        content = f"""
親愛的測試使用者：

您好！

這是一封測試郵件，用來確認 Email 發送功能正常運作。

如果您收到這封郵件，表示系統的 Email 發送功能已成功設定並運作正常。

測試資訊：
- 收件人：{recipient_email}
- 發送時間：{datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")}
- 發送方式：SMTP

--

智慧實習平台
自動測試系統
"""
        
        success, message, log_id = send_email(
            recipient_email=recipient_email,
            subject=subject,
            content=content,
            related_user_id=session.get('user_id')
        )
        
        if success:
            return jsonify({
                "success": True,
                "message": "測試郵件發送成功！請檢查收件箱。",
                "log_id": log_id
            })
        else:
            return jsonify({
                "success": False,
                "message": f"郵件發送失敗：{message}",
                "log_id": log_id
            }), 500
            
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": f"發生錯誤：{str(e)}"}), 500

# --------------------
# API：發送面試或錄取通知
# --------------------
@intern_exp_bp.route('/api/send_notification', methods=['POST'])
def send_notification():
    """發送 Email 通知（面試或錄取）"""
    try:
        if not require_login():
            return jsonify({"success": False, "message": "請先登入"}), 403
        
        data = request.get_json(silent=True) or {}
        student_id = data.get('student_id')
        student_email = data.get('student_email', '').strip()
        student_name = data.get('student_name', '').strip()
        notification_type = data.get('notification_type', 'interview')  # 'interview' 或 'admission'
        content = data.get('content', '').strip()
        company_name = data.get('company_name', '公司').strip()
        
        # 驗證必要參數
        if not student_id and not student_email:
            return jsonify({"success": False, "message": "請提供學生ID或Email"}), 400
        
        if notification_type == 'interview' and not content:
            return jsonify({"success": False, "message": "面試通知必須填寫通知內容"}), 400
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 如果提供了 student_id，從資料庫獲取學生資訊
        if student_id:
            cursor.execute("""
                SELECT id, name, email, username
                FROM users
                WHERE id = %s AND role = 'student'
            """, (student_id,))
            student_info = cursor.fetchone()
            
            if not student_info:
                cursor.close()
                conn.close()
                return jsonify({"success": False, "message": "找不到該學生資料"}), 404
            
            # 使用資料庫中的資訊
            student_email = student_info.get('email') or student_email
            student_name = student_info.get('name') or student_name
        
        if not student_email:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "學生Email資訊不完整，無法發送通知"}), 400
        
        if not student_name:
            student_name = "同學"
        
        # 獲取發送者資訊
        sender_id = session.get('user_id')
        cursor.execute("""
            SELECT name, role
            FROM users
            WHERE id = %s
        """, (sender_id,))
        sender_info = cursor.fetchone()
        sender_name = sender_info.get('name', '') if sender_info else ''
        
        # 根據通知類型發送不同的郵件
        if notification_type == 'interview':
            email_success, email_message, log_id = send_interview_email(
                student_email, 
                student_name, 
                company_name, 
                sender_name, 
                content
            )
        elif notification_type == 'admission':
            email_success, email_message, log_id = send_admission_email(
                student_email, 
                student_name, 
                company_name, 
                sender_name
            )
        else:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "無效的通知類型（必須是 'interview' 或 'admission'）"}), 400
        
        cursor.close()
        conn.close()
        
        if email_success:
            return jsonify({
                "success": True,
                "message": f"{'面試' if notification_type == 'interview' else '錄取'}通知發送成功！",
                "log_id": log_id,
                "student_email": student_email
            })
        else:
            return jsonify({
                "success": False,
                "message": f"郵件發送失敗：{email_message}",
                "log_id": log_id
            }), 500
            
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": f"發生錯誤：{str(e)}"}), 500
