-- 遷移腳本：移除 resume_folders 表，改用分類系統
-- 執行日期：請在執行前備份資料庫

-- 1. 在 resumes 表中添加 category 欄位
ALTER TABLE `resumes` 
ADD COLUMN `category` ENUM('draft', 'reviewing', 'rejected', 'approved') NOT NULL DEFAULT 'draft' 
COMMENT '履歷分類：草稿、審核中、退件、通過' 
AFTER `status`;

-- 2. 根據現有 status 設定初始 category 值
-- uploaded -> reviewing (審核中)
-- approved -> approved (通過)
-- rejected -> rejected (退件)
UPDATE `resumes` SET `category` = 'reviewing' WHERE `status` = 'uploaded';
UPDATE `resumes` SET `category` = 'approved' WHERE `status` = 'approved';
UPDATE `resumes` SET `category` = 'rejected' WHERE `status` = 'rejected';

-- 3. 移除 resumes 表中的 folder_id 外鍵約束
ALTER TABLE `resumes` 
DROP FOREIGN KEY `fk_resumes_folder`;

-- 4. 移除 resumes 表中的 folder_id 欄位
ALTER TABLE `resumes` 
DROP COLUMN `folder_id`;

-- 5. 刪除 resume_folders 表（請確認沒有其他依賴）
DROP TABLE IF EXISTS `resume_folders`;
