[CmdletBinding()]
param(
    [switch]$Offline,
    [switch]$NoOpen,
    [switch]$ForceRebuild,
    [ValidateRange(30, 1800)][int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$backendProcess = $null
$frontendProcess = $null
try {
    $context = Initialize-MarsNativeContext
    $existing = Read-MarsNativeState
    if ($null -ne $existing -and
        (Test-MarsNativeProcess -ProcessId ([int]$existing.backend_pid)) -and
        (Test-MarsNativeProcess -ProcessId ([int]$existing.frontend_pid))) {
        Write-Host "MARS 已在运行：http://127.0.0.1:$($context.FrontendPort)/" -ForegroundColor Green
        if (-not $NoOpen) { Start-Process "http://127.0.0.1:$($context.FrontendPort)/" }
        exit 0
    }
    if ($null -ne $existing) {
        Stop-MarsNativeProcess -ProcessId ([int]$existing.frontend_pid)
        Stop-MarsNativeProcess -ProcessId ([int]$existing.backend_pid)
        Remove-Item -LiteralPath $context.StatePath -Force -ErrorAction SilentlyContinue
    }

    Write-Host "正在准备 MARS 原生 Windows 运行环境（不使用 Docker）..." -ForegroundColor Cyan
    $runtime = Install-MarsNativeDependencies -Context $context -Offline:$Offline -ForceRebuild:$ForceRebuild
    Set-MarsNativeEnvironment -Context $context -Python $runtime.Python

    $backendStdout = Join-Path $context.RuntimeRoot "backend.log"
    $backendStderr = Join-Path $context.RuntimeRoot "backend.err.log"
    $frontendStdout = Join-Path $context.RuntimeRoot "frontend.log"
    $frontendStderr = Join-Path $context.RuntimeRoot "frontend.err.log"
    foreach ($path in @($backendStdout, $backendStderr, $frontendStdout, $frontendStderr)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }

    $backendProcess = Start-MarsNativeProcess -FilePath $runtime.Python -Arguments @(
        "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", [string]$context.BackendPort
    ) -WorkingDirectory $context.RepoRoot -StdoutPath $backendStdout -StderrPath $backendStderr
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    Wait-MarsNativeEndpoint -Name "后端" -Uri (
        "http://127.0.0.1:$($context.BackendPort)/health"
    ) -Deadline $deadline -Process $backendProcess

    [Environment]::SetEnvironmentVariable("PORT", [string]$context.FrontendPort, "Process")
    [Environment]::SetEnvironmentVariable("HOSTNAME", "127.0.0.1", "Process")
    $frontendServerRoot = Split-Path -Parent $runtime.FrontendServer
    $frontendProcess = Start-MarsNativeProcess -FilePath $runtime.Node -Arguments @(
        $runtime.FrontendServer
    ) -WorkingDirectory $frontendServerRoot -StdoutPath $frontendStdout -StderrPath $frontendStderr
    Wait-MarsNativeEndpoint -Name "前端" -Uri (
        "http://127.0.0.1:$($context.FrontendPort)/"
    ) -Deadline $deadline -Process $frontendProcess

    @{
        backend_pid = $backendProcess.Id
        frontend_pid = $frontendProcess.Id
        started_at = (Get-Date).ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath $context.StatePath -Encoding UTF8

    Write-Host ""
    Write-Host "MARS V3.1 原生 Windows 版已启动（无 Docker）" -ForegroundColor Green
    Write-Host "前端：http://127.0.0.1:$($context.FrontendPort)/"
    Write-Host "后端：http://127.0.0.1:$($context.BackendPort)/health"
    Write-Host "日志：$($context.RuntimeRoot)"
    if (-not $NoOpen) {
        Start-Process "http://127.0.0.1:$($context.FrontendPort)/"
    }
}
catch {
    if ($null -ne $frontendProcess) { Stop-MarsNativeProcess -ProcessId $frontendProcess.Id }
    if ($null -ne $backendProcess) { Stop-MarsNativeProcess -ProcessId $backendProcess.Id }
    Write-Host ""
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "请查看 deploy\windows-native\runtime 下的错误日志。" -ForegroundColor Yellow
    exit 1
}
