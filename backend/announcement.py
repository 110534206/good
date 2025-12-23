from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime, timedelta
import traceback

announcement_bp = Blueprint("announcement_bp", __name__)

# ------------------------------------------------------------
# Helper: 取得台灣時間
# ------------------------------------------------------------
def get_taiwan_time():
    """取得目前的台灣時間 (UTC+8)"""
    return datetime.utcnow() + timedelta(hours=8)

# ------------------------------------------------------------
# 頁面
# ------------------------------------------------------------
@announcement_bp.route("/manage_announcements")
def manage_announcements():
    """主任 / 科助公告管理頁"""
    role = session.get("role")
    if role not in ("director", "ta"):
        return "未授權", 403
    return render_template("user_shared/manage_announcements.html")

# ------------------------------------------------------------
# API：列出公告
# ------------------------------------------------------------
@announcement_bp.route("/api/list", methods=["GET"])
def list_announcements():
    try:
        conn = get_db()
        # 每次列出清單時，檢查是否有公告已到開始時間但尚未發送通知
        check_and_push_scheduled_announcements(conn)
        
        try:
            maybe_push_deadline_reminders(conn, hours_before=24)
        except Exception:
            traceback.print_exc()

        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM announcement ORDER BY created_at DESC")
        rows = cursor.fetchall() or []
        cursor.close()
        conn.close()
        return jsonify({"success": True, "data": rows})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "載入失敗"}), 500

# ------------------------------------------------------------
# API：新增公告 (已修改：判斷時間才發通知)
# ------------------------------------------------------------
@announcement_bp.route("/api/create", methods=["POST"])
def create_announcement():
    try:
        data = request.json
        title = data.get("title")
        content = data.get("content")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        is_published = data.get("is_published", 0)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO announcement (title, content, start_time, end_time, is_published, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (title, content, start_time, end_time, is_published, get_taiwan_time()))
        
        ann_id = cursor.lastrowid
        conn.commit()

        # 邏輯修改：只有當「已發布」且「時間已到」才發通知
        now_tw = get_taiwan_time()
        start_dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M') if start_time else now_tw
        
        if is_published and now_tw >= start_dt:
            push_announcement_notifications(conn, title, content, ann_id)

        cursor.close()
        conn.close()
        return jsonify({"success": True, "message": "公告已新增"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "新增失敗"}), 500

# ------------------------------------------------------------
# API：更新公告 (已修改：更新時判斷時間才發通知)
# ------------------------------------------------------------
@announcement_bp.route("/api/update/<int:aid>", methods=["POST"])
def update_announcement(aid):
    try:
        data = request.json
        title = data.get("title")
        content = data.get("content")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        is_published = data.get("is_published", 0)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE announcement
            SET title=%s, content=%s, start_time=%s, end_time=%s, is_published=%s
            WHERE id=%s
        """, (title, content, start_time, end_time, is_published, aid))
        conn.commit()

        # 邏輯修改：檢查時間狀態
        now_tw = get_taiwan_time()
        start_dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M') if start_time else now_tw

        if is_published and now_tw >= start_dt:
            push_announcement_notifications(conn, title, content, aid)

        cursor.close()
        conn.close()
        return jsonify({"success": True, "message": "公告已更新"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "更新失敗"}), 500

# ------------------------------------------------------------
# API：刪除公告 (已修改：連帶刪除相關通知)
# ------------------------------------------------------------
@announcement_bp.route("/api/delete/<int:aid>", methods=["DELETE"])
def delete_announcement(aid):
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 1. 先刪除通知表中對應此公告的通知 (link_url 包含 ID)
        target_link = f"/view_announcement/{aid}"
        cursor.execute("DELETE FROM notifications WHERE link_url = %s", (target_link,))
        
        # 2. 刪除公告
        cursor.execute("DELETE FROM announcement WHERE id=%s", (aid,))
        
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"success": True, "message": "公告及其相關通知已刪除"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "刪除失敗"}), 500

# ------------------------------------------------------------
# 功能函式：推送公告通知
# ------------------------------------------------------------
def push_announcement_notifications(conn, title, content, ann_id):
    """將公告通知發送給所有使用者，若已發送過則不重發"""
    try:
        cursor = conn.cursor(dictionary=True)
        link_url = f"/view_announcement/{ann_id}"
        
        # 檢查是否已經針對此連結發送過通知，避免重複
        cursor.execute("SELECT 1 FROM notifications WHERE link_url = %s LIMIT 1", (link_url,))
        if cursor.fetchone():
            cursor.close()
            return

        cursor.execute("SELECT id FROM users")
        users = cursor.fetchall() or []
        
        now = get_taiwan_time()
        for u in users:
            uid = u['id'] if isinstance(u, dict) else u[0]
            cursor.execute("""
                INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
                VALUES (%s, %s, %s, %s, %s, 0, %s)
            """, (uid, f"新公告：{title}", content[:50], "announcement", link_url, now))
        
        conn.commit()
        cursor.close()
    except Exception:
        traceback.print_exc()

# ------------------------------------------------------------
# 功能函式：檢查並補發「預約發布」的公告通知
# ------------------------------------------------------------
def check_and_push_scheduled_announcements(conn):
    """檢查已經到達開始時間但尚未推播通知的公告"""
    try:
        now_tw = get_taiwan_time()
        cursor = conn.cursor(dictionary=True)
        
        # 撈出：已發布、已到開始時間、且通知表中還沒有這則公告 ID 的資料
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
    except Exception:
        traceback.print_exc()

# ------------------------------------------------------------
# 自動化功能：截止提醒 (原有邏輯保留)
# ------------------------------------------------------------
def maybe_push_deadline_reminders(conn, hours_before=24):
    now = get_taiwan_time()
    cutoff_end = now + timedelta(hours=hours_before)
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT id, title, end_time FROM announcement
        WHERE is_published = 1 AND end_time IS NOT NULL AND end_time BETWEEN %s AND %s
    """, (now, cutoff_end))
    
    rows = cursor.fetchall() or []
    if not rows:
        cursor.close()
        return

    cursor.execute("SELECT id FROM users")
    users = cursor.fetchall() or []
    
    for row in rows:
        reminder_title = f"作業截止提醒：{row['title']}"
        cursor.execute("SELECT 1 FROM notifications WHERE title = %s LIMIT 1", (reminder_title,))
        if cursor.fetchone(): continue
        
        link = f"/view_announcement/{row['id']}"
        for u in users:
            uid = u['id'] if isinstance(u, dict) else u[0]
            cursor.execute("""
                INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
                VALUES (%s, %s, %s, %s, %s, 0, %s)
            """, (uid, reminder_title, f"此作業將於 {row['end_time']} 截止", "deadline", link, now))
    
    conn.commit()
    cursor.close()