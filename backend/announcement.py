from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime
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
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users")  # 全體使用者皆推送
    users = cursor.fetchall()

    notif_sql = """
        INSERT INTO notifications (user_id, title, message, link_url, is_read, created_at)
        VALUES (%s, %s, %s, %s, 0, NOW())
    """
    link = f"/view_announcement/{ann_id}"
    for u in users:
        cursor.execute(notif_sql, (u[0], f"新公告：{title}", content[:150] + "...", link))

    conn.commit()
    cursor.close()