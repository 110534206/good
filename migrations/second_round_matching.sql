-- 二輪媒合（補分發）資料表
-- 執行時機：實作二輪媒合功能前

-- 1. 廠商二輪參與意願與名額（每公司每學期一筆，廠商填寫是否參與、名額、薪資說明、職缺名稱）
CREATE TABLE IF NOT EXISTS `second_round_participation` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `semester_id` int(11) NOT NULL COMMENT '學期ID',
  `company_id` int(11) NOT NULL COMMENT '公司ID (internship_companies.id)',
  `agree` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否參與二輪：0=否 1=是',
  `quota` int(11) NOT NULL DEFAULT 0 COMMENT '二輪名額數',
  `salary_note` varchar(500) DEFAULT NULL COMMENT '薪資說明（可標示無薪、時薪等）',
  `job_title` varchar(200) DEFAULT NULL COMMENT '二輪職缺名稱（可與第一輪不同）',
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT NULL ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_semester_company` (`semester_id`, `company_id`),
  KEY `idx_semester` (`semester_id`),
  KEY `idx_company` (`company_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='廠商二輪媒合意願與名額';

-- 2. 主任指派未錄取學生到二輪廠商（一學生一公司一筆，可選職缺）
CREATE TABLE IF NOT EXISTS `second_round_assignments` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `semester_id` int(11) NOT NULL COMMENT '學期ID',
  `student_id` int(11) NOT NULL COMMENT '學生ID',
  `company_id` int(11) NOT NULL COMMENT '公司ID',
  `job_id` int(11) DEFAULT NULL COMMENT '職缺ID (internship_jobs.id)，可為空若廠商僅填職缺名稱',
  `status` enum('pending','accepted','rejected') NOT NULL DEFAULT 'pending' COMMENT '廠商名單確認：待確認/接受/不適合',
  `assigned_by` int(11) NOT NULL COMMENT '指派者 (主任 user_id)',
  `assigned_at` datetime NOT NULL DEFAULT current_timestamp(),
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT NULL ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_semester_student_company` (`semester_id`, `student_id`, `company_id`),
  KEY `idx_semester` (`semester_id`),
  KEY `idx_student` (`student_id`),
  KEY `idx_company` (`company_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='二輪媒合主任指派記錄';

-- 3. 若 internship_companies 尚無 agree_second_interview，可選執行（與 second_round_participation 並存，供既有二面通知邏輯使用）
-- ALTER TABLE internship_companies ADD COLUMN agree_second_interview tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否同意二輪/二面：0=否 1=是' AFTER status;
