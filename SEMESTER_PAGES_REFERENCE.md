# 學期連動頁面整理（系統需連接到學期的頁面）

## 一、已記住的參考檔案

- **`semesters (5).sql`**：學期表結構與資料。`semesters` 表含 `id`, `code`（如 1132）, `start_date`, `end_date`, `is_active`。當前學期為 `is_active = 1`（例如 1132, id=1）。
- **`good/admin_frontend/templates/admin/manage_semesters.html`**：學期管理後台頁面。
- **`internship_configs.sql`**：實習配置。`internship_configs` 表以 `admission_year`（入學年度，如 110、111）+ `semester_id` 對應「該屆實習在哪一學期」。
- **`users (7).sql`**：使用者表。學生有 `admission_year`、`current_semester_code`（實習學期，實際存的是 **semester id**，如 110 屆=1、111 屆=8）。

## 二、邏輯說明

- 系統目前設定為 **1132 學期**（`semesters.is_active = 1`，id=1）。
- **110 學年入學**的學生：`internship_configs` 對應 `semester_id = 1`（1132），即「本學期是他們的實習學期」，可進行實習作業流程。
- **111 學年入學**的學生：對應 `semester_id = 9`（1142），實習在下一學期，**不應**看到「本學期實習」專用頁面（如查看公司、填志願）。

判斷是否為「當前實習學期學生」：

- 學生可進入「本學期實習」頁面 **若且唯若**：  
  `users.current_semester_code`（該生的實習學期 id）**等於** 當前活躍學期 id（`semesters.id` WHERE `is_active = 1`）。

---

## 三、需要依學期限制存取的頁面（僅「當前實習學期」學生可看）

以下頁面/API 應加上檢查：**若登入者為學生，且其 `current_semester_code` ≠ 當前學期 id，則不允許進入（導向提示頁或 403）**。

### 3.1 頁面（Route）

| 頁面 | 後端檔案 | Route | 說明 |
|------|----------|--------|------|
| **查看公司 / 投遞履歷** | `good/backend/company.py` | `GET /look_company` | `look_company_page()`，目前僅檢查登入與 role，未區分學期。 |
| **填寫志願序** | `good/backend/preferences.py` | `GET /fill_preferences` | `fill_preferences_page()`，目前用 `get_current_semester_code` 取「本學期公司」，但未阻擋非本學期實習學生進入頁面。 |

### 3.2 相關 API（學生操作，建議一併做學期檢查）

| 功能 | 後端檔案 | Route | 說明 |
|------|----------|--------|------|
| 學生取得公司列表 | `company.py` | `GET /api/student/companies` | 供 look_company 使用，應只對「當前實習學期」學生回傳。 |
| 學生投遞履歷 | `company.py` | `POST /api/student/apply_company` | 應僅允許當前實習學期學生投遞。 |
| 學生我的投遞 | `company.py` | `GET /api/student/my_applications` | 同上。 |
| 取得志願序 / 儲存志願序 | `preferences.py` | `GET /api/get_my_preferences`, `POST /api/save_preferences` 等 | 填寫志願與學期綁定，應僅允許當前實習學期學生。 |

實作方式建議：

- 在 `semester.py` 新增 helper，例如：  
  `is_student_in_current_internship(cursor, user_id)`  
  實作：查 `users.current_semester_code` 與當前 `semesters.id`（is_active=1），相等則回傳 True。
- 在以上頁面與 API 中，若為學生則先呼叫此 helper，若為 False 則 redirect 至提示頁或回傳 403。

---

## 四、不需依學期區分的頁面（所有學生皆可）

以下與「撰寫/管理履歷」相關，實習前即可使用，**不需**依學期限制。

| 頁面 | 後端檔案 | Route | 說明 |
|------|----------|--------|------|
| **履歷管理 / 上傳與撰寫履歷** | `good/backend/resume.py` | `GET /upload_resume` | 學生在實習前即可撰寫履歷，不區分學期。 |
| 履歷管理（列表） | `resume.py` | `GET /resume_folders`（若有） | 同上。 |
| AI 履歷修改 | `resume.py` | `GET /ai_edit_resume` | 同上。 |

相關 API（取得/儲存履歷資料、上傳檔案等）若僅用於「撰寫與管理履歷」，也**不需**依學期阻擋。

---

## 五、前端選單與連結建議

- **學生首頁 / 側邊選單**（如 `student_home.html`, `upload_resume.html` 側欄, `look_company.html` 側欄, `intern_experience.html` 等）：
  - **履歷管理、填寫履歷、AI 履歷修改**：所有學生顯示。
  - **填寫志願序、投遞履歷（look_company）**：僅在「當前實習學期」時顯示或可點；非當學期可隱藏或改為灰階/提示「您本學期非實習學期，無法使用此功能」。
- 若後端已對 `/fill_preferences`、`/look_company` 做學期檢查，非當學期學生點到連結會被 redirect，前端可選擇性依 API 回傳的「是否為當前實習學期」來動態顯示/隱藏選單，以提升體驗。

---

## 六、其他可能與學期連動的學生功能（可再確認）

- **面試/錄取相關**（如面試時程、錄取結果查詢）：若業務上僅限「當學期實習學生」，建議同樣用 `current_semester_code` 與當前學期 id 比對後限制。
- **缺勤、實習成績、實習報告**：若依「實習學期」區分，多數會用 `semester_id` 或 `current_semester_code` 過濾資料即可，不一定需要阻擋進入頁面。

以上整理可作為實作「學期連動」時的檢查清單與實作參考。
