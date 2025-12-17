from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime, timedelta
import traceback

announcement_bp = Blueprint("announcement_bp", __name__)

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
    """只列出已發布且在有效期間的公告"""
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 在查詢前嘗試推送即將到期的公告提醒（不影響主要流程）
        try:
            maybe_push_deadline_reminders(conn, hours_before=24)
        except Exception:
            traceback.print_exc()

        cursor.execute("""
            SELECT id, title, content, start_time, end_time, created_at
            FROM announcement
            WHERE is_published = 1
              AND (start_time IS NULL OR start_time <= NOW())
              AND (end_time IS NULL OR end_time >= NOW())
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        return jsonify({"success": True, "data": rows})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "讀取公告失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------------------------------------------
# 頁面：公告詳情
# ------------------------------------------------------------
@announcement_bp.route("/view_announcement/<int:aid>")
def view_announcement(aid):
    """公告詳情頁"""
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT title, content, created_at FROM announcement WHERE id=%s", (aid,))
        row = cursor.fetchone()
        if not row:
            return "公告不存在", 404
        return render_template("user_shared/view_announcement.html", ann=row)
    except Exception:
        traceback.print_exc()
        return "伺服器錯誤", 500
    finally:
        cursor.close()
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

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO announcement (title, content, start_time, end_time, is_published, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (title, content, start_time, end_time, is_published, created_by))
        ann_id = cursor.lastrowid
        conn.commit()

        # 若公告為已發布，立即推送通知
        if is_published:
            push_announcement_notifications(conn, title, content, ann_id)

        return jsonify({"success": True, "message": "公告已新增"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "新增公告失敗"}), 500
    finally:
        cursor.close()
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
        cursor.execute("""
            UPDATE announcement
            SET title=%s, content=%s, start_time=%s, end_time=%s, is_published=%s
            WHERE id=%s
        """, (title, content, start_time, end_time, is_published, aid))
        conn.commit()

        # 若更新後設為已發布 → 推播通知
        if is_published:
            push_announcement_notifications(conn, title, content, aid)

        return jsonify({"success": True, "message": "公告已更新"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "更新公告失敗"}), 500
    finally:
        cursor.close()
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
        cursor.close()
        conn.close()


# ============================================================
# ✅ Helper：推送公告通知
# ============================================================
def push_announcement_notifications(conn, title, content, ann_id):
    """當公告發布時，推送給所有使用者通知"""
    from notification import create_notification
    
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users")  # 全體使用者皆推送
    users = cursor.fetchall()
    cursor.close()

    link = f"/view_announcement/{ann_id}"
    notification_title = f"新公告：{title}"
    notification_message = content[:150] + ("..." if len(content) > 150 else "")
    
    # 使用 create_notification 函數，設置 category 為 "announcement"
    for u in users:
        user_id = u[0]
        # 注意：create_notification 會自己創建連接，但我們已經有 conn
        # 為了保持一致性，我們直接使用 conn 但添加 category
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
            VALUES (%s, %s, %s, %s, %s, 0, NOW())
        """, (user_id, notification_title, notification_message, "announcement", link))
        cursor.close()

    conn.commit()


# ============================================================
# ✅ Helper：即將到期提醒
# ============================================================
def maybe_push_deadline_reminders(conn, hours_before=24):
    """
    對於距離 end_time 小於指定小時且尚未提醒過的公告，推送一次提醒通知給全體使用者。
    利用 notifications.title 做簡易去重，避免重複提醒。
    """
    cutoff_start = datetime.utcnow()
    cutoff_end = cutoff_start + timedelta(hours=hours_before)

    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT id, title, end_time
        FROM announcement
        WHERE is_published = 1
          AND end_time IS NOT NULL
          AND end_time BETWEEN %s AND %s
    """, (cutoff_start, cutoff_end))
    rows = cursor.fetchall() or []

    for row in rows:
        reminder_title = f"作業截止提醒：{row['title']}"

        # 已提醒就跳過
        cursor.execute("SELECT 1 FROM notifications WHERE title = %s LIMIT 1", (reminder_title,))
        if cursor.fetchone():
            continue

        # 取全體使用者
        cursor.execute("SELECT id FROM users")
        users = cursor.fetchall() or []

        link = f"/view_announcement/{row['id']}"
        for u in users:
            user_id = u['id'] if isinstance(u, dict) else u[0]
            cursor.execute("""
                INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
                VALUES (%s, %s, %s, %s, %s, 0, NOW())
            """, (
                user_id,
                reminder_title,
                f"作業將於 {row['end_time']} 截止，請盡速完成。",
                "announcement",
                link
            ))

    conn.commit()
    cursor.close()