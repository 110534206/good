-- 遷移腳本：將 category 欄位改為只有 draft 和 ready 兩個值
-- 執行日期：請在執行前備份資料庫

-- 1. 修改 category 欄位的 ENUM 定義
-- 先將現有的 reviewing, approved, rejected 轉換為對應的 status
-- reviewing -> status='uploaded' (審核中)
-- approved -> status='approved' (通過)
-- rejected -> status='rejected' (退件)

-- 更新 category='reviewing' 的履歷，確保 status='uploaded'
UPDATE `resumes` 
SET `status` = 'uploaded'
WHERE `category` = 'reviewing' AND `status` != 'uploaded';

-- 更新 category='approved' 的履歷，確保 status='approved'
UPDATE `resumes` 
SET `status` = 'approved'
WHERE `category` = 'approved' AND `status` != 'approved';

-- 更新 category='rejected' 的履歷，確保 status='rejected'
UPDATE `resumes` 
SET `status` = 'rejected'
WHERE `category` = 'rejected' AND `status` != 'rejected';

-- 將所有 reviewing, approved, rejected 的 category 改為 'ready'（正式版本）
-- 因為這些都是已經提交並進入審核流程的履歷
UPDATE `resumes` 
SET `category` = 'ready'
WHERE `category` IN ('reviewing', 'approved', 'rejected');

-- 2. 修改 category 欄位的 ENUM 定義
ALTER TABLE `resumes` 
MODIFY COLUMN `category` ENUM('draft', 'ready') NOT NULL DEFAULT 'draft' 
COMMENT '履歷分類：草稿、正式版本';
