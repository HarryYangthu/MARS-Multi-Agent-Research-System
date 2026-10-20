[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Source = '',
    [switch]$NoBrowser,
    [switch]$SkipConfigure
)
$updateNoBrowser = $NoBrowser
$updateSkipConfigure = $SkipConfigure
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$updateDirectory = Join-Path $root 'local/core-updates'
$receiptPath = Join-Path $updateDirectory 'latest.json'
$driverRecordPath = Join-Path $updateDirectory 'wrapper-driver.json'
$wrapperLock = $null
$mutex = $null
$locked = $false
$applied = $false
$transaction = ''
$wasRunning = $false

function Get-UpdatePython {
    if (Test-Path -LiteralPath $python -PathType Leaf) { return $python }
    $bootstrap = Join-Path $runtime 'tools/python-runtime/python/python.exe'
    if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) {
        $bundle = Read-MarsOfflineBundle $root
        if ($null -eq $bundle) { throw 'Keep the complete dependency folder, or install MARS before updating.' }
        Expand-MarsOfflineTool $bundle 'python' (Join-Path $runtime 'tools/python-runtime')
    }
    if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) { throw 'The bundled Python bootstrap is missing.' }
    return $bootstrap
}

function Read-TrustedUpdateDriver {
    if (-not (Test-Path -LiteralPath $driverRecordPath)) { throw 'Previous update needs its preserved recovery driver.' }
    $saved = Get-Content -LiteralPath $driverRecordPath -Raw | ConvertFrom-Json
    $path = [IO.Path]::GetFullPath([string]$saved.path)
    $parent = [IO.Path]::GetFullPath($updateDirectory).TrimEnd('\') + '\'
    if (-not $path.StartsWith($parent, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $path -PathType Leaf) -or
        ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $saved.sha256) {
        throw 'The preserved update recovery driver cannot be verified.'
    }
    return $path
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitProcess -or
        $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This updater requires Windows x64 and 64-bit PowerShell.' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    New-Item -ItemType Directory -Force -Path $updateDirectory | Out-Null
    $wrapperLock = [IO.File]::Open((Join-Path $updateDirectory 'wrapper.lock'), [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $mutexName = 'Local\MARS-native-' + ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant())))).Replace('-', '') }
    finally { $sha.Dispose() }
    $mutex = New-Object Threading.Mutex($false, $mutexName)
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another MARS launcher is installing, starting or updating this folder.' }
    if (Test-Path -LiteralPath $receiptPath) {
        $previous = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
        if ($previous.phase -in @('applying', 'applied')) {
            $driver = Read-TrustedUpdateDriver
            $recovery = Get-Content -LiteralPath $driverRecordPath -Raw | ConvertFrom-Json
            $common = [IO.Path]::GetFullPath([string]$recovery.common)
            $parent = [IO.Path]::GetFullPath($updateDirectory).TrimEnd('\') + '\'
            if (-not $common.StartsWith($parent, [StringComparison]::OrdinalIgnoreCase) -or
                -not (Test-Path -LiteralPath $common -PathType Leaf) -or
                ((Get-Item -LiteralPath $common).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
                (Get-FileHash -LiteralPath $common -Algorithm SHA256).Hash.ToLowerInvariant() -cne $recovery.common_sha256) {
                throw 'The preserved recovery helpers cannot be verified.'
            }
            # Recovery must not load the newly replaced launcher or configuration.
            . $common
            $servicePath = Join-Path $root 'local/windows/services.json'
            if (Test-Path -LiteralPath $servicePath) {
                $service = Get-Content -LiteralPath $servicePath -Raw | ConvertFrom-Json
                $backend = Get-MarsOwnedProcess $service.backend
                if ($null -ne $backend) {
                    [IO.File]::WriteAllText($service.shutdown_file, 'stop')
                    if (-not $backend.WaitForExit(30000)) { throw 'The owned backend is still shutting down; recovery has not changed files.' }
                }
                $frontend = Get-MarsOwnedProcess $service.frontend
                if ($null -ne $frontend) {
                    & taskkill.exe /PID $frontend.Id /T /F | Out-Null
                    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the owned frontend before recovery.' }
                }
                Remove-Item -LiteralPath $servicePath -Force
            }
            $updatePython = [string]$recovery.python
            Invoke-MarsNativeCommand $updatePython @($driver, '--root', $root, '--rollback', '--receipt', $receiptPath) $root
            Write-Host 'The interrupted core update was rolled back. Your dependencies and local data were preserved. Run Update-MARS.cmd again to update.' -ForegroundColor Yellow
            exit 0
        }
    }
    . (Join-Path $PSScriptRoot 'Mars.ps1') -LibraryOnly
    $NoBrowser = $updateNoBrowser
    $SkipConfigure = $updateSkipConfigure
    $updatePython = Get-UpdatePython
    $driver = Join-Path $updateDirectory ('update-driver-' + [guid]::NewGuid().ToString('N') + '.py')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'update_core.py') -Destination $driver
    $stableCommon = [IO.Path]::ChangeExtension($driver, '.ps1')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Common.ps1') -Destination $stableCommon
    @{ path = $driver; sha256 = (Get-FileHash -LiteralPath $driver -Algorithm SHA256).Hash.ToLowerInvariant();
       common = $stableCommon; common_sha256 = (Get-FileHash -LiteralPath $stableCommon -Algorithm SHA256).Hash.ToLowerInvariant();
       python = $updatePython } |
        ConvertTo-Json | Set-Content -LiteralPath $driverRecordPath -Encoding UTF8
    if ($Source -eq '') {
        $updates = Join-Path $root 'updates'
        New-Item -ItemType Directory -Force -Path $updates | Out-Null
        $Source = Join-Path $updates 'latest.zip'
        $part = $Source + '.' + [guid]::NewGuid().ToString('N') + '.part'
        try {
            Write-Host 'Downloading the MARS core only. Python, Node and installed dependencies are retained.'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/HarryYangthu/MARS-Multi-Agent-Research-System/archive/refs/heads/windows-core.zip' -OutFile $part -TimeoutSec 600
            Move-Item -LiteralPath $part -Destination $Source -Force
        } finally { Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue }
    }
    $Source = [IO.Path]::GetFullPath($Source)
    Invoke-MarsNativeCommand $updatePython @($driver, '--root', $root, '--source', $Source, '--check', '--receipt', $receiptPath) $root
    $state = Read-ServiceState
    if ($null -ne $state) {
        $wasRunning = ($null -ne (Get-MarsOwnedProcess $state.backend)) -or ($null -ne (Get-MarsOwnedProcess $state.frontend))
    }
    # Check the package before stopping anything; apply never installs dependencies.
    Stop-NativeServices
    Invoke-MarsNativeCommand $updatePython @($driver, '--root', $root, '--source', $Source, '--apply', '--receipt', $receiptPath) $root
    $appliedReceipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    if ($appliedReceipt.phase -ne 'applied') { throw 'The update did not produce an applied transaction receipt.' }
    $transaction = [string]$appliedReceipt.transaction_id
    $applied = $true
    Set-NativeEnvironment
    $env:npm_config_offline = 'true'
    if (-not (Test-InstallationCurrent)) { Install-OfflineDependencies }
    foreach ($script in Get-ChildItem -LiteralPath (Join-Path $root 'deploy/windows-native') -Filter '*.ps1') {
        $tokens = $null
        $errors = $null
        $null = [Management.Automation.Language.Parser]::ParseFile($script.FullName, [ref]$tokens, [ref]$errors)
        if ($errors.Count -ne 0) { throw "Updated PowerShell syntax is invalid: $($script.Name): $errors" }
    }
    $shell = [Diagnostics.Process]::GetCurrentProcess().Path
    $startupDriver = Join-Path $updateDirectory ('startup-driver-' + [guid]::NewGuid().ToString('N') + '.ps1')
    @'
param([string]$Launcher, [switch]$NoBrowser, [switch]$SkipConfigure)
$forwardNoBrowser = $NoBrowser
$forwardSkipConfigure = $SkipConfigure
. $Launcher -LibraryOnly -NoBrowser:$forwardNoBrowser -SkipConfigure:$forwardSkipConfigure
Start-NativeServices
'@ | Set-Content -LiteralPath $startupDriver -Encoding UTF8
    Invoke-MarsNativeCommand $python @('-c', 'import app.main, torch, tensorboard; assert torch.ones(2).sum().item() == 2') $root
    Ensure-FrontendBuild
    $startupArguments = @('-NoProfile', '-ExecutionPolicy', 'RemoteSigned', '-File', $startupDriver,
        '-Launcher', (Join-Path $root 'deploy/windows-native/Mars.ps1'))
    if ($NoBrowser) { $startupArguments += '-NoBrowser' }
    if ($SkipConfigure) { $startupArguments += '-SkipConfigure' }
    # The outer wrapper owns the launcher mutex; LibraryOnly loads fresh code
    # without taking it again, then genuinely starts through the new functions.
    Invoke-MarsNativeCommand $shell $startupArguments $root
    Invoke-MarsNativeCommand $updatePython @($driver, '--root', $root, '--commit', '--receipt', $receiptPath) $root
    Write-Host 'MARS core updated and health checks passed. Dependencies, settings, projects and results were retained.' -ForegroundColor Green
} catch {
    $failure = $_.Exception.Message
    if ($applied) {
        try {
            $current = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
            if ($current.transaction_id -cne $transaction -or $current.phase -ne 'applied') {
                throw 'Update transaction ownership changed; automatic rollback was not attempted.'
            }
            Stop-NativeServices
            Invoke-MarsNativeCommand $updatePython @($driver, '--root', $root, '--rollback', '--receipt', $receiptPath) $root
            Set-NativeEnvironment
            if (Test-InstallationCurrent) { Ensure-FrontendBuild }
            if ($wasRunning) { $NoBrowser = $true; Start-NativeServices }
            Write-Host 'The failed core update was rolled back. Local data and dependencies were preserved.' -ForegroundColor Yellow
        } catch { Write-Host ('Recovery also needs attention: ' + $_.Exception.Message) -ForegroundColor Red }
    }
    Write-Host ('MARS update failed: ' + $failure + '. If GitHub is unreachable, download the core ZIP elsewhere and drag it onto Update-MARS.cmd.') -ForegroundColor Red
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
    if ($null -ne $wrapperLock) { $wrapperLock.Dispose() }
}
exit 0
