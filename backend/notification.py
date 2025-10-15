from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime
import traceback

notification_bp = Blueprint('notification_bp', __name__)

# -----------------------------
# 前台頁面：使用者通知中心
# -----------------------------
@notification_bp.route('/')
def notifications_page():
    return render_template('user_shared/notifications.html')


# -----------------------------
# API：取得個人通知
# -----------------------------
@notification_bp.route('/api/my_notifications', methods=['GET'])
def get_my_notifications():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "message": "未登入"}), 401

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, user_id, title, message, link_url, is_read, created_at
            FROM notifications
            WHERE user_id = %s
            ORDER BY created_at DESC
        """, (user_id,))
        rows = cursor.fetchall()
        return jsonify({"success": True, "notifications": rows})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "系統錯誤"}), 500
    finally:
        cursor.close()
        conn.close()


# -----------------------------
# API：標記通知為已讀
# -----------------------------
@notification_bp.route('/api/mark_read/<int:notification_id>', methods=['POST'])
def mark_notification_read(notification_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "message": "未登入"}), 401

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE notifications
            SET is_read = 1
            WHERE id = %s AND user_id = %s
        """, (notification_id, user_id))
        conn.commit()
        return jsonify({"success": True, "message": "已更新狀態"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "系統錯誤"}), 500
    finally:
        cursor.close()
        conn.close()


# -----------------------------
# API：刪除通知
# -----------------------------
@notification_bp.route('/api/delete/<int:notification_id>', methods=['DELETE'])
def delete_notification(notification_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "message": "未登入"}), 401

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM notifications
            WHERE id = %s AND user_id = %s
        """, (notification_id, user_id))
        conn.commit()

        if cursor.rowcount > 0:
            return jsonify({"success": True, "message": "通知已刪除"})
        else:
            return jsonify({"success": False, "message": "找不到通知"}), 404
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "系統錯誤"}), 500
    finally:
        cursor.close()
        conn.close()


# -----------------------------
# API：建立履歷退件通知
# -----------------------------
@notification_bp.route('/api/create_resume_rejection', methods=['POST'])
def create_resume_rejection():
    data = request.get_json() or {}
    student_user_id = data.get('student_user_id')
    teacher_name = data.get('teacher_name', '老師')
    rejection_reason = data.get('rejection_reason', '')

    if not student_user_id:
        return jsonify({"success": False, "message": "缺少 student_user_id"}), 400

    # 通知內容
    title = '履歷退件通知'
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
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": "新增失敗"}), 500
    finally:
        cursor.close()
        conn.close()
