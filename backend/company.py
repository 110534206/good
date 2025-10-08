from flask import Blueprint, request, jsonify, render_template, session, send_file
from config import get_db
from datetime import datetime
import traceback
import pandas as pd
import io
from werkzeug.utils import secure_filename

company_bp = Blueprint("company_bp", __name__)

# =========================================================
# 頁面 - 上傳公司（單筆手動表單）
# =========================================================
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

        except Exception:
            print("❌ 上傳公司錯誤：", traceback.format_exc())
            return render_template('company/upload_company.html', error="伺服器錯誤，請稍後再試")

        finally:
            cursor.close()
            conn.close()

    return render_template('company/upload_company.html')

# =========================================================
# API - 批次上傳公司（含職缺）
# =========================================================
@company_bp.route("/api/upload_company_bulk", methods=["POST"])
def upload_company_bulk():
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
        inserted_company_count = 0
        inserted_job_count = 0

        for c in companies:
            company_name = c.get("company_name") or c.get("公司名稱") or ""
            if not company_name:
                continue  # 跳過無公司名稱的資料

            # ✅ 對應前端欄位名稱
            description = c.get("company_intro") or c.get("description") or c.get("公司簡介") or ""
            location = c.get("company_address") or c.get("location") or c.get("公司地址") or ""
            contact_person = c.get("contact_name") or c.get("contact_person") or c.get("聯絡人姓名") or ""
            contact_title = c.get("contact_title") or c.get("聯絡人職稱") or ""
            contact_email = c.get("contact_email") or c.get("聯絡信箱") or ""
            contact_phone = c.get("contact_phone") or c.get("聯絡電話") or ""

            # ✅ 插入公司資料
            cursor.execute("""
                INSERT INTO internship_companies
                (company_name, description, location, contact_person, contact_title, contact_email, contact_phone,
                 uploaded_by_user_id, uploaded_by_role, status, submitted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', NOW())
            """, (
                company_name,
                description,
                location,
                contact_person,
                contact_title,
                contact_email,
                contact_phone,
                uploaded_by_user_id,
                uploaded_by_role
            ))
            company_id = cursor.lastrowid
            inserted_company_count += 1

            # ✅ 插入職缺資料（從欄位或 fallback 單筆職缺）
            jobs = c.get("internship_jobs") or [{
                "title": c.get("internship_unit") or "",
                "description": c.get("internship_content") or "",
                "location": c.get("internship_location") or "",
                "period": c.get("internship_period") or "",
                "work_time": c.get("internship_time") or "",
                "slots": c.get("internship_quota") or "",
                "remark": c.get("remark") or ""
            }]

            for job in jobs:
                title = job.get("title") or ""
                if not title:
                    continue  # 沒有職缺名稱就跳過

                description = job.get("description") or ""
                department = job.get("department") or ""
                location = job.get("location") or ""
                period = job.get("period") or ""
                work_time = job.get("work_time") or ""
                slots = job.get("slots") or ""
                remark = job.get("remark") or ""

                cursor.execute("""
                    INSERT INTO internship_jobs
                    (company_id, title, description, department, location, period, work_time, slots, remark)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    company_id,
                    title,
                    description,
                    department,
                    location,
                    period,
                    work_time,
                    slots,
                    remark
                ))
                inserted_job_count += 1

        conn.commit()
        return jsonify({
            "success": True,
            "message": f"✅ 成功上傳 {inserted_company_count} 間公司、{inserted_job_count} 筆職缺資料"
        })

    except Exception:
        print("❌ 批次上傳錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass

# =========================================================
# API - 審核公司
# =========================================================
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

    except Exception:
        print("❌ 審核公司錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()

# =========================================================
# API - 取得待審核公司清單
# =========================================================
@company_bp.route("/api/get_pending_companies", methods=["GET"])
def api_get_pending_companies():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT 
                id,
                company_name,
                contact_person AS contact_name,
                contact_email,
                submitted_at AS upload_time,
                status
            FROM internship_companies
            WHERE status = 'pending'
            ORDER BY submitted_at DESC
        """)
        companies = cursor.fetchall()
        cursor.close()
        conn.close()

        return jsonify({
            "success": True,
            "companies": companies
        })

    except Exception:
        print("❌ 取得待審核公司清單錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

# =========================================================
# API - 取得已審核公司（歷史紀錄）
# =========================================================
@company_bp.route("/api/get_reviewed_companies", methods=["GET"])
def api_get_reviewed_companies():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT 
                id,
                company_name,
                status,
                submitted_at AS upload_time,
                reviewed_at
            FROM internship_companies
            WHERE status IN ('approved', 'rejected')
            ORDER BY reviewed_at DESC
        """)
        companies = cursor.fetchall()
        cursor.close()
        conn.close()

        return jsonify({"success": True, "companies": companies})
    except Exception:
        print("❌ 取得已審核公司錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
    
# =========================================================
# API - 取得單一公司詳細資料（含職缺）
# =========================================================
@company_bp.route("/api/get_company_detail", methods=["GET"])
def api_get_company_detail():
    try:
        company_id = request.args.get("company_id", type=int)
        if not company_id:
            return jsonify({"success": False, "message": "缺少 company_id"}), 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # ✅ 取得公司基本資料（含職稱）
        cursor.execute("""
            SELECT 
                id,
                company_name,
                description AS company_intro,
                location AS company_address,
                contact_person AS contact_name,
                contact_title,  -- 聯絡人職稱
                contact_email,
                contact_phone,
                submitted_at AS upload_time,
                status,
                reviewed_at
            FROM internship_companies
            WHERE id = %s
        """, (company_id,))
        company = cursor.fetchone()

        if not company:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "查無此公司"}), 404

        # ✅ 取得公司對應的所有實習職缺
        cursor.execute("""
            SELECT 
                title AS internship_unit,
                description AS internship_content,
                department AS internship_department,
                location AS internship_location,
                period AS internship_period,
                work_time AS internship_time,
                slots AS internship_quota,
                remark
            FROM internship_jobs
            WHERE company_id = %s
        """, (company_id,))
        jobs = cursor.fetchall()

        company["internship_jobs"] = jobs

        cursor.close()
        conn.close()

        return jsonify({"success": True, "company": company})

    except Exception:
        print("❌ 取得公司詳細資料錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500
   
# =========================================================
# 頁面 - 公司審核清單
# =========================================================
@company_bp.route('/approve_list')
def approve_company_list():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM internship_companies WHERE status = 'pending'")
        companies = cursor.fetchall()
        return render_template('company/approve_company.html', companies=companies)

    except Exception:
        print("❌ 讀取公司清單錯誤：", traceback.format_exc())
        return render_template('company/approve_company.html', error="伺服器錯誤")

    finally:
        cursor.close()
        conn.close()

# =========================================================
# API - 取得我上傳的公司（含職缺）
# =========================================================
@company_bp.route("/api/get_my_companies", methods=["GET"])
def api_get_my_companies():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
    SELECT 
        id,
        company_name,
        description AS company_intro,
        location AS company_address,
        contact_person AS contact_name,
        contact_title AS contact_title,
        contact_email,
        contact_phone,
        submitted_at AS upload_time,
        status
    FROM internship_companies
    WHERE uploaded_by_user_id = %s
    ORDER BY submitted_at DESC
""", (session["user_id"],))
    companies = cursor.fetchall()

    # 取得每間公司的職缺
    for c in companies:
        cursor.execute("""
            SELECT 
                title AS internship_unit,
                description AS internship_content,
                location AS internship_location,
                period AS internship_period,
                work_time AS internship_time,
                slots AS internship_quota,
                remark
            FROM internship_jobs
            WHERE company_id = %s
        """, (c["id"],))
        jobs = cursor.fetchall()
        c["internship_jobs"] = jobs

        # ✅ 如果有職缺，就攤平成第一筆讓前端直接使用
        if jobs:
            first_job = jobs[0]
            c.update(first_job)
        else:
            # ✅ 若沒有職缺，仍確保前端欄位存在避免 undefined
            c.update({
                "internship_unit": "",
                "internship_content": "",
                "internship_location": "",
                "internship_period": "",
                "internship_time": "",
                "internship_quota": "",
                "remark": ""
            })

    cursor.close()
    conn.close()

    return jsonify({"success": True, "companies": companies})

# =========================================================
# API - 上傳公司 Excel 檔案（純公司）
# =========================================================
@company_bp.route("/api/upload_company_file", methods=["POST"])
def api_upload_company_file():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    file = request.files.get("company_file")
    if not file:
        return jsonify({"success": False, "message": "沒有檔案"}), 400

    try:
        df = pd.read_excel(file)
        required_cols = ["公司名稱", "公司描述", "公司地點", "聯絡人", "聯絡電子郵件", "聯絡電話"]
        for col in required_cols:
            if col not in df.columns:
                return jsonify({"success": False, "message": f"缺少欄位：{col}"}), 400

        conn = get_db()
        cursor = conn.cursor()
        insert_sql = """
            INSERT INTO internship_companies
            (company_name, description, location, contact_person, contact_email, contact_phone,
             uploaded_by_user_id, uploaded_by_role, status, submitted_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',NOW())
        """

        inserted_count = 0
        for _, row in df.iterrows():
            cursor.execute(insert_sql, (
                row["公司名稱"], row["公司描述"], row["公司地點"],
                row["聯絡人"], row["聯絡電子郵件"], row["聯絡電話"],
                session["user_id"], session.get("role")
            ))
            inserted_count += 1

        conn.commit()
        return jsonify({"success": True, "message": f"成功上傳 {inserted_count} 筆公司，等待主任審核"})

    except Exception:
        print("❌ Excel 上傳錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()


# =========================================================
# API - 下載公司詳細資料 (Excel)
# =========================================================
@company_bp.route("/api/download_company/<int:company_id>", methods=["GET"])
def api_download_company_detail(company_id):
    if "user_id" not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT company_name, description, location, contact_person, contact_email, contact_phone, status, submitted_at, reviewed_at
            FROM internship_companies
            WHERE id = %s AND uploaded_by_user_id = %s
        """, (company_id, session["user_id"]))
        company = cursor.fetchone()

        if not company:
            return jsonify({"success": False, "message": "查無資料"}), 404

        df = pd.DataFrame([company])
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='公司資料', index=False)

        output.seek(0)
        filename = f"{company['company_name']}_詳細資料.xlsx"
        return send_file(output, download_name=filename, as_attachment=True,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    except Exception:
        print("❌ 下載公司詳細資料錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

    finally:
        cursor.close()
        conn.close()


# =========================================================
# API - 查詢公司狀態
# =========================================================
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


# =========================================================
# API - 刪除公司
# =========================================================
@company_bp.route("/api/delete_company", methods=["DELETE"])
def api_delete_company():
    company_id = request.args.get("company_id")
    if not company_id:
        return jsonify({"success": False, "message": "缺少 company_id"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM internship_companies 
        WHERE id=%s AND uploaded_by_user_id=%s
    """, (company_id, session.get("user_id")))
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"success": True, "message": "刪除成功"})

# =========================================================
# 頁面 - 公司審核頁面
# =========================================================
@company_bp.route("/approve_company")
def approve_company_page():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM internship_companies WHERE status='pending' ORDER BY submitted_at DESC")
    companies = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("company/approve_company.html", companies=companies)
