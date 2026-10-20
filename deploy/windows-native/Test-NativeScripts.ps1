param([string]$Root = ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))))
. (Join-Path $PSScriptRoot 'Common.ps1')
function Assert-True { param([bool]$Value, [string]$Label) if (-not $Value) { throw $Label } }
foreach ($file in Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1') {
    $tokens = $null
    $errors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
    Assert-True ($errors.Count -eq 0) "Syntax error in $($file.Name): $errors"
}
$config = Read-MarsNativeConfig (Join-Path $Root 'configs/windows_native.yaml')
Assert-True ($config.python_version -eq '3.11.14') 'Python runtime pin missing.'
$argsText = ConvertTo-MarsArgumentString @('D:\research folder\MARS', 'a"b', 'D:\tail\')
Assert-True ($argsText -eq '"D:\research folder\MARS" "a\"b" "D:\tail\\"') 'Windows argument quoting failed.'
$own = [Diagnostics.Process]::GetCurrentProcess()
$receipt = Get-MarsProcessReceipt $own
Assert-True ($null -ne (Get-MarsOwnedProcess $receipt)) 'Current process identity was not recognized.'
$receipt.started = '0'
Assert-True ($null -eq (Get-MarsOwnedProcess $receipt)) 'Changed process identity was accepted.'
$temp = Join-Path ([IO.Path]::GetTempPath()) ('mars-native-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    $bad = Join-Path $temp 'bad.yaml'
    [IO.File]::WriteAllText($bad, "backend_port: 8010`nfrontend_port: 8010`n")
    $rejected = $false
    try { Read-MarsNativeConfig $bad | Out-Null } catch { $rejected = $true }
    Assert-True $rejected 'Port collision was accepted.'
    $downloads = Join-Path $temp 'downloads'
    New-Item -ItemType Directory -Path $downloads | Out-Null
    $wrongHash = '0' * 64
    [IO.File]::WriteAllText((Join-Path $downloads "uv-$wrongHash.zip"), 'tampered archive fixture')
    $rejected = $false
    try { Install-MarsArchive 'uv' @{uv_url='https://example.invalid/never-requested';uv_sha256=$wrongHash;uv_executable='uv.exe'} $temp | Out-Null }
    catch { $rejected = $_.Exception.Message -match 'checksum mismatch' }
    Assert-True $rejected 'A corrupt cached download was accepted.'

    $fixture = Join-Path $temp 'product'
    foreach ($directory in @('configs', 'frontend/src', 'frontend/public', 'frontend/scripts')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $fixture $directory) | Out-Null
    }
    $nativePath = Join-Path $fixture 'configs/windows_native.yaml'
    Copy-Item -LiteralPath (Join-Path $Root 'configs/windows_native.yaml') -Destination $nativePath
    foreach ($name in @('frontend.yaml', 'local_runtime.yaml')) {
        Copy-Item -LiteralPath (Join-Path $Root "configs/$name") -Destination (Join-Path $fixture "configs/$name")
    }
    [IO.File]::WriteAllText((Join-Path $fixture 'uv.lock'), 'actual dependency lock bytes')
    [IO.File]::WriteAllText((Join-Path $fixture 'frontend/package-lock.json'), '{"lockfileVersion":3}')
    [IO.File]::WriteAllText((Join-Path $fixture 'frontend/package.json'), '{"name":"actual-file-contract"}')
    [IO.File]::WriteAllText((Join-Path $fixture 'frontend/src/page.tsx'), 'export default function Page() { return null }')
    $dependencies = Get-MarsDependencyFingerprint $fixture
    Assert-True ($dependencies.Count -eq 6) 'Dependency fingerprint must contain exactly the six pinned inputs.'
    $savedDependencies = $dependencies | ConvertTo-Json | ConvertFrom-Json
    $legacyFingerprint = @{
        'uv.lock' = $dependencies['uv.lock'].ToUpperInvariant()
        'frontend/package-lock.json' = $dependencies['frontend/package-lock.json'].ToUpperInvariant()
        'configs/windows_native.yaml' = (Get-FileHash -LiteralPath $nativePath -Algorithm SHA256).Hash
    } | ConvertTo-Json | ConvertFrom-Json
    Assert-True (Test-MarsInstalledFingerprint $legacyFingerprint $fixture) 'Unchanged real legacy dependency bindings must remain reusable.'
    $frontendBefore = Get-MarsFrontendFingerprint $fixture | ConvertTo-Json | ConvertFrom-Json
    foreach ($name in @('configs/frontend.yaml', 'configs/local_runtime.yaml')) {
        $beforeConfigChange = Get-MarsFrontendFingerprint $fixture | ConvertTo-Json | ConvertFrom-Json
        $configuration = Join-Path $fixture $name
        [IO.File]::AppendAllText($configuration, "`n# actual frontend build input changed`n")
        Assert-True (-not (Test-MarsFingerprint $beforeConfigChange (Get-MarsFrontendFingerprint $fixture))) "Changed $name must require a frontend build."
        Assert-True (Test-MarsFingerprint $savedDependencies (Get-MarsDependencyFingerprint $fixture)) "Changed $name must not reinstall dependencies."
    }
    [IO.File]::WriteAllText((Join-Path $fixture 'pyproject.toml'), 'description = "core metadata changed"')
    [IO.File]::WriteAllText((Join-Path $fixture 'WINDOWS-OFFLINE.json'), '{"dependency_directory":"dependencies/offline","source_marker":"changed"}')
    [IO.File]::WriteAllText((Join-Path $fixture 'frontend/src/page.tsx'), 'export default function Page() { return "updated core" }')
    Assert-True (Test-MarsFingerprint $savedDependencies (Get-MarsDependencyFingerprint $fixture)) 'Core/source marker changes must not reinstall dependencies.'
    Assert-True (-not (Test-MarsFingerprint $frontendBefore (Get-MarsFrontendFingerprint $fixture))) 'Changed frontend source must require a build.'
    $nativeText = [IO.File]::ReadAllText($nativePath)
    [IO.File]::WriteAllText($nativePath, ($nativeText -replace '(?m)^backend_port:.*$', 'backend_port: 18010'))
    Assert-True (Test-MarsFingerprint $savedDependencies (Get-MarsDependencyFingerprint $fixture)) 'Changing a local port must not reinstall dependencies.'
    [IO.File]::WriteAllText((Join-Path $fixture 'uv.lock'), 'actual changed dependency lock bytes')
    Assert-True (-not (Test-MarsFingerprint $savedDependencies (Get-MarsDependencyFingerprint $fixture))) 'Dependency lock changes must invalidate installation.'
    Assert-True (-not (Test-MarsInstalledFingerprint $legacyFingerprint $fixture)) 'Legacy receipt cannot approve changed dependency locks.'
    $modern = Get-MarsWindowsLayout $fixture
    Assert-True ($modern.Runtime -eq (Join-Path $fixture 'dependencies/windows')) 'New installation must use the separate dependency directory.'
    $legacy = Join-Path $fixture 'local/windows/tools'
    New-Item -ItemType Directory -Force -Path $legacy | Out-Null
    Assert-True ((Get-MarsWindowsLayout $fixture).Runtime -eq (Join-Path $fixture 'local/windows')) 'Legacy installed tools must remain usable.'
    New-Item -ItemType Directory -Force -Path $modern.Runtime | Out-Null
    Assert-True ((Get-MarsWindowsLayout $fixture).Runtime -eq $modern.Runtime) 'New installed dependencies take precedence over legacy tools.'

    # Real filesystem migration and junction, not an npm/service substitute.
    $oldModules = Join-Path $fixture 'frontend/node_modules'
    New-Item -ItemType Directory -Path $oldModules | Out-Null
    [IO.File]::WriteAllText((Join-Path $oldModules 'retained.txt'), 'installed dependency bytes')
    Connect-MarsFrontendDependencies $fixture
    $retained = Join-Path $fixture 'dependencies/frontend/node_modules/retained.txt'
    $retainedHash = (Get-FileHash -LiteralPath $retained -Algorithm SHA256).Hash
    Assert-True (((Get-Item -LiteralPath $oldModules).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) 'Core node_modules must be a junction.'
    Connect-MarsFrontendDependencies $fixture
    Assert-True ((Get-FileHash -LiteralPath $retained -Algorithm SHA256).Hash -ceq $retainedHash) 'Repeated connection must preserve installed dependency bytes.'
} finally { Remove-Item -LiteralPath $temp -Recurse -Force }
Write-Host 'Native syntax, configuration, quoting, process ownership, checksum, dependency/build fingerprints, legacy layout and real junction contracts passed.'
