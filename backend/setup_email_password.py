#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Email 密碼設定輔助工具
幫助您輕鬆設定 Gmail 應用程式密碼
"""

import os
import re

def setup_password():
    """協助設定 Email 密碼"""
    print("\n" + "="*60)
    print("🔧 Email 密碼設定工具")
    print("="*60 + "\n")
    
    print("📝 請輸入您的 Gmail 應用程式密碼（16 位）")
    print("   如果您還沒有應用程式密碼，請前往：")
    print("   https://myaccount.google.com/apppasswords\n")
    
    password = input("請輸入應用程式密碼: ").strip()
    
    if not password:
        print("❌ 錯誤：密碼不能為空")
        return False
    
    # 移除空格（如果有）
    password_clean = password.replace(" ", "")
    
    # 檢查長度（應該是 16 位）
    if len(password_clean) != 16:
        print(f"⚠️ 警告：密碼長度為 {len(password_clean)} 位，應該是 16 位")
        confirm = input("是否繼續使用此密碼？(y/n): ").strip().lower()
        if confirm != 'y':
            return False
    
    # 讀取 EMAIL.env 檔案
    env_path = os.path.join(os.path.dirname(__file__), 'EMAIL.env')
    
    if not os.path.exists(env_path):
        print(f"❌ 錯誤：找不到 EMAIL.env 檔案: {env_path}")
        return False
    
    # 讀取檔案內容
    with open(env_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 使用正則表達式替換 SMTP_PASSWORD
    # 匹配 SMTP_PASSWORD="" 或 SMTP_PASSWORD="任何內容"
    pattern = r'(SMTP_PASSWORD=")([^"]*)(")'
    replacement = f'\\1{password}\\3'
    
    if re.search(pattern, content):
        new_content = re.sub(pattern, replacement, content)
        
        # 備份原檔案
        backup_path = env_path + '.backup'
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅ 已備份原檔案到: {backup_path}")
        
        # 寫入新內容
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print(f"✅ 密碼已成功設定到 {env_path}")
        print("\n💡 提示：您現在可以執行以下命令測試 Email 發送：")
        print("   python test_email.py\n")
        return True
    else:
        print("❌ 錯誤：在 EMAIL.env 中找不到 SMTP_PASSWORD 設定")
        return False

if __name__ == "__main__":
    try:
        success = setup_password()
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️ 使用者取消設定")
        exit(1)
    except Exception as e:
        print(f"\n❌ 發生錯誤: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

