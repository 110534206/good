from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime, timedelta
import traceback

announcement_bp = Blueprint("announcement_bp", __name__)

# ------------------------------------------------------------
# Helper: 取得台灣時間
# ------------------------------------------------------------
def get_taiwan_time():
    """
    取得目前的台灣時間 (UTC+8)
    不管伺服器在哪個時區，都強制回傳台灣時間
    """
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
        cursor = conn.cursor(dictionary=True)

        try:
            maybe_push_deadline_reminders(conn, hours_before=24)
        except Exception:
            traceback.print_exc()

        cursor.execute("""
            SELECT id, title, content, start_time, end_time, created_at, is_published, published_at, created_by
            FROM announcement
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        return jsonify({"success": True, "data": rows})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "讀取公告失敗"}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()

# ------------------------------------------------------------
# API：新增公告
# ------------------------------------------------------------
@announcement_bp.route("/api/create", methods=["POST"])
def create_announcement():
    data = request.get_json() or {}
    title = data.get("title")
    content = data.get("content")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    is_published = 1 if data.get("is_published") else 0
    created_by = session.get("user_name", "系統")

    if not title or not content:
        return jsonify({"success": False, "message": "標題與內容不可空白"}), 400

    # ★ 修改：使用 get_taiwan_time()
    published_at_val = get_taiwan_time() if is_published else None

    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO announcement (title, content, start_time, end_time, is_published, published_at, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (title, content, start_time, end_time, is_published, published_at_val, created_by))
        
        ann_id = cursor.lastrowid
        conn.commit()

        if is_published:
            push_announcement_notifications(conn, title, content, ann_id)

        return jsonify({"success": True, "message": "公告已新增"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "新增公告失敗"}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()

# ------------------------------------------------------------
# API：更新公告
# ------------------------------------------------------------
@announcement_bp.route("/api/update/<int:aid>", methods=["POST"])
def update_announcement(aid):
    data = request.get_json() or {}
    title = data.get("title")
    content = data.get("content")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    is_published = 1 if data.get("is_published") else 0

    if not title or not content:
        return jsonify({"success": False, "message": "標題與內容不可空白"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()

        # ★ 修改：不再使用 SQL 的 NOW()，改用 Python 傳入台灣時間
        if is_published:
            current_tw_time = get_taiwan_time()
            cursor.execute("""
                UPDATE announcement
                SET title=%s, content=%s, start_time=%s, end_time=%s, is_published=%s, published_at=%s
                WHERE id=%s
            """, (title, content, start_time, end_time, is_published, current_tw_time, aid))
        else:
            cursor.execute("""
                UPDATE announcement
                SET title=%s, content=%s, start_time=%s, end_time=%s, is_published=%s, published_at=NULL
                WHERE id=%s
            """, (title, content, start_time, end_time, is_published, aid))

        conn.commit()

        if is_published:
            push_announcement_notifications(conn, title, content, aid)

        return jsonify({"success": True, "message": "公告已更新"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "更新公告失敗"}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()

# ------------------------------------------------------------
# API：刪除公告
# ------------------------------------------------------------
@announcement_bp.route("/api/delete/<int:aid>", methods=["DELETE"])
def delete_announcement(aid):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM announcement WHERE id=%s", (aid,))
        conn.commit()
        return jsonify({"success": True, "message": "公告已刪除"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "刪除失敗"}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()

# ------------------------------------------------------------
# 頁面：公告詳情
# ------------------------------------------------------------
@announcement_bp.route("/view_announcement/<int:aid>")
def view_announcement(aid):
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT title, content, created_at, start_time, end_time FROM announcement WHERE id=%s", (aid,))
        row = cursor.fetchone()
        if not row:
            return "公告不存在", 404
        return render_template("user_shared/view_announcement.html", ann=row)
    except Exception:
        traceback.print_exc()
        return "伺服器錯誤", 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()

# Helper 函式維持不變
def push_announcement_notifications(conn, title, content, ann_id):
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users")
        users = cursor.fetchall()
        cursor.close()

        link = f"/view_announcement/{ann_id}"
        notification_title = f"新公告：{title}"
        notification_message = content[:150] + ("..." if len(content) > 150 else "")
        
        # 通知時間也建議用台灣時間，但 MySQL NOW() 會用 Server 時間。
        # 這裡的 created_at 通常沒顯示給前端看，所以用 NOW() 沒關係，
        # 若要嚴謹也可以改成 Python 傳入。
        cursor = conn.cursor()
        for u in users:
            user_id = u['id'] if isinstance(u, dict) else u[0]
            cursor.execute("""
                INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
                VALUES (%s, %s, %s, %s, %s, 0, NOW())
            """, (user_id, notification_title, notification_message, "announcement", link))
        conn.commit()
        cursor.close()
    except Exception:
        traceback.print_exc()

def maybe_push_deadline_reminders(conn, hours_before=24):
    cutoff_start = datetime.utcnow()
    cutoff_end = cutoff_start + timedelta(hours=hours_before)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, title, end_time
        FROM announcement
        WHERE is_published = 1 AND end_time IS NOT NULL AND end_time BETWEEN %s AND %s
    """, (cutoff_start, cutoff_end))
    rows = cursor.fetchall() or []
    if not rows:
        cursor.close()
        return
    cursor.execute("SELECT id FROM users")
    users = cursor.fetchall() or []
    insert_cursor = conn.cursor()
    for row in rows:
        reminder_title = f"作業截止提醒：{row['title']}"
        check_cursor = conn.cursor()
        check_cursor.execute("SELECT 1 FROM notifications WHERE title = %s LIMIT 1", (reminder_title,))
        already_sent = check_cursor.fetchone()
        check_cursor.close()
        if already_sent: continue
        link = f"/view_announcement/{row['id']}"
        for u in users:
            user_id = u['id'] if isinstance(u, dict) else u[0]
            insert_cursor.execute("""
                INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
                VALUES (%s, %s, %s, %s, %s, 0, NOW())
            """, (user_id, reminder_title, f"作業將於 {row['end_time']} 截止，請盡速完成。", "announcement", link))
    conn.commit()
    insert_cursor.close()
    cursor.close()