from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime
import traceback

announcement_bp = Blueprint("announcement_bp", __name__)


# =========================================================
# 頁面：後台公告管理（主任、科助）
# =========================================================
@announcement_bp.route('/manage_announcements')
def manage_announcements_page():
    role = session.get('role')
    if role not in ["director", "ta"]:
        return "未授權", 403
    return render_template('user_shared/manage_announcements.html')


# =========================================================
# 頁面：公告牆（所有登入者可見）
# =========================================================
@announcement_bp.route('/')
def announcements_page():
    return render_template('user_shared/notifications.html')


# =========================================================
# API：列出公開公告（前台）
# =========================================================
@announcement_bp.route('/api/list', methods=['GET'])
def list_public_announcements():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, title, content, start_time, end_time, is_published, created_at, created_by
            FROM announcement
            WHERE is_published = 1
              AND (start_time IS NULL OR start_time <= NOW())
              AND (end_time IS NULL OR end_time >= NOW())
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        return jsonify({"success": True, "data": rows})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"資料查詢錯誤：{e}"}), 500
    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass


# =========================================================
# API：建立公告（主任、科助）
# =========================================================
@announcement_bp.route('/api/create', methods=['POST'])
def create_announcement():
    role = session.get('role')
    username = session.get('username')
    if role not in ['director', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json() or {}
    title = data.get('title', '').strip()
    content = data.get('content', '').strip()
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    is_published = 1 if data.get('is_published') else 0

    if not title or not content:
        return jsonify({"success": False, "message": "標題與內容為必填項"}), 400

    try:
        # 將時間轉換成 MySQL 可接受格式
        def parse_time(t):
            return datetime.fromisoformat(t) if t else None

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO announcement (title, content, start_time, end_time, is_published, created_at, created_by)
            VALUES (%s, %s, %s, %s, %s, NOW(), %s)
        """, (title, content, parse_time(start_time), parse_time(end_time), is_published, username))
        conn.commit()
        return jsonify({"success": True, "message": "公告已建立"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"建立公告時發生錯誤：{e}"}), 500
    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass


# =========================================================
# API：更新公告（主任、科助）
# =========================================================
@announcement_bp.route('/api/update/<int:announcement_id>', methods=['POST'])
def update_announcement(announcement_id):
    role = session.get('role')
    if role not in ['director', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json() or {}
    title = data.get('title', '').strip()
    content = data.get('content', '').strip()
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    is_published = 1 if data.get('is_published') else 0

    if not title or not content:
        return jsonify({"success": False, "message": "標題與內容為必填項"}), 400

    try:
        def parse_time(t):
            return datetime.fromisoformat(t) if t else None

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE announcement
            SET title=%s, content=%s, start_time=%s, end_time=%s, is_published=%s
            WHERE id=%s
        """, (title, content, parse_time(start_time), parse_time(end_time), is_published, announcement_id))
        conn.commit()

        if cursor.rowcount:
            return jsonify({"success": True, "message": "公告已更新"})
        else:
            return jsonify({"success": False, "message": "找不到該公告"}), 404
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"更新公告時發生錯誤：{e}"}), 500
    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass


# =========================================================
# API：刪除公告（主任、科助）
# =========================================================
@announcement_bp.route('/api/delete/<int:announcement_id>', methods=['DELETE'])
def delete_announcement(announcement_id):
    role = session.get('role')
    if role not in ['director', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM announcement WHERE id=%s", (announcement_id,))
        conn.commit()

        if cursor.rowcount:
            return jsonify({"success": True, "message": "公告已刪除"})
        else:
            return jsonify({"success": False, "message": "找不到該公告"}), 404
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"刪除公告時發生錯誤：{e}"}), 500
    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass
