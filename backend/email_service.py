import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import get_db
from datetime import datetime
import traceback
import os

# =========================================================
# 郵件設定
# =========================================================
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USER)
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "智慧實習平台")

# 是否啟用郵件功能
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() == "true"

# =========================================================
# 發送郵件
# =========================================================
def send_email(recipient_email, subject, content, related_user_id=None):
    """
    發送郵件
    
    參數:
        recipient_email: 收件人信箱
        subject: 郵件主旨
        content: 郵件內容（純文字）
        related_user_id: 相關使用者 ID（可選，用於記錄到 email_logs）
    
    回傳:
        tuple: (success: bool, message: str, log_id: int 或 None)
    """
    if not EMAIL_ENABLED:
        print("⚠️ 郵件功能未啟用（EMAIL_ENABLED=false）")
        return (False, "郵件功能未啟用", None)
    
    if not SMTP_USER or not SMTP_PASSWORD:
        print("⚠️ SMTP 設定不完整，無法發送郵件")
        return (False, "SMTP 設定不完整", None)
    
    if not recipient_email:
        return (False, "收件人信箱為空", None)
    
    conn = None
    cursor = None
    log_id = None
    
    try:
        # 1. 寫入郵件記錄（發送前）
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            INSERT INTO email_logs (recipient_email, subject, content, related_user_id, status, sent_at)
            VALUES (%s, %s, %s, %s, 'pending', NOW())
        """, (recipient_email, subject, content, related_user_id))
        log_id = cursor.lastrowid
        conn.commit()
        
        # 2. 發送郵件
        msg = MIMEMultipart()
        msg['From'] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
        msg['To'] = recipient_email
        msg['Subject'] = subject
        
        # 郵件內容（純文字）
        msg.attach(MIMEText(content, 'plain', 'utf-8'))
        
        # 連線至 SMTP 伺服器並發送
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()  # 啟用 TLS 加密
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        # 3. 更新郵件記錄狀態為成功
        cursor.execute("""
            UPDATE email_logs 
            SET status = 'sent', sent_at = NOW()
            WHERE id = %s
        """, (log_id,))
        conn.commit()
        
        print(f"✅ 郵件發送成功: {recipient_email} - {subject}")
        return (True, "郵件發送成功", log_id)
    
    except Exception as e:
        error_msg = str(e)
        print(f"❌ 郵件發送失敗: {error_msg}")
        traceback.print_exc()
        
        # 更新郵件記錄狀態為失敗
        if conn and cursor and log_id:
            try:
                # 若資料表中尚未有 error_message 欄位，請先執行以下 SQL：
                # ALTER TABLE email_logs ADD COLUMN error_message TEXT NULL;
                cursor.execute("""
                    UPDATE email_logs 
                    SET status = 'failed', error_message = %s
                    WHERE id = %s
                """, (error_msg, log_id))
                conn.commit()
            except Exception as e2:
                print(f"⚠️ 更新郵件記錄失敗: {e2}")
        
        return (False, f"郵件發送失敗: {error_msg}", log_id)
    
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# =========================================================
# 發送履歷審核通過通知郵件
# =========================================================
def send_resume_approval_email(student_email, student_name, reviewer_name):
    """
    發送履歷審核通過通知郵件
    """
    subject = "【智慧實習平台】履歷審核通過通知"
    content = f"""
親愛的 {student_name} 同學：

您好！

恭喜您！您的履歷已由 {reviewer_name} 老師審核通過。

您現在可以繼續進行後續的實習志願選填等步驟。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習平台
"""
    return send_email(student_email, subject, content)


# =========================================================
# 發送履歷退件通知郵件
# =========================================================
def send_resume_rejection_email(student_email, student_name, reviewer_name, rejection_reason=""):
    """
    發送履歷退件通知郵件
    
    參數:
        student_email: 學生信箱
        student_name: 學生姓名
        reviewer_name: 審核老師姓名
        rejection_reason: 退件原因
    
    回傳:
        tuple: (success: bool, message: str)
    """
    subject = "【智慧實習平台】履歷退件通知"
    content = f"""
親愛的 {student_name} 同學：

您好！

您的履歷已被 {reviewer_name} 老師退件。

退件原因：
{rejection_reason if rejection_reason else '請查看系統通知或聯絡老師'}

請依照老師的建議修改履歷後，重新上傳。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習系統
"""
    
    return send_email(student_email, subject, content)

# =========================================================
# 發送志願序退件通知郵件
# =========================================================
def send_preference_rejection_email(student_email, student_name, reviewer_name, rejection_reason=""):
    """
    發送志願序退件通知郵件
    
    參數:
        student_email: 學生信箱
        student_name: 學生姓名
        reviewer_name: 審核老師姓名
        rejection_reason: 退件原因
    
    回傳:
        tuple: (success: bool, message: str)
    """
    subject = "【智慧實習平台】志願序退件通知"
    content = f"""
親愛的 {student_name} 同學：

您好！

您的實習志願序已被 {reviewer_name} 老師退件。

退件原因：
{rejection_reason if rejection_reason else '請查看系統通知或聯絡老師'}

請依照老師的建議修改志願序後，重新提交。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習系統
"""
    
    return send_email(student_email, subject, content)

# =========================================================
# 發送錄取通知郵件
# =========================================================
def send_admission_email(student_email, student_name, company_name, teacher_name=""):
    """
    發送錄取通知郵件
    
    參數:
        student_email: 學生信箱
        student_name: 學生姓名
        company_name: 公司名稱
        teacher_name: 指導老師姓名（可選）
    
    回傳:
        tuple: (success: bool, message: str)
    """
    subject = "【智慧實習平台】實習錄取通知"
    content = f"""
親愛的 {student_name} 同學：

恭喜您！

您已被 {company_name} 錄取。

{f'您的指導老師為：{teacher_name}。' if teacher_name else ''}

請登入系統查看詳細資訊，並與指導老師聯繫後續實習事宜。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習平台
"""
    
    return send_email(student_email, subject, content)


