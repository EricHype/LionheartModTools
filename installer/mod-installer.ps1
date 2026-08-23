<#
Command-line installer for a Lionheart mod package. No Python needed.

WHAT IT DOES
------------
Rebuilds the player's data.dat with the mod applied: reads their archive, replaces the
entries the mod changes, adds the ones it introduces, and writes a new archive in its
place. That leaves the install in the configuration the game actually ships in.

An earlier version installed by copying into a loose <game>\data\ directory, which the
engine reads in preference to the archive. That does work -- it is what makes a hand-made
7-Zip extraction shadow data.dat -- but a stock install has no such directory. GOG's own
manifest lists 16 files and zero directories. Two things were also never verified on a
stock install: whether the engine finds a file that exists ONLY loose, which is the case
for every newly authored resource, and whether anything else changes when the directory is
absent. Rebuilding the archive depends on none of that.

If a loose data\ directory IS present -- an extracted install, or another mod's doing --
the mod's files are written there too, because otherwise the loose copies would shadow the
archive we just rebuilt and the install would silently do nothing.

WHAT A RELEASE CONTAINS
-----------------------
No content from the shipped game. A mod that changes an existing file would normally have
to carry the whole file, since the engine reads no patch format -- a 40-line edit to
Crossroads.zax would mean redistributing 1.2 MB of the publisher's map. Instead, files
that already exist in the player's archive ship as deltas against their own copy and are
rebuilt here; only newly authored files travel verbatim.

Every reconstruction is hash-checked at both ends, and all of them happen before anything
is written, so a patch that cannot find its original aborts a pristine install rather than
leaving a half-applied one. The archive is rebuilt to a temporary file, validated, and
only then swapped in.

STORE COMPRESSION IS MANDATORY
------------------------------
The game's archive parser rejects any entry whose compression method is not 0, and
.NET Framework's ZipArchive cannot produce one -- CompressionLevel.NoCompression writes
method 8 carrying stored blocks, which is both larger than the input and unreadable by the
game. The archive is therefore written by a small stored-only ZIP writer compiled at run
time (see LhZip below), and the finished file is re-opened and checked entry by entry
before it replaces anything.

Usage (normally via Install.bat / Uninstall.bat):
    powershell -ExecutionPolicy Bypass -File mod-installer.ps1 -Action install
    powershell -ExecutionPolicy Bypass -File mod-installer.ps1 -Action uninstall
    ... -GameDir "D:\Games\Lionheart"      to skip auto-detection
    ... -ModDir  "path	oelease.zip"     a folder or a release zip
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
. (Join-Path (Split-Path -Parent $PSCommandPath) 'lh-core.ps1')

# --- entry point ------------------------------------------------------------
try {
    Assert-GameClosed
    # Accepts an unpacked folder or the release .zip itself.
    $ctx = Get-Context $GameDir (Resolve-ModFolder $ModDir)
    if (-not (Test-GameDirWritable $ctx)) {
        Write-Host ""
        Write-Warn "Cannot write to $($ctx.Game) -- administrator rights are needed."
        Write-Host "  Nothing has been changed."
        exit 2
    }
    if ($Action -eq 'install') { Invoke-Install $ctx } else { Invoke-Uninstall $ctx }
    exit 0
} catch {
    Write-Host ""
    Write-Bad "FAILED: $($_.Exception.Message)"
    Write-Host ""
    exit 1
}
