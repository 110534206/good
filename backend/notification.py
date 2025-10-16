from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime
from markupsafe import escape
import traceback

notification_bp = Blueprint("notification_bp", __name__, url_prefix="/notifications")

# =========================================================
# 頁面
# =========================================================
@notification_bp.route("/")
def notifications_page():
    """一般使用者通知中心"""
    return render_template("user_shared/notifications.html")


# =========================================================
# 個人通知 API
# =========================================================
@notification_bp.route("/api/my_notifications", methods=["GET"])
def get_my_notifications():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "未登入"}), 401

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, title, message, link_url, is_read, created_at
            FROM notifications
            WHERE user_id = %s
            ORDER BY created_at DESC
        """, (user_id,))
        rows = cursor.fetchall()
        return jsonify({"success": True, "notifications": rows})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "讀取通知失敗"}), 500
    finally:
        cursor.close()
        conn.close()


@notification_bp.route("/api/mark_read/<int:nid>", methods=["POST"])
def mark_read(nid):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "未登入"}), 401

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE notifications SET is_read=1 WHERE id=%s AND user_id=%s", (nid, user_id))
        conn.commit()
        return jsonify({"success": True, "message": "已標記為已讀"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "更新失敗"}), 500
    finally:
        cursor.close()
        conn.close()


@notification_bp.route("/api/notification/delete/<int:nid>", methods=["DELETE"])
def delete_notification(nid):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "未登入"}), 401

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notifications WHERE id=%s AND user_id=%s", (nid, user_id))
        conn.commit()
        if cursor.rowcount == 0:
            return jsonify({"success": False, "message": "找不到該通知或已刪除"})
        return jsonify({"success": True, "message": "通知已刪除"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "刪除失敗"}), 500
    finally:
        cursor.close()
        conn.close()


# =========================================================
# 系統自動通知（例如履歷退件）
# =========================================================
@notification_bp.route("/api/create_resume_rejection", methods=["POST"])
def create_resume_rejection():
    data = request.get_json() or {}
    student_user_id = data.get("student_user_id")
    teacher_name = data.get("teacher_name", "老師")
    rejection_reason = escape(data.get("rejection_reason", ""))

    try:
        student_user_id = int(student_user_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "student_user_id 無效"}), 400

    title = "履歷退件通知"
    message = f"您的履歷已被 {teacher_name} 退件。\n"
    if rejection_reason:
        message += f"退件原因：{rejection_reason}\n"
    message += "請依建議修改後重新上傳。"

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO notifications (user_id, title, message, link_url, is_read, created_at)
            VALUES (%s, %s, %s, NULL, 0, NOW())
        """, (student_user_id, title, message))
        conn.commit()
        return jsonify({"success": True, "message": "退件通知已建立"})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "新增失敗"}), 500
    finally:
        cursor.close()
        conn.close()