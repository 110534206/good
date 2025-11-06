"""
邮件服务模块
用于发送系统通知邮件（履历退件、志愿序退件等）
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import get_db
from datetime import datetime
import traceback
import os

# =========================================================
# 邮件配置（从环境变量读取）
# =========================================================
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USER)
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "智慧實習系統")

# 是否启用邮件功能
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() == "true"

# =========================================================
# 发送邮件
# =========================================================
def send_email(recipient_email, subject, content, related_user_id=None):
    """
    发送邮件
    
    Args:
        recipient_email: 收件人邮箱
        subject: 邮件主题
        content: 邮件内容（纯文本）
        related_user_id: 相关用户ID（可选，用于记录到 email_logs）
    
    Returns:
        tuple: (success: bool, message: str, log_id: int or None)
    """
    if not EMAIL_ENABLED:
        print("⚠️ 邮件功能未启用（EMAIL_ENABLED=false）")
        return (False, "邮件功能未启用", None)
    
    if not SMTP_USER or not SMTP_PASSWORD:
        print("⚠️ SMTP 配置不完整，无法发送邮件")
        return (False, "SMTP 配置不完整", None)
    
    if not recipient_email:
        return (False, "收件人邮箱为空", None)
    
    conn = None
    cursor = None
    log_id = None
    
    try:
        # 1. 记录邮件日志（发送前）
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            INSERT INTO email_logs (recipient, subject, content, related_user_id, status, sent_at)
            VALUES (%s, %s, %s, %s, 'pending', NOW())
        """, (recipient_email, subject, content, related_user_id))
        log_id = cursor.lastrowid
        conn.commit()
        
        # 2. 发送邮件
        msg = MIMEMultipart()
        msg['From'] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
        msg['To'] = recipient_email
        msg['Subject'] = subject
        
        # 邮件正文（纯文本）
        msg.attach(MIMEText(content, 'plain', 'utf-8'))
        
        # 连接 SMTP 服务器并发送
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()  # 启用 TLS
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        # 3. 更新邮件日志状态为成功
        cursor.execute("""
            UPDATE email_logs 
            SET status = 'sent', sent_at = NOW()
            WHERE id = %s
        """, (log_id,))
        conn.commit()
        
        print(f"✅ 邮件发送成功: {recipient_email} - {subject}")
        return (True, "邮件发送成功", log_id)
    
    except Exception as e:
        error_msg = str(e)
        print(f"❌ 邮件发送失败: {error_msg}")
        traceback.print_exc()
        
        # 更新邮件日志状态为失败
        if conn and cursor and log_id:
            try:
                cursor.execute("""
                    UPDATE email_logs 
                    SET status = 'failed', error_message = %s
                    WHERE id = %s
                """, (error_msg, log_id))
                conn.commit()
            except Exception as e2:
                print(f"⚠️ 更新邮件日志失败: {e2}")
        
        return (False, f"邮件发送失败: {error_msg}", log_id)
    
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# =========================================================
# 发送履历退件通知邮件
# =========================================================
def send_resume_rejection_email(student_email, student_name, reviewer_name, rejection_reason=""):
    """
    发送履历退件通知邮件
    
    Args:
        student_email: 学生邮箱
        student_name: 学生姓名
        reviewer_name: 审核者姓名
        rejection_reason: 退件原因
    
    Returns:
        tuple: (success: bool, message: str)
    """
    subject = "【智慧實習系統】履歷退件通知"
    content = f"""
親愛的 {student_name} 同學：

您好！

您的履歷已被 {reviewer_name} 老師退件。

退件原因：
{rejection_reason if rejection_reason else '請查看系統通知或聯絡老師'}

請根據老師的建議修改履歷後，重新上傳履歷。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習系統
"""
    
    return send_email(student_email, subject, content)

# =========================================================
# 发送志愿序退件通知邮件
# =========================================================
def send_preference_rejection_email(student_email, student_name, reviewer_name, rejection_reason=""):
    """
    发送志愿序退件通知邮件
    
    Args:
        student_email: 学生邮箱
        student_name: 学生姓名
        reviewer_name: 审核者姓名
        rejection_reason: 退件原因
    
    Returns:
        tuple: (success: bool, message: str)
    """
    subject = "【智慧實習系統】志願序退件通知"
    content = f"""
親愛的 {student_name} 同學：

您好！

您的實習志願序已被 {reviewer_name} 老師退件。

退件原因：
{rejection_reason if rejection_reason else '請查看系統通知或聯絡老師'}

請根據老師的建議修改志願序後，重新提交志願序。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習系統
"""
    
    return send_email(student_email, subject, content)

# =========================================================
# 发送录取通知邮件（可选）
# =========================================================
def send_admission_email(student_email, student_name, company_name, teacher_name=""):
    """
    发送录取通知邮件
    
    Args:
        student_email: 学生邮箱
        student_name: 学生姓名
        company_name: 公司名称
        teacher_name: 指导老师姓名（可选）
    
    Returns:
        tuple: (success: bool, message: str)
    """
    subject = "【智慧實習系統】實習錄取通知"
    content = f"""
親愛的 {student_name} 同學：

恭喜您！

您已被 {company_name} 錄取。

{f'您的指導老師為：{teacher_name}。' if teacher_name else ''}

請登入系統查看詳細資訊，並與指導老師聯繫後續實習事宜。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習系統
"""
    
    return send_email(student_email, subject, content)

