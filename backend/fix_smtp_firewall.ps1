# PowerShell 腳本：允許 Python 通過 Windows 防火牆
# 以系統管理員身份執行此腳本

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Windows 防火牆設定 - 允許 SMTP 連線" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 檢查是否以管理員身份執行
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "❌ 錯誤：請以系統管理員身份執行此腳本！" -ForegroundColor Red
    Write-Host "   右鍵點擊 PowerShell，選擇「以系統管理員身份執行」" -ForegroundColor Yellow
    pause
    exit
}

Write-Host "✅ 已確認以管理員身份執行" -ForegroundColor Green
Write-Host ""

# 取得 Python 執行檔路徑
$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonPath) {
    $pythonPath = (Get-Command python3 -ErrorAction SilentlyContinue).Source
}

if ($pythonPath) {
    Write-Host "找到 Python: $pythonPath" -ForegroundColor Green
    
    # 允許 Python 通過防火牆
    Write-Host "正在新增防火牆規則..." -ForegroundColor Yellow
    try {
        New-NetFirewallRule -DisplayName "Python - SMTP Outbound" `
            -Direction Outbound `
            -Program $pythonPath `
            -Action Allow `
            -Profile Any `
            -ErrorAction SilentlyContinue | Out-Null
        
        Write-Host "✅ 已允許 Python 通過防火牆" -ForegroundColor Green
    } catch {
        Write-Host "⚠️  規則可能已存在" -ForegroundColor Yellow
    }
} else {
    Write-Host "⚠️  找不到 Python，將新增連接埠規則" -ForegroundColor Yellow
}

# 允許 SMTP 連接埠
Write-Host ""
Write-Host "正在新增 SMTP 連接埠規則..." -ForegroundColor Yellow

$ports = @(587, 465, 25)

foreach ($port in $ports) {
    try {
        New-NetFirewallRule -DisplayName "SMTP Port $port - Outbound" `
            -Direction Outbound `
            -Protocol TCP `
            -LocalPort $port `
            -Action Allow `
            -Profile Any `
            -ErrorAction SilentlyContinue | Out-Null
        
        Write-Host "✅ 已允許連接埠 $port" -ForegroundColor Green
    } catch {
        Write-Host "⚠️  連接埠 $port 規則可能已存在" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "✅ 防火牆設定完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "請重新測試 Email 發送功能" -ForegroundColor Yellow
Write-Host ""
pause

