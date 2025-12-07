# SMTP 防火牆規則設定腳本
# 請以系統管理員身分執行此腳本

Write-Host "正在添加 SMTP 防火牆規則..." -ForegroundColor Yellow

# 添加出站規則，允許埠 587
try {
    netsh advfirewall firewall add rule name="Allow SMTP Outbound (Port 587)" dir=out action=allow protocol=TCP localport=587
    Write-Host "✅ SMTP 防火牆規則添加成功！" -ForegroundColor Green
} catch {
    Write-Host "❌ 添加防火牆規則失敗：$_" -ForegroundColor Red
    Write-Host "請確認您以系統管理員身分執行此腳本" -ForegroundColor Yellow
    exit 1
}

# 測試連線到 Gmail SMTP 伺服器
Write-Host "`n正在測試連線到 Gmail SMTP 伺服器..." -ForegroundColor Yellow
try {
    $testResult = Test-NetConnection -ComputerName smtp.gmail.com -Port 587 -WarningAction SilentlyContinue
    if ($testResult.TcpTestSucceeded) {
        Write-Host "✅ 可以連線到 Gmail SMTP 伺服器（埠 587）" -ForegroundColor Green
    } else {
        Write-Host "⚠️ 無法連線到 Gmail SMTP 伺服器（埠 587）" -ForegroundColor Yellow
        Write-Host "可能原因：" -ForegroundColor Yellow
        Write-Host "  1. 網路連線問題" -ForegroundColor Yellow
        Write-Host "  2. ISP 阻擋了 SMTP 埠" -ForegroundColor Yellow
        Write-Host "  3. Gmail SMTP 伺服器暫時無法回應" -ForegroundColor Yellow
    }
} catch {
    Write-Host "⚠️ 測試連線時發生錯誤：$_" -ForegroundColor Yellow
}

Write-Host "`n完成！請重新嘗試發送郵件。" -ForegroundColor Cyan
