from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime
import traceback

company_bp = Blueprint("company_bp", __name__)

# -------------------------
# 頁面 - 上傳公司（單筆手動表單）
# -------------------------
@company_bp.route('/upload_company', methods=['GET', 'POST'])
def upload_company_form():
    if request.method == 'POST':
        try:
            company_name = request.form.get("company_name", "").strip()
            description = request.form.get("description", "").strip()
            location = request.form.get("location", "").strip()
            contact_person = request.form.get("contact_person", "").strip()
            contact_email = request.form.get("contact_email", "").strip()
            contact_phone = request.form.get("contact_phone", "").strip()

            # 基本檢查
            if not company_name:
                return render_template('company/upload_company.html', error="公司名稱為必填")

            uploaded_by_user_id = session.get("user_id")
            uploaded_by_role = session.get("role")
            if not uploaded_by_user_id or not uploaded_by_role:
                return render_template('company/upload_company.html', error="請先登入")

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO internship_companies
                (company_name, description, location, contact_person, contact_email, contact_phone,
                 uploaded_by_user_id, uploaded_by_role, status, submitted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', NOW())
            """, (
                company_name, description, location,
                contact_person, contact_email, contact_phone,
                uploaded_by_user_id, uploaded_by_role
            ))
            conn.commit()

            success_msg = f"✅ 公司「{company_name}」已成功上傳，狀態：待審核"
            return render_template('company/upload_company.html', success=success_msg)

        except Exception as e:
            print("❌ 上傳公司錯誤：", traceback.format_exc())
            return render_template('company/upload_company.html', error="伺服器錯誤，請稍後再試")

        finally:
            cursor.close()
            conn.close()

    # GET 載入空表單
    return render_template('company/upload_company.html')


# -------------------------
# API - 批次上傳公司（Excel/CSV → JSON）
# -------------------------
@company_bp.route("/api/upload_batch", methods=["POST"])
def api_upload_company():
    try:
        data = request.get_json()
        companies = data.get("companies", [])

        if not companies or not isinstance(companies, list):
            return jsonify({"success": False, "message": "缺少公司資料"}), 400

        uploaded_by_user_id = session.get("user_id")
        uploaded_by_role = session.get("role")
        if not uploaded_by_user_id or not uploaded_by_role:
            return jsonify({"success": False, "message": "請先登入"}), 401

        conn = get_db()
        cursor = conn.cursor()

        insert_sql = """
            INSERT INTO internship_companies
            (company_name, description, location, contact_person, contact_email, contact_phone,
             uploaded_by_user_id, uploaded_by_role, status, submitted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', NOW())
        """

        inserted_count = 0
        for c in companies:
            company_name = c.get("公司名稱") or c.get("company_name")
            if not company_name:
                continue  # 跳過缺少公司名稱的列

            cursor.execute(insert_sql, (
                company_name,
                c.get("公司描述") or c.get("description"),
                c.get("公司地點") or c.get("location"),
                c.get("聯絡人") or c.get("contact_person"),
                c.get("聯絡電子郵件") or c.get("contact_email"),
                c.get("聯絡電話") or c.get("contact_phone"),
                uploaded_by_user_id,
                uploaded_by_role
            ))
            inserted_count += 1

        conn.commit()
        return jsonify({"success": True, "message": f"成功上傳 {inserted_count} 筆公司資料"})

    except Exception as e:
        print("❌ 批次上傳錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()


# -------------------------
# API - 審核公司
# -------------------------
@company_bp.route("/api/approve", methods=["POST"])
def api_approve_company():
    data = request.get_json()
    company_id = data.get("company_id")
    status = data.get("status")

    if not company_id or status not in ['approved', 'rejected']:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT company_name, status FROM internship_companies WHERE id = %s", (company_id,))
        company_row = cursor.fetchone()

        if not company_row:
            return jsonify({"success": False, "message": "查無此公司"}), 404

        company_name, current_status = company_row
        if current_status != 'pending':
            return jsonify({"success": False, "message": f"公司已被審核過（目前狀態為 {current_status}）"}), 400

        cursor.execute("""
            UPDATE internship_companies
            SET status = %s, reviewed_at = %s
            WHERE id = %s
        """, (status, datetime.now(), company_id))
        conn.commit()

        action_text = '核准' if status == 'approved' else '拒絕'
        return jsonify({"success": True, "message": f"公司「{company_name}」已{action_text}"})

    except Exception as e:
        print("❌ 審核公司錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()


# -------------------------
# 頁面 - 公司審核清單
# -------------------------
@company_bp.route('/approve_list')
def approve_company_list():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM internship_companies WHERE status = 'pending'")
        companies = cursor.fetchall()
        return render_template('company/approve_company.html', companies=companies)

    except Exception as e:
        print("❌ 讀取公司清單錯誤：", traceback.format_exc())
        return render_template('company/approve_company.html', error="伺服器錯誤")

    finally:
        cursor.close()
        conn.close()

   # -------------------------
# API - 取得自己上傳的公司清單
# -------------------------
@company_bp.route("/api/get_my_companies", methods=["GET"])
def api_get_my_companies():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, company_name AS original_filename, status, submitted_at AS upload_time
        FROM internship_companies
        WHERE uploaded_by_user_id = %s
        ORDER BY submitted_at DESC
    """, (session["user_id"],))
    companies = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify({"success": True, "companies": companies})


# -------------------------
# API - 上傳公司 Excel 檔案
# -------------------------
@company_bp.route("/api/upload_company_file", methods=["POST"])
def api_upload_company_file():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    file = request.files.get("company_file")
    if not file:
        return jsonify({"success": False, "message": "沒有檔案"}), 400

    # TODO: 檔案解析 (用 pandas/openpyxl)
    # 暫時假設直接存入 DB 一筆紀錄，狀態 pending
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO internship_companies (company_name, uploaded_by_user_id, uploaded_by_role, status, submitted_at)
        VALUES (%s, %s, %s, 'pending', NOW())
    """, (file.filename, session["user_id"], session.get("role")))
    conn.commit()
    new_id = cursor.lastrowid
    cursor.close()
    conn.close()

    return jsonify({"success": True, "company_id": new_id})


# -------------------------
# API - 查詢公司狀態
# -------------------------
@company_bp.route("/api/company_status", methods=["GET"])
def api_company_status():
    company_id = request.args.get("company_id")
    if not company_id:
        return jsonify({"success": False, "message": "缺少 company_id"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT status FROM internship_companies WHERE id=%s", (company_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        return jsonify({"success": False, "message": "查無此公司"}), 404

    return jsonify({"success": True, "status": row["status"]})


# -------------------------
# API - 刪除公司
# -------------------------
@company_bp.route("/api/delete_company", methods=["DELETE"])
def api_delete_company():
    company_id = request.args.get("company_id")
    if not company_id:
        return jsonify({"success": False, "message": "缺少 company_id"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM internship_companies WHERE id=%s AND uploaded_by_user_id=%s",
                   (company_id, session.get("user_id")))
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"success": True, "message": "刪除成功"})
     
