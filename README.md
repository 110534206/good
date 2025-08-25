# 履歷管理系統 - 前後台共用架構

## 架構概述

這是一個採用**前後台共用後端**的履歷管理系統，具有以下特點：

- **後端統一**：使用單一 `app.py` 服務前後台
- **前端分離**：前台 (`frontend/`) 和後台 (`admin_frontend/`) 使用不同的模板
- **API 優先**：所有功能都透過 RESTful API 提供
- **配置管理**：支援多環境配置 

## 目錄結構

```
good/
├── backend/
│   ├── app.py              # 主應用程式 (前後台共用)
│   ├── config.py           # 配置管理
│   └── uploads/            # 檔案上傳目錄
├── frontend/
│   ├── templates/          # 前台模板
│   └── static/            # 前台靜態資源
└── admin_frontend/
    ├── templates/          # 後台模板
    └── static/            # 後台靜態資源
```

## 架構優勢

### ✅ 已實現的功能

1. **多模板支援**
   - 使用 `ChoiceLoader` 同時載入前台和後台模板
   - 前台模板優先載入，後台模板作為備用

2. **API 設計**
   - 所有功能都透過 `/api/*` 端點提供
   - 支援 JSON 格式的請求和回應
   - 統一的錯誤處理機制

3. **CORS 支援**
   - 已啟用跨域請求支援
   - 可配置允許的來源域名

4. **配置管理**
   - 支援開發、測試、生產環境
   - 環境變數配置
   - 統一的配置類別

5. **資料庫連接**
   - 統一的資料庫連接函數
   - 支援不同環境的資料庫配置

## 配置說明

### 環境變數

```bash
# 資料庫配置
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=
DB_DATABASE=user

# 上傳配置
UPLOAD_FOLDER=./uploads

# 前端路徑
FRONTEND_TEMPLATES=frontend/templates
ADMIN_TEMPLATES=admin_frontend/templates

# API 配置
API_PREFIX=/api

# 環境設定
FLASK_ENV=development  # development, production, testing
```

### 配置類別

- `DevelopmentConfig`: 開發環境配置
- `ProductionConfig`: 生產環境配置  
- `TestingConfig`: 測試環境配置

## API 端點

### 認證相關
- `POST /api/login` - 使用者登入
- `POST /api/register_student` - 學生註冊

### 使用者管理
- `GET /api/profile` - 取得個人資料
- `POST /api/saveProfile` - 更新個人資料
- `POST /api/admin/create_user` - 管理員新增使用者

### 履歷管理
- `POST /api/upload_resume` - 上傳履歷
- `GET /api/get_all_resumes` - 取得所有履歷
- `GET /api/get_all_students_resumes` - 取得所有學生履歷
- `POST /api/review_resume` - 審核履歷
- `GET /api/download_resume` - 下載履歷
- `DELETE /api/delete_resume` - 刪除履歷

## 頁面路由

### 前台頁面
- `/login` - 登入頁面
- `/register_student` - 學生註冊
- `/student_home` - 學生首頁
- `/upload_resume` - 上傳履歷
- `/profile` - 個人資料

### 後台頁面
- `/admin_home` - 管理員首頁
- `/teacher_home` - 教師首頁
- `/director_home` - 主任首頁
- `/review_resume` - 審核履歷

## 啟動方式

### 開發環境
```bash
cd backend
python app.py
```

### 生產環境
```bash
export FLASK_ENV=production
cd backend
python app.py
```

## 前後台分離建議

雖然目前使用模板渲染，但架構已支援完全的前後台分離：

1. **前台**：可以改用 React/Vue.js 等 SPA 框架
2. **後台**：可以改用 React Admin 等管理後台框架
3. **API**：保持現有的 RESTful API 設計

## 安全性考量

- 密碼使用 `werkzeug.security` 加密
- CORS 配置限制允許的來源
- 檔案上傳使用安全的檔名處理
- API 端點有適當的權限驗證

## 擴展性

- 模組化的配置系統
- 統一的錯誤處理
- 可擴展的 API 設計
- 支援多環境部署 