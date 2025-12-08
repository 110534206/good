# Email 功能設定指南

## 📧 快速開始

### 步驟 1：複製設定檔範本

```bash
cd backend
cp EMAIL.env.example EMAIL.env
```

### 步驟 2：設定 Gmail 應用程式密碼

1. 前往：https://myaccount.google.com/apppasswords
2. 選擇「郵件」應用程式
3. 選擇「其他（自訂名稱）」，輸入「智慧實習平台」
4. 點擊「建立」
5. 複製 16 位應用程式密碼

### 步驟 3：填入設定

編輯 `EMAIL.env` 檔案：

```env
SMTP_USER="your-email@gmail.com"        # 填入您的 Gmail 地址
SMTP_PASSWORD="your-app-password"       # 填入剛才複製的應用程式密碼
```

### 步驟 4：測試 Email 功能

```bash
python test_email_simple.py your-email@example.com
```

## 🔧 常見問題

### 問題 1：連線超時

**錯誤訊息**：`SMTP 連線超時:無法連線到郵件伺服器`

**解決方案**：
1. 檢查防火牆設定（允許 Python 和連接埠 587）
2. 執行 `python test_smtp_network.py` 診斷連線問題
3. 如果持續無法連線，考慮改用 Gmail API

### 問題 2：認證失敗

**錯誤訊息**：`SMTP 認證失敗`

**解決方案**：
1. 確認已啟用兩步驟驗證
2. 確認應用程式密碼正確（16 位）
3. 重新產生應用程式密碼

### 問題 3：網路環境阻擋

**情況**：公司/學校網路可能阻擋 SMTP 連線

**解決方案**：
1. 使用手機熱點測試
2. 改用 Gmail API（設定 `USE_SMTP="false"`）
3. 聯絡 IT 管理員開放連接埠 587

## 📝 兩種發送方式

### 方式 1：SMTP（推薦，簡單）

**優點**：
- 設定簡單
- 不需要 Google Cloud 專案
- 適合個人使用

**缺點**：
- 可能被防火牆阻擋
- 需要應用程式密碼

**設定**：
```env
USE_SMTP="true"
SMTP_USER="your-email@gmail.com"
SMTP_PASSWORD="your-app-password"
```

### 方式 2：Gmail API（更可靠）

**優點**：
- 不受防火牆影響
- 更安全
- 適合企業環境

**缺點**：
- 需要設定 Google Cloud 專案
- 需要 credentials.json

**設定**：
```env
USE_SMTP="false"
# 需要下載 credentials.json 並放在 backend/ 目錄
```

## 🛠️ 診斷工具

### 檢查設定
```bash
python check_email_config.py
```

### 測試網路連線
```bash
python test_smtp_network.py
```

### 測試 SMTP 連線
```bash
python test_smtp_connection.py
```

### 測試 Email 發送
```bash
python test_email_simple.py recipient@example.com
```

## ⚠️ 重要提醒

1. **不要將 `EMAIL.env` 推送到 GitHub**（已加入 .gitignore）
2. **應用程式密碼是敏感資訊**，請妥善保管
3. **如果更換 Gmail 帳號**，需要重新產生應用程式密碼
4. **如果連線持續失敗**，建議改用 Gmail API

## 📚 相關文件

- `README_SMTP_FIX.md` - SMTP 連線問題排除
- `SMTP_TROUBLESHOOTING.md` - 詳細故障排除指南

## 💡 需要幫助？

如果遇到問題：
1. 檢查 `README_SMTP_FIX.md`
2. 執行診斷工具
3. 查看錯誤訊息中的詳細說明

