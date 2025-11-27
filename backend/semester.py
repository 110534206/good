from flask import Blueprint, request, jsonify, session, render_template
from config import get_db
from datetime import datetime
import traceback

semester_bp = Blueprint("semester_bp", __name__, url_prefix="/semester")

# =========================================================
# Helper: 取得當前學期（可被其他模組導入使用）
# =========================================================
def get_current_semester(cursor):
    """取得當前活躍的學期"""
    cursor.execute("SELECT * FROM semesters WHERE is_active = 1 LIMIT 1")
    return cursor.fetchone()

# =========================================================
# Helper: 取得學期代碼（如 '1132'）（可被其他模組導入使用）
# =========================================================
def get_current_semester_code(cursor):
    """取得當前學期代碼"""
    semester = get_current_semester(cursor)
    return semester['code'] if semester else None

# =========================================================
# Helper: 取得當前學期ID（可被其他模組導入使用）
# =========================================================
def get_current_semester_id(cursor):
    """取得當前學期ID"""
    semester = get_current_semester(cursor)
    return semester['id'] if semester else None

# =========================================================
# API: 取得當前學期
# =========================================================
@semester_bp.route("/api/current", methods=["GET"])
def get_current():
    """取得當前學期資訊"""
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        semester = get_current_semester(cursor)
        if not semester:
            return jsonify({"success": False, "message": "目前沒有設定當前學期"}), 404
        
        # 格式化日期
        if isinstance(semester.get('start_date'), datetime):
            semester['start_date'] = semester['start_date'].strftime("%Y-%m-%d")
        if isinstance(semester.get('end_date'), datetime):
            semester['end_date'] = semester['end_date'].strftime("%Y-%m-%d")
        
        return jsonify({"success": True, "semester": semester})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 取得所有學期列表
# =========================================================
@semester_bp.route("/api/list", methods=["GET"])
def list_semesters():
    """取得所有學期列表（所有使用者都可以查看）"""
    # 移除權限檢查，讓學生也能查看學期列表
    # if session.get('role') not in ['admin', 'ta']:
    #     return jsonify({"success": False, "message": "未授權"}), 403
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT id, code, start_date, end_date, is_active, created_at
            FROM semesters
            ORDER BY code DESC
        """)
        semesters = cursor.fetchall()
        
        # 格式化日期
        for s in semesters:
            if isinstance(s.get('start_date'), datetime):
                s['start_date'] = s['start_date'].strftime("%Y-%m-%d")
            if isinstance(s.get('end_date'), datetime):
                s['end_date'] = s['end_date'].strftime("%Y-%m-%d")
            if isinstance(s.get('created_at'), datetime):
                s['created_at'] = s['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        
        return jsonify({"success": True, "semesters": semesters})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 建立新學期
# =========================================================
@semester_bp.route("/api/create", methods=["POST"])
def create_semester():
    """建立新學期（管理員/科助）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    code = data.get("code", "").strip()  # 如 '1132'
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    
    if not code:
        return jsonify({"success": False, "message": "請提供學期代碼"}), 400
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 檢查學期代碼是否已存在
        cursor.execute("SELECT id FROM semesters WHERE code = %s", (code,))
        if cursor.fetchone():
            return jsonify({"success": False, "message": "該學期代碼已存在"}), 400
        
        # 插入新學期（包含 created_at）
        cursor.execute("""
            INSERT INTO semesters (code, start_date, end_date, is_active, created_at)
            VALUES (%s, %s, %s, 0, NOW())
        """, (code, start_date, end_date))
        
        conn.commit()
        return jsonify({"success": True, "message": "學期建立成功"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 切換當前學期
# =========================================================
@semester_bp.route("/api/switch", methods=["POST"])
def switch_semester():
    """切換當前學期（管理員/科助）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    semester_id = data.get("semester_id")
    
    if not semester_id:
        return jsonify({"success": False, "message": "請提供學期ID"}), 400
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 檢查學期是否存在
        cursor.execute("SELECT id, code FROM semesters WHERE id = %s", (semester_id,))
        semester = cursor.fetchone()
        if not semester:
            return jsonify({"success": False, "message": "找不到該學期"}), 404
        
        # 關閉所有學期的 is_active
        cursor.execute("UPDATE semesters SET is_active = 0")
        
        # 啟用目標學期
        cursor.execute("UPDATE semesters SET is_active = 1 WHERE id = %s", (semester_id,))
        
        # 關閉上學期的公司開放狀態
        # 注意：這裡假設 company_openings 表有 semester 欄位
        current_code = semester['code']
        try:
            # 嘗試更新 company_openings 表（如果表存在）
            cursor.execute("""
                UPDATE company_openings 
                SET is_open = 0 
                WHERE semester != %s
            """, (current_code,))
        except Exception as e:
            # 如果表不存在或欄位不存在，只記錄錯誤但不影響主流程
            print(f"⚠️ 更新 company_openings 表時發生錯誤: {e}")
            pass
        
        conn.commit()
        return jsonify({
            "success": True, 
            "message": f"已切換至學期 {current_code}",
            "semester_code": current_code
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 更新學期資訊
# =========================================================
@semester_bp.route("/api/update/<int:semester_id>", methods=["PUT"])
def update_semester(semester_id):
    """更新學期資訊（管理員/科助）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 更新學期資訊
        update_fields = []
        params = []
        
        if start_date:
            update_fields.append("start_date = %s")
            params.append(start_date)
        if end_date:
            update_fields.append("end_date = %s")
            params.append(end_date)
        
        if not update_fields:
            return jsonify({"success": False, "message": "沒有提供要更新的欄位"}), 400
        
        params.append(semester_id)
        cursor.execute(f"""
            UPDATE semesters 
            SET {', '.join(update_fields)}
            WHERE id = %s
        """, params)
        
        conn.commit()
        return jsonify({"success": True, "message": "學期資訊更新成功"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 刪除學期
# =========================================================
@semester_bp.route("/api/delete/<int:semester_id>", methods=["DELETE"])
def delete_semester(semester_id):
    """刪除學期（管理員/科助，不能刪除當前學期）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 檢查是否為當前學期
        cursor.execute("SELECT is_active FROM semesters WHERE id = %s", (semester_id,))
        semester = cursor.fetchone()
        if not semester:
            return jsonify({"success": False, "message": "找不到該學期"}), 404
        
        if semester['is_active']:
            return jsonify({"success": False, "message": "無法刪除當前學期"}), 400
        
        # 檢查是否有資料關聯（履歷、志願序等）
        # 這裡可以添加檢查邏輯，但為了簡化，直接刪除
        # 注意：實際環境中可能需要軟刪除或阻止刪除
        
        # 刪除學期
        cursor.execute("DELETE FROM semesters WHERE id = %s", (semester_id,))
        conn.commit()
        
        return jsonify({"success": True, "message": "學期已刪除"})
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# 頁面路由：學期管理頁面（科助/管理員）
# =========================================================
@semester_bp.route("/manage")
def manage_semesters_page():
    """學期管理頁面"""
    if session.get('role') not in ['admin', 'ta']:
        from flask import redirect, url_for
        return redirect(url_for('auth_bp.login_page'))
    
    return render_template('admin/manage_semesters.html')

