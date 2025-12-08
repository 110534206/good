# Gmail API 設定說明

## 問題
如果遇到以下錯誤訊息：
```
找不到 Gmail 認證檔案：credentials.json
```

## 解決步驟

### 1. 前往 Google Cloud Console
訪問：https://console.cloud.google.com/

### 2. 建立或選擇專案
- 點擊頂部專案選擇器
- 建立新專案或選擇現有專案

### 3. 啟用 Gmail API
1. 在左側選單選擇「API 和服務」>「程式庫」
2. 搜尋「Gmail API」
3. 點擊「Gmail API」
4. 點擊「啟用」

### 4. 建立 OAuth 2.0 憑證
1. 前往「API 和服務」>「憑證」
2. 點擊「建立憑證」>「OAuth 用戶端 ID」
3. 如果尚未設定 OAuth 同意畫面，系統會提示您先設定：
   - 選擇「外部」
   - 填寫應用程式名稱（例如：智慧實習平台）
   - 填寫支援電子郵件
   - 儲存並繼續
4. 建立 OAuth 用戶端 ID：
   - **應用程式類型**：選擇「桌面應用程式」
   - **名稱**：輸入任意名稱（例如：智慧實習平台郵件服務）
   - 點擊「建立」
5. 下載憑證：
   - 點擊下載按鈕（JSON 格式）
   - 將下載的檔案重新命名為 `credentials.json`

### 5. 放置憑證檔案
將 `credentials.json` 放置在後端目錄：
```
c:\Featured\good\backend\credentials.json
```

### 6. 第一次授權
1. 重新啟動 Flask 應用程式
2. 當系統嘗試發送第一封郵件時，會自動開啟瀏覽器
3. 使用 Gmail 帳號登入並授權
4. 系統會自動產生 `token.json` 檔案

### 7. 驗證設定
確認以下檔案存在於 `c:\Featured\good\backend\` 目錄：
- ✅ `credentials.json`（從 Google Cloud Console 下載）
- ✅ `token.json`（第一次授權後自動產生）
- ✅ `EMAIL.env`（已設定 EMAIL_ENABLED="true"）

## 注意事項

1. **安全性**：
   - 不要將 `credentials.json` 和 `token.json` 上傳到公開的程式碼庫
   - 將這些檔案加入 `.gitignore`

2. **token 過期**：
   - 如果 `token.json` 過期，系統會自動重新授權
   - 可能需要重新執行授權流程

3. **測試**：
   - 設定完成後，可以透過後台建立新用戶來測試郵件發送功能
   - 檢查伺服器日誌確認郵件是否成功發送

## 疑難排解

### 問題：授權後仍無法發送郵件
- 檢查 `EMAIL.env` 中的 `EMAIL_ENABLED` 是否設為 `"true"`
- 檢查 `SMTP_USER` 是否設定正確的 Gmail 地址
- 檢查伺服器日誌中的錯誤訊息

### 問題：找不到 credentials.json
- 確認檔案名稱完全為 `credentials.json`（不含其他字元）
- 確認檔案位於正確的目錄：`c:\Featured\good\backend\`
- 檢查 `EMAIL.env` 中的 `GMAIL_CREDENTIALS_PATH` 設定

### 問題：token.json 權限錯誤
- 確認應用程式有讀寫 `token.json` 的權限
- 如果權限不足，刪除 `token.json` 讓系統重新產生
