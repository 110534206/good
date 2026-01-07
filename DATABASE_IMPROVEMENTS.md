# 資料庫表結構改進建議

## 📋 檢查結果總結

經過檢查 `resume_folders`、`student_job_applications`、`resumes` 三個資料表，發現以下需要改進的地方：

---

## 1. resume_folders 表

### 當前結構
```sql
CREATE TABLE `resume_folders` (
  `id` int(11) NOT NULL,
  `user_id` int(11) NOT NULL,
  `folder_name` varchar(255) NOT NULL DEFAULT '未命名履歷',
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

### 需要改進的地方

#### ✅ 建議 1：添加外鍵約束
- **問題**：`user_id` 沒有外鍵約束，無法保證數據完整性
- **影響**：如果刪除用戶，相關的資料夾記錄會成為孤兒記錄

#### ✅ 建議 2：添加索引
- **問題**：`user_id` 沒有索引，查詢效率低
- **影響**：當資料夾數量增加時，查詢會變慢

#### ✅ 建議 3：添加註釋
- **問題**：欄位缺少註釋，不利於維護

### 改進 SQL
```sql
-- 添加外鍵約束
ALTER TABLE `resume_folders`
  ADD CONSTRAINT `fk_resume_folders_user` 
  FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) 
  ON DELETE CASCADE ON UPDATE CASCADE;

-- 添加索引
ALTER TABLE `resume_folders`
  ADD INDEX `idx_user_id` (`user_id`);

-- 添加註釋（可選）
ALTER TABLE `resume_folders`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT COMMENT '資料夾ID',
  MODIFY `user_id` int(11) NOT NULL COMMENT '使用者ID（參考 users.id）',
  MODIFY `folder_name` varchar(255) NOT NULL DEFAULT '未命名履歷' COMMENT '資料夾名稱',
  MODIFY `created_at` datetime DEFAULT current_timestamp() COMMENT '建立時間',
  MODIFY `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp() COMMENT '更新時間';
```

---

## 2. student_job_applications 表

### ⚠️ 重要說明
**注意**：目前代碼中實際使用的是 `student_preferences` 表（見 `company.py:1778`），而不是 `student_job_applications` 表。`student_job_applications` 表可能是為未來使用而創建的。

**建議**：
- 如果計劃使用 `student_job_applications` 表，請按照以下改進
- 如果繼續使用 `student_preferences` 表，請參考「4. student_preferences 表改進建議」

### 當前結構
```sql
CREATE TABLE `student_job_applications` (
  `id` int(11) NOT NULL,
  `student_id` int(11) NOT NULL COMMENT '學生ID',
  `company_id` int(11) NOT NULL COMMENT '公司ID',
  `job_id` int(11) NOT NULL COMMENT '職缺ID',
  `folder_id` int(11) NOT NULL COMMENT '履歷資料夾ID',
  `status` enum('submitted','reviewing','accepted','rejected') DEFAULT 'submitted' COMMENT '投遞狀態',
  `applied_at` datetime DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='學生投遞職缺紀錄';
```

### 需要改進的地方

#### ⚠️ 重要問題 1：缺少 `resume_id` 字段
- **問題**：代碼中使用了 `resume_id`（見 `company.py:1723`），但表中沒有此字段
- **影響**：無法記錄具體投遞的履歷版本
- **建議**：添加 `resume_id` 字段

#### ✅ 建議 2：添加外鍵約束
- **問題**：
  - `student_id` 沒有外鍵約束到 `users` 表
  - `folder_id` 沒有外鍵約束到 `resume_folders` 表
  - `resume_id`（如果添加）需要外鍵約束到 `resumes` 表
- **影響**：數據完整性無法保證

#### ✅ 建議 3：添加索引優化
- **問題**：`folder_id` 和 `resume_id`（如果添加）沒有索引
- **影響**：查詢效率低

#### ✅ 建議 4：添加 `updated_at` 字段
- **問題**：只有 `applied_at`，沒有更新時間
- **影響**：無法追蹤狀態變更時間

### 改進 SQL
```sql
-- 1. 添加 resume_id 字段
ALTER TABLE `student_job_applications`
  ADD COLUMN `resume_id` int(10) UNSIGNED DEFAULT NULL COMMENT '履歷ID（參考 resumes.id）' AFTER `folder_id`;

-- 2. 添加 updated_at 字段
ALTER TABLE `student_job_applications`
  ADD COLUMN `updated_at` datetime DEFAULT NULL ON UPDATE current_timestamp() COMMENT '更新時間' AFTER `applied_at`;

-- 3. 添加外鍵約束
ALTER TABLE `student_job_applications`
  ADD CONSTRAINT `fk_student_job_applications_student` 
  FOREIGN KEY (`student_id`) REFERENCES `users` (`id`) 
  ON DELETE CASCADE ON UPDATE CASCADE,
  ADD CONSTRAINT `fk_student_job_applications_folder` 
  FOREIGN KEY (`folder_id`) REFERENCES `resume_folders` (`id`) 
  ON DELETE CASCADE ON UPDATE CASCADE,
  ADD CONSTRAINT `fk_student_job_applications_resume` 
  FOREIGN KEY (`resume_id`) REFERENCES `resumes` (`id`) 
  ON DELETE SET NULL ON UPDATE CASCADE;

-- 4. 添加索引
ALTER TABLE `student_job_applications`
  ADD INDEX `idx_folder_id` (`folder_id`),
  ADD INDEX `idx_resume_id` (`resume_id`),
  ADD INDEX `idx_student_id` (`student_id`),
  ADD INDEX `idx_status` (`status`);
```

---

## 3. resumes 表

### 當前結構
```sql
CREATE TABLE `resumes` (
  `id` int(10) UNSIGNED NOT NULL COMMENT '履歷ID',
  `user_id` int(10) UNSIGNED NOT NULL COMMENT '對應 users.id',
  `original_filename` varchar(255) NOT NULL COMMENT '上傳時原始檔名',
  `filepath` varchar(500) NOT NULL COMMENT '存放在伺服器的檔案路徑',
  `filesize` bigint(20) UNSIGNED NOT NULL COMMENT '檔案大小，單位 byte',
  `status` enum('uploaded','approved','rejected') NOT NULL DEFAULT 'uploaded' COMMENT '履歷狀態',
  `comment` text DEFAULT NULL COMMENT '老師審核留言',
  `note` text DEFAULT NULL COMMENT '備註',
  `created_at` datetime NOT NULL DEFAULT current_timestamp() COMMENT '建立時間',
  `updated_at` datetime DEFAULT NULL ON UPDATE current_timestamp() COMMENT '更新時間',
  `semester_id` int(11) NOT NULL COMMENT '履歷所屬學期 ID',
  `reviewed_by` int(10) UNSIGNED DEFAULT NULL COMMENT '履歷審核人ID (班導師ID)',
  `reviewed_at` datetime DEFAULT NULL COMMENT '履歷審核完成時間',
  `folder_id` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='學生履歷資料表';
```

### 需要改進的地方

#### ✅ 建議 1：添加外鍵約束
- **問題**：`folder_id` 沒有外鍵約束到 `resume_folders` 表
- **影響**：如果刪除資料夾，相關的履歷記錄會成為孤兒記錄
- **建議**：添加外鍵約束（使用 `ON DELETE SET NULL`，因為履歷不應該因為資料夾刪除而刪除）

#### ✅ 建議 2：添加索引優化
- **問題**：`folder_id` 沒有索引
- **影響**：根據資料夾查詢履歷時效率低（見 `resume.py:24539`）

#### ✅ 建議 3：添加註釋
- **問題**：`folder_id` 缺少註釋

### 改進 SQL
```sql
-- 1. 添加 folder_id 註釋
ALTER TABLE `resumes`
  MODIFY `folder_id` int(11) DEFAULT NULL COMMENT '履歷資料夾ID（參考 resume_folders.id）';

-- 2. 添加外鍵約束
ALTER TABLE `resumes`
  ADD CONSTRAINT `fk_resumes_folder` 
  FOREIGN KEY (`folder_id`) REFERENCES `resume_folders` (`id`) 
  ON DELETE SET NULL ON UPDATE CASCADE;

-- 3. 添加索引
ALTER TABLE `resumes`
  ADD INDEX `idx_folder_id` (`folder_id`);
```

---

## 4. student_preferences 表改進建議

### ⚠️ 重要問題：缺少 `folder_id` 和 `resume_id` 字段

**當前情況**：
- 代碼中接收了 `folder_id` 和 `resume_id`（見 `company.py:1722-1723`）
- 但插入 `student_preferences` 表時沒有保存這些字段（見 `company.py:1774-1775`）
- 註釋明確說明：「目前 student_preferences 表沒有 folder_id 和 resume_id 字段」

**影響**：
- 無法追蹤學生投遞時使用的具體履歷版本
- 無法區分不同資料夾的投遞記錄

### 建議改進 SQL

```sql
-- 1. 添加 folder_id 字段
ALTER TABLE `student_preferences`
  ADD COLUMN `folder_id` int(11) DEFAULT NULL COMMENT '履歷資料夾ID（參考 resume_folders.id）' AFTER `job_id`;

-- 2. 添加 resume_id 字段
ALTER TABLE `student_preferences`
  ADD COLUMN `resume_id` int(10) UNSIGNED DEFAULT NULL COMMENT '履歷ID（參考 resumes.id）' AFTER `folder_id`;

-- 3. 添加外鍵約束
ALTER TABLE `student_preferences`
  ADD CONSTRAINT `fk_student_preferences_folder` 
  FOREIGN KEY (`folder_id`) REFERENCES `resume_folders` (`id`) 
  ON DELETE SET NULL ON UPDATE CASCADE,
  ADD CONSTRAINT `fk_student_preferences_resume` 
  FOREIGN KEY (`resume_id`) REFERENCES `resumes` (`id`) 
  ON DELETE SET NULL ON UPDATE CASCADE;

-- 4. 添加索引
ALTER TABLE `student_preferences`
  ADD INDEX `idx_folder_id` (`folder_id`),
  ADD INDEX `idx_resume_id` (`resume_id`);
```

### 代碼修改建議

修改 `company.py:1778-1802`，在插入時包含 `folder_id` 和 `resume_id`：

```python
# 修改前
INSERT INTO student_preferences
(student_id, semester_id, company_id, job_id, job_title, status, submitted_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)

# 修改後
INSERT INTO student_preferences
(student_id, semester_id, company_id, job_id, folder_id, resume_id, job_title, status, submitted_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
```

---

## 📊 改進優先級

### 🔴 高優先級（必須修改）
1. **student_preferences 表添加 `folder_id` 和 `resume_id` 字段**
   - 代碼中已經接收這些參數，但沒有保存到數據庫
   - 影響功能完整性，無法追蹤投遞的履歷版本
   - **需要同時修改代碼和數據庫結構**

2. **student_job_applications 表添加 `resume_id` 字段**（如果計劃使用此表）
   - 表中缺少此字段，但代碼中可能需要

### 🟡 中優先級（建議修改）
2. **添加外鍵約束**
   - 保證數據完整性
   - 防止孤兒記錄

3. **添加索引優化**
   - 提升查詢效率
   - 特別是在數據量增加時

### 🟢 低優先級（可選）
4. **添加註釋**
   - 提升代碼可維護性

---

## 🚀 執行順序

建議按以下順序執行改進：

1. **先修改 resume_folders 表**（添加外鍵和索引）
2. **再修改 resumes 表**（添加外鍵和索引，依賴 resume_folders）
3. **最後修改 student_job_applications 表**（添加字段、外鍵和索引，依賴前兩個表）

---

## ⚠️ 注意事項

1. **備份數據**：執行任何 ALTER TABLE 操作前，請先備份數據庫
2. **測試環境**：建議先在測試環境執行，確認無誤後再在生產環境執行
3. **外鍵約束**：添加外鍵約束前，請確認現有數據符合約束條件
4. **索引影響**：添加索引會稍微影響 INSERT/UPDATE 性能，但會大幅提升 SELECT 性能

---

## 📝 完整改進 SQL 腳本

見 `database_improvements.sql` 文件（需要時可生成）

