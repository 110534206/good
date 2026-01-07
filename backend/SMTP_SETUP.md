# SMTP 郵件設定說明（使用應用密碼）

## 優點
- ✅ **更簡單**：不需要下載 `credentials.json` 文件
- ✅ **不需要 OAuth 2.0 授權流程**
- ✅ **設定快速**：只需取得應用密碼即可

## 設定步驟

### 1. 啟用兩步驟驗證
1. 前往 Google 帳戶安全設定：https://myaccount.google.com/security
2. 如果尚未啟用，請先啟用「兩步驟驗證」

### 2. 建立應用程式密碼
1. 前往「應用程式密碼」頁面：https://myaccount.google.com/apppasswords
2. 點擊「選擇應用程式」→ 選擇「郵件」
3. 點擊「選擇裝置」→ 選擇「其他（自訂名稱）」
4. 輸入名稱（例如：智慧實習平台）
5. 點擊「產生」
6. **複製 16 位數字的應用程式密碼**（格式：xxxx xxxx xxxx xxxx，去除空格）

### 3. 設定 EMAIL.env
編輯 `c:\Featured\good\backend\EMAIL.env` 文件：

```env
# 是否啟用郵件功能
EMAIL_ENABLED="true"

# 郵件發送方式：'smtp' 或 'gmail_api'
EMAIL_METHOD="smtp"

# SMTP 設定
SMTP_USER="si.pingtai@gmail.com"        # 你的 Gmail 信箱
SMTP_PASSWORD="你的16位應用密碼"        # 從步驟 2 取得的應用密碼（不含空格）
SMTP_FROM_NAME="智慧實習平台"           # 寄件人名稱
SMTP_HOST="smtp.gmail.com"              # Gmail SMTP 伺服器
SMTP_PORT="587"                          # Gmail SMTP 端口
```

**重要**：將 `SMTP_PASSWORD` 設為你從 Google 取得的 16 位應用密碼（不含空格）

### 4. 重啟 Flask 應用程式
設定完成後，重啟 Flask 應用程式使設定生效。

## 測試
1. 在後台建立新用戶（確保填寫了 email）
2. 檢查伺服器日誌，應該看到：
   ```
   📧 嘗試發送郵件到: [email]
      發送方式: smtp
   ✅ 郵件發送成功 (SMTP): [email] - [主旨]
   ```
3. 檢查用戶的郵箱，應該收到包含帳號密碼的郵件

## 疑難排解

### 問題：SMTP 應用密碼未設定
- 確認 `EMAIL.env` 中的 `SMTP_PASSWORD` 已正確設定
- 確認應用密碼是 16 位數字（不含空格）

### 問題：認證失敗
- 確認應用密碼正確（複製時不要包含空格）
- 確認 Gmail 帳號已啟用兩步驟驗證
- 確認 `SMTP_USER` 設定正確

### 問題：連接失敗
- 確認網路連線正常
- 確認防火牆允許連接到 `smtp.gmail.com:587`

## 切換回 Gmail API 方式
如果之後想使用 Gmail API 方式（需要 `credentials.json`），只需修改 `EMAIL.env`：

```env
EMAIL_METHOD="gmail_api"
```

然後按照 `GMAIL_API_SETUP.md` 的說明設定 OAuth 2.0。


