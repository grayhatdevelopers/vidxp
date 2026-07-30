$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $PSScriptRoot
$lock = Get-Content -Raw (Join-Path $scriptRoot "sidecars.json") |
    ConvertFrom-Json
$version = $lock.uv_version
$architecture = $env:PROCESSOR_ARCHITECTURE
if ($architecture -ne "AMD64") {
    throw "The first Windows desktop target supports x86-64 only (found $architecture)."
}

$target = "x86_64-pc-windows-msvc"
$targetLock = $lock.targets.$target
$archiveName = $targetLock.archive
$lockedChecksum = $targetLock.sha256.ToLowerInvariant()
$releaseRoot = "https://github.com/astral-sh/uv/releases/download/$version"
$binaryDirectory = Join-Path $scriptRoot "src-tauri\binaries"
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vidxp-uv-" + [guid]::NewGuid())
$resolvedTempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$resolvedTemporaryRoot = [System.IO.Path]::GetFullPath($temporaryRoot)
if (-not $resolvedTemporaryRoot.StartsWith($resolvedTempBase, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a temporary directory outside the system temporary root."
}

New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
New-Item -ItemType Directory -Force -Path $binaryDirectory | Out-Null
try {
    $archive = Join-Path $temporaryRoot $archiveName
    Invoke-WebRequest -Uri "$releaseRoot/$archiveName" -OutFile $archive
    $stream = [System.IO.File]::OpenRead($archive)
    try {
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        $actual = [System.BitConverter]::ToString(
            $hasher.ComputeHash($stream)
        ).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $stream.Dispose()
    }
    if ($actual -ne $lockedChecksum) {
        throw "The uv archive checksum did not match desktop/sidecars.json."
    }

    $expanded = Join-Path $temporaryRoot "expanded"
    Expand-Archive -LiteralPath $archive -DestinationPath $expanded
    $source = Get-ChildItem -LiteralPath $expanded -Recurse -File -Filter "uv.exe" |
        Select-Object -First 1
    if ($null -eq $source) {
        throw "The uv release archive did not contain uv.exe."
    }
    Copy-Item -LiteralPath $source.FullName -Destination (
        Join-Path $binaryDirectory "uv-$target.exe"
    )
}
finally {
    if (Test-Path -LiteralPath $resolvedTemporaryRoot) {
        Remove-Item -LiteralPath $resolvedTemporaryRoot -Recurse -Force
    }
}
