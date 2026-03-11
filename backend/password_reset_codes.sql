-- 忘記密碼驗證碼表（請在資料庫中執行此檔以啟用忘記密碼功能）
-- 適用於 MariaDB / MySQL

CREATE TABLE IF NOT EXISTS `password_reset_codes` (
  `id` int(10) UNSIGNED NOT NULL AUTO_INCREMENT,
  `email` varchar(100) NOT NULL,
  `code` varchar(10) NOT NULL,
  `expires_at` datetime NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_email_expires` (`email`, `expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='忘記密碼驗證碼';
