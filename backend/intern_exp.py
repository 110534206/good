from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for
from config import get_db
import traceback
from datetime import datetime

intern_exp_bp = Blueprint('intern_exp_bp', __name__, url_prefix='/intern_experience')

# --------------------
# Helpers
# --------------------
def require_login():
    return 'user_id' in session

def to_minguo(year):
    """輸入可為西元年或民國年，若是西元(>1911)則轉民國；若已是民國(<2000)則直接回傳。"""
    try:
        y = int(year)
        if y > 1911:
            return y - 1911
        return y
    except Exception:
        return None

def to_gregorian_if_needed(year):
    """若傳入為民國（例如 110），回傳西元；若是西元則回傳原本值。方便內部需要西元時使用（此專案多數不需要）。"""
    try:
        y = int(year)
        if y < 2000:
            return y + 1911
        return y
    except Exception:
        return None

# --------------------
# 頁面：整合列表 + 新增（前端 HTML）
# --------------------
@intern_exp_bp.route('/')
def page_intern_exp():
    if not require_login():
        return redirect(url_for('auth_bp.login'))

    # 新增邏輯：從 URL 取得 company_id 參數
    company_id = request.args.get('company_id')

    # 將 company_id 與目前登入者資訊傳遞給前端範本
    return render_template(
        'user_shared/intern_experience.html',
        initial_company_id=company_id,
        user_id=session.get('user_id')
    )

# --------------------
# API：公司清單（下拉用） - 從資料表抓取所有公司資料
# --------------------
@intern_exp_bp.route('/api/companies', methods=['GET'])
def get_companies():
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        # 從 internship_companies 資料表抓取所有公司資料（不限狀態）
        # 移除狀態限制，顯示資料表中的所有公司
        cursor.execute("""
            SELECT id, company_name, status 
            FROM internship_companies 
            ORDER BY company_name ASC
        """)
        companies = cursor.fetchall()
        
        # 確保回傳資料格式正確
        if not companies:
            companies = []
        
        return jsonify({
            "success": True, 
            "data": companies,
            "count": len(companies)
        })
    except Exception as e:
        print(f"抓取公司資料時發生錯誤: {traceback.format_exc()}")
        return jsonify({
            "success": False, 
            "message": f"無法載入公司資料: {str(e)}"
        }), 500

# --------------------
# API：取得某公司職缺
# --------------------
@intern_exp_bp.route('/api/jobs/<int:company_id>', methods=['GET'])
def get_jobs_by_company(company_id):
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, title FROM internship_jobs WHERE company_id = %s ORDER BY title", (company_id,))
        jobs = cursor.fetchall()
        return jsonify({"success": True, "data": jobs})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500

# --------------------
# API：心得列表（搜尋、年份篩選），只列公開(is_public = 1)
# --------------------
@intern_exp_bp.route('/api/list', methods=['GET'])
def get_experience_list():
    try:
        keyword = request.args.get('keyword', '').strip()
        year = request.args.get('year', '').strip()  # 前端會傳民國年
        company_id = request.args.get('company_id', '').strip()
        db = get_db()
        cursor = db.cursor(dictionary=True)

        query = """
            SELECT ie.id, ie.year, ie.content, ie.rating, ie.created_at,
                   u.id AS author_id, u.name AS author, 
                   c.id AS company_id, c.company_name,
                   j.id AS job_id, j.title AS job_title
            FROM internship_experiences ie
            JOIN users u ON ie.user_id = u.id
            LEFT JOIN internship_companies c ON ie.company_id = c.id
            LEFT JOIN internship_jobs j ON ie.job_id = j.id
            WHERE ie.is_public = 1
        """
        params = []

        if keyword:
            query += " AND c.company_name LIKE %s"
            params.append(f"%{keyword}%")

        if year:
            # year 前端傳民國年（如110），資料庫也儲存為民國年
            query += " AND ie.year = %s"
            params.append(year)

        if company_id:
            query += " AND ie.company_id = %s"
            params.append(company_id)

        query += " ORDER BY ie.created_at DESC"

        cursor.execute(query, params)
        experiences = cursor.fetchall()

        # 確保 year 為 int（並以民國年輸出）
        for e in experiences:
            try:
                e['year'] = int(e['year']) if e.get('year') is not None else None
            except:
                e['year'] = None

        return jsonify({"success": True, "data": experiences})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500

# --------------------
# API：查看單篇心得
# --------------------
@intern_exp_bp.route('/api/view/<int:exp_id>', methods=['GET'])
def view_experience(exp_id):
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT ie.id, ie.year, ie.content, ie.rating, ie.created_at, ie.user_id,
                   u.name AS author,
                   c.id AS company_id, c.company_name,
                   j.id AS job_id, j.title AS job_title
            FROM internship_experiences ie
            JOIN users u ON ie.user_id = u.id
            LEFT JOIN internship_companies c ON ie.company_id = c.id
            LEFT JOIN internship_jobs j ON ie.job_id = j.id
            WHERE ie.id = %s
        """, (exp_id,))
        exp = cursor.fetchone()
        if exp:
            try:
                exp['year'] = int(exp['year']) if exp.get('year') is not None else None
            except:
                exp['year'] = None
        return jsonify({"success": True, "data": exp})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500

# --------------------
# API：新增心得（接收 company_id, job_id, year, rating, content）
#       - year 可為西元或民國，會儲存為民國年
# --------------------
@intern_exp_bp.route('/api/add', methods=['POST'])
def add_experience():
    try:
        if not require_login():
            return jsonify({"success": False, "message": "請先登入"}), 403

        user_id = session['user_id']
        data = request.get_json() or {}
        company_id = data.get('company_id') or None
        job_id = data.get('job_id') or None
        year_raw = data.get('year')
        content = data.get('content') or ''
        rating = data.get('rating') or None

        # 轉換 year（若用者傳西元或民國皆接受，統一儲存為民國年）
        year = None
        if year_raw is not None and year_raw != '':
            try:
                year = to_minguo(int(year_raw))
            except:
                year = None

        db = get_db()
        cursor = db.cursor()

        cursor.execute("""
            INSERT INTO internship_experiences
                (user_id, company_id, job_id, year, content, rating, is_public, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, 1, NOW())
        """, (user_id, company_id, job_id, year, content, rating))

        db.commit()
        return jsonify({"success": True, "message": "心得已新增"})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500

# --------------------
# API：刪除心得（只能刪自己的）
# --------------------
@intern_exp_bp.route('/api/delete/<int:exp_id>', methods=['DELETE'])
def delete_experience(exp_id):
    try:
        if not require_login():
            return jsonify({"success": False, "message": "請先登入"}), 403

        user_id = session['user_id']
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT user_id FROM internship_experiences WHERE id = %s", (exp_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"success": False, "message": "心得不存在"}), 404

        # row may be tuple (cursor default) or dict; handle both
        owner_id = row[0] if not isinstance(row, dict) else row.get('user_id')
        if owner_id != user_id:
            return jsonify({"success": False, "message": "不能刪除他人的心得"}), 403

        cursor.execute("DELETE FROM internship_experiences WHERE id = %s", (exp_id,))
        db.commit()
        return jsonify({"success": True, "message": "已刪除"})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500
