import os
import base64
import traceback
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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
SMTP_FROM_EMAIL = os.getenv("SMTP_USER", "")  # Gmail 信箱

# SMTP 設定（使用應用密碼方式，更簡單）
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").replace(" ", "")  # Gmail 應用密碼（16位數字，自動去除空格）
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")  # Gmail SMTP 伺服器
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))  # Gmail SMTP 端口（587 for TLS）

# 郵件發送方式：'smtp' 或 'gmail_api'
EMAIL_METHOD = os.getenv("EMAIL_METHOD", "smtp").lower()  # 預設使用 SMTP

# 調試：打印郵件配置狀態（僅在模組載入時打印一次）
def _print_email_config():
    """打印郵件配置狀態（用於調試）"""
    print("=" * 50)
    print("📧 郵件服務配置檢查：")
    print(f"  EMAIL_ENABLED: {EMAIL_ENABLED}")
    print(f"  EMAIL_METHOD: {EMAIL_METHOD}")
    print(f"  SMTP_FROM_EMAIL: {SMTP_FROM_EMAIL}")
    print(f"  SMTP_FROM_NAME: {SMTP_FROM_NAME}")
    if EMAIL_METHOD == "smtp":
        print(f"  SMTP_PASSWORD: {'已設定' if SMTP_PASSWORD else '未設定'}")
        print(f"  SMTP_HOST: {SMTP_HOST}")
        print(f"  SMTP_PORT: {SMTP_PORT}")
    else:
        print(f"  CREDENTIALS_PATH: {CREDENTIALS_PATH} (存在: {os.path.exists(CREDENTIALS_PATH)})")
        print(f"  TOKEN_PATH: {TOKEN_PATH} (存在: {os.path.exists(TOKEN_PATH)})")
    print("=" * 50)

# 在模組載入時打印配置（僅在開發環境或需要調試時）
# 可以通過環境變數控制是否打印
if os.getenv("DEBUG_EMAIL_CONFIG", "false").lower() == "true":
    _print_email_config()

# =========================================================
# 建立 Gmail API Service
# =========================================================

def get_gmail_service():
    """建立 Gmail API Service，如果 credentials.json 不存在則拋出明確的錯誤"""
    # 取得當前檔案所在目錄
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 檢查檔案路徑是否為絕對路徑或相對路徑
    credentials_full_path = CREDENTIALS_PATH
    if not os.path.isabs(CREDENTIALS_PATH):
        # 如果是相對路徑，相對於 email_service.py 所在目錄
        credentials_full_path = os.path.join(current_dir, CREDENTIALS_PATH)
    
    if not os.path.exists(credentials_full_path):
        error_msg = (
            f"找不到 Gmail 認證檔案：{credentials_full_path}\n"
            f"請按照以下步驟設定 Gmail API：\n"
            f"1. 前往 Google Cloud Console (https://console.cloud.google.com/)\n"
            f"2. 建立或選擇專案\n"
            f"3. 啟用 Gmail API\n"
            f"4. 建立 OAuth 2.0 客戶端 ID（應用程式類型：桌面應用程式）\n"
            f"5. 下載憑證檔案並重新命名為 'credentials.json'\n"
            f"6. 將 credentials.json 放置在：{current_dir}\n"
            f"7. 第一次執行時，系統會自動開啟瀏覽器進行授權，並產生 token.json\n"
            f"\n詳細說明請參考：{current_dir}/GMAIL_API_SETUP.md"
        )
        raise FileNotFoundError(error_msg)
    
    # 使用完整路徑
    token_full_path = TOKEN_PATH
    if not os.path.isabs(TOKEN_PATH):
        token_full_path = os.path.join(current_dir, TOKEN_PATH)
    
    creds = None
    if os.path.exists(token_full_path):
        try:
            creds = Credentials.from_authorized_user_file(token_full_path, SCOPES)
        except Exception as e:
            print(f"⚠️ 讀取 token 檔案失敗: {e}")
            creds = None
    
    if not creds or not creds.valid:
        if not os.path.exists(credentials_full_path):
            raise FileNotFoundError(
                f"找不到 Gmail 認證檔案：{credentials_full_path}。"
                f"請確認檔案存在於後端目錄中。"
            )
        flow = InstalledAppFlow.from_client_secrets_file(credentials_full_path, SCOPES)
        creds = flow.run_local_server(port=0)
        # 儲存 token
        with open(token_full_path, 'w') as token_file:
            token_file.write(creds.to_json())
        print(f"✅ Gmail API token 已儲存至：{token_full_path}")
    service = build('gmail', 'v1', credentials=creds)
    return service

# =========================================================
# 發送郵件 (SMTP 方式 - 使用應用密碼)
# =========================================================

def send_email_smtp(recipient_email, subject, content):
    """
    使用 SMTP 發送郵件（使用應用密碼方式）
    
    參數:
        recipient_email: 收件人信箱
        subject: 郵件主旨
        content: 郵件內容 (純文字)
    
    回傳:
        (success: bool, message: str)
    """
    try:
        # 建立郵件
        msg = MIMEMultipart()
        msg['From'] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
        msg['To'] = recipient_email
        msg['Subject'] = subject
        
        # 添加郵件內容
        msg.attach(MIMEText(content, 'plain', 'utf-8'))
        
        print(f"📧 正在連接到 SMTP 伺服器: {SMTP_HOST}:{SMTP_PORT}")
        
        # 連接到 SMTP 伺服器並發送（添加超時設定）
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            print(f"✅ 已連接到 SMTP 伺服器")
            server.starttls()  # 啟用 TLS
            print(f"✅ TLS 已啟用")
            print(f"📧 正在登入...")
            server.login(SMTP_FROM_EMAIL, SMTP_PASSWORD)
            print(f"✅ 登入成功")
            print(f"📧 正在發送郵件...")
            server.send_message(msg)
            print(f"✅ 郵件已送出")
        
        print(f"✅ 郵件發送成功 (SMTP): {recipient_email} - {subject}")
        return (True, "郵件發送成功")
    except smtplib.SMTPException as e:
        err = str(e)
        print(f"❌ SMTP 錯誤: {err}")
        traceback.print_exc()
        return (False, f"SMTP 錯誤: {err}")
    except OSError as e:
        err = str(e)
        error_code = getattr(e, 'winerror', None) or getattr(e, 'errno', None)
        if error_code == 11001 or 'getaddrinfo failed' in err:
            friendly_err = (
                f"無法連接到 SMTP 伺服器 {SMTP_HOST}:{SMTP_PORT}。\n"
                f"可能的原因：\n"
                f"1. 網路連線問題（請檢查網路連線）\n"
                f"2. 防火牆阻止連接（請檢查防火牆設定）\n"
                f"3. DNS 解析失敗（請檢查 DNS 設定）\n"
                f"4. 需要使用代理伺服器\n"
                f"請確認網路連線正常，並檢查防火牆設定。"
            )
            print(f"❌ {friendly_err}")
            traceback.print_exc()
            return (False, friendly_err)
        else:
            print(f"❌ 網路錯誤: {err}")
            traceback.print_exc()
            return (False, f"網路錯誤: {err}")
    except Exception as e:
        err = str(e)
        print(f"❌ SMTP 郵件發送失敗: {err}")
        traceback.print_exc()
        return (False, f"SMTP 發送失敗: {err}")

# =========================================================
# 發送郵件 (Gmail API 方式 - OAuth 2.0)
# =========================================================

def send_email_gmail_api(recipient_email, subject, content, related_user_id=None):
    """
    使用 Gmail API 發送郵件（OAuth 2.0 方式）

    參數:
        recipient_email: 收件人信箱
        subject: 郵件主旨
        content: 郵件內容 (純文字)
        related_user_id: 可選，用來記錄 email_logs

    回傳:
        (success: bool, message: str, log_id: int 或 None)
    """
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
        print(f"📧 正在建立 Gmail API 服務...")
        print(f"   CREDENTIALS_PATH: {CREDENTIALS_PATH}")
        print(f"   TOKEN_PATH: {TOKEN_PATH}")
        service = get_gmail_service()
        print(f"✅ Gmail API 服務建立成功")

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
# 發送郵件 (主函數 - 自動選擇方式)
# =========================================================

def send_email(recipient_email, subject, content, related_user_id=None):
    """
    發送郵件（自動選擇 SMTP 或 Gmail API 方式）

    參數:
        recipient_email: 收件人信箱
        subject: 郵件主旨
        content: 郵件內容 (純文字)
        related_user_id: 可選，用來記錄 email_logs

    回傳:
        (success: bool, message: str, log_id: int 或 None)
    """
    # 調試：打印當前配置狀態
    print(f"📧 嘗試發送郵件到: {recipient_email}")
    print(f"   主旨: {subject}")
    print(f"   發送方式: {EMAIL_METHOD}")
    print(f"   EMAIL_ENABLED: {EMAIL_ENABLED}")
    print(f"   SMTP_FROM_EMAIL: {SMTP_FROM_EMAIL}")
    
    if not EMAIL_ENABLED:
        print("⚠️ 郵件功能未啟用 (EMAIL_ENABLED=false)")
        print(f"   環境變數 EMAIL_ENABLED 的值: {os.getenv('EMAIL_ENABLED', '未設定')}")
        return (False, "郵件功能未啟用", None)

    if not SMTP_FROM_EMAIL:
        print("⚠️ 寄件人信箱 (SMTP_FROM_EMAIL) 未設定")
        print(f"   環境變數 SMTP_USER 的值: {os.getenv('SMTP_USER', '未設定')}")
        return (False, "寄件人信箱未設定", None)

    if not recipient_email:
        print("⚠️ 收件人信箱為空")
        return (False, "收件人信箱為空", None)
    
    # 根據 EMAIL_METHOD 選擇發送方式
    if EMAIL_METHOD == "smtp":
        # 使用 SMTP 方式（需要應用密碼）
        if not SMTP_PASSWORD:
            error_msg = (
                "SMTP 應用密碼未設定。\n"
                "請按照以下步驟設定：\n"
                "1. 前往 Google 帳戶安全設定：https://myaccount.google.com/security\n"
                "2. 啟用兩步驟驗證（如果尚未啟用）\n"
                "3. 前往「應用程式密碼」頁面：https://myaccount.google.com/apppasswords\n"
                "4. 建立新的應用程式密碼（選擇「郵件」和「其他（自訂名稱）」）\n"
                "5. 複製 16 位數字的應用程式密碼\n"
                "6. 在 EMAIL.env 中設定：SMTP_PASSWORD=\"你的16位應用密碼\""
            )
            print(f"⚠️ {error_msg}")
            return (False, "SMTP 應用密碼未設定", None)
        
        # 使用 SMTP 發送
        conn = None
        cursor = None
        log_id = None
        
        try:
            # 寫入 email_logs (pending)
            conn = get_db()
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute("""
                    INSERT INTO email_logs (recipient_email, subject, content, related_user_id, status, sent_at)
                    VALUES (%s, %s, %s, %s, 'pending', NOW())
                """, (recipient_email, subject, content, related_user_id))
            except Exception:
                cursor.execute("""
                    INSERT INTO email_logs (recipient, subject, content, related_user_id, status, sent_at)
                    VALUES (%s, %s, %s, %s, 'pending', NOW())
                """, (recipient_email, subject, content, related_user_id))
            log_id = cursor.lastrowid
            conn.commit()
            
            # 發送郵件
            success, message = send_email_smtp(recipient_email, subject, content)
            
            # 更新 email_logs
            if success:
                cursor.execute("""
                    UPDATE email_logs
                    SET status = 'sent', sent_at = NOW()
                    WHERE id = %s
                """, (log_id,))
            else:
                try:
                    cursor.execute("""
                        UPDATE email_logs
                        SET status = 'failed', error_message = %s
                        WHERE id = %s
                    """, (message, log_id))
                except Exception:
                    cursor.execute("""
                        UPDATE email_logs
                        SET status = 'failed'
                        WHERE id = %s
                    """, (log_id,))
            conn.commit()
            
            return (success, message, log_id)
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"❌ 資料庫操作失敗: {e}")
            traceback.print_exc()
            return (False, f"資料庫操作失敗: {str(e)}", log_id)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    else:
        # 使用 Gmail API 方式（需要 credentials.json）
        return send_email_gmail_api(recipient_email, subject, content, related_user_id)

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

def send_account_creation_email(recipient_email, recipient_name, username, password):
    """
    發送帳號建立通知郵件（包含預設帳號密碼）
    
    參數:
        recipient_email: 收件人信箱
        recipient_name: 收件人姓名
        username: 預設帳號
        password: 預設密碼
    
    回傳:
        (success: bool, message: str, log_id: int 或 None)
    """
    subject = "【智慧實習平台】帳號建立通知"
    content = f"""
親愛的 {recipient_name}：

您好！

您的智慧實習平台帳號已建立完成。

【登入資訊】
帳號：{username}
密碼：{password}

【重要提醒】
1. 請使用上述帳號密碼登入系統
2. 登入後，您可以修改帳號和密碼（帳號只能修改一次）
3. 為了帳號安全，建議您盡快修改密碼

登入網址：請聯絡系統管理員取得

如有任何疑問，請聯絡系統管理員。

此為系統自動發送，請勿直接回覆此郵件。

--
智慧實習平台
"""
    return send_email(recipient_email, subject, content)


