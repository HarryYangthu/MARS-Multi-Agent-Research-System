[CmdletBinding()]
param(
    [switch]$Production,
    [switch]$Running,
    [switch]$Offline,
    [string]$ImageArchive = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

try {
    $context = Initialize-MarsDeployment -Production:$Production
    Assert-MarsDocker
    Test-MarsComposeConfiguration -Context $context
    if ($Offline) {
        Assert-MarsOfflinePorts -Context $context
        $verifiedArchive = Resolve-MarsImageArchive -Path $ImageArchive
        Write-Host "离线包 SHA256 校验通过：$verifiedArchive"
    }
    if ($Running) {
        $backendUrl = "http://127.0.0.1:$($context.BackendPort)"
        $health = Invoke-RestMethod -Uri "$backendUrl/health" -TimeoutSec 10
        $readiness = Invoke-RestMethod -Uri "$backendUrl/api/readiness" -TimeoutSec 10
        Assert-MarsReadiness -Context $context -Health $health -Readiness $readiness
        $response = Invoke-WebRequest -UseBasicParsing -Uri (
            "http://127.0.0.1:$($context.FrontendPort)/"
        ) -TimeoutSec 15
        if ($response.StatusCode -ne 200) {
            throw "前端未返回 HTTP 200。"
        }
        Write-Host "前端、后端及任务准入配置检查通过（不代表真实模型或 GPU 已验证）" -ForegroundColor Green
    }
    Write-Host "Windows Docker 前置检查通过" -ForegroundColor Green
    Write-Host "Docker：Linux containers"
    Write-Host "Overlay：$($context.OverlayPath)（只读挂载）"
    Write-Host "前端端口：$($context.FrontendPort)"
    Write-Host "后端端口：$($context.BackendPort)"
    Write-Host "生产模式：$($context.Production)"
}
catch {
    Write-Host "检查失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
