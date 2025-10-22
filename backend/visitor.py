from flask import Flask, render_template, request, jsonify, redirect, url_for

# 將應用程式實例命名為 app
app = Flask(__name__)

# ===============================================
# 1. 頁面路由 (Page Routes)
# ===============================================

@app.route('/')
@app.route('/login')
def show_entry_page():
    """
    網站入口頁面，顯示簡化的 login.html。
    由於前端的 login.html 已經被修改為只剩下「訪客進入」和「註冊」按鈕，
    這裡直接渲染 login.html 即可作為入口。
    """
    return render_template('login.html') 

@app.route('/role_selection')
def show_role_selection_page():
    """載入訪客身分選擇頁面 (對應您命名的 role_selection.html)"""
    return render_template('visitor_role_selection.html') 

@app.route('/student_home')
def show_student_home_page():
    """載入學生主頁 (對應您命名的 student_home.html 或 student_visitor.html)"""
    # 這裡假設您的學生主頁 HTML 檔案已經正確命名為 'student_home.html'
    # 如果您仍使用原始名稱，請改為 'student_visitor.html'
    return render_template('student_visitor.html') 

@app.route('/company_home')
def show_company_home_page():
    """載入廠商主頁 (對應您命名的 company_home.html 或 vendor_visitor.html)"""
    # 這裡假設您的廠商主頁 HTML 檔案已經正確命名為 'company_home.html'
    # 如果您仍使用原始名稱，請改為 'vendor_visitor.html'
    return render_template('vendor_visitor.html') 

# ===============================================
# 2. API 路由 (API Routes)
# *** /api/login 路由已移除，因為不再需要帳密登入邏輯 ***
# ===============================================

@app.route('/api/visitor/notification', methods=['GET'])
def api_student_notification():
    """提供學生主頁的公告輪播資料 (由 student_visitor.html 呼叫)"""
    # 此處返回模擬的 JSON 資料
    announcements = [
        "🔔 學生公告 1: 實習報名即將截止，請盡速完成！",
        "📢 學生公告 2: AI 履歷修改功能已上線。",
        "📅 學生公告 3: 11月20日舉辦媒合說明會。"
    ]
    return jsonify(announcements)


@app.route('/api/company/notification', methods=['GET'])
def api_company_notification():
    """提供廠商主頁的公告輪播資料 (由 vendor_visitor.html 呼叫)"""
    # 此處返回模擬的 JSON 資料
    announcements = [
        "✅ 廠商公告 1: 學生履歷審核已開放，請盡速查看。",
        "⚙️ 廠商公告 2: 請於本月底前確認職位需求。",
        "⚠️ 廠商公告 3: 系統將於下週進行維護。"
    ]
    return jsonify(announcements)

# ===============================================
# 3. 執行應用程式
# ===============================================
if __name__ == '__main__':
    # 在開發環境中運行
    app.run(debug=True)