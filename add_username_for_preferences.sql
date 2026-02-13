-- 檢查並確保 users 表有 username（學號）欄位
-- 若您的 users 表沒有 username 欄位，請執行以下步驟：

-- 1. 檢查 users 表結構
DESCRIBE users;

-- 2. 若無 username 欄位，新增欄位
-- ALTER TABLE users ADD COLUMN username VARCHAR(100) DEFAULT NULL COMMENT '學號/帳號';

-- 3. 若已有 username 欄位但資料為空，請從您的學籍系統或既有來源匯入學號
-- 例如手動更新：
-- UPDATE users SET username = 's12345' WHERE id = 1;
-- UPDATE users SET username = 's12346' WHERE id = 3;

-- 4. 確認 student_preferences 可正確 JOIN 到 users
-- 下列查詢應能取得學號，若有結果表示 JOIN 正常：
/*
SELECT sp.student_id, u.name, u.username AS student_number
FROM student_preferences sp
JOIN users u ON sp.student_id = u.id
WHERE sp.semester_id = 1
GROUP BY sp.student_id;
*/
