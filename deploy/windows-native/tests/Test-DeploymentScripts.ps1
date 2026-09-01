[CmdletBinding()]
param()

# Dependency-free control-flow and syntax tests for Windows PowerShell 5.1 and
# PowerShell 7. They do not install packages or start MARS processes.
$ErrorActionPreference = "Stop"
$sourceDeployRoot = Split-Path -Parent $PSScriptRoot
$sourceRepoRoot = [IO.Path]::GetFullPath((Join-Path $sourceDeployRoot "..\.."))
. (Join-Path $sourceDeployRoot "Common.ps1")
$script:AssertionCount = 0

function Assert-Test {
    param([bool]$Condition, [string]$Description)
    if (-not $Condition) { throw "FAILED: $Description" }
    $script:AssertionCount += 1
}

function Assert-TestThrows {
    param([scriptblock]$Action, [string]$Pattern)
    try {
        & $Action | Out-Null
    }
    catch {
        Assert-Test -Condition ($_.Exception.Message -like $Pattern) -Description (
            "expected '$Pattern'; received '$($_.Exception.Message)'"
        )
        return
    }
    throw "FAILED: expected an error matching '$Pattern'"
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("mars-native-script-test-" + [guid]::NewGuid().ToString("N"))
$settingNames = @("MARS_V31_OVERLAY_PATH", "MARS_FRONTEND_PORT", "MARS_BACKEND_PORT")
$savedSettings = @{}
foreach ($name in $settingNames) {
    $savedSettings[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}

try {
    foreach ($file in Get-ChildItem -LiteralPath $sourceDeployRoot -Filter "*.ps1" -Recurse) {
        $tokens = $null
        $parseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $file.FullName, [ref]$tokens, [ref]$parseErrors
        ) | Out-Null
        Assert-Test -Condition (@($parseErrors).Count -eq 0) -Description "PowerShell syntax: $($file.Name)"
    }

    $script:MarsRepoRoot = Join-Path $testRoot "mars_v2"
    $script:MarsNativeRoot = Join-Path $script:MarsRepoRoot "deploy\windows-native"
    $script:MarsRuntimeRoot = Join-Path $script:MarsNativeRoot "runtime"
    $script:MarsStatePath = Join-Path $script:MarsRuntimeRoot "processes.json"
    $testOverlay = Join-Path $testRoot "mars_v31_wireless"
    $packDir = Join-Path $testOverlay "project_packs\pimc"
    $adapterDir = Join-Path $testOverlay "src\mars_v31_wireless"
    foreach ($directory in @($script:MarsNativeRoot, $packDir, $adapterDir)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    Copy-Item -LiteralPath (Join-Path $sourceDeployRoot "windows.env.example") -Destination $script:MarsNativeRoot
    [IO.File]::WriteAllText((Join-Path $packDir "project_pack.yaml"), "test: true`n")
    [IO.File]::WriteAllText((Join-Path $adapterDir "adapter.py"), "# fixture`n")

    $context = Initialize-MarsNativeContext
    Assert-Test -Condition ($context.FrontendPort -eq 3001 -and $context.BackendPort -eq 8000) -Description "default ports"
    Assert-Test -Condition ($context.OverlayPath -eq $testOverlay) -Description "sibling overlay"
    Assert-Test -Condition (-not $context.Settings.ContainsKey("DEEPSEEK_API_KEY")) -Description "no empty API key"

    $env:MARS_FRONTEND_PORT = "8000"
    Assert-TestThrows -Action { Initialize-MarsNativeContext } -Pattern "*相同端口*"
    $env:MARS_FRONTEND_PORT = "3001"
    Assert-TestThrows -Action {
        ConvertTo-MarsNativePort -Name "fixture" -Value "0"
    } -Pattern "*65535*"
    $env:MARS_V31_OVERLAY_PATH = Join-Path $testRoot "missing"
    Assert-TestThrows -Action { Initialize-MarsNativeContext } -Pattern "*Overlay*"
    $env:MARS_V31_OVERLAY_PATH = $testOverlay

    $startCmd = Get-Content -LiteralPath (Join-Path $sourceRepoRoot "start-mars-windows.cmd") -Raw
    Assert-Test -Condition (-not $startCmd.Contains("deploy\windows\")) -Description "default launcher selects native deployment"

    Write-Host "PASS: $script:AssertionCount native Windows deployment script assertions." -ForegroundColor Green
}
finally {
    foreach ($name in $settingNames) {
        [Environment]::SetEnvironmentVariable($name, $savedSettings[$name], "Process")
    }
    if (Test-Path -LiteralPath $testRoot -PathType Container) {
        [IO.Directory]::Delete($testRoot, $true)
    }
}
