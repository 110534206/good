from flask import Blueprint, request, jsonify, session, render_template
from config import get_db
from datetime import datetime, date, timedelta
import traceback
import time
import re

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
# Helper: 是否為「當前實習學期」學生（可被其他模組導入使用）
# =========================================================
def is_student_in_current_internship(cursor, user_id):
    """
    判斷該使用者是否為學生且其實習學期為當前學期。
    採用預設值機制：存在一筆 internship_configs 符合
    (user_id = 該生 OR (user_id IS NULL AND admission_year = 該生屆數)) AND semester_id = 當前學期，
    ORDER BY user_id DESC 取一筆（個人設定優先）。
    供「查看公司／投遞履歷」「填寫志願序」等頁面限制使用。
    """
    if not user_id:
        return False
    current_semester_id = get_current_semester_id(cursor)
    if not current_semester_id:
        return False
    cursor.execute(
        "SELECT role, admission_year, username FROM users WHERE id = %s",
        (user_id,)
    )
    row = cursor.fetchone()
    if not row or row.get("role") != "student":
        return False
    admission_year_val = None
    if row.get("admission_year") is not None and str(row.get("admission_year", "")).strip() != "":
        try:
            admission_year_val = int(row["admission_year"])
        except (TypeError, ValueError):
            pass
    if admission_year_val is None and row.get("username") and len(row.get("username", "")) >= 3:
        try:
            admission_year_val = int(row["username"][:3])
        except (TypeError, ValueError):
            pass
    # 若仍無（例如 username 為 "s110123" 非數字開頭），從 username 中擷取 110/111/112 等屆別
    if admission_year_val is None and row.get("username"):
        m = re.search(r"(110|111|112|113|114)", str(row["username"]))
        if m:
            try:
                admission_year_val = int(m.group(1))
            except (TypeError, ValueError):
                pass
    # 有 user_id 的個人設定優先；若無則用 admission_year 匹配公版（admission_year_val 為 None 時僅匹配 user_id）
    if admission_year_val is not None:
        cursor.execute(
            """SELECT 1 FROM internship_configs
               WHERE semester_id = %s
                 AND (user_id = %s OR (user_id IS NULL AND admission_year = %s))
               ORDER BY user_id DESC
               LIMIT 1""",
            (current_semester_id, user_id, admission_year_val)
        )
    else:
        cursor.execute(
            """SELECT 1 FROM internship_configs
               WHERE semester_id = %s AND user_id = %s
               LIMIT 1""",
            (current_semester_id, user_id)
        )
    return cursor.fetchone() is not None

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
            SELECT id, code, start_date, end_date, is_active, created_at, auto_switch_at
            FROM semesters
            ORDER BY code DESC
        """)
        semesters = cursor.fetchall()
        
        # 格式化日期（DATE 欄位可能回傳 date 或 datetime）
        for s in semesters:
            for key in ('start_date', 'end_date'):
                v = s.get(key)
                if isinstance(v, (datetime, date)):
                    s[key] = v.strftime("%Y-%m-%d")
            if isinstance(s.get('created_at'), datetime):
                s['created_at'] = s['created_at'].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(s.get('auto_switch_at'), datetime):
                s['auto_switch_at'] = s['auto_switch_at'].strftime("%Y-%m-%d %H:%M:%S")
        
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
    auto_switch_at = data.get("auto_switch_at")
    
    if not code:
        return jsonify({"success": False, "message": "請提供學期代碼"}), 400
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 檢查學期代碼是否已存在
        cursor.execute("SELECT id FROM semesters WHERE code = %s", (code,))
        if cursor.fetchone():
            return jsonify({"success": False, "message": "該學期代碼已存在"}), 400
        
        # 插入新學期（包含 created_at, auto_switch_at）
        cursor.execute("""
            INSERT INTO semesters (code, start_date, end_date, is_active, created_at, auto_switch_at)
            VALUES (%s, %s, %s, 0, NOW(), %s)
        """, (code, start_date, end_date, auto_switch_at if auto_switch_at else None))
        
        conn.commit()
        return jsonify({"success": True, "message": "學期建立成功"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 切換當前學期 (內部與外部共用邏輯)
# =========================================================
def perform_semester_switch(semester_id):
    """執行學期切換的底層邏輯"""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 檢查學期是否存在
        cursor.execute("SELECT id, code FROM semesters WHERE id = %s", (semester_id,))
        semester = cursor.fetchone()
        if not semester:
            return False, "找不到該學期"
        
        # 關閉所有學期的 is_active
        cursor.execute("UPDATE semesters SET is_active = 0")
        
        # 啟用目標學期
        cursor.execute("UPDATE semesters SET is_active = 1 WHERE id = %s", (semester_id,))
        
        # 清除該學期的自動切換時間 (避免重複觸發)
        cursor.execute("UPDATE semesters SET auto_switch_at = NULL WHERE id = %s", (semester_id,))
        
        # 關閉上學期的公司開放狀態
        current_code = semester['code']
        try:
            # 嘗試更新 company_openings 表
            cursor.execute("""
                UPDATE company_openings 
                SET is_open = 0 
                WHERE semester != %s
            """, (current_code,))
        except Exception as e:
            print(f"⚠️ 更新 company_openings 表時發生錯誤: {e}")
            pass
        
        # 觸發實習流程範圍自動更新
        _auto_update_internship_ranges(cursor, current_code)
        
        conn.commit()
        return True, f"已切換至學期 {current_code}"
    except Exception as e:
        traceback.print_exc()
        return False, str(e)
    finally:
        cursor.close()
        conn.close()

@semester_bp.route("/api/switch", methods=["POST"])
def switch_semester():
    """切換當前學期（管理員/科助）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    semester_id = data.get("semester_id")
    
    if not semester_id:
        return jsonify({"success": False, "message": "請提供學期ID"}), 400
    
    success, message = perform_semester_switch(semester_id)
    if success:
        return jsonify({"success": True, "message": message})
    else:
        return jsonify({"success": False, "message": message}), 500

# =========================================================
# Helper: 根據入學年度自動更新實習流程範圍 (當學期切換時觸發)
# =========================================================
def _auto_update_internship_ranges(cursor, new_semester_code):
    """
    當學期切換時，根據入學年度自動調整 absence_default_semester_range
    邏輯範例：
    1. 針對所有已設定的入學年度
    2. 自動將「結束學期代碼」展延至新學期 (若新學期較晚)
    3. 或可根據年級 (新學期 - 入學年) 判斷是否開啟特定階段
    """
    try:
        print(f"🔄正在執行實習範圍自動更新，新學期: {new_semester_code}")
        
        # 1. 取得目前所有設定
        cursor.execute("SELECT id, admission_year, start_semester_code, end_semester_code FROM absence_default_semester_range")
        ranges = cursor.fetchall()
        
        for r in ranges:
            adm_year = r['admission_year']
            current_end = r['end_semester_code']
            
            # --- 範例邏輯：判斷年級 ---
            # 假設學期代碼格式為 1132 (113學年第2學期)
            try:
                current_year_part = int(str(new_semester_code)[:3])
                student_grade = current_year_part - adm_year + 1
                
                # 若您的規則是：「實習範圍始終包含最新學期」，則展延結束學期
                if str(new_semester_code) > str(current_end):
                    print(f"  - 更新 {adm_year} 屆 (約大{student_grade})：結束學期 {current_end} -> {new_semester_code}")
                    cursor.execute("""
                        UPDATE absence_default_semester_range 
                        SET end_semester_code = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (new_semester_code, r['id']))
                    
                # 若需要更複雜邏輯 (例如：大三才開始追蹤) 可在此擴充
                # if student_grade >= 3:
                #    ensure_range_covers(cursor, r['id'], new_semester_code)
                    
            except Exception as ex:
                print(f"  ⚠️ 處理 {adm_year} 屆時發生錯誤: {ex}")
                continue
                
    except Exception as e:
        print(f"❌ _auto_update_internship_ranges 執行失敗: {e}")
        # 不拋出錯誤，避免影響主切換流程



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
    auto_switch_at = data.get("auto_switch_at")
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 更新學期資訊
        update_fields = []
        params = []
        
        if start_date is not None:
            update_fields.append("start_date = %s")
            params.append(start_date)
            # 未明確傳入 auto_switch_at 時，依起始日期決定切換時間（該日 00:00:00）
            if "auto_switch_at" not in data and isinstance(start_date, str) and start_date.strip():
                switch_at = start_date.strip() + " 00:00:00"
                update_fields.append("auto_switch_at = %s")
                params.append(switch_at)
        if end_date is not None:
            update_fields.append("end_date = %s")
            params.append(end_date)
        
        # 若前端明確傳入 auto_switch_at 則依其值（允許清除）
        if "auto_switch_at" in data:
            val = data["auto_switch_at"]
            if not val:
                update_fields.append("auto_switch_at = NULL")
            else:
                update_fields.append("auto_switch_at = %s")
                params.append(val)
        
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
# Helper: 自動檢查並切換學期 (供排程器呼叫)
# =========================================================
def check_auto_switch():
    """檢查是否有到達自動切換時間的學期"""
    print(f"[{datetime.now()}] 執行學期自動切換檢查...")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 查詢 auto_switch_at <= NOW() 且 is_active = 0 的學期
        cursor.execute("""
            SELECT id, code, auto_switch_at 
            FROM semesters 
            WHERE is_active = 0 
              AND auto_switch_at IS NOT NULL 
              AND auto_switch_at <= NOW()
            ORDER BY auto_switch_at ASC
            LIMIT 1
        """)
        target = cursor.fetchone()
        
        if target:
            print(f"🔄 發現待切換學期: {target['code']} (預定: {target['auto_switch_at']})")
            success, msg = perform_semester_switch(target['id'])
            if success:
                print(f"✅ 自動切換成功: {msg}")
            else:
                print(f"❌ 自動切換失敗: {msg}")
        else:
            # print("無須切換")
            pass
            
    except Exception as e:
        print(f"❌ 自動切換檢查錯誤: {e}")
        traceback.print_exc()
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
# 實習配置 (internship_configs)：依 admission_year / user_id / semester_id 設定實習起迄
# 讀取優先順序：1) 該 user_id + 學期的個人設定；2) 無則用 user_id IS NULL 且 admission_year 相符之屆別公版。
# =========================================================

@semester_bp.route("/api/internship-configs", methods=["GET"])
def list_internship_configs():
    """取得實習配置列表（管理員/科助）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT ic.id, ic.admission_year, ic.user_id, ic.semester_id,
                   ic.intern_start_date, ic.intern_end_date,
                   s.code AS semester_code,
                   u.name AS user_name, u.username
            FROM internship_configs ic
            LEFT JOIN semesters s ON s.id = ic.semester_id
            LEFT JOIN users u ON u.id = ic.user_id
            ORDER BY ic.admission_year DESC, ic.user_id IS NULL DESC, ic.semester_id
        """)
        rows = cursor.fetchall()
        for r in rows:
            for key in ('intern_start_date', 'intern_end_date'):
                v = r.get(key)
                if isinstance(v, (datetime, date)):
                    r[key] = v.strftime("%Y-%m-%d")
        return jsonify({"success": True, "configs": rows})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@semester_bp.route("/api/internship-configs/options", methods=["GET"])
def internship_config_options():
    """取得下拉選單：學期列表、入學年度（來自 internship_configs + 學生屆別）、具 admission_year 的學生（管理員/科助）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        # 實習學期：以 internship_configs 出現過的 semester_id 對應的學期為主，若無則回傳全部學期
        cursor.execute("""
            SELECT DISTINCT s.id, s.code, s.start_date, s.end_date
            FROM semesters s
            INNER JOIN internship_configs ic ON ic.semester_id = s.id
            ORDER BY s.code DESC
        """)
        semesters_from_config = cursor.fetchall()
        if not semesters_from_config:
            cursor.execute("""
                SELECT id, code, start_date, end_date
                FROM semesters
                ORDER BY code DESC
            """)
            semesters = cursor.fetchall()
        else:
            semesters = semesters_from_config
        for s in semesters:
            for key in ('start_date', 'end_date'):
                v = s.get(key)
                if isinstance(v, (datetime, date)):
                    s[key] = v.strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT id, admission_year, name, username
            FROM users
            WHERE role = 'student' AND admission_year IS NOT NULL
            ORDER BY admission_year DESC, id
        """)
        students = cursor.fetchall()
        # 入學年度：來自 internship_configs 的 DISTINCT admission_year，再合併學生屆別
        cursor.execute("""
            SELECT DISTINCT admission_year
            FROM internship_configs
            ORDER BY admission_year DESC
        """)
        years_from_config = [r["admission_year"] for r in cursor.fetchall() if r.get("admission_year") is not None]
        cursor.execute("""
            SELECT DISTINCT admission_year
            FROM users
            WHERE role = 'student' AND admission_year IS NOT NULL
            ORDER BY admission_year DESC
        """)
        years_from_users = [r["admission_year"] for r in cursor.fetchall() if r.get("admission_year") is not None]
        seen = set()
        admission_years = []
        for y in years_from_config + years_from_users:
            if y is None:
                continue
            if y not in seen:
                seen.add(y)
                admission_years.append(y)
        admission_years.sort(reverse=True)
        return jsonify({
            "success": True,
            "semesters": semesters,
            "students": students,
            "admission_years": admission_years
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@semester_bp.route("/api/internship-configs", methods=["POST"])
def create_internship_config():
    """新增實習配置（管理員/科助）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    data = request.get_json() or {}
    admission_year = data.get("admission_year")
    user_id = data.get("user_id")  # 可為 null 表示該屆公版
    semester_id = data.get("semester_id")
    intern_start_date = data.get("intern_start_date")
    intern_end_date = data.get("intern_end_date")
    if admission_year is None or not semester_id or not intern_start_date or not intern_end_date:
        return jsonify({"success": False, "message": "請填寫入學年度、學期、實習開始日與結束日"}), 400
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            INSERT INTO internship_configs (admission_year, user_id, semester_id, intern_start_date, intern_end_date)
            VALUES (%s, %s, %s, %s, %s)
        """, (int(admission_year), user_id or None, int(semester_id), intern_start_date, intern_end_date))
        conn.commit()
        return jsonify({"success": True, "message": "已新增", "id": cursor.lastrowid})
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@semester_bp.route("/api/internship-configs/global-default", methods=["GET"])
def get_global_internship_default():
    """取得某屆+某學期的屆別預設實習日期（user_id IS NULL 的那筆），供全部套用彈窗帶入預設。"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    admission_year = request.args.get("admission_year", type=int)
    semester_id = request.args.get("semester_id", type=int)
    if admission_year is None or semester_id is None:
        return jsonify({"success": False, "message": "請提供 admission_year 與 semester_id"}), 400
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT intern_start_date, intern_end_date
            FROM internship_configs
            WHERE admission_year = %s AND user_id IS NULL AND semester_id = %s
            LIMIT 1
        """, (admission_year, semester_id))
        row = cursor.fetchone()
        if not row:
            return jsonify({"success": True, "found": False})
        for key in ('intern_start_date', 'intern_end_date'):
            v = row.get(key)
            if isinstance(v, (datetime, date)):
                row[key] = v.strftime("%Y-%m-%d")
        return jsonify({"success": True, "found": True, "intern_start_date": row["intern_start_date"], "intern_end_date": row["intern_end_date"]})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@semester_bp.route("/api/internship-configs/<int:config_id>", methods=["PUT"])
def update_internship_config(config_id):
    """更新實習配置（管理員/科助）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    data = request.get_json() or {}
    admission_year = data.get("admission_year")
    user_id = data.get("user_id")
    semester_id = data.get("semester_id")
    intern_start_date = data.get("intern_start_date")
    intern_end_date = data.get("intern_end_date")
    if admission_year is None or not semester_id or not intern_start_date or not intern_end_date:
        return jsonify({"success": False, "message": "請填寫入學年度、學期、實習開始日與結束日"}), 400
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            UPDATE internship_configs
            SET admission_year = %s, user_id = %s, semester_id = %s, intern_start_date = %s, intern_end_date = %s
            WHERE id = %s
        """, (int(admission_year), user_id or None, int(semester_id), intern_start_date, intern_end_date, config_id))
        conn.commit()
        if cursor.rowcount == 0:
            return jsonify({"success": False, "message": "找不到該筆配置"}), 404
        return jsonify({"success": True, "message": "已更新"})
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@semester_bp.route("/api/internship-configs/global", methods=["POST"])
def save_global_internship_config():
    """儲存屆別預設值（user_id = NULL），供全屆學生共用。若該屆+學期已存在預設則更新。"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    data = request.get_json() or {}
    admission_year = data.get("admission_year")
    semester_id = data.get("semester_id")
    intern_start_date = data.get("intern_start_date")
    intern_end_date = data.get("intern_end_date")
    if admission_year is None or not semester_id or not intern_start_date or not intern_end_date:
        return jsonify({"success": False, "message": "請填寫入學年度、學期、實習開始日與結束日"}), 400
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id FROM internship_configs
            WHERE admission_year = %s AND user_id IS NULL AND semester_id = %s
            LIMIT 1
        """, (int(admission_year), int(semester_id)))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("""
                UPDATE internship_configs
                SET intern_start_date = %s, intern_end_date = %s
                WHERE id = %s
            """, (intern_start_date, intern_end_date, existing["id"]))
            conn.commit()
            return jsonify({"success": True, "message": "已更新該屆預設值"})
        cursor.execute("""
            INSERT INTO internship_configs (admission_year, user_id, semester_id, intern_start_date, intern_end_date)
            VALUES (%s, NULL, %s, %s, %s)
        """, (int(admission_year), int(semester_id), intern_start_date, intern_end_date))
        conn.commit()
        return jsonify({"success": True, "message": "已新增該屆預設值", "id": cursor.lastrowid})
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@semester_bp.route("/api/internship-configs/batch", methods=["POST"])
def batch_internship_config():
    """為多個學生建立個人實習配置（同一學期、同一日期）。僅針對勾選的 user_id 寫入。"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    data = request.get_json() or {}
    user_ids = data.get("user_ids")
    semester_id = data.get("semester_id")
    intern_start_date = data.get("intern_start_date")
    intern_end_date = data.get("intern_end_date")
    if not user_ids or not isinstance(user_ids, list) or not semester_id or not intern_start_date or not intern_end_date:
        return jsonify({"success": False, "message": "請提供 user_ids 陣列、學期、實習開始日與結束日"}), 400
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        created = 0
        updated = 0
        for uid in user_ids:
            uid = int(uid)
            cursor.execute("SELECT admission_year, username FROM users WHERE id = %s AND role = 'student'", (uid,))
            u = cursor.fetchone()
            if not u:
                continue
            ay = u.get("admission_year")
            if ay is None or str(ay).strip() == "":
                try:
                    ay = int(u.get("username", "000")[:3])
                except (TypeError, ValueError):
                    ay = None
            if ay is None:
                continue
            try:
                admission_year = int(ay)
            except (TypeError, ValueError):
                continue
            cursor.execute(
                "SELECT id FROM internship_configs WHERE user_id = %s AND semester_id = %s LIMIT 1",
                (uid, int(semester_id))
            )
            ex = cursor.fetchone()
            if ex:
                cursor.execute("""
                    UPDATE internship_configs
                    SET intern_start_date = %s, intern_end_date = %s
                    WHERE id = %s
                """, (intern_start_date, intern_end_date, ex["id"]))
                updated += 1
            else:
                cursor.execute("""
                    INSERT INTO internship_configs (admission_year, user_id, semester_id, intern_start_date, intern_end_date)
                    VALUES (%s, %s, %s, %s, %s)
                """, (admission_year, uid, int(semester_id), intern_start_date, intern_end_date))
                created += 1
        conn.commit()
        return jsonify({"success": True, "message": f"已處理：新增 {created} 筆、更新 {updated} 筆"})
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@semester_bp.route("/api/internship-configs/<int:config_id>", methods=["DELETE"])
def delete_internship_config(config_id):
    """刪除實習配置（管理員/科助）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("DELETE FROM internship_configs WHERE id = %s", (config_id,))
        conn.commit()
        if cursor.rowcount == 0:
            return jsonify({"success": False, "message": "找不到該筆配置"}), 404
        return jsonify({"success": True, "message": "已刪除"})
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# Helper: 初始化資料庫欄位（添加 auto_switch_at 欄位）
# =========================================================
def ensure_auto_switch_column():
    """
    確保 semesters 表有 auto_switch_at 欄位
    如果欄位不存在，則自動添加
    返回: (success: bool, message: str)
    """
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 檢查欄位是否存在
        cursor.execute("SHOW COLUMNS FROM semesters LIKE 'auto_switch_at'")
        result = cursor.fetchone()
        
        if result:
            return True, "欄位 'auto_switch_at' 已存在"
        else:
            # 添加欄位
            cursor.execute("ALTER TABLE semesters ADD COLUMN auto_switch_at DATETIME NULL DEFAULT NULL")
            conn.commit()
            return True, "已成功添加 'auto_switch_at' 欄位"
            
    except Exception as e:
        traceback.print_exc()
        if 'conn' in locals():
            conn.rollback()
        return False, f"添加欄位失敗: {str(e)}"
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()

# =========================================================
# API: 初始化資料庫欄位（管理員/科助）
# =========================================================
@semester_bp.route("/api/ensure_column", methods=["POST"])
def ensure_column_api():
    """確保 semesters 表有 auto_switch_at 欄位（管理員/科助）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    success, message = ensure_auto_switch_column()
    if success:
        return jsonify({"success": True, "message": message})
    else:
        return jsonify({"success": False, "message": message}), 500

# =========================================================
# Helper: 驗證自動切換功能（測試用）
# =========================================================
def verify_auto_switch_logic(test_code="TEST_999", wait_seconds=3):
    """
    驗證自動切換功能的邏輯
    創建一個測試學期，設定自動切換時間，然後檢查是否會自動切換
    
    參數:
        test_code: 測試學期代碼
        wait_seconds: 等待秒數（用於測試）
    
    返回: (success: bool, message: str, details: dict)
    """
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 計算切換時間
        switch_time = (datetime.now() + timedelta(seconds=wait_seconds)).strftime('%Y-%m-%d %H:%M:%S')
        
        # 清理之前的測試資料
        cursor.execute("DELETE FROM semesters WHERE code = %s", (test_code,))
        conn.commit()
        
        # 創建測試學期
        cursor.execute("""
            INSERT INTO semesters (code, is_active, auto_switch_at, created_at)
            VALUES (%s, 0, %s, NOW())
        """, (test_code, switch_time))
        conn.commit()
        
        semester_id = cursor.lastrowid
        
        # 等待指定時間
        print(f"等待 {wait_seconds} 秒以觸發自動切換...")
        time.sleep(wait_seconds)
        
        # 執行自動切換檢查
        check_auto_switch()
        
        # 驗證結果
        cursor.execute("SELECT is_active, auto_switch_at FROM semesters WHERE id = %s", (semester_id,))
        row = cursor.fetchone()
        
        details = {
            "semester_id": semester_id,
            "test_code": test_code,
            "switch_time": switch_time,
            "is_active_after": row['is_active'],
            "auto_switch_at_cleared": row['auto_switch_at'] is None
        }
        
        if row['is_active'] == 1 and row['auto_switch_at'] is None:
            # 清理測試資料
            cursor.execute("DELETE FROM semesters WHERE id = %s", (semester_id,))
            conn.commit()
            return True, "自動切換功能驗證成功", details
        else:
            # 清理測試資料
            cursor.execute("DELETE FROM semesters WHERE id = %s", (semester_id,))
            conn.commit()
            return False, f"自動切換功能驗證失敗: is_active={row['is_active']}, auto_switch_at={row['auto_switch_at']}", details
            
    except Exception as e:
        traceback.print_exc()
        # 嘗試清理測試資料
        try:
            cursor.execute("DELETE FROM semesters WHERE code = %s", (test_code,))
            conn.commit()
        except:
            pass
        return False, f"驗證過程發生錯誤: {str(e)}", {}
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 驗證自動切換功能（管理員/科助，測試用）
# =========================================================
@semester_bp.route("/api/verify_auto_switch", methods=["POST"])
def verify_auto_switch_api():
    """驗證自動切換功能（管理員/科助，測試用）"""
    if session.get('role') not in ['admin', 'ta']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    data = request.get_json() or {}
    test_code = data.get("test_code", "TEST_999")
    wait_seconds = data.get("wait_seconds", 3)
    
    # 限制等待時間，避免過長
    if wait_seconds > 10:
        wait_seconds = 10
    
    success, message, details = verify_auto_switch_logic(test_code, wait_seconds)
    
    if success:
        return jsonify({
            "success": True,
            "message": message,
            "details": details
        })
    else:
        return jsonify({
            "success": False,
            "message": message,
            "details": details
        }), 500

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

