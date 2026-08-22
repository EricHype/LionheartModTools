<#
Player-facing installer for a Lionheart mod package. No Python, no repacking.

WHY THIS DOES NOT TOUCH data.dat
--------------------------------
A retail Lionheart install ships a COMPLETE loose mirror of data.dat's contents at
<game>\data\ -- verified on a GOG install: 19,077 loose files against 19,030 archive
entries, essentially all of them carrying the game's original 2002-2003 timestamps. The
engine reads a path from that mirror INSTEAD of data.dat whenever the mirror has it, which
is why the developer-side build syncs both.

So installing a mod for a player is a write into <game>\data\. data.dat is never modified
and never backed up -- only read, and only as a fallback source when reconstructing a
patched file. That removes the slowest and most dangerous part of the developer workflow
at a stroke: repacking a 1.6 GB archive takes minutes and, if it adopts an already-modded
data.dat as its "vanilla" baseline, corrupts the player's only way back. This cannot do
either.

WHAT A RELEASE CONTAINS
-----------------------
No content from the shipped game. A mod that changes an existing file would normally have
to carry the whole file, since the engine reads no patch format -- a 40-line edit to
Crossroads.zax would mean redistributing 1.2 MB of the publisher's map. Instead, files
that already exist in the player's install ship as deltas against their own copy and are
rebuilt here; only newly authored files travel verbatim.

Every reconstruction is hash-checked at both ends, and all of them happen before anything
is written, so a patch that cannot find its original aborts a pristine install rather than
leaving a half-applied one.

The cost is that uninstall has to be exact, so every overwritten file is backed up and
every written file is hashed. Uninstall restores only what is still byte-for-byte what we
wrote; anything a later mod changed underneath us is left alone and reported, because
silently reverting another mod's file would be the same class of bug one level down.

Usage (normally via Install.bat / Uninstall.bat):
    powershell -ExecutionPolicy Bypass -File mod-installer.ps1 -Action install
    powershell -ExecutionPolicy Bypass -File mod-installer.ps1 -Action uninstall
    ... -GameDir "D:\Games\Lionheart"      to skip auto-detection
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'uninstall')]
    [string]$Action = 'install',
    [string]$GameDir = '',
    [string]$ModDir = ''
)

$ErrorActionPreference = 'Stop'
if (-not $ModDir) { $ModDir = Split-Path -Parent $PSCommandPath }

function Write-Step ($m) { Write-Host ""; Write-Host $m -ForegroundColor Cyan }
function Write-Ok   ($m) { Write-Host "  $m" -ForegroundColor Green }
function Write-Warn ($m) { Write-Host "  $m" -ForegroundColor Yellow }
function Write-Bad  ($m) { Write-Host "  $m" -ForegroundColor Red }

function Get-FileHashSafe ($Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Get-BytesHash ([byte[]]$Bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

# --- delta reconstruction ---------------------------------------------------
# A release ships no content from the shipped game: files that already exist in the
# player's install travel as a delta against their own copy and are rebuilt here. The
# format is deliberately dumb to apply -- copy a byte range from the source, or append
# literal bytes -- so this walks a byte array and never has to reason about lines or
# line endings. Getting that wrong would silently corrupt CRLF game data.
function Invoke-Delta ([byte[]]$Source, $Delta) {
    $actual = Get-BytesHash $Source
    if ($actual -ne $Delta.srcSha256) { return $null }   # not the file this patch expects

    $out = New-Object System.IO.MemoryStream
    try {
        foreach ($op in $Delta.ops) {
            if ($op[0] -eq 'c') {
                $offset = [int]$op[1]; $length = [int]$op[2]
                if ($offset -lt 0 -or ($offset + $length) -gt $Source.Length) {
                    throw "Patch is corrupt: copy runs past the end of the source file."
                }
                $out.Write($Source, $offset, $length)
            } else {
                $literal = [Convert]::FromBase64String($op[1])
                $out.Write($literal, 0, $literal.Length)
            }
        }
        $result = $out.ToArray()
    } finally { $out.Dispose() }

    if ((Get-BytesHash $result) -ne $Delta.dstSha256) {
        throw "Patch applied but did not produce the expected file. The download may be damaged."
    }
    return $result
}

# The player's own copy of a shipped file, preferred from the loose mirror (already a
# plain file) and falling back to the archive. Returns $null if neither is the original
# the patch was built against -- normally because another mod owns that file.
function Get-VanillaBytes ($ctx, $Rel, $Delta) {
    $loosePath = Join-Path $ctx.Loose $Rel
    if (Test-Path -LiteralPath $loosePath) {
        $bytes = [System.IO.File]::ReadAllBytes($loosePath)
        $rebuilt = Invoke-Delta $bytes $Delta
        if ($rebuilt) { return $rebuilt }
    }
    $dat = Join-Path $ctx.Game 'data.dat'
    if ((Test-Path -LiteralPath $dat) -and (-not $ctx.ArchiveUnusable)) {
        if (-not $ctx.Archive) {
            # An unreadable archive is a fallback that is not available, not a crash. A
            # player whose data.dat is damaged or repacked oddly should get the message
            # about which file could not be rebuilt, not a raw .NET exception.
            try {
                Add-Type -AssemblyName System.IO.Compression.FileSystem
                $ctx.Archive = [System.IO.Compression.ZipFile]::OpenRead($dat)
            } catch {
                $ctx.ArchiveUnusable = $true
                return $null
            }
        }
        $entry = $ctx.Archive.GetEntry($Rel)
        if ($entry) {
            $stream = $entry.Open()
            $mem = New-Object System.IO.MemoryStream
            try {
                $stream.CopyTo($mem)
                $rebuilt = Invoke-Delta $mem.ToArray() $Delta
                if ($rebuilt) { return $rebuilt }
            } finally { $stream.Dispose(); $mem.Dispose() }
        }
    }
    return $null
}

# --- locating the game ------------------------------------------------------
# Registry first: a GOG install records its own path, which beats guessing. Steam and
# retail installs fall through to the common-path list and then to asking.
function Find-GameDir {
    $roots = @(
        'HKLM:\SOFTWARE\WOW6432Node\GOG.com\Games',
        'HKLM:\SOFTWARE\GOG.com\Games',
        'HKCU:\SOFTWARE\GOG.com\Games'
    )
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        foreach ($key in Get-ChildItem $root) {
            $props = Get-ItemProperty $key.PSPath
            if ($props.gameName -like '*Lionheart*' -and $props.path) {
                if (Test-GameDir $props.path) { return $props.path }
            }
        }
    }
    $guesses = @(
        "${env:ProgramFiles(x86)}\GOG Galaxy\Games\Lionheart - Legacy of the Crusader",
        "$env:ProgramFiles\GOG Galaxy\Games\Lionheart - Legacy of the Crusader",
        "${env:ProgramFiles(x86)}\GOG.com\Lionheart",
        "${env:ProgramFiles(x86)}\Black Isle\Lionheart",
        "${env:ProgramFiles(x86)}\Steam\steamapps\common\Lionheart",
        'C:\GOG Games\Lionheart - Legacy of the Crusader'
    )
    foreach ($g in $guesses) { if (Test-GameDir $g) { return $g } }
    return $null
}

function Test-GameDir ($Path) {
    if (-not $Path) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Path 'Lionheart.exe')) -and
           (Test-Path -LiteralPath (Join-Path $Path 'data.dat'))
}

function Resolve-GameDir ($Supplied) {
    if ($Supplied) {
        if (-not (Test-GameDir $Supplied)) {
            throw "That folder does not look like a Lionheart install (no Lionheart.exe and data.dat): $Supplied"
        }
        return $Supplied
    }
    $found = Find-GameDir
    if ($found) { return $found }
    Write-Warn "Could not find Lionheart automatically."
    Write-Host  "  Paste the folder containing Lionheart.exe, or press Enter to cancel."
    $typed = Read-Host "  Game folder"
    if (-not $typed) { throw "Cancelled -- no game folder given." }
    $typed = $typed.Trim('"')
    if (-not (Test-GameDir $typed)) {
        throw "No Lionheart.exe and data.dat in: $typed"
    }
    return $typed
}

# --- shared setup -----------------------------------------------------------
function Get-Context ($GameDirParam) {
    $manifestPath = Join-Path $ModDir 'mod.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "No mod.json beside this script -- run it from inside the unzipped mod folder."
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

    # payload.json says which files travel verbatim and which are deltas. Absent only in
    # a full-form package (a checkout of the mod repo), where everything is verbatim.
    $payloadPath = Join-Path $ModDir 'payload.json'
    if (Test-Path -LiteralPath $payloadPath) {
        $payload = Get-Content -LiteralPath $payloadPath -Raw | ConvertFrom-Json
    } else {
        $payload = [pscustomobject]@{ verbatim = $manifest.files; patched = @() }
    }

    $game = Resolve-GameDir $GameDirParam
    $stateDir = Join-Path $game "mods\$($manifest.id)"
    return [pscustomobject]@{
        Manifest = $manifest
        Payload  = $payload
        Game     = $game
        Loose    = Join-Path $game 'data'
        State    = $stateDir
        Backup   = Join-Path $stateDir 'backup'
        Record   = Join-Path $stateDir 'install-record.json'
        Archive  = $null
        ArchiveUnusable = $false
    }
}

function Assert-GameClosed {
    if (Get-Process -Name 'Lionheart' -ErrorAction SilentlyContinue) {
        throw "Lionheart is running. Close the game and run this again."
    }
}

# --- install ----------------------------------------------------------------
function Invoke-Install ($ctx) {
    $m = $ctx.Manifest
    Write-Host ""
    Write-Host "  $($m.name) $($m.version)" -ForegroundColor White
    Write-Host "  Installing to: $($ctx.Game)"

    if (-not (Test-Path -LiteralPath $ctx.Loose)) {
        throw ("This install has no data\ folder, which is where mod files go. " +
               "That is unusual -- a normal Lionheart install ships one. Nothing was changed.")
    }

    # An existing record means Fixt is already installed. Roll it back first so the
    # backups describe the player's own files rather than our previous install.
    if (Test-Path -LiteralPath $ctx.Record) {
        Write-Step "An earlier version is already installed -- removing it first."
        Invoke-Uninstall $ctx -Quiet
    }

    # Work out the full contents of every file BEFORE touching anything on disk. A patch
    # that cannot find its original should abort a pristine install, not leave a half-
    # applied one behind.
    $nPatched = @($ctx.Payload.patched).Count
    Write-Step "Preparing $($m.files.Count) file(s)"
    if ($nPatched) {
        Write-Host "  Rebuilding $nPatched file(s) from your own game data..."
    }

    $content = @{}
    foreach ($rel in $ctx.Payload.verbatim) {
        $srcPath = Join-Path (Join-Path $ModDir 'files') $rel
        if (-not (Test-Path -LiteralPath $srcPath)) {
            # Overwhelmingly the cause: some resource paths run to ~110 characters, so
            # unzipping into a deep folder pushes them past Windows' 260-char limit and
            # the extractor drops them without saying so. The download is fine; where it
            # was unzipped is not.
            throw ("Mod file missing: $rel`n" +
                   "  This usually means the unzip was silently truncated by Windows' " +
                   "260-character path limit.`n" +
                   "  Move the unzipped folder somewhere short, like C:\Fixt, and run this again.")
        }
        $content[$rel] = [System.IO.File]::ReadAllBytes($srcPath)
    }
    foreach ($rel in $ctx.Payload.patched) {
        $patchPath = Join-Path (Join-Path $ModDir 'patches') "$rel.lhpatch"
        if (-not (Test-Path -LiteralPath $patchPath)) {
            throw ("Patch missing: $rel`n" +
                   "  This usually means the unzip was silently truncated by Windows' " +
                   "260-character path limit.`n" +
                   "  Move the unzipped folder somewhere short, like C:\Fixt, and run this again.")
        }
        $delta = Get-Content -LiteralPath $patchPath -Raw | ConvertFrom-Json
        $rebuilt = Get-VanillaBytes $ctx $rel $delta
        if (-not $rebuilt) {
            throw ("Could not rebuild $rel.`n" +
                   "  This mod patches your own game files rather than shipping copies of them, " +
                   "so it needs the original.`n" +
                   "  Neither data\$rel nor data.dat holds the version this patch expects -- " +
                   "usually because another mod changed that file.`n" +
                   "  Remove other mods, or verify/reinstall the game, and try again. " +
                   "Nothing has been changed.")
        }
        $content[$rel] = $rebuilt
    }
    if ($ctx.Archive) { $ctx.Archive.Dispose(); $ctx.Archive = $null }

    Write-Step "Writing $($m.files.Count) file(s)"
    New-Item -ItemType Directory -Path $ctx.Backup -Force | Out-Null
    $entries = @()
    foreach ($rel in $m.files) {
        $dstPath = Join-Path $ctx.Loose $rel
        $existed = Test-Path -LiteralPath $dstPath

        # Back up whatever is there now -- vanilla, or another mod's file. Either way it
        # is what this player had before us, and it is what uninstall must put back.
        if ($existed) {
            $bak = Join-Path $ctx.Backup $rel
            New-Item -ItemType Directory -Path (Split-Path -Parent $bak) -Force | Out-Null
            Copy-Item -LiteralPath $dstPath -Destination $bak -Force
        }

        New-Item -ItemType Directory -Path (Split-Path -Parent $dstPath) -Force | Out-Null
        [System.IO.File]::WriteAllBytes($dstPath, $content[$rel])

        $entries += [pscustomobject]@{
            path         = $rel
            existedBefore = $existed
            installedHash = (Get-FileHashSafe $dstPath)
        }
    }

    $record = [pscustomobject]@{
        id          = $m.id
        name        = $m.name
        version     = $m.version
        installedAt = (Get-Date).ToString('s')
        entries     = $entries
    }
    New-Item -ItemType Directory -Path $ctx.State -Force | Out-Null
    # Written without a BOM. Set-Content -Encoding utf8 on PowerShell 5.1 always emits
    # one, which any other tool reading this record as plain UTF-8 JSON chokes on.
    $json = $record | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($ctx.Record, $json, (New-Object System.Text.UTF8Encoding($false)))

    $added = @($entries | Where-Object { -not $_.existedBefore }).Count
    Write-Ok "$($entries.Count) file(s) in place ($added new, $($entries.Count - $added) replaced)."
    Write-Ok "Backups and the uninstall record are in $($ctx.State)"
    Write-Host ""
    Write-Host "  Done. Start a NEW GAME -- see the readme for why." -ForegroundColor Green
}

# --- uninstall --------------------------------------------------------------
function Invoke-Uninstall ($ctx, [switch]$Quiet) {
    if (-not (Test-Path -LiteralPath $ctx.Record)) {
        throw "No install record at $($ctx.Record) -- this mod does not appear to be installed."
    }
    $record = Get-Content -LiteralPath $ctx.Record -Raw | ConvertFrom-Json
    if (-not $Quiet) {
        Write-Host ""
        Write-Host "  Removing $($record.name) $($record.version) from $($ctx.Game)"
    }

    $restored = 0; $deleted = 0; $skipped = @()
    foreach ($e in $record.entries) {
        $dstPath = Join-Path $ctx.Loose $e.path
        $current = Get-FileHashSafe $dstPath

        # If the file is no longer what we wrote, something else owns it now. Reverting
        # it would silently undo whatever that was.
        if ($current -and $e.installedHash -and $current -ne $e.installedHash) {
            $skipped += $e.path
            continue
        }
        if ($e.existedBefore) {
            $bak = Join-Path $ctx.Backup $e.path
            if (Test-Path -LiteralPath $bak) {
                New-Item -ItemType Directory -Path (Split-Path -Parent $dstPath) -Force | Out-Null
                Copy-Item -LiteralPath $bak -Destination $dstPath -Force
                $restored++
            } else {
                $skipped += $e.path
            }
        } elseif (Test-Path -LiteralPath $dstPath) {
            Remove-Item -LiteralPath $dstPath -Force
            $deleted++
        }
    }

    Remove-Item -LiteralPath $ctx.State -Recurse -Force -ErrorAction SilentlyContinue

    if (-not $Quiet) {
        Write-Ok "$restored file(s) restored, $deleted mod-added file(s) removed."
        if ($skipped.Count) {
            Write-Warn "$($skipped.Count) file(s) left alone -- changed since install, probably by another mod:"
            foreach ($s in $skipped) { Write-Host "      $s" }
        }
        Write-Host ""
        Write-Host "  Done." -ForegroundColor Green
    }
}

# --- entry point ------------------------------------------------------------
try {
    Assert-GameClosed
    $ctx = Get-Context $GameDir
    if ($Action -eq 'install') { Invoke-Install $ctx } else { Invoke-Uninstall $ctx }
    exit 0
} catch {
    Write-Host ""
    Write-Bad "FAILED: $($_.Exception.Message)"
    Write-Host ""
    exit 1
}
