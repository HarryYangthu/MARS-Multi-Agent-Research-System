[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

try {
    $context = Initialize-MarsDeployment -SkipMountValidation
    Assert-MarsDocker
    $arguments = @("compose") + $context.ComposeArguments + @("down", "--remove-orphans")
    Invoke-MarsDocker -Arguments $arguments -Description "MARS 停止"
    Write-Host "MARS 已停止。运行数据和配置 volumes 已保留。" -ForegroundColor Green
}
catch {
    Write-Host "停止失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
