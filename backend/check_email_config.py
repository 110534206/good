#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
檢查 Email 設定
"""

import os
from dotenv import load_dotenv

# 載入環境變數
env_path = os.path.join(os.path.dirname(__file__), 'EMAIL.env')
if os.path.exists(env_path):
    load_dotenv(env_path)

print("\n" + "="*60)
print("📋 Email 設定檢查")
print("="*60 + "\n")

smtp_user = os.getenv("SMTP_USER", "")
smtp_password = os.getenv("SMTP_PASSWORD", "")
email_enabled = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
use_smtp = os.getenv("USE_SMTP", "true").lower() == "true"

print(f"✅ 郵件功能啟用: {email_enabled}")
print(f"✅ 使用 SMTP: {use_smtp}")
print(f"✅ 寄件人信箱: {smtp_user if smtp_user else '❌ 未設定'}")
print(f"✅ SMTP 密碼: {'✅ 已設定' if smtp_password else '❌ 未設定'}")

if smtp_password:
    # 顯示密碼長度（不顯示實際密碼）
    password_clean = smtp_password.replace(" ", "").strip()
    print(f"   - 密碼長度（含空格）: {len(smtp_password)}")
    print(f"   - 密碼長度（不含空格）: {len(password_clean)}")
    print(f"   - 預期長度: 16")
    
    if len(password_clean) != 16:
        print(f"   ⚠️ 警告：密碼長度不正確！應該是 16 位")
    
    # 檢查是否包含空格
    if " " in smtp_password:
        print(f"   - 密碼包含空格，將自動去除")
    
    print(f"   - 密碼前 4 位: {password_clean[:4]}...")
    print(f"   - 密碼後 4 位: ...{password_clean[-4:]}")

print("\n" + "="*60)
print("💡 如果認證失敗，請確認：")
print("="*60)
print("1. 應用程式密碼是否正確複製（16 位）")
print("2. 是否已啟用兩步驟驗證")
print("3. 帳號是否正確：", smtp_user)
print("4. 可以嘗試重新產生應用程式密碼")
print("   https://myaccount.google.com/apppasswords")
print()

