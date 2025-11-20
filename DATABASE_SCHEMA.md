# 智慧實習系統 - 資料庫結構文件

本文檔記錄了智慧實習系統的完整資料庫結構，基於程式碼分析與業務流程說明。

## 📊 資料表總覽

系統共包含 **15個核心資料表**，分為以下四大類別：

### I. 實習與職缺管理 (4個)
- `internship_companies` - 實習公司資料與審核
- `internship_jobs` - 實習公司提供的職缺細節
- `company_openings` - 科助控制特定公司在特定學期的開放狀態
- `internship_experiences` - 學生實習錄取結果與心得記錄

### II. 學生履歷與志願序 (2個)
- `resumes` - 學生履歷檔案與審核流程
- `student_preferences` - 學生填寫的志願序清單

### III. 基礎使用者與課程管理 (4個)
- `users` - 系統所有使用者的帳號與基本資料
- `classes` - 班級基本資訊
- `semesters` - 實習學期設定
- `classes_teacher` - 老師與班級的多對多關係（定義班導）

### IV. 關聯與日誌紀錄 (5個)
- `teacher_student_relations` - 學生被錄取後，與指導老師的綁定關係
- `announcement` - 系統公告內容管理
- `notifications` - 系統發送給特定使用者的通知紀錄
- `email_logs` - 系統發送郵件的紀錄
- `system_logs` - 系統核心操作或錯誤的日誌記錄

---

## 📋 詳細資料表結構

### 1. users (使用者表)
**核心功能：** 系統所有使用者的帳號與基本資料

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 使用者ID | AUTO_INCREMENT |
| username | VARCHAR | 學號/編號 | 學生帳號，用於登入 |
| name | VARCHAR | 姓名 | |
| email | VARCHAR | 電子郵件 | 學生必填，老師/主任可選 |
| password | VARCHAR | 密碼（雜湊） | |
| role | ENUM | 角色 | 'student', 'teacher', 'director', 'ta', 'admin', 'vendor' |
| class_id | INT (FK) | 班級ID | 僅學生有值，參考 classes.id |
| status | VARCHAR | 狀態 | 廠商狀態等 |
| avatar_url | VARCHAR | 頭像URL | |
| current_semester_code | VARCHAR | 當前學期代碼 | 如 '1132' |
| created_at | DATETIME | 建立時間 | |
| updated_at | DATETIME | 更新時間 | |

**索引：**
- PRIMARY KEY (id)
- INDEX (username)
- INDEX (class_id)
- INDEX (role)

**流程關聯：**
- 帳號登入與角色權限劃分
- 履歷自動標註學號（從 username）
- 履歷自動標註班級（從 class_id）

---

### 2. classes (班級表)
**核心功能：** 班級基本資訊

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 班級ID | AUTO_INCREMENT |
| name | VARCHAR | 班級名稱 | 如 'A' |
| department | VARCHAR | 系所 | 如 '管科' |
| admission | INT | 入學年度 | 民國年 |
| admission_year | INT | 入學屆數 | 民國年 |

**索引：**
- PRIMARY KEY (id)
- INDEX (department)

**流程關聯：**
- 履歷自動標註班級
- 班導查看所屬班級履歷/志願序

---

### 3. classes_teacher (老師班級關係表)
**核心功能：** 老師與班級的多對多關係（定義班導）

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 關係ID | AUTO_INCREMENT |
| teacher_id | INT (FK) | 老師ID | 參考 users.id (role='teacher' 或 'director') |
| class_id | INT (FK) | 班級ID | 參考 classes.id |
| role | VARCHAR | 角色 | '班導師' 或 '任課老師' |
| created_at | DATETIME | 建立時間 | |
| updated_at | DATETIME | 更新時間 | |

**索引：**
- PRIMARY KEY (id)
- UNIQUE KEY (teacher_id, class_id)
- INDEX (teacher_id)
- INDEX (class_id)

**流程關聯：**
- 班導查看所屬班級履歷/志願序
- 主任透過 classes_teacher -> classes.department 判斷科系

---

### 4. semesters (學期表)
**核心功能：** 實習學期設定

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 學期ID | AUTO_INCREMENT |
| code | VARCHAR | 學期代碼 | 如 '1132' (113學年第2學期) |
| start_date | DATE | 開始日期 | |
| end_date | DATE | 結束日期 | |
| is_active | BOOLEAN | 是否為當前學期 | |

**索引：**
- PRIMARY KEY (id)
- UNIQUE KEY (code)
- INDEX (is_active)

**流程關聯：**
- 學期切換時自動關閉上學期公司
- 生成新學期公司池（待開放）

---

### 5. resumes (履歷表)
**核心功能：** 學生履歷檔案與審核流程

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 履歷ID | AUTO_INCREMENT |
| user_id | INT (FK) | 學生ID | 參考 users.id |
| semester_id | INT (FK) | 學期ID | 調整：參考 semesters.id（可選） |
| original_filename | VARCHAR | 原始檔名 | |
| filepath | VARCHAR | 檔案路徑 | |
| filesize | INT | 檔案大小（位元組） | |
| status | ENUM | 審核狀態 | 'uploaded', 'approved', 'rejected' |
| comment | TEXT | 審核意見 | 班導填寫 |
| note | TEXT | 備註 | 學生可填寫 |
| reviewed_by | INT (FK) | 審核者ID | 參考 users.id（班導） |
| created_at | DATETIME | 建立時間 | |
| updated_at | DATETIME | 更新時間 | |

**索引：**
- PRIMARY KEY (id)
- INDEX (user_id)
- INDEX (semester_id)
- INDEX (status)
- INDEX (reviewed_by)

**流程關聯：**
- 學生上傳履歷
- 系統自動標註學期（從 students.current_semester_code 或 semesters.is_active）
- 系統自動標註班級（從 users.class_id）
- 系統自動標註學號（從 users.username）
- 班導審核履歷
- 履歷退件時自動發送通知

---

### 6. student_preferences (學生志願序表)
**核心功能：** 學生填寫的志願序清單

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 志願序ID | AUTO_INCREMENT |
| student_id | INT (FK) | 學生ID | 參考 users.id |
| semester_id | INT (FK) | 學期ID | 調整：參考 semesters.id（可選） |
| company_id | INT (FK) | 公司ID | 參考 internship_companies.id |
| job_id | INT (FK) | 職缺ID | 參考 internship_jobs.id |
| preference_order | INT | 志願順序 | 1-5 |
| job_title | VARCHAR | 職缺名稱 | 快取欄位，避免 JOIN |
| status | ENUM | 審核狀態 | 'pending', 'approved', 'rejected' |
| submitted_at | DATETIME | 提交時間 | |

**索引：**
- PRIMARY KEY (id)
- UNIQUE KEY (student_id, preference_order)
- INDEX (student_id)
- INDEX (semester_id)
- INDEX (company_id)
- INDEX (job_id)

**流程關聯：**
- 學生填寫志願序（顯示當學期科助開放的公司）
- 班導審核志願序
- 志願序退件時自動發送通知
- 錄取時自動綁定公司與學生

---

### 7. internship_companies (實習公司表)
**核心功能：** 實習公司資料與審核

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 公司ID | AUTO_INCREMENT |
| company_name | VARCHAR | 公司名稱 | |
| uploaded_by_user_id | INT (FK) | 上傳者ID | 參考 users.id（廠商或指導老師） |
| advisor_user_id | INT (FK) | 指導老師ID | 參考 users.id（role='teacher'） |
| status | ENUM | 審核狀態 | 'pending', 'approved', 'rejected' |
| description | TEXT | 公司簡介 | |
| location | VARCHAR | 公司地址 | |
| company_address | VARCHAR | 公司地址（別名） | |
| contact_person | VARCHAR | 聯絡人 | |
| contact_name | VARCHAR | 聯絡人姓名（別名） | |
| contact_title | VARCHAR | 聯絡人職稱 | |
| contact_email | VARCHAR | 聯絡信箱 | |
| contact_phone | VARCHAR | 聯絡電話 | |
| company_doc_path | VARCHAR | 公司資料檔案路徑 | Word 檔 |
| reject_reason | TEXT | 退件原因 | |
| submitted_at | DATETIME | 提交時間 | |
| reviewed_at | DATETIME | 審核時間 | |

**索引：**
- PRIMARY KEY (id)
- INDEX (uploaded_by_user_id)
- INDEX (advisor_user_id)
- INDEX (status)

**流程關聯：**
- 廠商/指導老師上傳公司資料
- 科助審核公司資料
- 指導老師追蹤公司
- 學生志願序頁面顯示

---

### 8. internship_jobs (職缺表)
**核心功能：** 實習公司提供的職缺細節

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 職缺ID | AUTO_INCREMENT |
| company_id | INT (FK) | 公司ID | 參考 internship_companies.id |
| title | VARCHAR | 職缺名稱 | |
| description | TEXT | 職缺說明 | |
| slots | INT | 名額 | |
| period | VARCHAR | 實習期間 | |
| work_time | VARCHAR | 工作時間 | |
| salary | VARCHAR | 薪資 | |
| remark | TEXT | 備註 | |
| is_active | BOOLEAN | 是否啟用 | |

**索引：**
- PRIMARY KEY (id)
- INDEX (company_id)
- INDEX (is_active)

**流程關聯：**
- 廠商/老師上傳職缺說明
- 學生志願序選擇職缺

---

### 9. company_openings (公司開放表)
**核心功能：** 科助控制特定公司在特定學期的開放狀態

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 開放ID | AUTO_INCREMENT |
| company_id | INT (FK) | 公司ID | 參考 internship_companies.id |
| semester | VARCHAR | 學期代碼 | 如 '1132' |
| is_open | BOOLEAN | 是否開放 | 預設 FALSE |
| opened_at | DATETIME | 開放時間 | |
| opened_by | INT (FK) | 開放者ID | 參考 users.id（科助） |

**索引：**
- PRIMARY KEY (id)
- UNIQUE KEY (company_id, semester)
- INDEX (semester)
- INDEX (is_open)

**流程關聯：**
- 科助決定本學期開放公司
- 影響學生志願序頁面顯示（僅顯示 is_open=TRUE 的公司）

---

### 10. internship_experiences (實習心得表)
**核心功能：** 學生實習錄取結果與心得記錄

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 心得ID | AUTO_INCREMENT |
| user_id | INT (FK) | 學生ID | 參考 users.id |
| company_id | INT (FK) | 公司ID | 參考 internship_companies.id |
| job_id | INT (FK) | 職缺ID | 參考 internship_jobs.id |
| year | INT | 實習年度 | 民國年 |
| content | TEXT | 實習心得內容 | |
| rating | INT | 評分 | 1-5 |
| is_public | BOOLEAN | 是否公開 | 預設 TRUE |
| verified_by_teacher_id | INT (FK) | 審核心得老師ID | 參考 users.id |
| created_at | DATETIME | 建立時間 | |

**索引：**
- PRIMARY KEY (id)
- INDEX (user_id)
- INDEX (company_id)
- INDEX (job_id)
- INDEX (year)
- INDEX (is_public)

**流程關聯：**
- 實習錄取結果紀錄
- 實習心得上傳
- 本屆心得（顯示於該學生頁面）
- 歷屆心得（供後屆學生瀏覽）

---

### 11. teacher_student_relations (師生關係表)
**核心功能：** 學生被錄取後，與指導老師的綁定關係

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 關係ID | AUTO_INCREMENT |
| teacher_id | INT (FK) | 指導老師ID | 參考 users.id |
| student_id | INT (FK) | 學生ID | 參考 users.id |
| company_id | INT (FK) | 公司ID | 參考 internship_companies.id |
| semester | VARCHAR | 學期代碼 | 如 '1132' |
| role | VARCHAR | 角色 | 如 '指導老師' |
| created_at | DATETIME | 建立時間 | |

**索引：**
- PRIMARY KEY (id)
- UNIQUE KEY (teacher_id, student_id, semester)
- INDEX (teacher_id)
- INDEX (student_id)
- INDEX (company_id)

**流程關聯：**
- 錄取學生時，自動綁定指導老師與學生
- 指導老師查看錄取該公司學生的履歷、志願序

---

### 12. announcement (公告表)
**核心功能：** 系統公告內容管理

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 公告ID | AUTO_INCREMENT |
| title | VARCHAR | 標題 | |
| content | TEXT | 內容 | |
| start_time | DATETIME | 開始時間 | 可為 NULL |
| end_time | DATETIME | 結束時間 | 可為 NULL |
| is_published | BOOLEAN | 是否發布 | |
| created_by | VARCHAR | 建立者 | |
| created_at | DATETIME | 建立時間 | |

**索引：**
- PRIMARY KEY (id)
- INDEX (is_published)
- INDEX (start_time, end_time)

**流程關聯：**
- 科助發布公告
- 同步至跑馬燈和公告頁
- 公告發布時自動推送通知給所有使用者

---

### 13. notifications (通知表)
**核心功能：** 系統發送給特定使用者的通知紀錄

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 通知ID | AUTO_INCREMENT |
| user_id | INT (FK) | 使用者ID | 參考 users.id |
| title | VARCHAR | 標題 | |
| message | TEXT | 訊息內容 | |
| link_url | VARCHAR | 連結URL | 可為 NULL |
| is_read | BOOLEAN | 是否已讀 | 預設 FALSE |
| created_at | DATETIME | 建立時間 | |

**索引：**
- PRIMARY KEY (id)
- INDEX (user_id)
- INDEX (is_read)
- INDEX (created_at)

**流程關聯：**
- 履歷/志願序退件時自動發送通知（跑馬燈+公告頁+Email）
- 公告發布時自動推送通知
- 學生查看個人通知中心

---

### 14. email_logs (郵件日誌表)
**核心功能：** 系統發送通知郵件的紀錄

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 日誌ID | AUTO_INCREMENT |
| recipient | VARCHAR | 收件人 | 電子郵件 |
| subject | VARCHAR | 主旨 | |
| content | TEXT | 內容 | |
| related_user_id | INT (FK) | 相關使用者ID | 參考 users.id |
| status | ENUM | 發送狀態 | 'sent', 'failed', 'pending' |
| sent_at | DATETIME | 發送時間 | |
| error_message | TEXT | 錯誤訊息 | |

**索引：**
- PRIMARY KEY (id)
- INDEX (related_user_id)
- INDEX (status)
- INDEX (sent_at)

**流程關聯：**
- 系統發送通知郵件的紀錄
- 履歷/志願序退件時自動發送 Email

---

### 15. system_logs (系統日誌表)
**核心功能：** 系統核心操作或錯誤的日誌記錄

| 欄位名稱 | 類型 | 說明 | 備註 |
|---------|------|------|------|
| id | INT (PK) | 日誌ID | AUTO_INCREMENT |
| user_id | INT (FK) | 使用者ID | 參考 users.id（可為 NULL） |
| action | VARCHAR | 操作類型 | 如 'login', 'upload_resume', 'approve_company' |
| target_type | VARCHAR | 目標類型 | 如 'resume', 'company', 'user' |
| target_id | INT | 目標ID | |
| detail | TEXT | 詳細資訊 | JSON 格式 |
| ip_address | VARCHAR | IP 位址 | |
| created_at | DATETIME | 建立時間 | |

**索引：**
- PRIMARY KEY (id)
- INDEX (user_id)
- INDEX (action)
- INDEX (target_type, target_id)
- INDEX (created_at)

**流程關聯：**
- 管理員查看系統紀錄與錯誤日誌
- 追蹤系統操作歷史

---

## 🔄 主要流程與資料關聯

### 履歷流程
1. 學生上傳履歷 → `resumes` 新增記錄（status='uploaded'）
2. 系統自動標註：
   - 學期：從 `users.current_semester_code` 或 `semesters.is_active`
   - 班級：從 `users.class_id` → `classes.name`
   - 學號：從 `users.username`
3. 班導審核 → 更新 `resumes.status`（'approved' 或 'rejected'）
4. 若退件 → 自動新增 `notifications` 記錄，並發送 Email（記錄到 `email_logs`）

### 志願序流程
1. 學生填寫志願序 → `student_preferences` 新增記錄
2. 僅顯示 `company_openings.is_open=TRUE` 且 `company_openings.semester` 為當前學期的公司
3. 班導審核 → 更新 `student_preferences.status`
4. 若退件 → 自動新增 `notifications` 記錄

### 公司開放流程
1. 廠商/指導老師上傳公司 → `internship_companies` 新增記錄（status='pending'）
2. 科助審核 → 更新 `internship_companies.status`（'approved' 或 'rejected'）
3. 科助決定開放公司 → `company_openings` 新增/更新記錄（is_open=TRUE）
4. 學生志願序頁面僅顯示已開放的公司

### 錄取流程
1. 廠商錄取學生（Email 通知）→ 系統同步紀錄
2. 自動綁定：
   - `teacher_student_relations` 新增記錄（綁定指導老師與學生）
   - `internship_experiences` 可選：記錄錄取結果
3. 學生在「我的實習成果」頁看到：
   - 錄取公司（從 `internship_companies`）
   - 實習期間（從 `internship_experiences.year`）
   - 最終錄取志願（從 `student_preferences.preference_order`）

### 實習心得流程
1. 學生上傳實習心得 → `internship_experiences` 新增記錄
2. 系統分類：
   - 本屆心得：`is_public=TRUE`，顯示於該學生頁面
   - 歷屆心得：供後屆學生瀏覽，附該公司關鍵資訊

---

## 📝 注意事項

1. **學期管理**：系統需要支援學期切換，關閉上學期公司，生成新學期公司池
2. **權限控制**：
   - 科助是「實習公司開放」的最終決策者
   - 主任僅能查看與建議，但不直接審核
   - 班導只能審核所屬班級的履歷與志願序
3. **自動化通知**：
   - 履歷/志願序退件時自動發送通知（跑馬燈 + 公告頁 + Email）
   - 公告發布時自動推送通知給所有使用者
4. **資料完整性**：
   - 外鍵約束確保資料關聯正確
   - 軟刪除機制（如需要）可透過 status 欄位實現

---

## 🔍 資料庫查詢範例

### 查詢學生履歷（含學期、班級資訊）
```sql
SELECT 
    r.id, r.original_filename, r.status, r.created_at,
    u.username AS student_number, u.name AS student_name,
    c.name AS class_name, c.department,
    s.code AS semester_code
FROM resumes r
JOIN users u ON r.user_id = u.id
LEFT JOIN classes c ON u.class_id = c.id
LEFT JOIN semesters s ON r.semester_id = s.id
WHERE u.role = 'student'
ORDER BY r.created_at DESC;
```

### 查詢本學期已開放的公司
```sql
SELECT 
    ic.id, ic.company_name, ic.status,
    co.is_open, co.opened_at
FROM internship_companies ic
JOIN company_openings co ON ic.id = co.company_id
WHERE co.semester = '1132'  -- 當前學期
  AND co.is_open = TRUE
  AND ic.status = 'approved'
ORDER BY ic.company_name;
```

### 查詢學生志願序（含公司與職缺資訊）
```sql
SELECT 
    u.name AS student_name, u.username AS student_number,
    sp.preference_order, sp.submitted_at,
    ic.company_name, ij.title AS job_title
FROM student_preferences sp
JOIN users u ON sp.student_id = u.id
JOIN internship_companies ic ON sp.company_id = ic.id
JOIN internship_jobs ij ON sp.job_id = ij.id
WHERE sp.semester_id = 1  -- 當前學期
ORDER BY u.name, sp.preference_order;
```

---

本文檔基於程式碼分析與業務流程說明整理，實際資料表結構可能因實作細節而略有差異。建議對照實際資料庫結構進行驗證。




