[CmdletBinding()]
param()

# Dependency-free tests for both Windows PowerShell 5.1 and PowerShell 7.
# All Docker responses below are TEST DOUBLES. No daemon, image, provider,
# user configuration, or existing MARS volume is touched by this script.
$ErrorActionPreference = "Stop"
$sourceDeployRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $sourceDeployRoot "Common.ps1")
$script:AssertionCount = 0
$script:FakeDockerExit = 0
$script:FakeDockerPlatform = "linux/amd64"

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

function New-TestReadiness {
    return [PSCustomObject]@{
        ready = $true
        runtime_mode = "development"
        mock_mode = "auto"
        execution_device = "cpu"
        execution_backend = "mock"
        checks = @()
    }
}

function docker {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $global:LASTEXITCODE = $script:FakeDockerExit
    if ($Arguments[0] -eq "version") { return "linux|amd64" }
    if ($Arguments[0] -eq "compose") { return "Docker Compose version v2.39.0-test" }
    if ($Arguments[0] -eq "image") { return $script:FakeDockerPlatform }
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("mars-windows-script-test-" + [guid]::NewGuid().ToString("N"))
$settingNames = @(
    "MARS_V31_OVERLAY_PATH", "MARS_FRONTEND_PORT", "MARS_BACKEND_PORT",
    "MARS_EXECUTION_DEVICE", "MARS_RUNTIME_MODE", "MARS_MOCK_MODE",
    "PIMC_REPO_HOST_PATH", "PIMC_DATA_HOST_PATH"
)
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
    $script:MarsDeployRoot = Join-Path $script:MarsRepoRoot "deploy\windows"
    $testOverlay = Join-Path $testRoot "mars_v31_wireless"
    $packDir = Join-Path $testOverlay "project_packs\pimc"
    $adapterDir = Join-Path $testOverlay "src\mars_v31_wireless"
    foreach ($directory in @($script:MarsDeployRoot, $packDir, $adapterDir)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    Copy-Item -LiteralPath (Join-Path $sourceDeployRoot "windows.env.example") -Destination $script:MarsDeployRoot
    [IO.File]::WriteAllText((Join-Path $packDir "project_pack.yaml"), "test: true`n")
    [IO.File]::WriteAllText((Join-Path $adapterDir "adapter.py"), "# test fixture only`n")

    $context = Initialize-MarsDeployment
    Assert-Test -Condition ($context.FrontendPort -eq 3001 -and $context.BackendPort -eq 8000) -Description "default ports"
    Assert-Test -Condition ($context.ExecutionDevice -eq "cpu") -Description "CPU first"
    Assert-Test -Condition ($context.RuntimeMode -eq "development" -and $context.MockMode -eq "auto") -Description "demo defaults"
    Assert-Test -Condition (-not $context.Settings.ContainsKey("DEEPSEEK_API_KEY")) -Description "no blank credential overriding UI persistence"
    Assert-MarsOfflinePorts -Context $context
    Assert-MarsDocker
    Assert-MarsImagePlatform -Image "test-image"

    $env:MARS_EXECUTION_DEVICE = "invalid-device"
    Assert-TestThrows -Action { Initialize-MarsDeployment } -Pattern "*cpu*gpu*"
    $env:MARS_EXECUTION_DEVICE = "cpu"
    $env:MARS_FRONTEND_PORT = "8000"
    Assert-TestThrows -Action { Initialize-MarsDeployment } -Pattern "*同一个端口*"
    $env:MARS_FRONTEND_PORT = "3001"
    Assert-TestThrows -Action { ConvertTo-MarsPort -Name "test-port" -Value "65536" } -Pattern "*65535*"
    Assert-TestThrows -Action {
        Assert-MarsOfflinePorts -Context ([PSCustomObject]@{ FrontendPort = 3101; BackendPort = 8000 })
    } -Pattern "*3001*8000*"

    $env:MARS_V31_OVERLAY_PATH = Join-Path $testRoot "missing-overlay"
    Assert-TestThrows -Action { Initialize-MarsDeployment } -Pattern "*Overlay*"
    $env:MARS_V31_OVERLAY_PATH = $testOverlay
    Assert-TestThrows -Action { Initialize-MarsDeployment -Production } -Pattern "*PIMC_REPO_HOST_PATH*PIMC_DATA_HOST_PATH*"
    $env:MARS_RUNTIME_MODE = "production"
    Assert-TestThrows -Action { Initialize-MarsDeployment } -Pattern "*-Production*"
    $env:MARS_RUNTIME_MODE = "development"

    $testRepo = Join-Path $testRoot "private-repo-fixture"
    $testData = Join-Path $testRoot "private-data-fixture"
    New-Item -ItemType Directory -Path (Join-Path $testRepo "tools") -Force | Out-Null
    New-Item -ItemType Directory -Path $testData -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $testRepo "tools\mars_adapter_entry.py"), "# test fixture only`n")
    [IO.File]::WriteAllText((Join-Path $testRepo "mars_baseline_manifest.json"), "{}")
    [IO.File]::WriteAllText((Join-Path $testData "mars_data_manifest.json"), "{}")
    $env:PIMC_REPO_HOST_PATH = $testRepo
    $env:PIMC_DATA_HOST_PATH = $testData
    $productionContext = Initialize-MarsDeployment -Production
    Assert-Test -Condition ($productionContext.RuntimeMode -eq "production" -and $productionContext.MockMode -eq "never") -Description "production does not inherit demo mode"
    Assert-Test -Condition ($productionContext.ComposeArguments -contains (Join-Path $script:MarsDeployRoot "compose.production.yaml")) -Description "production mount override is applied"

    $health = [PSCustomObject]@{ status = "ok"; distribution = "v31-wireless" }
    $ready = New-TestReadiness
    Assert-MarsReadiness -Context $context -Health $health -Readiness $ready
    $ready.ready = $false
    $ready.checks = @([PSCustomObject]@{ severity = "blocker"; ready = $false; message = "missing provider fixture" })
    Assert-TestThrows -Action { Assert-MarsReadiness -Context $context -Health $health -Readiness $ready } -Pattern "*missing provider fixture*"
    $ready = New-TestReadiness
    $ready.execution_device = "gpu"
    Assert-TestThrows -Action { Assert-MarsReadiness -Context $context -Health $health -Readiness $ready } -Pattern "*不一致*"
    $ready = New-TestReadiness
    $ready.ready = "true"
    Assert-TestThrows -Action { Assert-MarsReadiness -Context $context -Health $health -Readiness $ready } -Pattern "*尚不能创建任务*"
    $ready = New-TestReadiness
    $ready.runtime_mode = "production"
    $ready.mock_mode = "never"
    Assert-TestThrows -Action { Assert-MarsReadiness -Context $productionContext -Health $health -Readiness $ready } -Pattern "*mock*"
    $ready.execution_backend = "local_command"
    Assert-MarsReadiness -Context $productionContext -Health $health -Readiness $ready
    Assert-TestThrows -Action {
        Assert-MarsReadiness -Context $context -Health ([PSCustomObject]@{ status = "ok" }) -Readiness (New-TestReadiness)
    } -Pattern "*distribution*"

    $archive = Join-Path $testRoot "fixture.tar"
    [IO.File]::WriteAllText($archive, "not a real Docker archive; hash test only")
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText(($archive + ".sha256"), "$hash  fixture.tar`n")
    Assert-Test -Condition ((Resolve-MarsImageArchive -Path $archive) -eq $archive) -Description "valid SHA256 sidecar"
    [IO.File]::WriteAllText(($archive + ".sha256"), "")
    Assert-TestThrows -Action { Resolve-MarsImageArchive -Path $archive } -Pattern "*格式错误*"
    [IO.File]::WriteAllText(($archive + ".sha256"), ("0" * 64))
    Assert-TestThrows -Action { Resolve-MarsImageArchive -Path $archive } -Pattern "*校验失败*"
    [IO.File]::Delete($archive + ".sha256")
    Assert-TestThrows -Action { Resolve-MarsImageArchive -Path $archive } -Pattern "*缺少 SHA256*"

    $script:FakeDockerPlatform = "linux/arm64"
    Assert-TestThrows -Action { Assert-MarsImagePlatform -Image "test-image" } -Pattern "*架构错误*"
    $script:FakeDockerExit = 7
    Assert-TestThrows -Action { Invoke-MarsDocker -Arguments @("test-only") -Description "fixture" } -Pattern "*7*"
    $global:LASTEXITCODE = 0

    Write-Host "PASS: $script:AssertionCount Windows deployment script assertions (Docker mocked)." -ForegroundColor Green
}
finally {
    foreach ($name in $settingNames) {
        [Environment]::SetEnvironmentVariable($name, $savedSettings[$name], "Process")
    }
    # This exact GUID directory was created by this test and contains fixtures only.
    if (Test-Path -LiteralPath $testRoot -PathType Container) {
        [IO.Directory]::Delete($testRoot, $true)
    }
}
