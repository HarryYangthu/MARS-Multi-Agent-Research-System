[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

try {
    $context = Initialize-MarsNativeContext
    $offlineRoot = Join-Path $PSScriptRoot "offline"
    if (Test-Path -LiteralPath $offlineRoot) {
        if (-not $Force) {
            throw "离线依赖目录已经存在；确认更新时使用 -Force：$offlineRoot"
        }
        Remove-Item -LiteralPath $offlineRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $offlineRoot | Out-Null
    $runtime = Install-MarsNativeDependencies -Context $context -ForceRebuild

    $wheelRoot = Join-Path $offlineRoot "wheels"
    New-Item -ItemType Directory -Path $wheelRoot | Out-Null
    $staticCpu = Test-MarsNativeEnabled (
        Get-MarsNativeSetting -Name "MARS_INSTALL_STATIC_CPU" -Values $context.Settings -Default "false"
    )
    $overlayPackage = $context.OverlayPath
    if ($staticCpu) { $overlayPackage = "$($context.OverlayPath)[static]" }
    $syntheticRoot = Join-Path $context.RepoRoot "projects\synthetic_regression"
    Invoke-MarsNativeChecked -FilePath $runtime.Python -Arguments @(
        "-m", "pip", "wheel", "--wheel-dir", $wheelRoot,
        $context.RepoRoot, $overlayPackage, $syntheticRoot
    ) -Description "导出 Windows Python wheels"
    Invoke-MarsNativeChecked -FilePath $runtime.Python -Arguments @(
        "-m", "pip", "download", "--dest", $wheelRoot, "setuptools", "wheel"
    ) -Description "导出 Python 构建依赖"

    $frontendRoot = Join-Path $offlineRoot "frontend"
    $serverRoot = Split-Path -Parent $runtime.FrontendServer
    Copy-Item -LiteralPath $serverRoot -Destination $frontendRoot -Recurse

    $manifestPath = Join-Path $offlineRoot "MANIFEST.sha256"
    $lines = foreach ($file in Get-ChildItem -LiteralPath $offlineRoot -File -Recurse | Sort-Object FullName) {
        if ($file.FullName -eq $manifestPath) { continue }
        $relative = $file.FullName.Substring($offlineRoot.Length + 1).Replace("\", "/")
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
        "$hash  $relative"
    }
    [IO.File]::WriteAllLines($manifestPath, $lines, (New-Object Text.UTF8Encoding($false)))
    Write-Host "原生 Windows 离线依赖已导出：$offlineRoot" -ForegroundColor Green
    Write-Host "静态 CPU 依赖：$staticCpu"
    Write-Host "请连同 mars_v2 和 mars_v31_wireless 一起按公司流程带入内网。"
}
catch {
    Write-Host "导出失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
