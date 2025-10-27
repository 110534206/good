from flask import Blueprint, request, jsonify, render_template, session, send_file
from config import get_db
from datetime import datetime
import traceback
import pandas as pd
import io
import os
import traceback 
from flask import current_app
from werkzeug.utils import secure_filename

company_bp = Blueprint("company_bp", __name__)

# =========================================================
# 路由 - 下載公司上傳範本
# =========================================================
@company_bp.route('/download_company_template', methods=['GET'])
def download_company_template():
    try:
        # 🎯 檔案名稱 🎯
        template_file_name = "公司上傳範本.xlsx"
        
        # 1. 獲取 Flask 專案的根目錄 (e.g., C:\Featured\good\backend)
        backend_dir = current_app.root_path
        # 2. 退回一層到專案總目錄 (e.g., C:\Featured\good)
        project_root = os.path.dirname(backend_dir) 
        
        # 3. 組合檔案的完整路徑：[專案總目錄]/frontend/static/examples/公司上傳範本.xlsx
        file_path = os.path.join(
            project_root, 
            'frontend', 
            'static', 
            'examples', 
            template_file_name
        ) 

        # 檢查檔案是否存在
        if not os.path.exists(file_path):
            print(f"❌ 找不到範本檔案 (修正路徑): {file_path}")
            return jsonify({"success": False, "message": "找不到範本檔案，請聯繫管理員確認檔案位置"}), 500

        # 使用 send_file 將檔案送出給使用者
        return send_file(
            file_path,
            as_attachment=True, 
            download_name=template_file_name,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' 
        )

    except Exception as e:
        print("❌ [download_company_template] 發生錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": "下載失敗，伺服器內部錯誤"}), 500


# =========================================================
# 輔助函數：解析 Excel 檔案中的公司資料和職缺資料
# =========================================================
def parse_excel_file(file_storage):
    """
    解析上傳的 Excel 檔案，從 '公司資料' 和 '實習職缺' 工作表中提取資料。
    """
    try:
        # 將 FileStorage 內容讀取到 BytesIO 緩衝區
        file_bytes = io.BytesIO(file_storage.read())
        
        # 使用 pandas 讀取 Excel 檔案，指定要讀取的工作表
        df_dict = pd.read_excel(
            file_bytes,
            sheet_name=['公司資料', '實習職缺'],
            header=0,
            dtype=str,  # 將所有資料視為字串
            keep_default_na=False # 保持空值為空字串，而不是 NaN
        )
        
        df_company = df_dict.get('公司資料')
        if df_company is None:
            raise ValueError("找不到工作表名稱 '公司資料'。請確認工作表名稱正確。")

        df_jobs = df_dict.get('實習職缺')
        if df_jobs is None:
            raise ValueError("找不到工作表名稱 '實習職缺'。請確認工作表名稱正確。")
            
        # 轉換為 JSON 格式 (list of dictionaries)
        company_data = df_company.to_dict('records')
        jobs_data = df_jobs.to_dict('records')
        
        return {
            'success': True,
            'company_data': company_data,
            'jobs_data': jobs_data
        }

    except ValueError as ve:
        return {'success': False, 'message': str(ve)}
    except Exception as e:
        print("❌ [parse_excel_file] 發生錯誤:", e)
        traceback.print_exc()
        return {'success': False, 'message': f"解析檔案失敗: {e}"}

# =========================================================
# 頁面 - 上傳公司（單筆手動表單 / 批量 Excel）
# =========================================================
@company_bp.route('/upload_company', methods=['GET', 'POST'])
def upload_company_form():
    if "user_id" not in session and request.method == 'POST':
        return jsonify({"success": False, "message": "請先登入才能上傳資料"}), 401

    if request.method == 'POST':
        if 'excel_file' in request.files:
            file = request.files['excel_file']
            if file.filename == '':
                return jsonify({"success": False, "message": "請選擇檔案"}), 400
            if not file.filename.endswith(('.xlsx', '.xls')):
                return jsonify({"success": False, "message": "請上傳 .xlsx 或 .xls 格式的 Excel 檔案"}), 400

            action = request.form.get('action')

            # 預覽
            if action == 'preview':
                result = parse_excel_file(file)
                return jsonify(result)

            # 最終提交
            elif action == 'final_submit':
                result = parse_excel_file(file)
                if not result['success']:
                    return jsonify(result), 400

                conn = None
                cursor = None
                try:
                    company_data_list = result['company_data']
                    jobs_data_list = result['jobs_data']

                    if not company_data_list:
                        return jsonify({"success": False, "message": "Excel 檔案中沒有公司資料"}), 400

                    conn = get_db()
                    cursor = conn.cursor()

                    total_jobs = 0
                    inserted_companies = []

                    # 🔁 逐筆處理每家公司
                    for company_row in company_data_list:
                        cursor.execute("""
                            INSERT INTO internship_companies
                                (company_name, description, location, contact_person, contact_title, 
                                 contact_email, contact_phone, uploaded_by_user_id, uploaded_by_role, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                        """, (
                            company_row.get("公司名稱", ""), 
                            company_row.get("公司簡介", ""), 
                            company_row.get("公司地址", ""),
                            company_row.get("聯絡人姓名", ""), 
                            company_row.get("聯絡人職稱", ""), 
                            company_row.get("聯絡信箱", ""), 
                            company_row.get("聯絡電話", ""),
                            session["user_id"], 
                            session.get("role", "teacher")
                        ))

                        company_id = cursor.lastrowid
                        inserted_companies.append(company_row.get("公司名稱", ""))

                        # 🔍 找出該公司對應的職缺資料
                        related_jobs = [
                            j for j in jobs_data_list 
                            if j.get("公司名稱") == company_row.get("公司名稱")
                        ]

                        for job_row in related_jobs:
                            cursor.execute("""
                                INSERT INTO internship_jobs
                                    (company_id, title, description, period, salary, work_time, slots, remark)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            """, (
                               company_id,
                               job_row.get("實習職位", ""),      # title
                               job_row.get("實習內容", ""),      # description
                               job_row.get("實習期間", ""),      # period
                               job_row.get("薪資", ""),          # salary
                               job_row.get("實習時段", ""),      # work_time
                               job_row.get("崗位人數", ""),      # slots
                               job_row.get("備註", "")           # remark
                            ))
                            total_jobs += 1

                    conn.commit()

                    return jsonify({
                        "success": True,
                        "message": f"✅ 成功上傳 {len(company_data_list)} 間公司，共 {total_jobs} 筆職缺，等待審核。",
                        "companies": inserted_companies
                    })

                except Exception as e:
                    if conn:
                        conn.rollback()
                    print("❌ [final_submit] 資料庫寫入錯誤:", e)
                    traceback.print_exc()
                    return jsonify({"success": False, "message": f"資料庫寫入錯誤：{str(e)}"}), 500
                finally:
                    if cursor:
                        cursor.close()
                    if conn:
                        conn.close()

            return jsonify({"success": False, "message": "未知的上傳請求動作"}), 400

        else:
            print("❌ POST 請求類型錯誤：非檔案上傳")
            return jsonify({"success": False, "message": "POST 請求類型錯誤或缺少 Excel 檔案"}), 400

    return render_template('company/upload_company.html')

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
                ic.id,
                u.name AS upload_teacher_name,
                ic.company_name,
                ic.contact_person AS contact_name,
                ic.contact_email,
                ic.submitted_at AS upload_time,
                ic.status
            FROM internship_companies ic
            LEFT JOIN users u ON ic.uploaded_by_user_id = u.id
            LEFT JOIN classes_teacher ct ON ct.teacher_id = u.id
            WHERE ic.status = 'pending'
            ORDER BY ic.submitted_at DESC
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
                ic.id,
                u.name AS upload_teacher_name,
                ic.company_name, 
                ic.status,
                ic.submitted_at AS upload_time,
                ic.reviewed_at
            FROM internship_companies ic
            LEFT JOIN users u ON ic.uploaded_by_user_id = u.id
            LEFT JOIN classes_teacher ct ON ct.teacher_id = u.id
            WHERE ic.status IN ('approved', 'rejected')
            ORDER BY ic.reviewed_at DESC
        """)

        companies = cursor.fetchall()
        cursor.close()
        conn.close()

        return jsonify({"success": True, "companies": companies})

    except Exception:
        print("❌ 取得已審核公司錯誤：", traceback.format_exc())
        return jsonify({"success": False, "message": "伺服器錯誤"}), 500

# =========================================================
# API - 公司退件
# =========================================================
@company_bp.route('/api/reject_company', methods=['POST'])
def reject_company():
    try:
        data = request.get_json()
        company_id = data.get('company_id')
        reason = data.get('reason', '').strip()

        if not company_id or not reason:
            return jsonify(success=False, message="缺少退件參數"), 400

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE internship_companies
            SET status='rejected',
                reject_reason=%s,
                reviewed_at=NOW()
            WHERE id=%s
        """, (reason, company_id))
        conn.commit()
        return jsonify(success=True, message="公司已退件，理由已保存")
    except Exception as e:
        print("❌ reject_company error:", e)
        return jsonify(success=False, message="退件失敗，伺服器錯誤")
    finally:
        cursor.close()
        conn.close()

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
          contact_title,
          contact_email,
          contact_phone,
          submitted_at AS upload_time,
          status,
          reviewed_at,
          reject_reason
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
                department AS department,
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
                department AS department, 
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
        required_cols = ["公司名稱", "公司描述", "公司地點", "聯絡人", "聯絡人職稱", "聯絡電子郵件", "聯絡電話"]
        for col in required_cols:
            if col not in df.columns:
                return jsonify({"success": False, "message": f"缺少欄位：{col}"}), 400

        conn = get_db()
        cursor = conn.cursor()
        insert_sql = """
        INSERT INTO internship_companies
       (company_name, description, location, contact_person, contact_title, contact_email, contact_phone,
       uploaded_by_user_id, uploaded_by_role, status, submitted_at)
       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',NOW())
       """

        inserted_count = 0
        for _, row in df.iterrows():
         cursor.execute(insert_sql, (
         row["公司名稱"], row["公司描述"], row["公司地點"],
         row["聯絡人"], row["聯絡人職稱"], row["聯絡電子郵件"], row["聯絡電話"],
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
# API - 下載公司詳細資料 (Excel, 中文欄位 + 含職缺)
# =========================================================
@company_bp.route("/api/download_company/<int:company_id>", methods=["GET"])
def api_download_company_detail(company_id):
    if "user_id" not in session:
        return jsonify({"success": False, "message": "請先登入"}), 401

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 取得公司資料
        cursor.execute("""
            SELECT 
                company_name,
                description,
                location,
                contact_person,
                contact_title,
                contact_email,
                contact_phone,
                status,
                submitted_at,
                reviewed_at
            FROM internship_companies
            WHERE id = %s AND uploaded_by_user_id = %s
        """, (company_id, session["user_id"]))
        company = cursor.fetchone()

        if not company:
            return jsonify({"success": False, "message": "查無資料"}), 404

        # 取得職缺資料
        cursor.execute("""
            SELECT 
                title,
                description AS job_description,
                department,
                period,
                work_time,
                slots,
                remark
            FROM internship_jobs
            WHERE company_id = %s
        """, (company_id,))
        jobs = cursor.fetchall()

        # ---- 中文欄位名稱轉換 ----
        company_data = {
            "公司名稱": company["company_name"],
            "公司簡介": company["description"],
            "公司地址": company["location"],
            "聯絡人姓名": company["contact_person"],
            "聯絡人職稱": company["contact_title"],
            "聯絡信箱": company["contact_email"],
            "聯絡電話": company["contact_phone"],
            "上傳時間": company["submitted_at"].strftime("%Y-%m-%d %H:%M:%S") if company["submitted_at"] else "",
            "審核時間": company["reviewed_at"].strftime("%Y-%m-%d %H:%M:%S") if company["reviewed_at"] else "",
            "目前狀態": "核准" if company["status"] == "approved" else "拒絕" if company["status"] == "rejected" else "待審核"
        }

        # ---- 建立 Excel ----
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            # 公司基本資料
            pd.DataFrame([company_data]).to_excel(writer, sheet_name='公司資料', index=False)

            # 若有職缺，加入第二張工作表
            if jobs:
                job_df = pd.DataFrame(jobs)
                # 改中文欄位名稱
                job_df.rename(columns={
                    "title": "實習單位名稱",
                    "job_description": "工作內容",
                    "department": "部門",
                    "period": "實習期間",
                    "work_time": "實習時間",
                    "slots": "需求人數",
                    "remark": "備註"
                }, inplace=True)
                job_df.to_excel(writer, sheet_name='實習職缺', index=False)

        output.seek(0)
        filename = f"{company['company_name']}_詳細資料.xlsx"
        return send_file(
            output,
            download_name=filename,
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

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
def delete_company():
    try:
        # 登入檢查
        if "user_id" not in session:
            return jsonify({"success": False, "message": "未登入"}), 401

        company_id = request.args.get("company_id")
        if not company_id:
            return jsonify({"success": False, "message": "缺少公司ID"}), 400

        db = get_db()
        cursor = db.cursor()

        # 🔹 先刪除該公司底下的所有職缺
        cursor.execute("DELETE FROM internship_jobs WHERE company_id = %s", (company_id,))

        # 🔹 再刪除公司資料
        cursor.execute("DELETE FROM internship_companies WHERE id = %s", (company_id,))

        db.commit()
        cursor.close()
        db.close()

        return jsonify({"success": True, "message": "資料已成功刪除"})

    except Exception as e:
        print("❌ [delete_company] 發生錯誤:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": "刪除失敗，請稍後再試"}), 500
    
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

# =========================
# 頁面 - 公司管理前端頁
# =========================
@company_bp.route("/manage_companies")
def manage_companies_page():
    return render_template("user_shared/manage_companies.html")