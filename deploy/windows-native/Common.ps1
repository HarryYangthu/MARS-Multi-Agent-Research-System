Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-MarsNativeConfig {
    param([string]$Path)
    $result = @{}
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*(#.*)?$') { continue }
        if ($line -notmatch '^([a-z][a-z0-9_]*):\s+([^\s].*?)\s*$') {
            throw "Expected a flat YAML scalar in $Path"
        }
        if ($result.ContainsKey($Matches[1])) { throw 'Duplicate native configuration key.' }
        $result[$Matches[1]] = $Matches[2]
    }
    foreach ($name in @('backend_port', 'frontend_port')) {
        $value = 0
        if (-not [int]::TryParse($result[$name], [ref]$value) -or $value -lt 1024 -or $value -gt 65535) {
            throw "Invalid $name (expected 1024..65535)."
        }
    }
    if ($result.backend_port -eq $result.frontend_port) { throw 'Frontend/backend ports must differ.' }
    return $result
}

function Invoke-MarsNativeCommand {
    param([string]$Executable, [string[]]$Arguments, [string]$Directory)
    Push-Location -LiteralPath $Directory
    try {
        & $Executable @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $Executable" }
    } finally { Pop-Location }
}

function ConvertTo-MarsArgumentString {
    param([string[]]$Arguments)
    # Windows CommandLineToArgvW quoting, including spaces and trailing slashes.
    return (($Arguments | ForEach-Object {
        $quoted = [regex]::Replace($_, '(\\*)"', '$1$1\"')
        $quoted = [regex]::Replace($quoted, '(\\+)$', '$1$1')
        '"' + $quoted + '"'
    }) -join ' ')
}

function Install-MarsArchive {
    param([string]$Name, [hashtable]$Config, [string]$Runtime)
    $hash = $Config["${Name}_sha256"]
    $url = $Config["${Name}_url"]
    if ($hash -notmatch '^[a-f0-9]{64}$' -or -not $url.StartsWith('https://')) {
        throw "Invalid download manifest for $Name"
    }
    $target = Join-Path $Runtime "tools/$Name"
    $executable = Join-Path $target $Config["${Name}_executable"]
    $receipt = Join-Path $target 'archive.sha256'
    if ((Test-Path -LiteralPath $executable) -and (Test-Path -LiteralPath $receipt) -and
        ([IO.File]::ReadAllText($receipt).Trim() -eq $hash)) { return $executable }
    $downloads = Join-Path $Runtime 'downloads'
    New-Item -ItemType Directory -Force -Path $downloads | Out-Null
    $archive = Join-Path $downloads "$Name-$hash.zip"
    if (-not (Test-Path -LiteralPath $archive)) {
        Write-Host "Downloading $Name from official distribution..."
        $part = "$archive.part"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $part -TimeoutSec 600
        if ((Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash -ne $hash) {
            Remove-Item -LiteralPath $part -Force
            throw "$Name download checksum mismatch."
        }
        Move-Item -LiteralPath $part -Destination $archive -Force
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $hash) {
        throw "Cached archive checksum mismatch: $archive"
    }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $target -Force
    if (-not (Test-Path -LiteralPath $executable)) { throw "Missing executable after extracting $Name" }
    [IO.File]::WriteAllText($receipt, $hash)
    return $executable
}

function Test-MarsPort {
    param([int]$Port)
    $client = New-Object Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        return ($task.Wait(500) -and $client.Connected)
    } catch { return $false } finally { $client.Dispose() }
}

function Get-MarsProcessReceipt {
    param([Diagnostics.Process]$Process)
    $Process.Refresh()
    return @{ pid = $Process.Id; started = $Process.StartTime.ToUniversalTime().Ticks.ToString(); executable = $Process.Path }
}

function Get-MarsOwnedProcess {
    param($Receipt)
    if ($null -eq $Receipt) { return $null }
    $process = Get-Process -Id ([int]$Receipt.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    try {
        if ($process.StartTime.ToUniversalTime().Ticks.ToString() -ne $Receipt.started -or
            $process.Path -ne $Receipt.executable) { return $null }
    } catch { return $null }
    return $process
}

function Get-MarsDependencyFingerprint {
    param([string]$Root)
    $result = @{}
    foreach ($file in @('pyproject.toml', 'uv.lock', 'frontend/package-lock.json', 'configs/windows_native.yaml')) {
        $result[$file] = (Get-FileHash -LiteralPath (Join-Path $Root $file) -Algorithm SHA256).Hash
    }
    return $result
}
