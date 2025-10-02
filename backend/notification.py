from flask import Blueprint, render_template, jsonify, request
from datetime import datetime
import json
from config import get_db

notification_bp = Blueprint("notification", __name__)

# ------------------------
# API - 新增公告
# ------------------------
@notification_bp.route("/api/announcements/create", methods=["POST"])
def create_announcement():
    conn = get_db()
    cursor = conn.cursor()
    try:
        title = request.form.get("title")
        content = request.form.get("content")
        type_ = request.form.get("type")
        target_roles = request.form.get("target_roles")
        deadline = request.form.get("deadline")
        is_important = request.form.get("is_important", 0)

        if not title or not content or not type_:
            return jsonify({"success": False, "message": "標題、內容及類型為必填項目"}), 400

        if target_roles:
            target_roles = json.dumps(target_roles.split(','))
        else:
            target_roles = '[]'

        if deadline:
            deadline = datetime.strptime(deadline, "%Y-%m-%dT%H:%M")
        else:
            deadline = None

        cursor.execute("""
            INSERT INTO notification (title, content, type, target_roles, deadline, is_important, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'draft', NOW())
        """, (
            title, content, type_, target_roles, deadline, is_important
        ))
        conn.commit()
        return jsonify({"success": True, "message": "公告新增成功"})
    except Exception as e:
        print("❌ 新增公告失敗：", e)
        return jsonify({"success": False, "message": "新增公告失敗"}), 500
    finally:
        cursor.close()
        conn.close()

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
# ✅ API - 後台公告清單列表（撈全部）
# ------------------------
@notification_bp.route("/api/announcements", methods=["GET"])
def list_all_announcements():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, title, content, target_roles, created_at, visible_from, visible_until, deadline, is_important, status, type
            FROM notification
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()

        announcements = []
        for row in rows:
            announcements.append({
                "id": row[0],
                "title": row[1],
                "content": row[2],
                "target_roles": json.loads(row[3]) if row[3] else [],
                "created_at": row[4].isoformat() if row[4] else None,
                "visible_from": row[5].isoformat() if row[5] else None,
                "visible_until": row[6].isoformat() if row[6] else None,
                "deadline": row[7].isoformat() if row[7] else None,
                "is_important": row[8],
                "status": row[9],
                "type": row[10]
            })

        return jsonify({"success": True, "announcements": announcements})
    except Exception as e:
        print("❌ 取得公告列表失敗：", e)
        return jsonify({"success": False, "message": "取得公告列表失敗"}), 500
    finally:
        cursor.close()
        conn.close()

@notification_bp.route("/api/announcements/list", methods=["GET"])
def list_announcements():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, title, content, target_roles, created_at, visible_from, visible_until, deadline, is_important, status, type
            FROM notification
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()

        announcements = []
        for row in rows:
            announcements.append({
                "id": row[0],
                "title": row[1],
                "content": row[2],
                "target_roles": json.loads(row[3]) if row[3] else [],
                "created_at": row[4].isoformat() if row[4] else None,
                "visible_from": row[5].isoformat() if row[5] else None,
                "visible_until": row[6].isoformat() if row[6] else None,
                "deadline": row[7].isoformat() if row[7] else None,
                "is_important": row[8],
                "status": row[9],
                "type": row[10]
            })

        return jsonify({"success": True, "announcements": announcements})
    except Exception as e:
        print("❌ 取得公告列表失敗：", e)
        return jsonify({"success": False, "message": "取得公告列表失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------
# ✅ API - 前台公告列表（只撈已發佈 published 的）
# ------------------------
@notification_bp.route("/notifications/api/announcements", methods=["GET"])
def get_public_announcements():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, title, content, target_roles, created_at, deadline, is_important, status, type
            FROM notification
            WHERE status = 'published'
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()

        announcements = []
        for row in rows:
            announcements.append({
                "id": row[0],
                "title": row[1],
                "content": row[2],
                "target_roles": json.loads(row[3]) if row[3] else [],
                "created_at": row[4].isoformat() if row[4] else None,
                "deadline": row[5].isoformat() if row[5] else None,
                "is_important": row[6],
                "status": row[7],
                "type": row[8],
                "source": "系統"  # 或者自訂來源分類
            })

        return jsonify({"success": True, "announcements": announcements})
    except Exception as e:
        print("❌ 取得前台公告失敗：", e)
        return jsonify({"success": False, "message": "取得公告失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------
# API - 刪除公告
# ------------------------
@notification_bp.route("/api/announcements/delete/<int:announcement_id>", methods=["DELETE"])
def delete_announcement(announcement_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM notification WHERE id = %s", (announcement_id,))
        conn.commit()

        if cursor.rowcount > 0:
            return jsonify({"success": True, "message": "公告已刪除"})
        else:
            return jsonify({"success": False, "message": "公告未找到"}), 404
    except Exception as e:
        print("❌ 刪除公告失敗：", e)
        return jsonify({"success": False, "message": "刪除公告失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------
# 網頁 - 管理公告頁面
# ------------------------
@notification_bp.route('/manage_announcements')
def manage_announcements():
    return render_template('user_shared/manage_announcements.html')

# ------------------------
# 網頁 - 使用者通知頁面（前台通知中心）
# ------------------------
@notification_bp.route('/notifications')
def notifications():
    return render_template('user_shared/notifications.html')

# ------------------------
# 測試函式（可移除）
# ------------------------
def check_and_generate_reminders():
    print("🔔 check_and_generate_reminders 執行中...（此為測試函式）")
