from flask import Blueprint, request, jsonify, render_template, session
from config import get_db
from datetime import datetime

company_bp = Blueprint("company_bp", __name__)

# -------------------------
# API - 上傳公司
# -------------------------
@company_bp.route('/upload_company', methods=['GET', 'POST'])
def upload_company_form():
    if request.method == 'POST':
        try:
            data = request.form
            company_name = data.get("company_name")
            company_description = data.get("description")
            company_location = data.get("location")
            contact_person = data.get("contact_person")
            contact_email = data.get("contact_email")
            contact_phone = data.get("contact_phone")

            # 公司名稱必填
            if not company_name:
                return render_template('upload_company.html', error="公司名稱為必填")

            # 從 session 拿上傳者 id
            uploaded_by_user_id = session.get("user_id")
            if not uploaded_by_user_id:
                return render_template('upload_company.html', error="請先登入")

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO internship_companies
                (company_name, description, location, contact_person, contact_email, contact_phone, 
                 uploaded_by_user_id, status, submitted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', NOW())
            """, (
                company_name,
                company_description,
                company_location,
                contact_person,
                contact_email,
                contact_phone,
                uploaded_by_user_id
            ))
            conn.commit()
            cursor.close()
            conn.close()

            # 上傳成功訊息，告知狀態是待審核
            success_msg = "公司資訊已成功上傳，狀態：待審核"
            return render_template('upload_company.html', success=success_msg)

        except Exception as e:
            print("❌ 錯誤：", e)
            return render_template('upload_company.html', error="伺服器錯誤，請稍後再試")
    else:
            return render_template('company/upload_company.html')

# -------------------------
# API - 審核公司
# -------------------------
@company_bp.route("/api/approve_company", methods=["POST"])
def api_approve_company():
    data = request.get_json()
    company_id = data.get("company_id")
    status = data.get("status")

    if not company_id or status not in ['approved', 'rejected']:
        return jsonify({"success": False, "message": "參數錯誤"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()

        reviewed_at = datetime.now()

        # 取得公司資訊
        cursor.execute("SELECT company_name, status FROM internship_companies WHERE id = %s", (company_id,))
        company_row = cursor.fetchone()

        if not company_row:
            return jsonify({"success": False, "message": "查無此公司"}), 404

        company_name, current_status = company_row

        # 防止重複審核
        if current_status != 'pending':
            return jsonify({"success": False, "message": f"公司已被審核過（目前狀態為 {current_status}）"}), 400

        # 更新公司狀態與審核時間
        cursor.execute("""
            UPDATE internship_companies
            SET status = %s, reviewed_at = %s
            WHERE id = %s
        """, (status, reviewed_at, company_id))

        conn.commit()

        action_text = '核准' if status == 'approved' else '拒絕'
        return jsonify({"success": True, "message": f"公司已{action_text}"}), 200

    except Exception as e:
        print("審核公司錯誤：", e)
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()


# -------------------------
# 頁面 - 公司審核清單
# -------------------------
@company_bp.route('/approve_company')
def approve_company():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM internship_companies WHERE status = 'pending'")
    companies = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('company/approve_company.html', companies=companies)
