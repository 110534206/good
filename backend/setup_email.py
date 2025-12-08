#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Email 功能快速設定腳本
幫助其他開發者快速設定 Email 功能
"""

import os
import shutil

def setup_email():
    """設定 Email 功能"""
    print("\n" + "="*60)
    print("📧 Email 功能快速設定")
    print("="*60 + "\n")
    
    # 檢查 EMAIL.env 是否已存在
    env_path = os.path.join(os.path.dirname(__file__), 'EMAIL.env')
    example_path = os.path.join(os.path.dirname(__file__), 'EMAIL.env.example')
    
    if os.path.exists(env_path):
        print("⚠️  EMAIL.env 檔案已存在")
        response = input("   是否要覆蓋現有設定？(y/N): ").strip().lower()
        if response != 'y':
            print("   取消設定")
            return
    
    # 複製範本
    if not os.path.exists(example_path):
        print("❌ 錯誤：找不到 EMAIL.env.example 檔案")
        print("   請確認檔案存在於 backend/ 目錄中")
        return
    
    try:
        shutil.copy(example_path, env_path)
        print("✅ 已複製 EMAIL.env.example 為 EMAIL.env")
    except Exception as e:
        print(f"❌ 複製檔案失敗：{str(e)}")
        return
    
    print("\n" + "="*60)
    print("📝 請按照以下步驟完成設定：")
    print("="*60)
    print("\n1. 取得 Gmail 應用程式密碼：")
    print("   https://myaccount.google.com/apppasswords")
    print("\n2. 編輯 EMAIL.env 檔案：")
    print(f"   {env_path}")
    print("\n3. 填入以下資訊：")
    print("   - SMTP_USER: 您的 Gmail 地址")
    print("   - SMTP_PASSWORD: Gmail 應用程式密碼（16位）")
    print("\n4. 測試 Email 功能：")
    print("   python test_email_simple.py your-email@example.com")
    print("\n" + "="*60)
    print("💡 提示：")
    print("   - 詳細說明請查看 README_EMAIL_SETUP.md")
    print("   - 如果遇到連線問題，請查看 README_SMTP_FIX.md")
    print("="*60 + "\n")

if __name__ == "__main__":
    try:
        setup_email()
    except KeyboardInterrupt:
        print("\n\n⚠️  使用者中斷設定")
    except Exception as e:
        print(f"\n❌ 發生錯誤：{str(e)}")

