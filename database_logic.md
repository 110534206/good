
---

# 履歷管理系統：多對多資料庫架構說明

## 1. 核心邏輯架構 (Core Logic)

本系統採用 **「樞紐 (Pivot) + 分支 (Branches)」** 的設計模式。

* **resumes**: 儲存實體檔案資訊。
* **student_info**: 儲存學生的基本個人資料。
* **resume_content_mapping**: 作為中心樞紐，記錄「哪份履歷檔案」連結到「哪位學生」。
* **關聯表 (Junction Tables)**: 實現多對多關係，讓同一筆原始資料（如：一張證照）可以被多份履歷草稿差異化引用。

---

## 2. 資料表詳細定義 (Schema Definitions)

### A. 基礎資料表 (Base Tables)

AI 應注意這些表存儲的是「原始資料」，不直接連結履歷 ID：

* `student_info`: 學生基本資料（StuID, 姓名, 電話...）。
* `student_certifications`: 學生擁有的所有證照。
* `course_grades`: 學生所有學期的課程成績。
* `absence_records`: 學生所有的缺勤紀錄。
* `resumes`: 學生上傳的履歷 PDF/Word 檔案實體紀錄。

### B. 樞紐對照表 (Pivot Table)

#### `resume_content_mapping`

* **用途**: 連結檔案與學生。
* **關鍵欄位**:
* `id`: 內部唯一識別碼，供下方關聯表連結。
* `resume_id`: 連結 `resumes.id`。
* `stu_info_id`: 連結 `student_info.StuID`。



### C. 多對多關聯表 (Junction Tables)

這些表取代了舊有的 `TEXT` 逗號分隔欄位，確保資料完整性：

#### 1. 證照關聯表 (`resume_cert_rel`)

* `mapping_id` (FK): 關聯 `resume_content_mapping.id`
* `cert_id` (FK): 關聯 `student_certifications.id`

#### 2. 成績關聯表 (`resume_grade_rel`)

* `mapping_id` (FK): 關聯 `resume_content_mapping.id`
* `grade_id` (FK): 關聯 `course_grades.id`

#### 3. 缺勤紀錄關聯表 (`resume_absence_rel`)

* `mapping_id` (FK): 關聯 `resume_content_mapping.id`
* `absence_id` (FK): 關聯 `absence_records.id`

#### 4. 語文能力關聯表 (`resume_lang_rel`)

* `mapping_id` (FK): 關聯 `resume_content_mapping.id`
* `lang_skill_id` (FK): 關聯 `student_languageskills.id`

---

## 3. 關鍵關聯規則 (Relationship Rules for AI)

1. **一對多關係 (One-to-Many)**:
* 一個學生 (`student_info`) 可以擁有多個履歷映射 (`resume_content_mapping`)。
* 一份履歷檔案 (`resumes`) 只會對應到一個 `resume_content_mapping`。


2. **多對多關係 (Many-to-Many)**:
* 透過中間表，一份履歷草稿可以選取多個證照/成績。
* 同一筆證照/成績紀錄，可以同時被標記在不同的履歷草稿中。


3. **級聯操作 (Cascade Policy)**:
* 當 `resumes` 紀錄被刪除，對應的 `resume_content_mapping` 及其所有中間表的關聯紀錄必須自動刪除。
* 當原始內容（如 `course_grades`）被刪除，所有引用該成績的履歷關聯也應自動清理。



---

## 4. 查詢範例邏輯 (Query Logic)

若要讀取一份履歷的所有勾選內容，Cursor 應遵循此 SQL 邏輯：

```sql
SELECT m.*, s.StuName, c.CertName, g.CourseName, l.Language, l.Level
FROM resume_content_mapping m
JOIN student_info s ON m.stu_info_id = s.StuID
LEFT JOIN resume_cert_rel rcr ON m.id = rcr.mapping_id
LEFT JOIN student_certifications c ON rcr.cert_id = c.id
LEFT JOIN resume_grade_rel rgr ON m.id = rgr.mapping_id
LEFT JOIN course_grades g ON rgr.grade_id = g.id
LEFT JOIN resume_lang_rel rlr ON m.id = rlr.mapping_id
LEFT JOIN student_languageskills l ON rlr.lang_skill_id = l.id
WHERE m.resume_id = ?
```