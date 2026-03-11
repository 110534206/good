from flask import Blueprint, request, jsonify, session
from config import get_db
from semester import get_current_semester_code
from datetime import datetime
import traceback

intern_weekly_bp = Blueprint("intern_weekly_bp", __name__, url_prefix="/intern_weekly")


def require_login():
  return "user_id" in session


def _format_row_dates(row):
  """將 row 中的 date / datetime 欄位轉為字串，避免 JSON 序列化問題。"""
  for key in ("due_date",):
    if row.get(key) and hasattr(row[key], "strftime"):
      row[key] = row[key].strftime("%Y-%m-%d")
  for key in ("filled_at", "reviewed_at", "created_at", "updated_at"):
    if row.get(key) and hasattr(row[key], "strftime"):
      row[key] = row[key].strftime("%Y-%m-%d %H:%M:%S")


@intern_weekly_bp.route("/api/intern_period", methods=["GET"])
def get_intern_period():
  """從 internship_configs 取得當前學生的實習起訖日期。"""
  try:
    if not require_login():
      return jsonify({"success": False, "message": "請先登入"}), 403

    user_id = session["user_id"]
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
      SELECT intern_start_date, intern_end_date
      FROM internship_configs
      WHERE user_id = %s
      ORDER BY id DESC LIMIT 1
    """, (user_id,))
    row = cursor.fetchone()

    if not row:
      cursor.execute("""
        SELECT ic.intern_start_date, ic.intern_end_date
        FROM internship_configs ic
        JOIN semesters s ON ic.semester_id = s.id AND s.is_active = 1
        WHERE ic.user_id IS NULL
        ORDER BY ic.id DESC LIMIT 1
      """)
      row = cursor.fetchone()

    cursor.close()

    if not row or not row.get("intern_start_date"):
      return jsonify({"success": False, "message": "找不到實習期間設定"})

    start = row["intern_start_date"]
    end = row.get("intern_end_date")

    return jsonify({
      "success": True,
      "start_date": start.strftime("%Y-%m-%d") if hasattr(start, "strftime") else str(start),
      "end_date": end.strftime("%Y-%m-%d") if end and hasattr(end, "strftime") else (str(end) if end else None),
    })
  except Exception as e:
    traceback.print_exc()
    return jsonify({"success": False, "message": str(e)}), 500


@intern_weekly_bp.route("/api/save", methods=["POST"])
def save_weekly():
  """
  儲存或更新學生的實習週記。
  以 (student_id, semester, week_index) 作為一筆週記的唯一鍵。
  """
  try:
    if not require_login():
      return jsonify({"success": False, "message": "請先登入"}), 403

    user_id = session["user_id"]
    data = request.get_json() or {}

    semester = (data.get("semester") or "").strip()
    week_index = data.get("week_index")
    title = (data.get("title") or "").strip()
    work_notes = (data.get("work_notes") or "").strip()
    reflection = (data.get("reflection") or "").strip()
    due_date = (data.get("due_date") or "").strip() or None
    company_id = data.get("company_id") or None

    if not semester or not week_index:
      return jsonify({"success": False, "message": "缺少學期別或週次資訊"}), 400

    filled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # 檢查是否已經有該週的紀錄
    cursor.execute(
      """
      SELECT id FROM intern_weeklies
      WHERE student_id = %s AND semester = %s AND week_index = %s
      """,
      (user_id, semester, week_index),
    )
    row = cursor.fetchone()

    if row:
      cursor.execute(
        """
        UPDATE intern_weeklies
        SET company_id=%s,
            title=%s,
            work_notes=%s,
            reflection=%s,
            due_date=%s,
            filled_at=%s,
            status=%s,
            updated_at=NOW()
        WHERE id=%s
        """,
        (
          company_id,
          title,
          work_notes,
          reflection,
          due_date,
          filled_at,
          "submitted",
          row["id"],
        ),
      )
    else:
      cursor.execute(
        """
        INSERT INTO intern_weeklies
          (student_id, semester, week_index, title, company_id, work_notes, reflection,
           due_date, filled_at, status, created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        """,
        (
          user_id,
          semester,
          week_index,
          title,
          company_id,
          work_notes,
          reflection,
          due_date,
          filled_at,
          "submitted",
        ),
      )

    db.commit()
    cursor.close()

    return jsonify({"success": True, "filled_at": filled_at})
  except Exception as e:
    traceback.print_exc()
    return jsonify({"success": False, "message": str(e)}), 500


@intern_weekly_bp.route("/api/my_list", methods=["GET"])
def my_weeklies():
  """
  取得登入學生某一學期的所有週記紀錄。
  前端會用 week_index 來對應自動產生的區間。
  """
  try:
    if not require_login():
      return jsonify({"success": False, "message": "請先登入"}), 403

    user_id = session["user_id"]
    semester = (request.args.get("semester") or "").strip()

    if not semester:
      return jsonify({"success": False, "message": "缺少學期別"}), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
      """
      SELECT
        id, semester, week_index, title, work_notes, reflection,
        due_date, filled_at, status, teacher_comment, reviewed_at
      FROM intern_weeklies
      WHERE student_id = %s AND semester = %s
      ORDER BY week_index ASC
      """,
      (user_id, semester),
    )
    rows = cursor.fetchall() or []
    cursor.close()

    for row in rows:
      _format_row_dates(row)

    return jsonify({"success": True, "weeklies": rows})
  except Exception as e:
    traceback.print_exc()
    return jsonify({"success": False, "message": str(e)}), 500


# =====================================================
# 指導老師 API：取得其負責學生的所有已提交週記
# =====================================================
@intern_weekly_bp.route("/api/review_list", methods=["GET"])
def review_list():
  try:
    if not require_login():
      return jsonify({"success": False, "message": "請先登入"}), 403

    role = session.get("role")
    if role not in ("teacher", "director", "class_teacher"):
      return jsonify({"success": False, "message": "未授權"}), 403

    teacher_id = session["user_id"]
    db = get_db()
    cursor = db.cursor(dictionary=True)

    semester_code = get_current_semester_code(cursor)

    if role == "director":
      cursor.execute("""
        SELECT DISTINCT
          iw.id, iw.student_id, iw.semester, iw.week_index, iw.title,
          iw.work_notes, iw.reflection, iw.file_path,
          iw.due_date, iw.filled_at, iw.status,
          iw.teacher_comment, iw.reviewed_at,
          u.username AS student_number,
          u.name     AS student_name,
          c.name     AS class_name,
          ic.company_name
        FROM intern_weeklies iw
        JOIN users u ON iw.student_id = u.id
        LEFT JOIN classes c ON u.class_id = c.id
        LEFT JOIN internship_companies ic ON iw.company_id = ic.id
        WHERE iw.status != 'draft'
        ORDER BY iw.filled_at DESC
      """)
    else:
      cursor.execute("""
        SELECT DISTINCT
          iw.id, iw.student_id, iw.semester, iw.week_index, iw.title,
          iw.work_notes, iw.reflection, iw.file_path,
          iw.due_date, iw.filled_at, iw.status,
          iw.teacher_comment, iw.reviewed_at,
          u.username AS student_number,
          u.name     AS student_name,
          c.name     AS class_name,
          ic.company_name
        FROM intern_weeklies iw
        JOIN users u ON iw.student_id = u.id
        LEFT JOIN classes c ON u.class_id = c.id
        LEFT JOIN internship_companies ic ON iw.company_id = ic.id
        JOIN teacher_student_relations tsr ON tsr.student_id = iw.student_id
        WHERE tsr.teacher_id = %s
          AND iw.status != 'draft'
        ORDER BY iw.filled_at DESC
      """, (teacher_id,))

    rows = cursor.fetchall() or []

    for row in rows:
      _format_row_dates(row)

    if role == "director":
      cursor.execute("""
        SELECT DISTINCT ic.company_name
        FROM internship_companies ic
        WHERE ic.status = 'approved'
        ORDER BY ic.company_name
      """)
    else:
      cursor.execute("""
        SELECT DISTINCT ic.company_name
        FROM internship_companies ic
        WHERE ic.advisor_user_id = %s
        ORDER BY ic.company_name
      """, (teacher_id,))

    companies = [r["company_name"] for r in cursor.fetchall() if r.get("company_name")]

    if role == "director":
      cursor.execute("""
        SELECT DISTINCT
          u.id AS student_id, u.username AS student_number, u.name AS student_name,
          c.name AS class_name, ic.company_name
        FROM matching_results mr
        JOIN users u ON mr.student_id = u.id
        LEFT JOIN classes c ON u.class_id = c.id
        LEFT JOIN internship_companies ic ON mr.company_id = ic.id
        ORDER BY ic.company_name, u.username
      """)
    else:
      cursor.execute("""
        SELECT DISTINCT
          u.id AS student_id, u.username AS student_number, u.name AS student_name,
          c.name AS class_name, ic.company_name
        FROM matching_results mr
        JOIN users u ON mr.student_id = u.id
        LEFT JOIN classes c ON u.class_id = c.id
        JOIN internship_companies ic ON mr.company_id = ic.id
        WHERE ic.advisor_user_id = %s
        ORDER BY ic.company_name, u.username
      """, (teacher_id,))

    students = cursor.fetchall() or []
    cursor.close()

    return jsonify({"success": True, "items": rows, "companies": companies, "students": students})
  except Exception as e:
    traceback.print_exc()
    return jsonify({"success": False, "message": str(e)}), 500


# =====================================================
# 指導老師 API：批閱完成
# =====================================================
@intern_weekly_bp.route("/api/approve/<int:weekly_id>", methods=["POST"])
def approve_weekly(weekly_id):
  try:
    if not require_login():
      return jsonify({"success": False, "message": "請先登入"}), 403

    role = session.get("role")
    if role not in ("teacher", "director", "class_teacher"):
      return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json() or {}
    comment = (data.get("teacher_comment") or "").strip()

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
      """
      UPDATE intern_weeklies
      SET status = 'reviewed',
          teacher_comment = %s,
          reviewed_at = NOW(),
          updated_at = NOW()
      WHERE id = %s
      """,
      (comment, weekly_id),
    )
    db.commit()
    cursor.close()
    return jsonify({"success": True, "message": "批閱完成"})
  except Exception as e:
    traceback.print_exc()
    return jsonify({"success": False, "message": str(e)}), 500


# =====================================================
# 指導老師 API：退回修改
# =====================================================
@intern_weekly_bp.route("/api/return/<int:weekly_id>", methods=["POST"])
def return_weekly(weekly_id):
  try:
    if not require_login():
      return jsonify({"success": False, "message": "請先登入"}), 403

    role = session.get("role")
    if role not in ("teacher", "director", "class_teacher"):
      return jsonify({"success": False, "message": "未授權"}), 403

    data = request.get_json() or {}
    comment = (data.get("teacher_comment") or "").strip()

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
      """
      UPDATE intern_weeklies
      SET status = 'returned',
          teacher_comment = %s,
          reviewed_at = NOW(),
          updated_at = NOW()
      WHERE id = %s
      """,
      (comment, weekly_id),
    )
    db.commit()
    cursor.close()
    return jsonify({"success": True, "message": "已退回修改"})
  except Exception as e:
    traceback.print_exc()
    return jsonify({"success": False, "message": str(e)}), 500

