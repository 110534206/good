from flask import Flask, redirect, url_for, session
from flask_cors import CORS
from jinja2 import ChoiceLoader, FileSystemLoader
from dotenv import load_dotenv
import os

# -------------------------
# 建立 Flask app
# -------------------------
load_dotenv(dotenv_path='GEMINI_API_KEY.env')
load_dotenv(dotenv_path='EMAIL.env')
app = Flask(
    __name__,
    static_folder='../frontend/static',
    template_folder='../frontend/templates'
)

# secret_key 與檔案設定
app.secret_key = os.getenv("SECRET_KEY", "your_secret_key")
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# CORS
CORS(app, supports_credentials=True)

# -------------------------
# Jinja2 載入前台 + 管理員模板
# -------------------------
app.jinja_loader = ChoiceLoader([
    app.jinja_loader,
    FileSystemLoader('../admin_frontend/templates')
])

# -------------------------
# 載入 Blueprint
# -------------------------
from auth import auth_bp
from company import company_bp
from resume import resume_bp
from admin import admin_bp
from users import users_bp
from notification import notification_bp
from preferences import preferences_bp
from announcement import announcement_bp
from intern_exp import intern_exp_bp 
from ai_tools import ai_bp
from semester import semester_bp
from admission import admission_bp
from director_overview import director_overview_bp
from ta_statistics import ta_statistics_bp
from student_results import student_results_bp

# 註冊 Blueprint
app.register_blueprint(auth_bp)
app.register_blueprint(company_bp)
app.register_blueprint(resume_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(users_bp)
app.register_blueprint(notification_bp)
app.register_blueprint(preferences_bp)
app.register_blueprint(announcement_bp, url_prefix="/announcement")
app.register_blueprint(intern_exp_bp)
app.register_blueprint(ai_bp)
app.register_blueprint(semester_bp)
app.register_blueprint(admission_bp)
app.register_blueprint(director_overview_bp)
app.register_blueprint(ta_statistics_bp, url_prefix='/ta/statistics')
app.register_blueprint(student_results_bp)

# -------------------------
# 首頁路由（使用者前台）
# -------------------------
@app.route("/")
def index():
    if "username" in session and session.get("role") == "student":
        return redirect(url_for("users_bp.student_home")) 
    return redirect(url_for("auth_bp.login_page"))

# -------------------------
# 管理員首頁（後台）
# -------------------------
@app.route("/admin")
def admin_index():
    if "username" in session and session.get("role") == "admin":
        return redirect(url_for("admin_bp.admin_home"))
    return redirect(url_for("auth_bp.login_page"))

# -------------------------
# 主程式入口
# -------------------------
if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=5000, debug=True)
    except (KeyboardInterrupt, SystemExit):
        pass  # No scheduler to shut down
