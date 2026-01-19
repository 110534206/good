# 履歷管理系統遷移：從資料夾系統改為分類系統

## 概述
將履歷管理從「資料夾」系統改為「分類」系統，讓學生可以自行設定分類（草稿、審核、退件、通過），不再需要建立和命名資料夾。

## 資料庫變更

### 1. SQL 遷移腳本
執行 `migration_remove_folders_add_category.sql` 來：
- 在 `resumes` 表中添加 `category` 欄位（ENUM: 'draft', 'reviewing', 'rejected', 'approved'）
- 根據現有 `status` 設定初始 `category` 值
- 移除 `resumes` 表中的 `folder_id` 欄位和外鍵約束
- 刪除 `resume_folders` 資料表

### 2. 資料表變更
- **resumes 表**：
  - 新增：`category` ENUM('draft', 'reviewing', 'rejected', 'approved') NOT NULL DEFAULT 'draft'
  - 移除：`folder_id` 欄位

- **resume_folders 表**：
  - 完全刪除

## 前端變更

### resume_folders.html
- 完全重寫，改為顯示分類標籤（草稿、審核中、退件、通過）
- 每個分類顯示該分類下的履歷列表
- 可以通過下拉選單修改履歷的分類
- 移除資料夾相關的所有功能（新增資料夾、刪除資料夾、修改資料夾名稱）

## 後端 API 變更

### 新增 API
1. **GET /api/my_resumes**
   - 獲取當前學生的所有履歷（包含分類資訊）
   - 返回：`{success: true, resumes: [...]}`

2. **PUT /api/resumes/<resume_id>/category**
   - 更新履歷的分類
   - 請求體：`{category: 'draft'|'reviewing'|'rejected'|'approved'}`
   - 返回：`{success: true}`

### 廢棄的 API（已註釋）
- `GET /api/resume_folders` - 改用 `/api/my_resumes`
- `POST /api/resume_folders` - 不再需要
- `DELETE /api/resume_folders/<folder_id>` - 不再需要
- `PUT /api/resume_folders/<folder_id>` - 不再需要
- `GET /api/resume_folders/<folder_id>/resumes` - 不再需要

## 其他修改

### company.py
- 移除投遞履歷時對 `folder_id` 的檢查和更新
- 只使用 `resume_id` 來記錄投遞的履歷
- 更新 `student_preferences` 的 INSERT 語句，移除 `folder_id` 欄位

## 分類說明

- **draft（草稿）**：學生正在編輯的履歷
- **reviewing（審核中）**：已提交等待審核的履歷
- **rejected（退件）**：審核未通過的履歷
- **approved（通過）**：審核通過的履歷

## 遷移步驟

1. **備份資料庫**
   ```sql
   -- 備份 resumes 和 resume_folders 表
   ```

2. **執行遷移腳本**
   ```sql
   source migration_remove_folders_add_category.sql;
   ```

3. **驗證資料**
   - 確認所有履歷都有正確的 `category` 值
   - 確認沒有遺漏的資料

4. **部署代碼**
   - 更新前端代碼
   - 更新後端代碼
   - 重啟服務

5. **測試功能**
   - 測試分類切換
   - 測試修改分類
   - 測試新增履歷
   - 測試投遞履歷

## 注意事項

- 舊的資料夾資料將無法恢復
- 如果 `student_preferences` 表中有 `folder_id` 欄位，需要手動處理或保留（不影響新功能）
- 確保所有使用 `folder_id` 的地方都已更新

## 回滾方案

如果需要回滾：
1. 恢復資料庫備份
2. 恢復舊版代碼
3. 重新部署
