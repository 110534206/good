from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
from config import get_db
from datetime import datetime
from collections import defaultdict

preferences_bp = Blueprint("preferences_bp", __name__)

# -------------------------
# API - 志願填寫
# -------------------------
@preferences_bp.route('/fill_preferences', methods=['GET', 'POST'])
def fill_preferences():
    # 1. 登入檢查
    if 'user_id' not in session or session.get('role') != 'student':
        return redirect(url_for('auth_bp.login_page'))

    student_id = session['user_id']

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    message = None

    if request.method == 'POST':
        preferences = []
        for i in range(1, 6):
            company_id = request.form.get(f'preference_{i}')
            if company_id:
                preferences.append((student_id, i, company_id, datetime.now()))

        try:
            # 刪除舊志願
            cursor.execute("DELETE FROM student_preferences WHERE student_id = %s", (student_id,))
            conn.commit()

            # 新增志願
            if preferences:
                cursor.executemany("""
                    INSERT INTO student_preferences (student_id, preference_order, company_id, submitted_at)
                    VALUES (%s, %s, %s, %s)
                """, preferences)
                conn.commit()
                message = "✅ 志願序已成功送出"
            else:
                message = "⚠️ 未選擇任何志願，公司清單已重置"
        except Exception as e:
            print("寫入志願錯誤：", e)
            message = "❌ 發生錯誤，請稍後再試"

    # 不管是 GET 還是 POST，都要載入公司列表及該學生已填的志願
    cursor.execute("SELECT id, company_name FROM internship_companies WHERE status = 'approved'")
    companies = cursor.fetchall()

    cursor.execute("""
        SELECT preference_order, company_id 
        FROM student_preferences 
        WHERE student_id = %s 
        ORDER BY preference_order
    """, (student_id,))
    prefs = cursor.fetchall()

    cursor.close()
    conn.close()

    # 把 prefs 轉成 list，index 對應志願順序 -1
    submitted_preferences = [None] * 5
    for pref in prefs:
        order = pref['preference_order']
        company_id = pref['company_id']
        if 1 <= order <= 5:
            submitted_preferences[order - 1] = company_id

    return render_template(
        'fill_preferences.html',
        companies=companies,
        submitted_preferences=submitted_preferences,
        message=message
    )

# -------------------------
# API - 選擇角色
# -------------------------
@preferences_bp.route('/api/select_role', methods=['POST'])
def select_role():
    data = request.json
    username = data.get("username")
    role = data.get("role")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM users WHERE username=%s AND role=%s", (username, role))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if user:
        session["user_id"] = user["id"]
        session["role"] = role
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "無此角色"}), 404


# -------------------------
# 班導查看志願序
# -------------------------
@preferences_bp.route('/review_preferences')
def review_preferences():
    if 'username' not in session or session.get('role') not in ['teacher', 'director']:
        return redirect(url_for('auth_bp.login_page'))

    user_id = session.get('user_id')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 確認是否為班導
        cursor.execute("""
            SELECT c.id AS class_id
            FROM classes c
            JOIN classes_teacher ct ON c.id = ct.class_id
            WHERE ct.teacher_id = %s AND ct.role = '班導師'
        """, (user_id,))
        class_info = cursor.fetchone()
        if not class_info:
            return "你不是班導，無法查看志願序", 403

        class_id = class_info['class_id']

        # 查詢班上學生及其志願
        cursor.execute("""
            SELECT 
                u.id AS student_id,
                u.name AS student_name,
                sp.preference_order,
                ic.company_name,
                sp.submitted_at
            FROM users u
            LEFT JOIN student_preferences sp ON u.id = sp.student_id
            LEFT JOIN internship_companies ic ON sp.company_id = ic.id
            WHERE u.class_id = %s
            ORDER BY u.name, sp.preference_order
        """, (class_id,))
        results = cursor.fetchall()

        # 整理資料結構給前端使用
        student_data = defaultdict(list)
        for row in results:
            if row['preference_order'] and row['company_name']:
                student_data[row['student_name']].append({
                    'order': row['preference_order'],
                    'company': row['company_name'],
                    'submitted_at': row['submitted_at']
                })

        return render_template('review_preferences.html', student_data=student_data)

    except Exception as e:
        print("取得志願資料錯誤：", e)
        return "伺服器錯誤", 500
    finally:
        cursor.close()
        conn.close()

