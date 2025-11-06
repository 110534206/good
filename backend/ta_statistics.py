"""
科助端统计报表模块
生成完整的统计图表数据
"""
from flask import Blueprint, request, jsonify, session
from config import get_db
from datetime import datetime
from semester import get_current_semester_code
import traceback

ta_statistics_bp = Blueprint("ta_statistics_bp", __name__, url_prefix="/ta/statistics")

# =========================================================
# API: 取得全系統統計總覽
# =========================================================
@ta_statistics_bp.route("/api/overview", methods=["GET"])
def get_overview():
    """科助端取得全系統統計總覽"""
    if 'user_id' not in session or session.get('role') not in ['ta', 'admin']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取當前學期
        current_semester_code = get_current_semester_code(cursor)
        
        # 1. 學生總數統計
        cursor.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'student'")
        total_students = cursor.fetchone()['total']
        
        # 2. 履歷統計
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT r.user_id) AS students_with_resume,
                COUNT(DISTINCT CASE WHEN r.status = 'approved' THEN r.user_id END) AS students_approved,
                COUNT(DISTINCT CASE WHEN r.status = 'rejected' THEN r.user_id END) AS students_rejected,
                COUNT(DISTINCT CASE WHEN r.status = 'uploaded' THEN r.user_id END) AS students_pending,
                COUNT(*) AS total_resumes
            FROM resumes r
            WHERE r.semester_id = (SELECT id FROM semesters WHERE is_active = 1 LIMIT 1)
        """)
        resume_stats = cursor.fetchone()
        
        # 3. 志願序統計
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT sp.student_id) AS students_with_preferences,
                COUNT(DISTINCT CASE WHEN sp.status = 'approved' THEN sp.student_id END) AS students_approved,
                COUNT(DISTINCT CASE WHEN sp.status = 'rejected' THEN sp.student_id END) AS students_rejected,
                COUNT(DISTINCT CASE WHEN sp.status = 'pending' THEN sp.student_id END) AS students_pending,
                COUNT(*) AS total_preferences
            FROM student_preferences sp
            WHERE sp.semester_id = (SELECT id FROM semesters WHERE is_active = 1 LIMIT 1)
        """)
        preference_stats = cursor.fetchone()
        
        # 4. 公司統計
        cursor.execute("""
            SELECT 
                COUNT(*) AS total_companies,
                COUNT(CASE WHEN status = 'approved' THEN 1 END) AS approved_companies,
                COUNT(CASE WHEN status = 'pending' THEN 1 END) AS pending_companies,
                COUNT(CASE WHEN status = 'rejected' THEN 1 END) AS rejected_companies
            FROM internship_companies
        """)
        company_stats = cursor.fetchone()
        
        # 5. 各公司被選擇次數（前10名）
        cursor.execute("""
            SELECT 
                ic.company_name,
                COUNT(sp.id) AS preference_count
            FROM internship_companies ic
            LEFT JOIN student_preferences sp ON ic.id = sp.company_id
            GROUP BY ic.id, ic.company_name
            ORDER BY preference_count DESC
            LIMIT 10
        """)
        top_companies = cursor.fetchall()
        
        # 6. 計算完成率
        resume_completion_rate = round(
            (resume_stats['students_with_resume'] or 0) * 100.0 / total_students 
            if total_students > 0 else 0, 2
        )
        preference_completion_rate = round(
            (preference_stats['students_with_preferences'] or 0) * 100.0 / total_students 
            if total_students > 0 else 0, 2
        )
        resume_approval_rate = round(
            (resume_stats['students_approved'] or 0) * 100.0 / (resume_stats['students_with_resume'] or 1)
            if resume_stats['students_with_resume'] else 0, 2
        )
        preference_approval_rate = round(
            (preference_stats['students_approved'] or 0) * 100.0 / (preference_stats['students_with_preferences'] or 1)
            if preference_stats['students_with_preferences'] else 0, 2
        )
        
        return jsonify({
            "success": True,
            "current_semester": current_semester_code,
            "total_students": total_students,
            "resume_stats": {
                **resume_stats,
                "completion_rate": resume_completion_rate,
                "approval_rate": resume_approval_rate
            },
            "preference_stats": {
                **preference_stats,
                "completion_rate": preference_completion_rate,
                "approval_rate": preference_approval_rate
            },
            "company_stats": company_stats,
            "top_companies": top_companies
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 取得各班級統計（列表）
# =========================================================
@ta_statistics_bp.route("/api/classes", methods=["GET"])
def get_classes_statistics():
    """科助端取得各班級統計列表"""
    if 'user_id' not in session or session.get('role') not in ['ta', 'admin']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取當前學期ID
        cursor.execute("SELECT id FROM semesters WHERE is_active = 1 LIMIT 1")
        current_semester = cursor.fetchone()
        semester_id = current_semester['id'] if current_semester else None
        
        # 查詢所有班級的統計
        cursor.execute("""
            SELECT 
                c.id AS class_id,
                c.name AS class_name,
                c.department,
                COUNT(DISTINCT u.id) AS total_students,
                -- 履歷統計
                COUNT(DISTINCT r.user_id) AS students_with_resume,
                COUNT(DISTINCT CASE WHEN r.status = 'approved' THEN r.user_id END) AS students_resume_approved,
                COUNT(DISTINCT CASE WHEN r.status = 'rejected' THEN r.user_id END) AS students_resume_rejected,
                COUNT(DISTINCT CASE WHEN r.status = 'uploaded' THEN r.user_id END) AS students_resume_pending,
                -- 志願序統計
                COUNT(DISTINCT sp.student_id) AS students_with_preferences,
                COUNT(DISTINCT CASE WHEN sp.status = 'approved' THEN sp.student_id END) AS students_preferences_approved,
                COUNT(DISTINCT CASE WHEN sp.status = 'rejected' THEN sp.student_id END) AS students_preferences_rejected,
                COUNT(DISTINCT CASE WHEN sp.status = 'pending' THEN sp.student_id END) AS students_preferences_pending
            FROM classes c
            LEFT JOIN users u ON u.class_id = c.id AND u.role = 'student'
            LEFT JOIN resumes r ON r.user_id = u.id AND r.semester_id = %s
            LEFT JOIN student_preferences sp ON sp.student_id = u.id AND sp.semester_id = %s
            GROUP BY c.id, c.name, c.department
            ORDER BY c.department, c.name
        """, (semester_id, semester_id))
        
        classes_stats = cursor.fetchall()
        
        # 計算完成率和通過率
        for stat in classes_stats:
            total = stat['total_students'] or 0
            # 履歷完成率
            stat['resume_completion_rate'] = round(
                (stat['students_with_resume'] or 0) * 100.0 / total if total > 0 else 0, 2
            )
            # 履歷通過率
            stat['resume_approval_rate'] = round(
                (stat['students_resume_approved'] or 0) * 100.0 / (stat['students_with_resume'] or 1)
                if stat['students_with_resume'] else 0, 2
            )
            # 志願序完成率
            stat['preference_completion_rate'] = round(
                (stat['students_with_preferences'] or 0) * 100.0 / total if total > 0 else 0, 2
            )
            # 志願序通過率
            stat['preference_approval_rate'] = round(
                (stat['students_preferences_approved'] or 0) * 100.0 / (stat['students_with_preferences'] or 1)
                if stat['students_with_preferences'] else 0, 2
            )
        
        return jsonify({
            "success": True,
            "classes": classes_stats
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 取得錄取統計
# =========================================================
@ta_statistics_bp.route("/api/admissions", methods=["GET"])
def get_admission_statistics():
    """科助端取得錄取統計"""
    if 'user_id' not in session or session.get('role') not in ['ta', 'admin']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取當前學期代碼
        current_semester_code = get_current_semester_code(cursor)
        
        # 錄取統計
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT tsr.student_id) AS total_admitted_students,
                COUNT(DISTINCT tsr.company_id) AS total_companies_with_admissions,
                COUNT(DISTINCT tsr.teacher_id) AS total_teachers_involved
            FROM teacher_student_relations tsr
            WHERE tsr.semester = %s
        """, (current_semester_code,))
        admission_stats = cursor.fetchone()
        
        # 各公司錄取人數
        cursor.execute("""
            SELECT 
                ic.company_name,
                COUNT(DISTINCT tsr.student_id) AS admitted_count
            FROM internship_companies ic
            LEFT JOIN teacher_student_relations tsr ON ic.id = tsr.company_id AND tsr.semester = %s
            GROUP BY ic.id, ic.company_name
            HAVING admitted_count > 0
            ORDER BY admitted_count DESC
            LIMIT 10
        """, (current_semester_code,))
        company_admissions = cursor.fetchall()
        
        # 各指導老師負責學生數
        cursor.execute("""
            SELECT 
                u.name AS teacher_name,
                COUNT(DISTINCT tsr.student_id) AS student_count
            FROM teacher_student_relations tsr
            JOIN users u ON tsr.teacher_id = u.id
            WHERE tsr.semester = %s
            GROUP BY tsr.teacher_id, u.name
            ORDER BY student_count DESC
            LIMIT 10
        """, (current_semester_code,))
        teacher_stats = cursor.fetchall()
        
        return jsonify({
            "success": True,
            "current_semester": current_semester_code,
            "admission_stats": admission_stats,
            "company_admissions": company_admissions,
            "teacher_stats": teacher_stats
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 取得時間序列統計（履歷/志願序提交趨勢）
# =========================================================
@ta_statistics_bp.route("/api/trends", methods=["GET"])
def get_trends():
    """科助端取得時間序列統計（履歷/志願序提交趨勢）"""
    if 'user_id' not in session or session.get('role') not in ['ta', 'admin']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 獲取當前學期ID
        cursor.execute("SELECT id FROM semesters WHERE is_active = 1 LIMIT 1")
        current_semester = cursor.fetchone()
        semester_id = current_semester['id'] if current_semester else None
        
        # 履歷提交趨勢（按日期）
        cursor.execute("""
            SELECT 
                DATE(r.created_at) AS date,
                COUNT(*) AS count
            FROM resumes r
            WHERE r.semester_id = %s
            GROUP BY DATE(r.created_at)
            ORDER BY date ASC
        """, (semester_id,))
        resume_trends = cursor.fetchall()
        
        # 志願序提交趨勢（按日期）
        cursor.execute("""
            SELECT 
                DATE(sp.submitted_at) AS date,
                COUNT(*) AS count
            FROM student_preferences sp
            WHERE sp.semester_id = %s
            GROUP BY DATE(sp.submitted_at)
            ORDER BY date ASC
        """, (semester_id,))
        preference_trends = cursor.fetchall()
        
        # 格式化日期
        for trend in resume_trends + preference_trends:
            if isinstance(trend.get('date'), datetime):
                trend['date'] = trend['date'].strftime("%Y-%m-%d")
        
        return jsonify({
            "success": True,
            "resume_trends": resume_trends,
            "preference_trends": preference_trends
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"查詢失敗: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()

# =========================================================
# API: 匯出統計報表（Excel）
# =========================================================
@ta_statistics_bp.route("/api/export", methods=["GET"])
def export_statistics():
    """科助端匯出統計報表（Excel）"""
    if 'user_id' not in session or session.get('role') not in ['ta', 'admin']:
        return jsonify({"success": False, "message": "未授權"}), 403
    
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter
        from flask import send_file
        import io
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 獲取當前學期
        current_semester_code = get_current_semester_code(cursor)
        
        # 創建 Excel 工作簿
        wb = Workbook()
        
        # Sheet 1: 總覽統計
        ws1 = wb.active
        ws1.title = "總覽統計"
        
        # 標題
        ws1['A1'] = f"智慧實習系統統計報表 - {current_semester_code or '當前學期'}"
        ws1['A1'].font = Font(bold=True, size=14)
        ws1.merge_cells('A1:D1')
        
        # 獲取總覽數據（使用現有的 API 邏輯）
        cursor.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'student'")
        total_students = cursor.fetchone()['total']
        
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT r.user_id) AS students_with_resume,
                COUNT(DISTINCT CASE WHEN r.status = 'approved' THEN r.user_id END) AS students_approved
            FROM resumes r
            WHERE r.semester_id = (SELECT id FROM semesters WHERE is_active = 1 LIMIT 1)
        """)
        resume_stats = cursor.fetchone()
        
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT sp.student_id) AS students_with_preferences,
                COUNT(DISTINCT CASE WHEN sp.status = 'approved' THEN sp.student_id END) AS students_approved
            FROM student_preferences sp
            WHERE sp.semester_id = (SELECT id FROM semesters WHERE is_active = 1 LIMIT 1)
        """)
        preference_stats = cursor.fetchone()
        
        # 寫入總覽數據
        row = 3
        ws1[f'A{row}'] = "項目"
        ws1[f'B{row}'] = "數量"
        ws1[f'C{row}'] = "完成率"
        row += 1
        
        ws1[f'A{row}'] = "總學生數"
        ws1[f'B{row}'] = total_students
        row += 1
        
        ws1[f'A{row}'] = "已上傳履歷人數"
        ws1[f'B{row}'] = resume_stats['students_with_resume'] or 0
        ws1[f'C{row}'] = f"{round((resume_stats['students_with_resume'] or 0) * 100.0 / total_students if total_students > 0 else 0, 2)}%"
        row += 1
        
        ws1[f'A{row}'] = "已填寫志願序人數"
        ws1[f'B{row}'] = preference_stats['students_with_preferences'] or 0
        ws1[f'C{row}'] = f"{round((preference_stats['students_with_preferences'] or 0) * 100.0 / total_students if total_students > 0 else 0, 2)}%"
        
        # Sheet 2: 各班級統計
        ws2 = wb.create_sheet("各班級統計")
        
        # 獲取各班級統計
        cursor.execute("""
            SELECT 
                c.name AS class_name,
                c.department,
                COUNT(DISTINCT u.id) AS total_students,
                COUNT(DISTINCT r.user_id) AS students_with_resume,
                COUNT(DISTINCT sp.student_id) AS students_with_preferences
            FROM classes c
            LEFT JOIN users u ON u.class_id = c.id AND u.role = 'student'
            LEFT JOIN resumes r ON r.user_id = u.id
            LEFT JOIN student_preferences sp ON sp.student_id = u.id
            GROUP BY c.id, c.name, c.department
            ORDER BY c.department, c.name
        """)
        classes_stats = cursor.fetchall()
        
        # 寫入表頭
        headers = ['班級名稱', '系所', '總學生數', '已上傳履歷', '已填寫志願序', '履歷完成率', '志願序完成率']
        for col, header in enumerate(headers, 1):
            cell = ws2.cell(row=1, column=col)
            cell.value = header
            cell.font = Font(bold=True)
        
        # 寫入數據
        for idx, stat in enumerate(classes_stats, 2):
            ws2.cell(row=idx, column=1, value=stat['class_name'])
            ws2.cell(row=idx, column=2, value=stat['department'])
            ws2.cell(row=idx, column=3, value=stat['total_students'] or 0)
            ws2.cell(row=idx, column=4, value=stat['students_with_resume'] or 0)
            ws2.cell(row=idx, column=5, value=stat['students_with_preferences'] or 0)
            
            total = stat['total_students'] or 0
            resume_rate = round((stat['students_with_resume'] or 0) * 100.0 / total if total > 0 else 0, 2)
            pref_rate = round((stat['students_with_preferences'] or 0) * 100.0 / total if total > 0 else 0, 2)
            ws2.cell(row=idx, column=6, value=f"{resume_rate}%")
            ws2.cell(row=idx, column=7, value=f"{pref_rate}%")
        
        # 調整欄寬
        for col in range(1, len(headers) + 1):
            ws2.column_dimensions[get_column_letter(col)].width = 15
        
        # 保存到內存
        excel_buffer = io.BytesIO()
        wb.save(excel_buffer)
        excel_buffer.seek(0)
        
        # 生成檔案名稱
        filename = f"實習系統統計報表_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        return send_file(
            excel_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"匯出失敗: {str(e)}"}), 500
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

