[CmdletBinding()]
param([switch]$Running)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

try {
    $context = Initialize-MarsNativeContext
    $basePython = Resolve-MarsBasePython
    $node = Resolve-MarsNode
    Write-Host "Python 3.11：$($basePython.FilePath)"
    Write-Host "Node.js 20+：$($node.Node)"
    Write-Host "Overlay：$($context.OverlayPath)"
    Write-Host "前端端口：$($context.FrontendPort)"
    Write-Host "后端端口：$($context.BackendPort)"
    if ($Running) {
        & (Join-Path $PSScriptRoot "Status-Mars.ps1")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    Write-Host "MARS 原生 Windows 前置检查通过（不使用 Docker）" -ForegroundColor Green
}
catch {
    Write-Host "检查失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
