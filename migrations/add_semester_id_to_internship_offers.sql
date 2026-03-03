-- ============================================================
-- 在 internship_offers 新增 semester_id 欄位
-- 用途：區分不同學期的錄取結果，方便多學期並存與篩選
-- ============================================================

-- 1. 新增欄位（允許 NULL，既有資料可先不填）
ALTER TABLE `internship_offers`
  ADD COLUMN `semester_id` int(11) DEFAULT NULL COMMENT '學期 ID，對應 semesters.id' AFTER `job_id`;

-- 2. 索引（依學期查詢用）
ALTER TABLE `internship_offers`
  ADD KEY `idx_semester_id` (`semester_id`);

-- 3. 外鍵（選用；若 semesters 表存在且需約束，可取消註解）
-- ALTER TABLE `internship_offers`
--   ADD CONSTRAINT `fk_internship_offers_semester`
--   FOREIGN KEY (`semester_id`) REFERENCES `semesters` (`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- 4. 既有資料補上學期（依實際學期代碼修改 '1132'，若無既有資料可略過）
UPDATE `internship_offers` io
SET io.semester_id = (SELECT id FROM semesters WHERE code = '1132' LIMIT 1)
WHERE io.semester_id IS NULL;
