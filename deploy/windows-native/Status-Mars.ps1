[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

try {
    $context = Initialize-MarsNativeContext
    $state = Read-MarsNativeState
    if ($null -eq $state) {
        throw "未找到运行状态；请先双击 start-mars-windows-native.cmd。"
    }
    $backendRunning = Test-MarsNativeProcess -ProcessId ([int]$state.backend_pid)
    $frontendRunning = Test-MarsNativeProcess -ProcessId ([int]$state.frontend_pid)
    Write-Host "后端进程：$backendRunning（PID $($state.backend_pid)）"
    Write-Host "前端进程：$frontendRunning（PID $($state.frontend_pid)）"
    if (-not $backendRunning -or -not $frontendRunning) {
        throw "至少一个 MARS 进程已经退出，请检查 runtime 错误日志。"
    }
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($context.BackendPort)/health" -TimeoutSec 10
    $frontend = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$($context.FrontendPort)/" -TimeoutSec 15
    if ($health.status -ne "ok" -or $frontend.StatusCode -ne 200) {
        throw "服务进程存在，但健康检查没有通过。"
    }
    Write-Host "MARS 原生 Windows 服务运行正常：http://127.0.0.1:$($context.FrontendPort)/" -ForegroundColor Green
}
catch {
    Write-Host "状态检查失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
