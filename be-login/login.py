import mysql.connector

# 建立連線
conn = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",  # XAMPP 預設無密碼
    database="ai_resume_db"
)

# 建立 cursor
cursor = conn.cursor()

# 範例：建立資料表
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    password VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL
)
""")

# 範例：新增一筆帳號
sql = "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)"
val = ("user", "1234", "teacher")
cursor.execute(sql, val)
conn.commit()

print("新增成功，ID:", cursor.lastrowid)

# 關閉連線
cursor.close()
conn.close()
