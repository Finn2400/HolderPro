# SPDX-License-Identifier: AGPL-3.0-or-later
[CmdletBinding()]
param(
    [ValidateSet("windows-x86_64")]
    [string]$Preset = "windows-x86_64",
    [string]$PrusaSlicerSource = $env:PRUSASLICER_SOURCE_DIR,
    [string]$DepsPrefix = $env:HOLDERPRO_PRUSASLICER_DEPS_PREFIX,
    [string]$Version = $(if ($env:HOLDERPRO_VERSION) { $env:HOLDERPRO_VERSION } else { "0.1.0a1" }),
    [string]$BuildId = $(if ($env:HOLDERPRO_BUILD_ID) { $env:HOLDERPRO_BUILD_ID } elseif ($env:GITHUB_SHA) { $env:GITHUB_SHA } else { "local" }),
    [switch]$DownloadSource,
    [switch]$SkipDeps,
    [switch]$NoTest
)

$ErrorActionPreference = "Stop"
$Commit = "b028299c770b8380ee81c921a2867d522f288123"
$DependencyAllowlist = @(
    "Blosc", "Boost", "CGAL", "CURL", "Cereal", "EXPAT", "Eigen", "GLEW",
    "GMP", "JPEG", "LibBGCode", "MPFR", "NLopt", "NanoSVG", "OpenEXR",
    "OpenVDB", "PNG", "Qhull", "TBB", "ZLIB", "heatshrink",
    "json", "z3"
)
$Root = Split-Path -Parent $PSScriptRoot
$CacheRoot = if ($env:HOLDERPRO_NATIVE_CACHE) {
    $env:HOLDERPRO_NATIVE_CACHE
} elseif ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "HolderPro\native-cache"
} else {
    Join-Path $HOME ".cache\holderpro\native"
}
$DependencyBuild = if ($env:HOLDERPRO_PRUSASLICER_DEPS_BUILD_DIR) {
    $env:HOLDERPRO_PRUSASLICER_DEPS_BUILD_DIR
} else {
    Join-Path $CacheRoot "deps\$Preset-$Commit"
}
$DependencyDownloads = Join-Path $CacheRoot "downloads"
$CachedDepsPrefix = Join-Path $DependencyBuild "destdir\usr\local"

function Assert-LastExitCode([string]$Action) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed with exit code $LASTEXITCODE"
    }
}

function Require-Command([string]$Name, [string]$Guidance) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing prerequisite '$Name'. $Guidance"
    }
}

Require-Command "cmake" "Install CMake explicitly, then retry."
Require-Command "ninja" "Install Ninja explicitly, then retry."
Require-Command "cl.exe" "Run this script from a Visual Studio 2022 x64 Developer PowerShell."
if ($env:VSCMD_ARG_TGT_ARCH -and $env:VSCMD_ARG_TGT_ARCH -ne "x64") {
    throw "The windows-x86_64 preset requires an x64 Developer PowerShell; current target is $env:VSCMD_ARG_TGT_ARCH"
}

if (-not $PrusaSlicerSource) {
    $PrusaSlicerSource = Join-Path $CacheRoot "prusaslicer-$Commit"
}

if (-not (Test-Path -LiteralPath $PrusaSlicerSource -PathType Container) -and $DownloadSource) {
    Require-Command "git" "Git is needed only for the explicit -DownloadSource operation."
    New-Item -ItemType Directory -Force -Path $CacheRoot | Out-Null
    $SourceParent = Split-Path -Parent $PrusaSlicerSource
    if ($SourceParent) {
        New-Item -ItemType Directory -Force -Path $SourceParent | Out-Null
    }
    $TemporarySource = "$PrusaSlicerSource.partial.$PID"
    if (Test-Path -LiteralPath $TemporarySource) {
        throw "Temporary source path already exists: $TemporarySource"
    }
    try {
        Write-Host "Fetching pinned PrusaSlicer source into $PrusaSlicerSource"
        & git init -q $TemporarySource
        Assert-LastExitCode "git init"
        # Prevent Git for Windows from rewriting the reviewed upstream bytes.
        & git -C $TemporarySource config core.autocrlf false
        Assert-LastExitCode "git core.autocrlf configuration"
        & git -C $TemporarySource config core.eol lf
        Assert-LastExitCode "git core.eol configuration"
        & git -C $TemporarySource remote add origin https://github.com/prusa3d/PrusaSlicer.git
        Assert-LastExitCode "git remote add"
        & git -C $TemporarySource fetch --depth 1 origin $Commit
        Assert-LastExitCode "git fetch"
        & git -C $TemporarySource checkout -q --detach FETCH_HEAD
        Assert-LastExitCode "git checkout"
        Move-Item -LiteralPath $TemporarySource -Destination $PrusaSlicerSource
    } finally {
        if (Test-Path -LiteralPath $TemporarySource) {
            Remove-Item -Recurse -Force -LiteralPath $TemporarySource
        }
    }
}

if (-not (Test-Path -LiteralPath $PrusaSlicerSource -PathType Container)) {
    throw "Complete PrusaSlicer 2.9.6 source not found at '$PrusaSlicerSource'. Use -PrusaSlicerSource, set PRUSASLICER_SOURCE_DIR, populate $CacheRoot, or explicitly pass -DownloadSource."
}

if (Test-Path -LiteralPath (Join-Path $PrusaSlicerSource ".git")) {
    Require-Command "git" "Git is required to verify a checkout; source archives are verified by CMake hashes."
    $ActualCommit = (& git -C $PrusaSlicerSource rev-parse HEAD).Trim()
    Assert-LastExitCode "git rev-parse"
    if ($ActualCommit -ne $Commit) {
        throw "PrusaSlicer source is $ActualCommit; expected $Commit"
    }
    & git -C $PrusaSlicerSource diff --quiet --ignore-submodules --
    Assert-LastExitCode "tracked-source verification"
    & git -C $PrusaSlicerSource diff --cached --quiet --ignore-submodules --
    Assert-LastExitCode "staged-source verification"
}

if ($SkipDeps -and -not $DepsPrefix) {
    if (Test-Path -LiteralPath $CachedDepsPrefix -PathType Container) {
        $DepsPrefix = $CachedDepsPrefix
    } else {
        throw "-SkipDeps requires a cached dependency prefix, -DepsPrefix, or HOLDERPRO_PRUSASLICER_DEPS_PREFIX"
    }
}

if (-not $DepsPrefix) {
    Require-Command "git" "Git is required by PrusaSlicer's dependency build."
    New-Item -ItemType Directory -Force -Path $DependencyBuild, $DependencyDownloads | Out-Null
    $PreviousPolicyMinimum = $env:CMAKE_POLICY_VERSION_MINIMUM
    try {
        # CMake 4 removed implicit compatibility with dependency projects whose
        # minimum predates 3.5. Preserve that documented compatibility without
        # modifying the pinned PrusaSlicer checkout.
        $env:CMAKE_POLICY_VERSION_MINIMUM = "3.5"
        $PrefetchArguments = @(
            "-DHOLDERPRO_DEP_DOWNLOAD_DIR=$DependencyDownloads",
            "-P",
            (Join-Path $Root "scripts\prefetch-native-dependencies.cmake")
        )
        & cmake @PrefetchArguments
        Assert-LastExitCode "pinned native dependency prefetch"
        Write-Host "Building pinned PrusaSlicer dependencies in $DependencyBuild"
        $DependencyArguments = @(
            "-S", (Join-Path $PrusaSlicerSource "deps"),
            "-B", $DependencyBuild,
            "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DDEP_DOWNLOAD_DIR=$DependencyDownloads",
            "-DDEP_DEBUG=OFF",
            "-DPrusaSlicer_deps_SELECT_ALL=OFF"
        )
        foreach ($Package in $DependencyAllowlist) {
            $DependencyArguments += "-DPrusaSlicer_deps_SELECT_$Package=ON"
        }
        & cmake @DependencyArguments
        Assert-LastExitCode "PrusaSlicer dependency configure"
        # Upstream projects parallelize their own compilation; keep the outer
        # ExternalProject driver serial to avoid oversubscribing release builders.
        & cmake --build $DependencyBuild --target deps
        Assert-LastExitCode "PrusaSlicer dependency build"
    } finally {
        if ($null -eq $PreviousPolicyMinimum) {
            Remove-Item Env:CMAKE_POLICY_VERSION_MINIMUM -ErrorAction SilentlyContinue
        } else {
            $env:CMAKE_POLICY_VERSION_MINIMUM = $PreviousPolicyMinimum
        }
    }
    $DepsPrefix = $CachedDepsPrefix
}
if (-not (Test-Path -LiteralPath $DepsPrefix -PathType Container)) {
    throw "PrusaSlicer dependency prefix does not exist: $DepsPrefix"
}

$env:PRUSASLICER_SOURCE_DIR = (Resolve-Path -LiteralPath $PrusaSlicerSource).Path
$env:HOLDERPRO_PRUSASLICER_DEPS_PREFIX = (Resolve-Path -LiteralPath $DepsPrefix).Path
$env:HOLDERPRO_PRUSASLICER_DEPS_BIN = Join-Path $env:HOLDERPRO_PRUSASLICER_DEPS_PREFIX "bin"
$env:PATH = "$env:HOLDERPRO_PRUSASLICER_DEPS_BIN;$env:PATH"

Push-Location (Join-Path $Root "native")
try {
    & cmake --preset $Preset "-DHOLDERPRO_VERSION=$Version" "-DHOLDERPRO_BUILD_ID=$BuildId" "-DHOLDERPRO_RUNTIME_DEPENDENCY_DIR=$env:HOLDERPRO_PRUSASLICER_DEPS_BIN"
    Assert-LastExitCode "HolderPro native configure"
    & cmake --build --preset $Preset --parallel
    Assert-LastExitCode "HolderPro native build"
    if (-not $NoTest) {
        & ctest --preset $Preset
        Assert-LastExitCode "HolderPro native tests"
    }
} finally {
    Pop-Location
}

$Engine = Join-Path $Root "native\build\$Preset\holderpro-organic-engine.exe"
if (-not (Test-Path -LiteralPath $Engine -PathType Leaf)) {
    throw "Expected engine was not produced: $Engine"
}
$ProvenanceText = (& $Engine --version-json) -join "`n"
Assert-LastExitCode "engine version smoke test"
$Provenance = $ProvenanceText | ConvertFrom-Json
if ($Provenance.os -ne "windows" -or $Provenance.architecture -ne "x86_64") {
    throw "Engine provenance does not match windows-x86_64: $ProvenanceText"
}
Write-Output $ProvenanceText
Write-Host "Built $Engine"
