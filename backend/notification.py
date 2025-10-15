from flask import Blueprint, render_template, jsonify, request
from datetime import datetime
import json
from config import get_db
from flask import session

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

        # 處理 target_roles
        if target_roles:
            try:
                # 如果是 JSON 字符串，直接使用
                if target_roles.startswith('['):
                    target_roles_json = target_roles
                else:
                    # 如果是逗號分隔的字符串，轉換為 JSON
                    target_roles_json = json.dumps(target_roles.split(','))
            except:
                target_roles_json = '[]'
        else:
            target_roles_json = '[]'

        # 處理 deadline
        deadline_datetime = None
        if deadline:
            try:
                deadline_datetime = datetime.strptime(deadline, "%Y-%m-%dT%H:%M")
            except ValueError:
                return jsonify({"success": False, "message": "截止時間格式錯誤"}), 400

        # 處理 is_important
        is_important_bool = 1 if is_important == "1" else 0

        cursor.execute("""
            INSERT INTO notification (title, content, type, target_roles, deadline, is_important, status, created_at, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, 'published', NOW(), %s)
        """, (
            title, content, type_, target_roles_json, deadline_datetime, is_important_bool, 'ta'
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

    # 模擬登入使用者資訊（實際應從 session 取得）
    current_user = {
        "id": session.get("user_id"),
        "role": session.get("role"),             # e.g., 'student', 'teacher'
        "class_name": session.get("class_name")  # e.g., '四孝'
    }

    try:
        cursor.execute("""
            SELECT 
                id, title, content, target_roles, created_at, deadline, is_important,
                status, type, created_by, target_class, target_user_id
            FROM notification
            WHERE status = 'published'
            ORDER BY is_important DESC, created_at DESC
        """)
        rows = cursor.fetchall()

        announcements = []
        for row in rows:
            (
                id, title, content, target_roles_json, created_at, deadline, is_important,
                status, type_, created_by, target_class, target_user_id
            ) = row

            # 解析目標角色
            target_roles = []
            if target_roles_json:
                try:
                    target_roles = json.loads(target_roles_json)
                except Exception as e:
                    print(f"❗ 無法解析 target_roles：{e}")
                    target_roles = []

            # 權限過濾邏輯
            visible = False

            # ✅ 條件一：未指定任何目標 → 視為公開
            if not target_roles and not target_class and not target_user_id:
                visible = True

            # ✅ 條件二：符合角色
            elif current_user["role"] in target_roles:
                visible = True

            # ✅ 條件三：符合班級
            elif target_class and target_class == current_user["class_name"]:
                visible = True

            # ✅ 條件四：符合個人使用者 ID
            elif target_user_id and str(target_user_id) == str(current_user["id"]):
                visible = True

            # ❌ 不符合者略過
            if not visible:
                continue

            # 判斷公告來源（前端顯示用途）
            if created_by == 'ta':
                source = "科助"
            elif created_by == 'teacher':
                source = "老師"
            elif created_by == 'director':
                source = "主任"
            else:
                source = "系統"

            # 加入公告內容
            announcements.append({
                "id": id,
                "title": title,
                "content": content,
                "target_roles": target_roles,
                "created_at": created_at.isoformat() if created_at else None,
                "deadline": deadline.isoformat() if deadline else None,
                "is_important": is_important,
                "status": status,
                "type": type_,
                "source": source
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
# API - 自動生成通知（當班導退件學生履歷時）
# ------------------------
@notification_bp.route("/api/notifications/create_resume_rejection", methods=["POST"])
def create_resume_rejection_notification():
    """當班導退件學生履歷時，自動為該學生創建通知"""
    data = request.get_json()
    student_username = data.get("student_username")
    teacher_name = data.get("teacher_name", "老師")
    rejection_reason = data.get("rejection_reason", "")
    
    if not student_username:
        return jsonify({"success": False, "message": "缺少學生帳號"}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        # 創建退件通知
        title = f"履歷退件通知"
        content = f"您的履歷已被{teacher_name}退件。"
        if rejection_reason:
            content += f"\n\n退件原因：{rejection_reason}"
        content += "\n\n請根據老師的建議修改履歷後重新上傳。"
        
        cursor.execute("""
            INSERT INTO notification (title, content, type, target_roles, is_important, status, created_at, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
        """, (
            title, content, 'reminder', json.dumps(['student']), 1, 'published', 'system'
        ))
        
        conn.commit()
        return jsonify({"success": True, "message": "退件通知已發送"})
    except Exception as e:
        print("❌ 創建退件通知失敗：", e)
        return jsonify({"success": False, "message": "創建退件通知失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------
# API - 自動生成截止日期提醒
# ------------------------
@notification_bp.route("/api/notifications/create_deadline_reminder", methods=["POST"])
def create_deadline_reminder():
    """為截止日期創建提醒通知"""
    data = request.get_json()
    deadline_type = data.get("deadline_type")  # 'resume' 或 'preference'
    deadline_datetime = data.get("deadline_datetime")
    target_roles = data.get("target_roles", ["student"])
    
    if not deadline_type or not deadline_datetime:
        return jsonify({"success": False, "message": "缺少必要參數"}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        # 根據類型設定標題和內容
        if deadline_type == "resume":
            title = "履歷上傳截止提醒"
            content = f"履歷上傳截止時間為：{deadline_datetime}\n\n請盡快上傳您的履歷，逾期將無法提交。"
        elif deadline_type == "preference":
            title = "志願序填寫截止提醒"
            content = f"志願序填寫截止時間為：{deadline_datetime}\n\n請盡快填寫您的志願序，逾期將無法修改。"
        else:
            return jsonify({"success": False, "message": "無效的截止類型"}), 400
        
        # 解析截止時間
        deadline_dt = datetime.strptime(deadline_datetime, "%Y-%m-%dT%H:%M")
        
        cursor.execute("""
            INSERT INTO notification (title, content, type, target_roles, deadline, is_important, status, created_at, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s)
        """, (
            title, content, 'deadline', json.dumps(target_roles), deadline_dt, 1, 'published', 'ta'
        ))
        
        conn.commit()
        return jsonify({"success": True, "message": "截止日期提醒已創建"})
    except Exception as e:
        print("❌ 創建截止日期提醒失敗：", e)
        return jsonify({"success": False, "message": "創建截止日期提醒失敗"}), 500
    finally:
        cursor.close()
        conn.close()

# ------------------------
# 自動提醒檢查函式
# ------------------------
def check_and_generate_reminders():
    """檢查並生成自動提醒"""
    print("🔔 檢查自動提醒...")
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        # 檢查即將到來的截止日期
        cursor.execute("""
            SELECT id, title, deadline, target_roles
            FROM notification
            WHERE type = 'deadline' 
            AND status = 'published'
            AND deadline IS NOT NULL
            AND deadline > NOW()
            AND deadline <= DATE_ADD(NOW(), INTERVAL 1 DAY)
            AND reminder_generated = 0
        """)
        
        upcoming_deadlines = cursor.fetchall()
        
        for deadline in upcoming_deadlines:
            notification_id, title, deadline_dt, target_roles = deadline
            
            # 創建提醒通知
            reminder_title = f"⏰ 截止提醒：{title}"
            reminder_content = f"提醒：{title}\n截止時間：{deadline_dt.strftime('%Y-%m-%d %H:%M')}\n\n請注意時間，盡快完成相關作業。"
            
            cursor.execute("""
                INSERT INTO notification (title, content, type, target_roles, is_important, status, created_at, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
            """, (
                reminder_title, reminder_content, 'reminder', target_roles, 1, 'published', 'system'
            ))
            
            # 標記原通知已生成提醒
            cursor.execute("""
                UPDATE notification 
                SET reminder_generated = 1 
                WHERE id = %s
            """, (notification_id,))
        
        conn.commit()
        print(f"✅ 已生成 {len(upcoming_deadlines)} 個截止提醒")
        
    except Exception as e:
        print(f"❌ 自動提醒檢查失敗：{e}")
    finally:
        cursor.close()
        conn.close()
