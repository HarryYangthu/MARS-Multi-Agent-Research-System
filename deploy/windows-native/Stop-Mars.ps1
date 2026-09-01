[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

try {
    $state = Read-MarsNativeState
    if ($null -eq $state) {
        Write-Host "MARS 当前没有原生 Windows 进程记录。"
        exit 0
    }
    Stop-MarsNativeProcess -ProcessId ([int]$state.frontend_pid)
    Stop-MarsNativeProcess -ProcessId ([int]$state.backend_pid)
    Remove-Item -LiteralPath $script:MarsStatePath -Force -ErrorAction SilentlyContinue
    Write-Host "MARS 已停止；runs、knowledge 和配置均已保留。" -ForegroundColor Green
}
catch {
    Write-Host "停止失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
