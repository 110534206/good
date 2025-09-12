from flask import Blueprint, request, jsonify
from config import get_db

company_bp = Blueprint("company_bp", __name__)

# 取得所有公司資料
@company_bp.route("/api/companies", methods=["GET"])
def get_companies():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM internship_companies")
    companies = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "data": companies})

# 新增公司資料
@company_bp.route("/api/companies", methods=["POST"])
def add_company():
    data = request.get_json()
    name = data.get("name")
    address = data.get("address")
    status = "pending"

    if not name or not address:
        return jsonify({"success": False, "message": "資料不完整"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO internship_companies (name, address, status) VALUES (%s, %s, %s)",
        (name, address, status)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "公司新增成功"})

# 核准公司
@company_bp.route("/api/companies/<int:company_id>/approve", methods=["POST"])
def approve_company(company_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE internship_companies SET status='approved' WHERE id=%s", (company_id,)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "公司已核准"})
