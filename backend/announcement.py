from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime, timedelta
import traceback


# 註：此處需根據你的資料庫實作匯入模型，例如：from models import Announcement

# 必須先定義 Blueprint 物件，才能在下方使用 @announcement_bp
announcement_bp = Blueprint('announcement_bp', __name__)

@announcement_bp.route('/api/check_status', methods=['GET'])
def check_assignment_status():
    """檢查目前是否在實習申請作業時間內"""
    try:
        now = datetime.now()
        
        # 搜尋標題包含 "[作業]" 且已發布的最新一筆公告
        # assignment = Announcement.query.filter(
        #     Announcement.title.contains("[作業]"),
        #     Announcement.is_published == 1
        # ).order_by(Announcement.id.desc()).first()

        # --- 以下為邏輯演示，請根據資料庫查詢結果修改 ---
        # 假設資料庫中最新的結束時間是 2025-12-24 11:55:00
        # 如果 assignment 為 None，則預設 is_open 為 True
        
        # 範例判斷：
        # is_open = assignment.start_time <= now <= assignment.end_time
        is_open = False # 根據您的圖片目前應為結束狀態
        end_time_str = "2025-12-24 11:55:00"

        return jsonify({
            "success": True,
            "is_open": is_open,
            "end_time": end_time_str,
            "server_time": now.strftime('%Y-%m-%d %H:%M:%S')
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# 確保其他的 API 路由也使用 @announcement_bp
@announcement_bp.route('/api/list', methods=['GET'])
def list_announcements():
    # 這裡放原本的 list 邏輯
    return jsonify({"success": True, "data": []})


announcement_bp = Blueprint("announcement_bp", __name__)

def get_taiwan_time():
    """取得目前的台灣時間 (UTC+8)"""
    return datetime.utcnow() + timedelta(hours=8)

# --- 頁面路由 ---
@announcement_bp.route("/manage_announcements")
def manage_announcements():
    role = session.get("role")
    if role not in ("director", "ta", "admin"):
        return "未授權", 403
    return render_template("user_shared/manage_announcements.html")

# --- API：列出公告並自動檢查預約發布 ---
@announcement_bp.route("/api/list", methods=["GET"])
def list_announcements():
    try:
        conn = get_db()
        # 關鍵：進入列表時，自動掃描並補發「時間已到但尚未通知」的公告
        check_and_push_scheduled_announcements(conn)
        
        # 同步檢查截止提醒 (統一標題為公告)
        maybe_push_deadline_reminders(conn)

        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM announcement ORDER BY created_at DESC")
        rows = cursor.fetchall() or []
        
        # 格式化時間，確保不進行時區轉換（直接使用資料庫中的時間）
        for row in rows:
            if row.get('start_time'):
                if isinstance(row['start_time'], datetime):
                    row['start_time'] = row['start_time'].strftime('%Y-%m-%d %H:%M:%S')
                else:
                    row['start_time'] = str(row['start_time'])
            if row.get('end_time'):
                if isinstance(row['end_time'], datetime):
                    row['end_time'] = row['end_time'].strftime('%Y-%m-%d %H:%M:%S')
                else:
                    row['end_time'] = str(row['end_time'])
            if row.get('created_at'):
                if isinstance(row['created_at'], datetime):
                    row['created_at'] = row['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                else:
                    row['created_at'] = str(row['created_at'])
        
        cursor.close()
        conn.close()
        return jsonify({"success": True, "data": rows})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "載入失敗"}), 500

# --- API：新增公告 ---
@announcement_bp.route("/api/create", methods=["POST"])
def create_announcement():
    try:
        data = request.json
        title, content = data.get("title"), data.get("content")
        start_time, end_time = data.get("start_time"), data.get("end_time")
        is_published = data.get("is_published", 0)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO announcement (title, content, start_time, end_time, is_published, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (title, content, start_time, end_time, is_published, get_taiwan_time()))
        
        ann_id = cursor.lastrowid
        conn.commit()

        # 【同步通知】儲存後若符合發布條件，立即發布到通知頁面
        now_tw = get_taiwan_time()
        start_dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M') if start_time else now_tw
        if is_published and now_tw >= start_dt:
            push_announcement_notifications(conn, title, content, ann_id)

        cursor.close()
        conn.close()
        return jsonify({"success": True, "message": "公告已新增並同步至通知頁面"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "新增失敗"}), 500

# --- API：更新公告 ---
@announcement_bp.route("/api/update/<int:ann_id>", methods=["POST"])
def update_announcement(ann_id):
    try:
        data = request.json
        title = data.get("title")
        content = data.get("content")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        is_published = data.get("is_published", 0)

        conn = get_db()
        cursor = conn.cursor()
        
        # 更新資料庫中的公告資訊
        cursor.execute("""
            UPDATE announcement 
            SET title=%s, content=%s, start_time=%s, end_time=%s, is_published=%s
            WHERE id=%s
        """, (title, content, start_time, end_time, is_published, ann_id))
        
        conn.commit()

        # 如果更新後狀態為「已發布」且時間已到，同樣觸發推送通知邏輯
        now_tw = get_taiwan_time()
        # 轉換時間格式以進行比較
        try:
            start_dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M') if start_time else now_tw
        except:
            start_dt = now_tw # 防錯處理

        if is_published and now_tw >= start_dt:
            push_announcement_notifications(conn, title, content, ann_id)

        cursor.close()
        conn.close()
        return jsonify({"success": True, "message": "公告更新成功"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "更新失敗"}), 500

# --- API：刪除公告 ---
@announcement_bp.route("/api/delete/<int:ann_id>", methods=["DELETE"])
def delete_announcement(ann_id):
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 1. 執行刪除 (公告表)
        cursor.execute("DELETE FROM announcement WHERE id = %s", (ann_id,))
        
        # 2. 同步刪除相關通知 (避免使用者點到已不存在的公告)
        link_url = f"/view_announcement/{ann_id}"
        cursor.execute("DELETE FROM notifications WHERE link_url = %s", (link_url,))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({"success": True, "message": f"ID {ann_id} 刪除成功"})
    except Exception:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": "刪除失敗"}), 500
# --- 核心函式：發布通知到通知頁面 ---
def push_announcement_notifications(conn, title, content, ann_id):
    """將公告推送到 notifications 資料表"""
    try:
        cursor = conn.cursor(dictionary=True)
        link_url = f"/view_announcement/{ann_id}"
        
        # 避免重複發送
        cursor.execute("SELECT 1 FROM notifications WHERE link_url = %s AND category = 'announcement' LIMIT 1", (link_url,))
        if cursor.fetchone():
            return

        cursor.execute("SELECT id FROM users")
        users = cursor.fetchall() or []
        now = get_taiwan_time()
        
        for u in users:
            uid = u['id'] if isinstance(u, dict) else u[0]
            cursor.execute("""
                INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
                VALUES (%s, %s, %s, %s, %s, 0, %s)
            """, (uid, f"公告：{title}", content[:50], "announcement", link_url, now))
        conn.commit()
    except Exception:
        traceback.print_exc()

# --- 自動檢查：預約時間已到的公告 ---
def check_and_push_scheduled_announcements(conn):
    now_tw = get_taiwan_time()
    cursor = conn.cursor(dictionary=True)
    # 尋找：已勾選發布、時間已到、但在通知頁面還沒出現的公告
    cursor.execute("""
        SELECT id, title, content FROM announcement 
        WHERE is_published = 1 AND start_time <= %s
        AND id NOT IN (
            SELECT DISTINCT CAST(SUBSTRING_INDEX(link_url, '/', -1) AS UNSIGNED) 
            FROM notifications 
            WHERE category = 'announcement' AND link_url LIKE '/view_announcement/%'
        )
    """, (now_tw,))
    pending = cursor.fetchall() or []
    for ann in pending:
        push_announcement_notifications(conn, ann['title'], ann['content'], ann['id'])
    cursor.close()

# --- API：儲存作業截止時間 ---
@announcement_bp.route("/api/save_deadlines", methods=["POST"])
def save_deadlines():
    """儲存作業截止時間到 announcement 資料表"""
    try:
        if "user_id" not in session:
            return jsonify({"success": False, "message": "請先登入"}), 403
        
        role = session.get("role")
        if role not in ("director", "ta", "admin"):
            return jsonify({"success": False, "message": "未授權"}), 403
        
        data = request.json
        pref_deadline = data.get("pref_deadline")  # 志願序截止時間
        resume_deadline = data.get("resume_deadline")  # 履歷上傳截止時間
        
        if not pref_deadline or not resume_deadline:
            return jsonify({"success": False, "message": "請完整填寫兩個截止時間"}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        now = get_taiwan_time()
        created_by = session.get("username") or session.get("name") or "系統"
        
        # 轉換時間格式（從 datetime-local 格式轉換為資料庫格式）
        # datetime-local 輸入的時間是本地時間（台灣時間），直接使用，不做時區轉換
        def convert_datetime(dt_str):
            if not dt_str:
                return None
            # datetime-local 格式: "2025-12-31T14:30" -> "2025-12-31 14:30:00"
            # 直接轉換，不進行時區轉換，因為輸入的就是台灣時間
            dt_formatted = dt_str.replace('T', ' ') + ':00'
            return dt_formatted
        
        pref_dt = convert_datetime(pref_deadline)
        resume_dt = convert_datetime(resume_deadline)
        
        # 驗證時間格式並確保是有效的台灣時間
        try:
            # 解析時間以確保格式正確
            datetime.strptime(pref_dt, '%Y-%m-%d %H:%M:%S')
            datetime.strptime(resume_dt, '%Y-%m-%d %H:%M:%S')
        except ValueError as e:
            return jsonify({"success": False, "message": f"時間格式錯誤：{str(e)}"}), 400
        
        # 格式化時間顯示（只移除秒數，保留時分）
        def format_datetime_display(dt_str):
            """格式化時間顯示：2025-12-25 00:00:00 -> 2025-12-25 00:00"""
            if not dt_str:
                return ""
            # 只移除最後的秒數部分（":00"），保留時分
            if dt_str.endswith(':00'):
                return dt_str[:-3]  # 移除最後的 ':00'
            return dt_str
        
        pref_dt_display = format_datetime_display(pref_dt)
        resume_dt_display = format_datetime_display(resume_dt)
        
        # 建立志願序截止公告
        pref_title = "[作業] 填寫志願序截止時間"
        pref_content = f"請注意！填寫志願序的截止時間為：{pref_dt_display}，請務必在截止時間前完成志願序填寫。"
        
        cursor.execute("""
            INSERT INTO announcement (title, content, start_time, end_time, is_published, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (pref_title, pref_content, now, pref_dt, 1, created_by, now))
        pref_ann_id = cursor.lastrowid
        
        # 建立履歷上傳截止公告
        resume_title = "[作業] 上傳履歷截止時間"
        resume_content = f"請注意！上傳履歷的截止時間為：{resume_dt_display}，請務必在截止時間前完成履歷上傳。"
        
        cursor.execute("""
            INSERT INTO announcement (title, content, start_time, end_time, is_published, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (resume_title, resume_content, now, resume_dt, 1, created_by, now))
        resume_ann_id = cursor.lastrowid
        
        conn.commit()
        
        # 發送通知給所有學生
        push_announcement_notifications(conn, pref_title, pref_content, pref_ann_id)
        push_announcement_notifications(conn, resume_title, resume_content, resume_ann_id)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "截止時間已儲存並通知學生",
            "pref_ann_id": pref_ann_id,
            "resume_ann_id": resume_ann_id
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"儲存失敗：{str(e)}"}), 500

# --- API：檢查作業截止時間狀態 ---
@announcement_bp.route("/api/check_deadline", methods=["GET"])
def check_deadline():
    """檢查指定作業的截止時間狀態"""
    try:
        assignment_type = request.args.get("type")  # "resume" 或 "preference"
        
        if assignment_type not in ["resume", "preference"]:
            return jsonify({"success": False, "message": "無效的作業類型"}), 400
        
        # 根據類型確定標題
        if assignment_type == "resume":
            title_pattern = "[作業] 上傳履歷截止時間"
        else:  # preference
            title_pattern = "[作業] 填寫志願序截止時間"
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        now = get_taiwan_time()
        
        # 查詢最新的截止時間公告（按創建時間降序）
        cursor.execute("""
            SELECT id, title, end_time, created_at 
            FROM announcement 
            WHERE title = %s AND is_published = 1
            ORDER BY created_at DESC 
            LIMIT 1
        """, (title_pattern,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result:
            # 沒有設定截止時間，允許操作
            return jsonify({
                "success": True,
                "has_deadline": False,
                "is_expired": False,
                "deadline": None,
                "message": "尚未設定截止時間"
            })
        
        deadline = result['end_time']
        if isinstance(deadline, datetime):
            deadline_dt = deadline
        else:
            # 如果是字符串，轉換為 datetime
            try:
                deadline_dt = datetime.strptime(str(deadline), '%Y-%m-%d %H:%M:%S')
            except:
                deadline_dt = datetime.strptime(str(deadline), '%Y-%m-%d %H:%M')
        
        is_expired = now > deadline_dt
        
        return jsonify({
            "success": True,
            "has_deadline": True,
            "is_expired": is_expired,
            "deadline": deadline_dt.strftime('%Y-%m-%d %H:%M:%S') if isinstance(deadline_dt, datetime) else str(deadline),
            "message": "已過期" if is_expired else "尚未過期"
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"檢查失敗：{str(e)}"}), 500

# --- 自動化功能：截止提醒 (統一為公告標題) ---
def maybe_push_deadline_reminders(conn, hours_before=24):
    now = get_taiwan_time()
    cutoff_end = now + timedelta(hours=hours_before)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, title, end_time FROM announcement
        WHERE is_published = 1 AND end_time IS NOT NULL AND end_time BETWEEN %s AND %s
    """, (now, cutoff_end))
    rows = cursor.fetchall() or []
    for row in rows:
        reminder_title = f"公告：{row['title']}"
        cursor.execute("SELECT 1 FROM notifications WHERE title = %s LIMIT 1", (reminder_title,))
        if cursor.fetchone(): continue
        push_announcement_notifications(conn, row['title'], f"內容將於 {row['end_time']} 截止", row['id'])

