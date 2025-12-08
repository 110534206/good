# 📧 Email 功能設定說明

## 首次設定

### 1. 複製設定檔

```bash
cd backend
cp EMAIL.env.example EMAIL.env
```

### 2. 編輯 EMAIL.env

填入您的 Gmail 帳號和應用程式密碼（詳見 `backend/README_EMAIL_SETUP.md`）

### 3. 測試

```bash
cd backend
python test_email_simple.py your-email@example.com
```

## ⚠️ 注意事項

- `EMAIL.env` 包含敏感資訊，**不會**被推送到 GitHub
- 每個開發者需要建立自己的 `EMAIL.env` 檔案
- 如果遇到連線問題，請參考 `backend/README_EMAIL_SETUP.md`

## 🔒 安全性

- ✅ `EMAIL.env` 已加入 `.gitignore`
- ✅ `EMAIL.env.example` 是範本，不包含真實密碼
- ✅ 應用程式密碼只存在於本地 `EMAIL.env` 檔案

## 📖 詳細文件

請查看 `backend/README_EMAIL_SETUP.md` 取得完整的設定指南。

