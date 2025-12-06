import os
import base64
import traceback
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import get_db

# =========================================================
# Gmail API 設定
# =========================================================

# 是否啟用郵件功能
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() == "true"

# OAuth 與 token 路徑
CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.json")

# Gmail API 權限
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# 寄件人名稱與信箱
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "智慧實習平台")
SMTP_FROM_EMAIL = os.getenv("SMTP_USER", "")  # 假設 SMTP_USER 是你的 Gmail 地址

# =========================================================
# 建立 Gmail API Service
# =========================================================

def get_gmail_service():
    """建立 Gmail API Service，如果 credentials.json 不存在則拋出明確的錯誤"""
    if not os.path.exists(CREDENTIALS_PATH):
        raise FileNotFoundError(
            f"找不到 Gmail 認證檔案：{CREDENTIALS_PATH}。"
            f"請確認檔案存在於後端目錄中，或檢查 EMAIL.env 中的 GMAIL_CREDENTIALS_PATH 設定。"
        )
    
    creds = None
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception as e:
            print(f"⚠️ 讀取 token 檔案失敗: {e}")
            creds = None
    
    if not creds or not creds.valid:
        if not os.path.exists(CREDENTIALS_PATH):
            raise FileNotFoundError(
                f"找不到 Gmail 認證檔案：{CREDENTIALS_PATH}。"
                f"請確認檔案存在於後端目錄中。"
            )
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
        creds = flow.run_local_server(port=0)
        # 儲存 token
        with open(TOKEN_PATH, 'w') as token_file:
            token_file.write(creds.to_json())
    service = build('gmail', 'v1', credentials=creds)
    return service

# =========================================================
# 發送郵件 (Gmail API)
# =========================================================

def send_email(recipient_email, subject, content, related_user_id=None):
    """
    使用 Gmail API 發送郵件

    參數:
        recipient_email: 收件人信箱
        subject: 郵件主旨
        content: 郵件內容 (純文字)
        related_user_id: 可選，用來記錄 email_logs

    回傳:
        (success: bool, message: str, log_id: int 或 None)
    """
    if not EMAIL_ENABLED:
        print("⚠️ 郵件功能未啟用 (EMAIL_ENABLED=false)")
        return (False, "郵件功能未啟用", None)

    if not SMTP_FROM_EMAIL:
        print("⚠️ 寄件人信箱 (SMTP_FROM_EMAIL) 未設定")
        return (False, "寄件人信箱未設定", None)

    if not recipient_email:
        return (False, "收件人信箱為空", None)

    conn = None
    cursor = None
    log_id = None

    try:
        # 寫入 email_logs (pending)
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        # 檢查欄位名稱：可能是 recipient 或 recipient_email
        try:
            cursor.execute("""
                INSERT INTO email_logs (recipient_email, subject, content, related_user_id, status, sent_at)
                VALUES (%s, %s, %s, %s, 'pending', NOW())
            """, (recipient_email, subject, content, related_user_id))
        except Exception:
            # 如果失敗，嘗試使用 recipient 欄位名稱
            cursor.execute("""
                INSERT INTO email_logs (recipient, subject, content, related_user_id, status, sent_at)
                VALUES (%s, %s, %s, %s, 'pending', NOW())
            """, (recipient_email, subject, content, related_user_id))
        log_id = cursor.lastrowid
        conn.commit()

        # 建立 Gmail service
        service = get_gmail_service()

        # 建立郵件內容
        message = MIMEText(content, 'plain', 'utf-8')
        message['to'] = recipient_email
        message['from'] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
        message['subject'] = subject

        raw_bytes = message.as_bytes()
        raw_b64 = base64.urlsafe_b64encode(raw_bytes).decode()

        # 呼叫 Gmail API 寄信
        send_result = service.users().messages().send(
            userId='me',
            body={'raw': raw_b64}
        ).execute()

        # 更新 email_logs 成功
        cursor.execute("""
            UPDATE email_logs
            SET status = 'sent', sent_at = NOW()
            WHERE id = %s
        """, (log_id,))
        conn.commit()

        print(f"✅ 郵件發送成功: {recipient_email} - {subject} (Gmail API)")
        return (True, "郵件發送成功", log_id)

    except Exception as e:
        err = str(e)
        print(f"❌ 郵件發送失敗: {err}")
        traceback.print_exc()

        # 處理 FileNotFoundError，提供更友好的錯誤訊息
        if isinstance(e, FileNotFoundError) and 'credentials.json' in err:
            friendly_err = "Gmail 認證檔案未設定，請聯絡系統管理員設定郵件服務"
        elif 'credentials.json' in err:
            friendly_err = "Gmail 認證檔案設定錯誤，請聯絡系統管理員"
        else:
            friendly_err = err

        # 更新 email_logs 為失敗
        if conn and cursor and log_id:
            try:
                # 先嘗試更新 status 和 error_message
                try:
                    cursor.execute("""
                        UPDATE email_logs
                        SET status = 'failed', error_message = %s
                        WHERE id = %s
                    """, (friendly_err, log_id))
                except Exception:
                    # 如果 error_message 欄位不存在，只更新 status
                    cursor.execute("""
                        UPDATE email_logs
                        SET status = 'failed'
                        WHERE id = %s
                    """, (log_id,))
                conn.commit()
            except Exception as inner_e:
                print(f"⚠️ 更新記錄失敗: {inner_e}")

        return (False, friendly_err, log_id)

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# =========================================================
# 審核履歷通過/退件、實習錄取通知郵件
# =========================================================

def send_resume_approval_email(student_email, student_name, reviewer_name=""):
    subject = "【智慧實習平台】履歷審核通過通知"
    content = f"""
親愛的 {student_name} 同學：

您好！

您提交的履歷已由 {reviewer_name} 老師審核通過。
您現在可以繼續進行後續實習相關流程。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習平台
"""
    return send_email(student_email, subject, content)

def send_resume_rejection_email(student_email, student_name, reviewer_name, rejection_reason=""):
    subject = "【智慧實習平台】履歷退件通知"
    content = f"""
親愛的 {student_name} 同學：

您好！

您提交的履歷已被 {reviewer_name} 老師退件。

退件原因：
{rejection_reason if rejection_reason else '請登入系統查看老師的留言或聯絡老師。'}

請根據老師的建議修改履歷後，重新提交。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習平台
"""
    return send_email(student_email, subject, content)

def send_preference_rejection_email(student_email, student_name, reviewer_name, rejection_reason=""):
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
智慧實習平台
"""
    return send_email(student_email, subject, content)

def send_admission_email(student_email, student_name, company_name, teacher_name=""):
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

def send_interview_email(student_email, student_name, company_name, vendor_name="", custom_content=""):
    """
    發送面試通知郵件
    
    參數:
        student_email: 學生 Email
        student_name: 學生姓名
        company_name: 公司名稱
        vendor_name: 廠商姓名（可選）
        custom_content: 自訂通知內容（可選）
    
    回傳:
        (success: bool, message: str, log_id: int 或 None)
    """
    subject = "【智慧實習平台】面試通知"
    
    if custom_content:
        content = f"""
親愛的 {student_name} 同學：

您好！

{company_name} 邀請您參加面試。

{f'聯絡人：{vendor_name}' if vendor_name else ''}

面試相關資訊：
{custom_content}

請您準備相關資料，並準時參加面試。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習平台
"""
    else:
        content = f"""
親愛的 {student_name} 同學：

您好！

{company_name} 邀請您參加面試。

{f'聯絡人：{vendor_name}' if vendor_name else ''}

請您準備相關資料，並準時參加面試。詳細面試時間與地點將另行通知。

如有任何疑問，請聯絡您的班導師或系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習平台
"""
    
    return send_email(student_email, subject, content)



