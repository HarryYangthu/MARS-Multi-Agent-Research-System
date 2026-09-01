[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

try {
    $context = Initialize-MarsDeployment -SkipMountValidation
    Assert-MarsDocker
    $arguments = @("compose") + $context.ComposeArguments + @("ps")
    Invoke-MarsDocker -Arguments $arguments -Description "MARS 状态读取"
    Write-Host "设备：$($context.ExecutionDevice.ToUpperInvariant())"
    Write-Host "前端：http://127.0.0.1:$($context.FrontendPort)/"
    Write-Host "后端：http://127.0.0.1:$($context.BackendPort)/health"
}
catch {
    Write-Host "状态读取失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
