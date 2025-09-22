from flask import Blueprint, render_template, jsonify
from datetime import datetime
import json
from config import get_db

notification_bp = Blueprint("notification", __name__, url_prefix="/notifications")

@notification_bp.route('/')
def notifications():
    return render_template('user_shared/notifications.html')

@notification_bp.route("/api/notification", methods=["GET"])
def get_notification():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        now = datetime.now()
        cursor.execute("""
            SELECT 
                id, title, content, created_by, created_at,
                target_roles, status, visible_from, visible_until,
                is_important, view_count
            FROM notification
            WHERE status = 'published'
              AND (visible_from IS NULL OR visible_from <= %s)
              AND (visible_until IS NULL OR visible_until >= %s)
            ORDER BY is_important DESC, created_at DESC
        """, (now, now))
        rows = cursor.fetchall()

        for row in rows:
            row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            row["visible_from"] = row["visible_from"].strftime("%Y-%m-%d %H:%M:%S") if row["visible_from"] else None
            row["visible_until"] = row["visible_until"].strftime("%Y-%m-%d %H:%M:%S") if row["visible_until"] else None
            row["source"] = row.pop("created_by") or "平台"

            if row["target_roles"]:
                try:
                    row["target_roles"] = json.loads(row["target_roles"])
                except Exception:
                    row["target_roles"] = []
            else:
                row["target_roles"] = []

        return jsonify({"success": True, "announcements": rows})

    except Exception as e:
        print("❌ 取得公告失敗：", e)
        return jsonify({"success": False, "message": "取得公告失敗"}), 500
    finally:
        cursor.close()
        conn.close()
