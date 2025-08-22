import app
from app import get_db, generate_password_hash

def create_test_user():
    conn = get_db()
    cursor = conn.cursor()
    
    # 創建測試用戶
    test_username = "test123"
    test_password = "test123"
    test_role = "student"
    test_email = "test123@stu.ukn.edu.tw"
    
    # 檢查用戶是否已存在
    cursor.execute("SELECT * FROM users WHERE username = %s", (test_username,))
    if cursor.fetchone():
        print("測試用戶已存在")
        return
    
    # 創建新用戶
    hashed_password = generate_password_hash(test_password)
    cursor.execute(
        "INSERT INTO users (username, password, role, email) VALUES (%s, %s, %s, %s)",
        (test_username, hashed_password, test_role, test_email)
    )
    conn.commit()
    print(f"創建測試用戶成功: {test_username}, 密碼: {test_password}")
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    create_test_user() 