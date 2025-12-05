from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime
from markupsafe import escape
import traceback

notification_bp = Blueprint("notification_bp", __name__)

# =========================================================
# 頁面
# =========================================================
@notification_bp.route("/notifications")
def notifications_page():
    """一般使用者通知中心"""
    return render_template("user_shared/notifications.html")

# =========================================================
# 通知建立 Helper 函式
# =========================================================
def create_notification(user_id, title, message, category="general", link_url=None):
    """統一建立通知，支援分類、自動分類"""
    try:
        # ================================
        # 1. 自動分類（若 category = general）
        # ================================
        if category == "general":
            title_lower = title.lower()
            msg_lower = message.lower()

            # 履歷相關
            if any(k in title_lower for k in ["履歷", "resume"]) or \
               any(k in msg_lower for k in ["履歷", "resume"]):
                category = "resume"

            # 志願序
            elif any(k in title_lower for k in ["志願序", "ranking"]) or \
                 any(k in msg_lower for k in ["志願序", "ranking"]):
                category = "ranking"

            # 實習公司
            elif any(k in title_lower for k in ["公司", "實習", "廠商", "intern"]) or \
                 any(k in msg_lower for k in ["公司", "實習", "廠商", "intern"]):
                category = "company"

            # 審核通知
            elif any(k in title_lower for k in ["審核", "批准", "退件"]) or \
                 any(k in msg_lower for k in ["審核", "批准", "退件"]):
                category = "approval"

        # ================================
        # 2. 寫入資料庫
        # ================================
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO notifications (user_id, title, message, link_url, is_read, created_at)
            VALUES (%s, %s, %s, %s, 0, NOW())
        """, (user_id, title, message, link_url))
        conn.commit()
        return True

    except Exception:
        traceback.print_exc()
        return False

    finally:
        cursor.close()
        conn.close()

# =========================================================
# 分類輔助函數
# =========================================================
def _detect_category(title, message):
    """根據標題和內容自動判斷通知分類"""
    title_lower = (title or "").lower()
    msg_lower = (message or "").lower()
    
    # 履歷相關
    if any(k in title_lower for k in ["履歷", "resume"]) or \
       any(k in msg_lower for k in ["履歷", "resume"]):
        return "resume"
    
    # 志願序
    elif any(k in title_lower for k in ["志願序", "ranking"]) or \
         any(k in msg_lower for k in ["志願序", "ranking"]):
        return "ranking"
    
    # 實習公司
    elif any(k in title_lower for k in ["公司", "實習", "廠商", "intern"]) or \
         any(k in msg_lower for k in ["公司", "實習", "廠商", "intern"]):
        return "company"
    
    # 審核通知
    elif any(k in title_lower for k in ["審核", "批准", "退件"]) or \
         any(k in msg_lower for k in ["審核", "批准", "退件"]):
        return "approval"
    
    # 預設為一般/公告
    return "general"

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
        
        # 為每個通知動態計算分類
        for row in rows:
            row["category"] = _detect_category(row.get("title"), row.get("message"))
        
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
# 系統自動通知 API 範例
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

    # 使用 helper 函式建立通知
    success = create_notification(student_user_id, title, message, category="resume")
    if success:
        return jsonify({"success": True, "message": "退件通知已建立"})
    else:
        return jsonify({"success": False, "message": "新增失敗"}), 500

# =========================================================
# 範例：志願序通知
# =========================================================
@notification_bp.route("/api/create_ranking_update", methods=["POST"])
def create_ranking_update():
    data = request.get_json() or {}
    student_user_id = data.get("student_user_id")
    update_info = escape(data.get("update_info", ""))

    try:
        student_user_id = int(student_user_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "student_user_id 無效"}), 400

    title = "志願序更新通知"
    message = f"您的志願序有新的更新：\n{update_info}"

    success = create_notification(student_user_id, title, message, category="ranking")
    if success:
        return jsonify({"success": True, "message": "志願序通知已建立"})
    else:
        return jsonify({"success": False, "message": "新增失敗"}), 500

# =========================================================
# 範例：實習公司通知
# =========================================================
@notification_bp.route("/api/create_company_announcement", methods=["POST"])
def create_company_announcement():
    data = request.get_json() or {}
    student_user_id = data.get("student_user_id")
    company_name = escape(data.get("company_name", "公司"))
    content = escape(data.get("content", ""))

    try:
        student_user_id = int(student_user_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "student_user_id 無效"}), 400

    title = f"{company_name} 公告"
    message = content

    success = create_notification(student_user_id, title, message, category="company")
    if success:
        return jsonify({"success": True, "message": "公司通知已建立"})
    else:
        return jsonify({"success": False, "message": "新增失敗"}), 500
