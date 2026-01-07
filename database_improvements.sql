-- ============================================================
-- 資料庫表結構改進 SQL 腳本
-- 適用於：resume_folders, student_job_applications, resumes , student_preferences 表
-- ============================================================

-- ============================================================
-- 1. resume_folders 表改進
-- ============================================================

-- 1.1 添加外鍵約束（user_id -> users.id）
ALTER TABLE `resume_folders`
  ADD CONSTRAINT `fk_resume_folders_user` 
  FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) 
  ON DELETE CASCADE ON UPDATE CASCADE;

-- 1.2 添加索引（提升查詢效率）
ALTER TABLE `resume_folders`
  ADD INDEX `idx_user_id` (`user_id`);

-- 1.3 添加註釋（提升可維護性）
ALTER TABLE `resume_folders`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT COMMENT '資料夾ID',
  MODIFY `user_id` int(11) NOT NULL COMMENT '使用者ID（參考 users.id）',
  MODIFY `folder_name` varchar(255) NOT NULL DEFAULT '未命名履歷' COMMENT '資料夾名稱',
  MODIFY `created_at` datetime DEFAULT current_timestamp() COMMENT '建立時間',
  MODIFY `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp() COMMENT '更新時間';

-- ============================================================
-- 2. resumes 表改進
-- ============================================================

-- 2.1 添加 folder_id 註釋
ALTER TABLE `resumes`
  MODIFY `folder_id` int(11) DEFAULT NULL COMMENT '履歷資料夾ID（參考 resume_folders.id）';

-- 2.2 添加外鍵約束（folder_id -> resume_folders.id）
-- 注意：使用 ON DELETE SET NULL，因為履歷不應該因為資料夾刪除而刪除
ALTER TABLE `resumes`
  ADD CONSTRAINT `fk_resumes_folder` 
  FOREIGN KEY (`folder_id`) REFERENCES `resume_folders` (`id`) 
  ON DELETE SET NULL ON UPDATE CASCADE;

-- 2.3 添加索引（提升查詢效率）
ALTER TABLE `resumes`
  ADD INDEX `idx_folder_id` (`folder_id`);

-- ============================================================
-- 3. student_job_applications 表改進
-- ============================================================

-- 3.1 添加 resume_id 字段（重要：代碼中已使用此字段）
ALTER TABLE `student_job_applications`
  ADD COLUMN `resume_id` int(10) UNSIGNED DEFAULT NULL COMMENT '履歷ID（參考 resumes.id）' AFTER `folder_id`;

-- 3.2 添加 updated_at 字段（追蹤狀態變更時間）
ALTER TABLE `student_job_applications`
  ADD COLUMN `updated_at` datetime DEFAULT NULL ON UPDATE current_timestamp() COMMENT '更新時間' AFTER `applied_at`;

-- 3.3 添加外鍵約束
-- 3.3.1 student_id -> users.id
ALTER TABLE `student_job_applications`
  ADD CONSTRAINT `fk_student_job_applications_student` 
  FOREIGN KEY (`student_id`) REFERENCES `users` (`id`) 
  ON DELETE CASCADE ON UPDATE CASCADE;

-- 3.3.2 folder_id -> resume_folders.id
ALTER TABLE `student_job_applications`
  ADD CONSTRAINT `fk_student_job_applications_folder` 
  FOREIGN KEY (`folder_id`) REFERENCES `resume_folders` (`id`) 
  ON DELETE CASCADE ON UPDATE CASCADE;

-- 3.3.3 resume_id -> resumes.id
ALTER TABLE `student_job_applications`
  ADD CONSTRAINT `fk_student_job_applications_resume` 
  FOREIGN KEY (`resume_id`) REFERENCES `resumes` (`id`) 
  ON DELETE SET NULL ON UPDATE CASCADE;

-- 3.4 添加索引（提升查詢效率）
ALTER TABLE `student_job_applications`
  ADD INDEX `idx_folder_id` (`folder_id`),
  ADD INDEX `idx_resume_id` (`resume_id`),
  ADD INDEX `idx_student_id` (`student_id`),
  ADD INDEX `idx_status` (`status`);

-- ============================================================
-- 4. student_preferences 表改進（重要：代碼中實際使用的表）
-- ============================================================

-- 4.1 添加 folder_id 字段（代碼中已接收但未保存）
ALTER TABLE `student_preferences`
  ADD COLUMN `folder_id` int(11) DEFAULT NULL COMMENT '履歷資料夾ID（參考 resume_folders.id）' AFTER `job_id`;

-- 4.2 添加 resume_id 字段（代碼中已接收但未保存）
ALTER TABLE `student_preferences`
  ADD COLUMN `resume_id` int(10) UNSIGNED DEFAULT NULL COMMENT '履歷ID（參考 resumes.id）' AFTER `folder_id`;

-- 4.3 添加外鍵約束
ALTER TABLE `student_preferences`
  ADD CONSTRAINT `fk_student_preferences_folder` 
  FOREIGN KEY (`folder_id`) REFERENCES `resume_folders` (`id`) 
  ON DELETE SET NULL ON UPDATE CASCADE,
  ADD CONSTRAINT `fk_student_preferences_resume` 
  FOREIGN KEY (`resume_id`) REFERENCES `resumes` (`id`) 
  ON DELETE SET NULL ON UPDATE CASCADE;

-- 4.4 添加索引
ALTER TABLE `student_preferences`
  ADD INDEX `idx_folder_id` (`folder_id`),
  ADD INDEX `idx_resume_id` (`resume_id`);

