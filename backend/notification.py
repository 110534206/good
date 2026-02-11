from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime, timedelta
from markupsafe import escape
import traceback

notification_bp = Blueprint("notification_bp", __name__)


def _taiwan_now():
    """與公告時間一致：使用台灣時間 (UTC+8) 判斷「現在」是否在公告區間內"""
    return datetime.utcnow() + timedelta(hours=8)

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

            # 媒合相關（優先判斷，避免被其他類別誤判）
            if any(k in title_lower for k in ["媒合", "matching"]) or \
               any(k in msg_lower for k in ["媒合", "matching"]):
                category = "matching"

            # 履歷相關
            elif any(k in title_lower for k in ["履歷", "resume"]) or \
                 any(k in msg_lower for k in ["履歷", "resume"]):
                category = "resume"

            # 志願序
            elif any(k in title_lower for k in ["志願序", "ranking"]) or \
                 any(k in msg_lower for k in ["志願序", "ranking"]):
                category = "ranking"

            # 實習心得
            elif any(k in title_lower for k in ["實習心得", "心得退件", "心得審核", "experience"]) or \
                 any(k in msg_lower for k in ["實習心得", "心得退件", "心得審核", "experience"]):
                category = "experience"

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
            INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
            VALUES (%s, %s, %s, %s, %s, 0, NOW())
        """, (user_id, title, message, category, link_url))
        conn.commit()
        print(f"[通知創建成功] user_id={user_id}, title={title}, category={category}")
        return True

    except Exception as e:
        print(f"[通知創建失敗] user_id={user_id}, title={title}, 錯誤: {str(e)}")
        traceback.print_exc()
        return False

    finally:
        if cursor:
            cursor.close()
        if conn:
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

    # 獲取可選的類別篩選參數
    category_filter = request.args.get("category", None)

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 1. 直接從 notifications 讀取資料（公告已經在發佈時同步寫入）
        #    不再另外「補齊」公告，避免把不該收到的公告推給其他角色
        if category_filter and category_filter != "all":
            cursor.execute("""
                SELECT n.id, n.title, n.message, n.category, n.link_url, n.is_read, n.created_at,
                       a.start_time
                FROM notifications n
                LEFT JOIN announcement a ON n.link_url = CONCAT('/view_announcement/', a.id)
                WHERE n.user_id = %s AND n.category = %s
                ORDER BY n.created_at DESC
            """, (user_id, category_filter))
        else:
            cursor.execute("""
                SELECT n.id, n.title, n.message, n.category, n.link_url, n.is_read, n.created_at,
                       a.start_time, a.end_time
                FROM notifications n
                LEFT JOIN announcement a ON n.link_url = CONCAT('/view_announcement/', a.id)
                WHERE n.user_id = %s
                ORDER BY n.created_at DESC
            """, (user_id,))
        
        all_rows = cursor.fetchall() or []
        
        # 2. 為每個通知動態計算分類並格式化時間
        for row in all_rows:
            # 如果沒有 category，則自動檢測
            if not row.get("category"):
                row["category"] = _detect_category(row.get("title"), row.get("message"))
            
            # 格式化 created_at 時間（確保正確顯示）
            created_at = row.get("created_at")
            if isinstance(created_at, datetime):
                row["created_at"] = created_at.strftime("%Y-%m-%d %H:%M:%S")
            elif created_at:
                try:
                    if isinstance(created_at, str):
                        if 'T' in created_at:
                            parsed = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        else:
                            parsed = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
                        row["created_at"] = parsed.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        row["created_at"] = str(created_at)
                except:
                    row["created_at"] = str(created_at)
            else:
                row["created_at"] = ""
            
            # 格式化 start_time（如果是公告）
            start_time = row.get("start_time")
            if isinstance(start_time, datetime):
                row["start_time"] = start_time.strftime("%Y-%m-%d %H:%M:%S")
            elif start_time:
                try:
                    if isinstance(start_time, str):
                        if 'T' in start_time:
                            parsed = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                        else:
                            parsed = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
                        row["start_time"] = parsed.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        row["start_time"] = str(start_time)
                except:
                    row["start_time"] = str(start_time) if start_time else None
            else:
                row["start_time"] = None
            
            # 格式化 end_time（如果是公告）
            end_time = row.get("end_time")
            if isinstance(end_time, datetime):
                row["end_time"] = end_time.strftime("%Y-%m-%d %H:%M:%S")
            elif end_time:
                try:
                    if isinstance(end_time, str):
                        if 'T' in end_time:
                            parsed = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                        else:
                            parsed = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
                        row["end_time"] = parsed.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        row["end_time"] = str(end_time)
                except:
                    row["end_time"] = str(end_time) if end_time else None
            else:
                row["end_time"] = None
        
        # 3. 按時間排序（最新的在前）
        all_rows.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        
        # 4. 如果有類別篩選，再次過濾
        if category_filter and category_filter != "all":
            all_rows = [row for row in all_rows if row.get("category") == category_filter]
        
        return jsonify({"success": True, "notifications": all_rows})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "讀取通知失敗"}), 500
    finally:
        cursor.close()
        conn.close()


@notification_bp.route("/api/my_notifications/unread_count", methods=["GET"])
def get_visible_unread_count():
    """
    回傳「可見」未讀數量：排除已結束、未開始的公告，與通知頁面預設顯示一致。
    主頁鈴鐺與通知中心的「未讀 X」皆為此數字。
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "未登入"}), 401

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        # 與 get_my_notifications 一致：JOIN 公告表取得 start_time/end_time（CAST 確保型別一致）
        cursor.execute("""
            SELECT n.id, n.is_read, n.category, n.link_url,
                   a.start_time, a.end_time
            FROM notifications n
            LEFT JOIN announcement a ON n.link_url = CONCAT('/view_announcement/', CAST(a.id AS CHAR))
            WHERE n.user_id = %s
        """, (user_id,))
        rows = cursor.fetchall() or []
        cursor.close()
        conn.close()

        now = _taiwan_now()
        count = 0
        for row in rows:
            if row.get("is_read"):
                continue
            start_time = row.get("start_time")
            end_time = row.get("end_time")
            # 有 JOIN 到公告時間則視為公告，需在區間內才計入
            is_announcement = start_time is not None or end_time is not None
            if is_announcement:
                try:
                    st = start_time
                    if st is not None and isinstance(st, str):
                        st = datetime.strptime(st[:19], "%Y-%m-%d %H:%M:%S") if len(st) >= 19 else now
                    started = start_time is None or (st <= now if isinstance(st, datetime) else True)
                except Exception:
                    started = True
                try:
                    et = end_time
                    if et is not None and isinstance(et, str):
                        et = datetime.strptime(et[:19], "%Y-%m-%d %H:%M:%S") if len(et) >= 19 else now
                    not_ended = end_time is None or (et >= now if isinstance(et, datetime) else True)
                except Exception:
                    not_ended = True
                if not (started and not_ended):
                    continue
            count += 1

        return jsonify({"success": True, "unread_count": count})
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "讀取未讀數失敗"}), 500


@notification_bp.route("/api/mark_read/<nid>", methods=["POST"])
def mark_read(nid):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "未登入"}), 401
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 處理從 announcement 表轉換來的通知（格式：ann_xxx）
        if str(nid).startswith("ann_"):
            ann_id = str(nid).replace("ann_", "")
            link_url = f"/view_announcement/{ann_id}"
            
            # 查找該用戶是否已有對應的通知記錄
            cursor.execute("""
                SELECT id FROM notifications 
                WHERE user_id = %s AND link_url = %s
            """, (user_id, link_url))
            existing = cursor.fetchone()
            
            if existing:
                # 如果存在，更新為已讀
                cursor.execute("UPDATE notifications SET is_read=1 WHERE id=%s AND user_id=%s", 
                             (existing[0], user_id))
            else:
                # 如果不存在，創建新的通知記錄並標記為已讀
                cursor.execute("""
                    SELECT title, content FROM announcement WHERE id = %s
                """, (ann_id,))
                ann_data = cursor.fetchone()
                if ann_data:
                    title = f"公告：{ann_data[0]}"
                    message = ann_data[1][:200] if ann_data[1] else ""
                    cursor.execute("""
                        INSERT INTO notifications (user_id, title, message, category, link_url, is_read, created_at)
                        VALUES (%s, %s, %s, %s, %s, 1, NOW())
                    """, (user_id, title, message, "announcement", link_url))
            conn.commit()
        else:
            # 處理正常的通知 ID
            try:
                nid_int = int(nid)
                cursor.execute("UPDATE notifications SET is_read=1 WHERE id=%s AND user_id=%s", 
                             (nid_int, user_id))
                conn.commit()
            except ValueError:
                return jsonify({"success": False, "message": "無效的通知ID"}), 400
        
        return jsonify({"success": True, "message": "已標記為已讀"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"更新失敗：{str(e)}"}), 500
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
