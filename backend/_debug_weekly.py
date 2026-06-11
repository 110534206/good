from config import get_db
from datetime import date
from semester import get_student_internship_config, is_weekly_journal_open

conn = get_db()
cur = conn.cursor(dictionary=True)

cur.execute("SELECT id, code, is_active, start_date, end_date FROM semesters ORDER BY code")
print("=== semesters ===")
for r in cur.fetchall():
    print(r)

cur.execute("""
    SELECT ic.id, ic.admission_year, ic.user_id, ic.semester_id, s.code AS semester_code,
           ic.intern_start_date, ic.intern_end_date
    FROM internship_configs ic
    LEFT JOIN semesters s ON s.id = ic.semester_id
    ORDER BY ic.admission_year, ic.user_id
""")
print("\n=== internship_configs ===")
for r in cur.fetchall():
    print(r)

cur.execute("""
    SELECT id, username, admission_year, role FROM users
    WHERE role = 'student' AND (admission_year = 110 OR username LIKE '110%')
    LIMIT 5
""")
print("\n=== sample 110 students ===")
students = cur.fetchall()
for r in students:
    print(r)

print("\n=== weekly journal check ===")
print("today:", date.today())
for s in students:
    cfg = get_student_internship_config(cur, s["id"])
    open_ = is_weekly_journal_open(cur, s["id"])
    print(f"user {s['id']} ({s['username']}): config={cfg}, open={open_}")

cur.close()
conn.close()
