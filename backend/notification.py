from flask import Blueprint, render_template, jsonify, request
from datetime import datetime, timedelta
import json
from config import get_db
notification_bp = Blueprint("notification", __name__)

# ------------------------
# API - 編輯公告
# ------------------------
@notification_bp.route("/api/announcements/update/<int:announcement_id>", methods=["POST"])
def update_announcement(announcement_id):
    data = request.get_json()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE notification
            SET title=%s, content=%s, target_roles=%s, visible_from=%s, visible_until=%s, deadline=%s, is_important=%s
            WHERE id=%s
        """, (
            data.get("title"),
            data.get("content"),
            json.dumps(data.get("target_roles", [])),
            data.get("visible_from"),
            data.get("visible_until"),
            data.get("deadline"),
            data.get("is_important", 0),
            announcement_id
        ))
        conn.commit()
        return jsonify({"success": True, "message": "公告已更新"})
    except Exception as e:
        print("❌ 編輯公告失敗：", e)
        return jsonify({"success": False, "message": "編輯公告失敗"}), 500
    finally:
        cursor.close()
        conn.close()


# ------------------------
# API - 發布公告
# ------------------------
@notification_bp.route("/api/announcements/publish/<int:announcement_id>", methods=["POST"])
def publish_announcement(announcement_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE notification
            SET status='published', visible_from=NOW()
            WHERE id=%s
        """, (announcement_id,))
        conn.commit()
        return jsonify({"success": True, "message": "公告已發布"})
    except Exception as e:
        print("❌ 發布公告失敗：", e)
        return jsonify({"success": False, "message": "發布公告失敗"}), 500
    finally:
        cursor.close()
        conn.close()


# ------------------------
# API - 封存公告
# ------------------------
@notification_bp.route("/api/announcements/archive/<int:announcement_id>", methods=["POST"])
def archive_announcement(announcement_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE notification
            SET status='archived', visible_until=NOW()
            WHERE id=%s
        """, (announcement_id,))
        conn.commit()
        return jsonify({"success": True, "message": "公告已封存"})
    except Exception as e:
        print("❌ 封存公告失敗：", e)
        return jsonify({"success": False, "message": "封存公告失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------
# API - 公告清單列表
# ------------------------
@notification_bp.route("/api/announcements/list", methods=["GET"])
def list_announcements():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, title, content, target_roles, visible_from, visible_until, deadline, is_important, status
            FROM notification
            ORDER BY visible_from DESC
        """)
        rows = cursor.fetchall()
        
        # 將資料轉換為 JSON-friendly 格式
        announcements = []
        for row in rows:
            announcements.append({
                "id": row[0],
                "title": row[1],
                "content": row[2],
                "target_roles": json.loads(row[3]) if row[3] else [],
                "visible_from": row[4].isoformat() if row[4] else None,
                "visible_until": row[5].isoformat() if row[5] else None,
                "deadline": row[6].isoformat() if row[6] else None,
                "is_important": row[7],
                "status": row[8]
            })

        return jsonify({"success": True, "announcements": announcements})
    
    except Exception as e:
        print("❌ 取得公告列表失敗：", e)
        return jsonify({"success": False, "message": "取得公告列表失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------
# 網頁 - 管理公告頁面
# ------------------------
@notification_bp.route('/manage_announcements')
def manage_announcements():
    return render_template('user_shared/manage_announcements.html')

def check_and_generate_reminders():
  print("🔔 check_and_generate_reminders 執行中...（此為測試函式）")
