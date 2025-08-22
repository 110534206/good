import requests
import json

def test_login():
    url = "http://localhost:5000/api/login"
    
    # 測試數據 - 使用資料庫中存在的用戶
    test_cases = [
        {"username": "110534235", "password": "A123456789"},  # 學生
        {"username": "user", "password": "password"},  # 教師或行政
        {"username": "admin", "password": "admin"},  # 管理員
    ]
    
    for i, test_data in enumerate(test_cases, 1):
        print(f"\n測試 {i}: {test_data['username']}")
        try:
            response = requests.post(url, json=test_data)
            print(f"狀態碼: {response.status_code}")
            print(f"回應: {response.json()}")
        except Exception as e:
            print(f"錯誤: {e}")

if __name__ == "__main__":
    test_login() 