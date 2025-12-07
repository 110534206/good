# 🚀 Email 功能快速開始（其他電腦）

## 3 步驟完成設定

### 步驟 1：執行設定腳本

```bash
cd backend
python setup_email.py
```

或手動複製：
```bash
cp EMAIL.env.example EMAIL.env
```

### 步驟 2：取得 Gmail 應用程式密碼

1. 前往：https://myaccount.google.com/apppasswords
2. 選擇「郵件」→「其他（自訂名稱）」→ 輸入「智慧實習平台」
3. 點擊「建立」
4. 複製 16 位應用程式密碼

### 步驟 3：編輯 EMAIL.env

開啟 `backend/EMAIL.env`，填入：

```env
SMTP_USER="your-email@gmail.com"        # 您的 Gmail 地址
SMTP_PASSWORD="your-app-password"       # 剛才複製的應用程式密碼
```

### 步驟 4：測試

```bash
python test_email_simple.py your-email@example.com
```

## ✅ 成功標誌

如果看到：
```
✅ 郵件發送成功！
```

表示設定完成！

## ❌ 如果遇到問題

### 連線超時
- 檢查防火牆設定
- 執行 `python test_smtp_network.py` 診斷
- 參考 `README_SMTP_FIX.md`

### 認證失敗
- 確認應用程式密碼正確（16位）
- 確認已啟用兩步驟驗證
- 重新產生應用程式密碼

## 📚 更多資訊

- 詳細設定：`README_EMAIL_SETUP.md`
- 問題排除：`README_SMTP_FIX.md`
- 診斷工具：`test_smtp_network.py`

