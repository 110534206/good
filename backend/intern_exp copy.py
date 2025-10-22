from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for
from config import get_db
import traceback
from datetime import datetime
import traceback

intern_exp_bp = Blueprint('intern_exp_bp', __name__, url_prefix='/intern_experience')


# ✅ Helper - 確認是否登入
def require_login():
    if 'user_id' not in session:
        return False
    return True


# ✅ 頁面：實習心得主畫面
@intern_exp_bp.route('/')
def page_intern_exp():
    if not require_login():
        return redirect(url_for('auth_bp.login'))
    return render_template('user_shared/intern_experience.html')


# ✅ API：取得心得列表（搜尋 + 年份篩選）
@intern_exp_bp.route('/api/list', methods=['GET'])
def get_experience_list():
    try:
        keyword = request.args.get('keyword', '')
        year = request.args.get('year', '')

        db = get_db()
        cursor = db.cursor(dictionary=True)

        query = """
            SELECT ie.id, ie.year, ie.content, ie.rating,
                   u.name AS author, c.name AS company_name, ie.created_at
            FROM internship_experiences ie
            JOIN users u ON ie.user_id = u.id
            LEFT JOIN companies c ON ie.company_id = c.id
            WHERE ie.is_public = 1
        """
        params = []

        if keyword:
            query += " AND c.name LIKE %s"
            params.append(f"%{keyword}%")

        if year:
            query += " AND ie.year = %s"
            params.append(year)

        query += " ORDER BY ie.created_at DESC"

        cursor.execute(query, params)
        experiences = cursor.fetchall()
        return jsonify({"success": True, "data": experiences})

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500


# ✅ API：查看單篇心得
@intern_exp_bp.route('/api/view/<int:exp_id>', methods=['GET'])
def view_experience(exp_id):
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT ie.*, u.name AS author, c.name AS company_name
            FROM internship_experiences ie
            JOIN users u ON ie.user_id = u.id
            LEFT JOIN companies c ON ie.company_id = c.id
            WHERE ie.id = %s
        """, (exp_id,))

        exp = cursor.fetchone()
        return jsonify({"success": True, "data": exp})

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500


# ✅ API：新增心得
@intern_exp_bp.route('/api/add', methods=['POST'])
def add_experience():
    try:
        if not require_login():
            return jsonify({"success": False, "message": "請先登入"}), 403

        user_id = session['user_id']
        data = request.json
        company_id = data.get('company_id')
        year = data.get('year')
        content = data.get('content')
        rating = data.get('rating')

        db = get_db()
        cursor = db.cursor()

        cursor.execute("""
            INSERT INTO internship_experiences (user_id, company_id, year, content, rating, is_public, created_at)
            VALUES (%s, %s, %s, %s, %s, 1, NOW())
        """, (user_id, company_id, year, content, rating))

        db.commit()
        return jsonify({"success": True, "message": "心得已新增"})

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500


# ✅ API：刪除心得（只能刪自己的）
@intern_exp_bp.route('/api/delete/<int:exp_id>', methods=['DELETE'])
def delete_experience(exp_id):
    try:
        if not require_login():
            return jsonify({"success": False, "message": "請先登入"}), 403

        user_id = session['user_id']
        db = get_db()
        cursor = db.cursor()

        cursor.execute("SELECT user_id FROM internship_experiences WHERE id = %s", (exp_id,))
        exp = cursor.fetchone()

        if not exp:
            return jsonify({"success": False, "message": "心得不存在"}), 404

        if exp[0] != user_id:
            return jsonify({"success": False, "message": "不能刪除他人的心得"}), 403

        cursor.execute("DELETE FROM internship_experiences WHERE id = %s", (exp_id,))
        db.commit()

        return jsonify({"success": True, "message": "已刪除"})

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500
