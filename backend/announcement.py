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

# --- API：獲取單個公告詳情 ---
@announcement_bp.route("/api/get/<int:ann_id>", methods=["GET"])
def get_announcement(ann_id):
    """獲取單個公告的詳細信息"""
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM announcement WHERE id = %s", (ann_id,))
        row = cursor.fetchone()
        
        if not row:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "找不到該公告"}), 404
        
        # 格式化時間
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
        return jsonify({"success": True, "data": row})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "載入失敗"}), 500

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
    """
    新增公告：
    - 前端可傳入 target_roles (list[str]) 指定要通知的角色
      例如：["student", "teacher"]
    """
    try:
        data = request.json
        title, content = data.get("title"), data.get("content")
        start_time, end_time = data.get("start_time"), data.get("end_time")
        is_published = data.get("is_published", 0)
        target_roles = data.get("target_roles") or []
        print("[create_announcement] payload target_roles =", target_roles)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO announcement (title, content, start_time, end_time, is_published, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (title, content, start_time, end_time, is_published, get_taiwan_time()))
        
        ann_id = cursor.lastrowid
        conn.commit()  # 先提交公告，確保公告已存在

        # 【同步通知】如果已發布，立即同步到通知頁面（不等待時間）
        # 這樣可以確保公告創建後立即出現在通知列表中
        if is_published:
            push_announcement_notifications(conn, title, content, ann_id, target_roles=target_roles)

        cursor.close()
        conn.close()
        return jsonify({"success": True, "message": "公告已新增並同步至通知頁面"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "新增失敗"}), 500

# --- API：更新公告 ---
@announcement_bp.route("/api/update/<int:ann_id>", methods=["POST"])
def update_announcement(ann_id):
    """
    更新公告：
    - 如果有傳 target_roles，後續推播將以該角色清單為準
    """
    try:
        data = request.json
        title = data.get("title")
        content = data.get("content")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        is_published = data.get("is_published", 0)
        target_roles = data.get("target_roles") or []
        print(f"[update_announcement] ann_id={ann_id}, target_roles={target_roles}")

        conn = get_db()
        cursor = conn.cursor()
        
        # 更新資料庫中的公告資訊
        cursor.execute("""
            UPDATE announcement 
            SET title=%s, content=%s, start_time=%s, end_time=%s, is_published=%s
            WHERE id=%s
        """, (title, content, start_time, end_time, is_published, ann_id))
        
        conn.commit()

        # 【同步通知】如果更新後狀態為「已發布」，立即同步到通知頁面
        if is_published:
            push_announcement_notifications(conn, title, content, ann_id, target_roles=target_roles)

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
def push_announcement_notifications(conn, title, content, ann_id, target_roles=None):
    """
    將公告推送到 notifications 資料表。
    - target_roles: 可選，若提供則只對這些角色的使用者建立通知
      例如 ["student", "teacher"]。
      若為空或 None，則維持原本邏輯：對所有使用者建立通知。
    """
    try:
        cursor = conn.cursor(dictionary=True)
        link_url = f"/view_announcement/{ann_id}"
        now = get_taiwan_time()

        # 正規化角色清單
        valid_roles = {"student", "teacher", "director", "ta", "admin", "vendor", "class_teacher"}
        roles = [r for r in (target_roles or []) if r in valid_roles]

        if roles:
            # 只取指定角色的使用者
            placeholders = ", ".join(["%s"] * len(roles))
            cursor.execute(f"SELECT id FROM users WHERE role IN ({placeholders})", roles)
        else:
            # 未指定角色 → 所有人
            cursor.execute("SELECT id FROM users")

        users = cursor.fetchall() or []
        
        # 為每個用戶創建通知記錄（如果還不存在）
        for u in users:
            uid = u['id'] if isinstance(u, dict) else u[0]
            
            # 檢查該用戶是否已有此公告的通知記錄
            cursor.execute("""
                SELECT id FROM notifications 
                WHERE user_id = %s AND link_url = %s
            """, (uid, link_url))
            
            if not cursor.fetchone():
                # 如果不存在，創建新通知
                cursor.execute("""
                    INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
                    VALUES (%s, %s, %s, %s, %s, 0, %s)
                """, (uid, f"公告：{title}", content[:200] if content else "", "announcement", link_url, now))
        
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
        target_roles = data.get("target_roles") or []
        
        # 檢查是否至少有一個截止時間
        if not pref_deadline and not resume_deadline:
            return jsonify({"success": False, "message": "請填寫至少一個截止時間"}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        now = get_taiwan_time()
        created_by = session.get("username") or session.get("name") or "系統"
        
        # 轉換時間格式（從 datetime-local 格式轉換為資料庫格式）
        # datetime-local 輸入的時間是本地時間（台灣時間），直接使用，不做時區轉換
        def convert_datetime(dt_str):
            if not dt_str:
                return None
            # 如果已經是 "YYYY-MM-DD HH:mm" 格式，直接加上秒數
            if 'T' in dt_str:
                # datetime-local 格式: "2025-12-31T14:30" -> "2025-12-31 14:30:00"
                dt_formatted = dt_str.replace('T', ' ') + ':00'
            else:
                # 已經是 "YYYY-MM-DD HH:mm" 格式
                dt_formatted = dt_str + ':00'
            return dt_formatted
        
        # 使用前端提供的標題、內容、開始時間、結束時間
        title = data.get("title")
        content = data.get("content")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        
        ann_id = None
        
        # 處理志願序截止時間
        if pref_deadline:
            # 如果前端提供了完整的公告資訊，使用前端的資訊
            if title and content and start_time and end_time:
                start_dt = convert_datetime(start_time)
                end_dt = convert_datetime(end_time)
            else:
                # 否則使用舊的邏輯（向後兼容）
                pref_dt = convert_datetime(pref_deadline)
                title = "[作業] 填寫志願序截止時間"
                content = f"請注意！填寫志願序的截止時間為：{pref_dt.replace(':00', '')}，請務必在截止時間前完成志願序填寫。"
                start_dt = now
                end_dt = pref_dt
            
            # 驗證時間格式
            try:
                datetime.strptime(start_dt, '%Y-%m-%d %H:%M:%S')
                datetime.strptime(end_dt, '%Y-%m-%d %H:%M:%S')
            except ValueError as e:
                return jsonify({"success": False, "message": f"時間格式錯誤：{str(e)}"}), 400
            
            cursor.execute("""
                INSERT INTO announcement (title, content, start_time, end_time, is_published, created_by, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (title, content, start_dt, end_dt, 1, created_by, now))
            ann_id = cursor.lastrowid
            conn.commit()  # 先提交公告，確保公告已存在
            
            # 發送通知給指定角色（若未指定則維持原本行為：所有使用者）
            push_announcement_notifications(conn, title, content, ann_id, target_roles=target_roles)
        
        # 處理履歷上傳截止時間
        if resume_deadline:
            # 如果前端提供了完整的公告資訊，使用前端的資訊
            if title and content and start_time and end_time:
                start_dt = convert_datetime(start_time)
                end_dt = convert_datetime(end_time)
            else:
                # 否則使用舊的邏輯（向後兼容）
                resume_dt = convert_datetime(resume_deadline)
                title = "[作業] 上傳履歷截止時間"
                content = f"請注意！上傳履歷的截止時間為：{resume_dt.replace(':00', '')}，請務必在截止時間前完成履歷上傳。"
                start_dt = now
                end_dt = resume_dt
            
            # 驗證時間格式
            try:
                datetime.strptime(start_dt, '%Y-%m-%d %H:%M:%S')
                datetime.strptime(end_dt, '%Y-%m-%d %H:%M:%S')
            except ValueError as e:
                return jsonify({"success": False, "message": f"時間格式錯誤：{str(e)}"}), 400
            
            cursor.execute("""
                INSERT INTO announcement (title, content, start_time, end_time, is_published, created_by, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (title, content, start_dt, end_dt, 1, created_by, now))
            ann_id = cursor.lastrowid
            conn.commit()  # 先提交公告，確保公告已存在
            
            # 發送通知給指定角色（若未指定則維持原本行為：所有使用者）
            push_announcement_notifications(conn, title, content, ann_id, target_roles=target_roles)
        cursor.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "截止時間已儲存並通知學生",
            "ann_id": ann_id
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
        
        # 根據類型確定標題模式（兼容有無空格的格式）
        if assignment_type == "resume":
            title_pattern = "[作業]%上傳履歷截止時間"
        else:  # preference
            title_pattern = "[作業]%填寫志願序截止時間"
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        now = get_taiwan_time()
        
        # 查詢最新的截止時間公告（按創建時間降序，使用 LIKE 匹配標題）
        cursor.execute("""
            SELECT id, title, end_time, created_at 
            FROM announcement 
            WHERE title LIKE %s AND is_published = 1
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

