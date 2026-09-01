[CmdletBinding()]
param(
    [switch]$Offline,
    [switch]$Production,
    [switch]$NoOpen,
    [string]$ImageArchive = "",
    [ValidateRange(30, 1800)][int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

try {
    $context = Initialize-MarsDeployment -Production:$Production
    Assert-MarsDocker
    Test-MarsComposeConfiguration -Context $context

    if ($Offline) {
        Assert-MarsOfflinePorts -Context $context
        $ImageArchive = Resolve-MarsImageArchive -Path $ImageArchive
        $expectedPlatform = Get-MarsSetting -Name "MARS_DOCKER_PLATFORM" -Values $context.Settings -Default "linux/amd64"
        if ($expectedPlatform -ne "linux/amd64") {
            throw "Windows 离线包只允许 linux/amd64，当前配置：$expectedPlatform"
        }
        Invoke-MarsDocker -Arguments @("load", "--input", $ImageArchive) -Description "离线镜像导入"
        $backendImage = Get-MarsSetting -Name "MARS_BACKEND_IMAGE" -Values $context.Settings -Default "mars-v31-backend:windows-amd64"
        $frontendImage = Get-MarsSetting -Name "MARS_FRONTEND_IMAGE" -Values $context.Settings -Default "mars-v31-frontend:windows-amd64"
        $redisImage = Get-MarsSetting -Name "MARS_REDIS_IMAGE" -Values $context.Settings -Default "redis:7.4-alpine"
        foreach ($image in @($backendImage, $frontendImage, $redisImage)) {
            Assert-MarsImagePlatform -Image $image -Expected $expectedPlatform
        }
        $upArguments = @("compose") + $context.ComposeArguments + @(
            "up", "--detach", "--no-build", "--pull", "never", "--remove-orphans"
        )
    }
    else {
        $upArguments = @("compose") + $context.ComposeArguments + @(
            "up", "--detach", "--build", "--remove-orphans"
        )
    }

    Write-Host "正在启动 MARS（$($context.ExecutionDevice.ToUpperInvariant()) 模式）..." -ForegroundColor Cyan
    Invoke-MarsDocker -Arguments $upArguments -Description "MARS 启动"

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $backendUrl = "http://127.0.0.1:$($context.BackendPort)/health"
    $frontendUrl = "http://127.0.0.1:$($context.FrontendPort)/"
    Wait-MarsHttpEndpoint -Name "后端" -Uri $backendUrl -Deadline $deadline
    Wait-MarsHttpEndpoint -Name "前端" -Uri $frontendUrl -Deadline $deadline

    $health = Invoke-RestMethod -Uri $backendUrl -TimeoutSec 5
    $readiness = Invoke-RestMethod -Uri (
        "http://127.0.0.1:$($context.BackendPort)/api/readiness"
    ) -TimeoutSec 10
    Assert-MarsReadiness -Context $context -Health $health -Readiness $readiness

    Write-Host ""
    Write-Host "MARS V3.1 已启动" -ForegroundColor Green
    Write-Host "前端：$frontendUrl"
    Write-Host "后端：http://127.0.0.1:$($context.BackendPort)"
    Write-Host "设备：$($context.ExecutionDevice.ToUpperInvariant())"
    Write-Host "数据：Docker Linux volumes（重启保留）"
    if ($Production) {
        Write-Host "模式：生产，真实 PIMC 仓库与数据只读挂载"
    }
    Write-Host "运行模式：$($context.RuntimeMode)，mock 策略：$($context.MockMode)"
    if (-not $NoOpen) {
        Start-Process $frontendUrl
    }
}
catch {
    Write-Host ""
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "已启动的容器和数据会保留，修正配置后可重新启动；停止请运行 stop-mars.cmd。"
    Write-Host "可运行 Test-Mars.ps1 做环境检查。" -ForegroundColor Yellow
    exit 1
}
