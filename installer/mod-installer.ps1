<#
Player-facing installer for a Lionheart mod package. No Python needed.

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
Add-Type -AssemblyName System.IO.Compression.FileSystem

# .NET Framework's ZipArchive cannot write a stored entry. CompressionLevel.NoCompression
# produces method 8 (deflate carrying stored blocks) -- measurably LARGER than the input,
# and rejected outright by the game's archive parser, which fails on any method but 0.
# PowerShell 5.1 has no way around that, so the archive is written here instead: stored
# entries only, no ZIP64 (the archive is 1.6 GB with 19k entries, far inside the 32-bit
# limits, and the game would not read ZIP64 anyway).
Add-Type -TypeDefinition @'
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;

public static class LhZip
{
    static readonly uint[] Table = CreateTable();

    static uint[] CreateTable()
    {
        uint[] t = new uint[256];
        for (uint i = 0; i < 256; i++)
        {
            uint c = i;
            for (int k = 0; k < 8; k++) c = ((c & 1) != 0) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
            t[i] = c;
        }
        return t;
    }

    static uint Crc32(byte[] buf)
    {
        uint c = 0xFFFFFFFFu;
        for (int i = 0; i < buf.Length; i++) c = Table[(c ^ buf[i]) & 0xFF] ^ (c >> 8);
        return c ^ 0xFFFFFFFFu;
    }

    static void W16(Stream s, int v) { s.WriteByte((byte)(v & 0xFF)); s.WriteByte((byte)((v >> 8) & 0xFF)); }
    static void W32(Stream s, uint v)
    {
        s.WriteByte((byte)(v & 0xFF)); s.WriteByte((byte)((v >> 8) & 0xFF));
        s.WriteByte((byte)((v >> 16) & 0xFF)); s.WriteByte((byte)((v >> 24) & 0xFF));
    }

    class Rec { public string Name; public uint Crc; public uint Size; public long Offset; public int Time; public int Date; }

    static void DosStamp(DateTimeOffset stamp, out int time, out int date)
    {
        DateTime d = stamp.LocalDateTime;
        if (d.Year < 1980) d = new DateTime(1980, 1, 1);
        time = (d.Hour << 11) | (d.Minute << 5) | (d.Second / 2);
        date = ((d.Year - 1980) << 9) | (d.Month << 5) | d.Day;
    }

    static void WriteEntry(FileStream fs, List<Rec> recs, string name, byte[] data, int time, int date)
    {
        if (fs.Position + data.Length > 0xFFFFF000L)
            throw new IOException("Archive would exceed 4 GB, which needs ZIP64 -- the game cannot read that.");
        byte[] nb = Encoding.ASCII.GetBytes(name);
        Rec r = new Rec();
        r.Name = name; r.Crc = Crc32(data); r.Size = (uint)data.Length;
        r.Offset = fs.Position; r.Time = time; r.Date = date;

        W32(fs, 0x04034b50); W16(fs, 10); W16(fs, 0); W16(fs, 0);   // sig, ver 1.0, flags, method 0
        W16(fs, time); W16(fs, date);
        W32(fs, r.Crc); W32(fs, r.Size); W32(fs, r.Size);
        W16(fs, nb.Length); W16(fs, 0);
        fs.Write(nb, 0, nb.Length);
        fs.Write(data, 0, data.Length);
        recs.Add(r);
    }

    public static int[] Rebuild(string srcPath, string dstPath, IDictionary replace, IList drop)
    {
        List<Rec> recs = new List<Rec>();
        HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
        int written = 0, added = 0;

        using (FileStream fs = new FileStream(dstPath, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1 << 20))
        {
            using (ZipArchive src = ZipFile.OpenRead(srcPath))
            {
                foreach (ZipArchiveEntry e in src.Entries)
                {
                    if (e.FullName.EndsWith("/")) continue;
                    if (drop != null && drop.Contains(e.FullName)) continue;
                    seen.Add(e.FullName);

                    byte[] data;
                    if (replace.Contains(e.FullName)) { data = (byte[])replace[e.FullName]; }
                    else
                    {
                        data = new byte[e.Length];
                        using (Stream rs = e.Open())
                        {
                            int off = 0;
                            while (off < data.Length)
                            {
                                int n = rs.Read(data, off, data.Length - off);
                                if (n <= 0) break;
                                off += n;
                            }
                        }
                    }
                    int t, d; DosStamp(e.LastWriteTime, out t, out d);
                    WriteEntry(fs, recs, e.FullName, data, t, d);
                    written++;
                }
            }

            foreach (DictionaryEntry kv in replace)
            {
                string name = (string)kv.Key;
                if (seen.Contains(name)) continue;
                int t, d; DosStamp(DateTimeOffset.Now, out t, out d);
                WriteEntry(fs, recs, name, (byte[])kv.Value, t, d);
                added++;
            }

            long cdStart = fs.Position;
            foreach (Rec r in recs)
            {
                byte[] nb = Encoding.ASCII.GetBytes(r.Name);
                W32(fs, 0x02014b50); W16(fs, 20); W16(fs, 10); W16(fs, 0); W16(fs, 0);
                W16(fs, r.Time); W16(fs, r.Date);
                W32(fs, r.Crc); W32(fs, r.Size); W32(fs, r.Size);
                W16(fs, nb.Length); W16(fs, 0); W16(fs, 0);
                W16(fs, 0); W16(fs, 0); W32(fs, 0);
                W32(fs, (uint)r.Offset);
                fs.Write(nb, 0, nb.Length);
            }
            long cdEnd = fs.Position;

            W32(fs, 0x06054b50); W16(fs, 0); W16(fs, 0);
            W16(fs, recs.Count); W16(fs, recs.Count);
            W32(fs, (uint)(cdEnd - cdStart)); W32(fs, (uint)cdStart); W16(fs, 0);
        }
        return new int[] { written, added };
    }
}
'@ -Language CSharp -ReferencedAssemblies @(
    'System.IO.Compression', 'System.IO.Compression.FileSystem')

function Write-Step ($m) { Write-Host ""; Write-Host $m -ForegroundColor Cyan }
function Write-Ok   ($m) { Write-Host "  $m" -ForegroundColor Green }
function Write-Warn ($m) { Write-Host "  $m" -ForegroundColor Yellow }
function Write-Bad  ($m) { Write-Host "  $m" -ForegroundColor Red }

function Get-BytesHash ([byte[]]$Bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function Read-ZipEntry ($Archive, $Name) {
    $entry = $Archive.GetEntry($Name)
    if (-not $entry) { return $null }
    $stream = $entry.Open()
    $mem = New-Object System.IO.MemoryStream
    try {
        $stream.CopyTo($mem)
        return $mem.ToArray()
    } finally { $stream.Dispose(); $mem.Dispose() }
}

# --- delta reconstruction ---------------------------------------------------
# The delta format is deliberately dumb to apply: copy a byte range from the source, or
# append literal bytes. This walks a byte array and never has to reason about lines or
# line endings -- a place where an off-by-one would silently corrupt CRLF game data.
function Invoke-Delta ([byte[]]$Source, $Delta) {
    if ((Get-BytesHash $Source) -ne $Delta.srcSha256) { return $null }

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

# --- locating the game ------------------------------------------------------
function Test-GameDir ($Path) {
    if (-not $Path) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Path 'Lionheart.exe')) -and
           (Test-Path -LiteralPath (Join-Path $Path 'data.dat'))
}

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
    if (-not (Test-GameDir $typed)) { throw "No Lionheart.exe and data.dat in: $typed" }
    return $typed
}

# --- setup ------------------------------------------------------------------
function Get-Context ($GameDirParam) {
    $manifestPath = Join-Path $ModDir 'mod.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "No mod.json beside this script -- run it from inside the unzipped mod folder."
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

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
        Dat      = Join-Path $game 'data.dat'
        Loose    = Join-Path $game 'data'
        State    = $stateDir
        Backup   = Join-Path $stateDir 'backup'
        Record   = Join-Path $stateDir 'install-record.json'
    }
}

function Assert-GameClosed {
    if (Get-Process -Name 'Lionheart' -ErrorAction SilentlyContinue) {
        throw "Lionheart is running. Close the game and run this again."
    }
}

# Exit code 2 means "this would work with administrator rights", and Install.bat re-launches
# elevated on seeing it. Checked by actually writing a file rather than by asking whether
# the user is an administrator: a GOG Galaxy install grants the logged-in user write access
# to its Games folder, so the common case needs no elevation at all and should not be made
# to click through a UAC prompt for nothing.
function Test-GameDirWritable ($ctx) {
    $probe = Join-Path $ctx.Game ".mod-install-probe"
    try {
        [System.IO.File]::WriteAllText($probe, "probe")
        Remove-Item -LiteralPath $probe -Force
        return $true
    } catch {
        return $false
    }
}

function Assert-DiskSpace ($ctx) {
    $needed = (Get-Item -LiteralPath $ctx.Dat).Length + 200MB
    $drive = (Get-Item -LiteralPath $ctx.Game).PSDrive
    if ($drive -and $drive.Free -lt $needed) {
        throw ("Not enough free space on $($drive.Name): -- rebuilding the archive needs about " +
               "$([math]::Round($needed / 1GB, 1)) GB free and there is " +
               "$([math]::Round($drive.Free / 1GB, 1)) GB.")
    }
}

# --- rebuilding the archive -------------------------------------------------
# $Replace maps entry name -> byte[]. Names not in the source archive are appended.
# $Drop is a list of entry names to omit entirely (used by uninstall).
function Write-Archive ($ctx, $Replace, $Drop) {
    $tmp = "$($ctx.Dat).mod.tmp"
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }

    try {
        $counts = [LhZip]::Rebuild($ctx.Dat, $tmp, $Replace, [string[]]$Drop)
    } catch {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
        throw
    }
    $written = $counts[0]; $added = $counts[1]

    # Validate before swapping. A half-written archive replacing a good one would leave
    # the player with an unlaunchable game and no obvious way back.
    $expected = $written + $added
    $check = [System.IO.Compression.ZipFile]::OpenRead($tmp)
    try {
        $count = $check.Entries.Count
        if ($count -ne $expected) {
            throw "Rebuilt archive has $count entries, expected $expected -- refusing to install it."
        }
        # Store-only is not a preference: the game's parser rejects any other method.
        foreach ($e in $check.Entries) {
            if ($e.CompressedLength -ne $e.Length) {
                throw "Rebuilt archive compressed '$($e.FullName)' -- the game would refuse to load it."
            }
        }
    } finally { $check.Dispose() }

    Move-Item -LiteralPath $tmp -Destination $ctx.Dat -Force
    return @{ Written = $written; Added = $added }
}

# --- install ----------------------------------------------------------------
function Invoke-Install ($ctx) {
    $m = $ctx.Manifest
    Write-Host ""
    Write-Host "  $($m.name) $($m.version)" -ForegroundColor White
    Write-Host "  Installing to: $($ctx.Game)"

    if (Test-Path -LiteralPath $ctx.Record) {
        Write-Step "An earlier version is already installed -- removing it first."
        Invoke-Uninstall $ctx -Quiet
    }
    Assert-DiskSpace $ctx

    # Reconstruct everything before touching the archive.
    $nPatched = @($ctx.Payload.patched).Count
    Write-Step "Preparing $($m.files.Count) file(s)"
    if ($nPatched) { Write-Host "  Rebuilding $nPatched file(s) from your own game data..." }

    # Ordinal, not PowerShell's default case-insensitive hashtable: archive entry
    # names are matched byte-for-byte by the writer and must be here too.
    $content = New-Object 'System.Collections.Generic.Dictionary[string,byte[]]'
    $originals = New-Object 'System.Collections.Generic.Dictionary[string,byte[]]'
    foreach ($rel in $ctx.Payload.verbatim) {
        $srcPath = Join-Path (Join-Path $ModDir 'files') $rel
        if (-not (Test-Path -LiteralPath $srcPath)) {
            # Some resource paths run to ~110 characters, so unzipping into a deep folder
            # pushes them past Windows' 260-char limit and the extractor drops them
            # without saying so. The download is fine; where it was unzipped is not.
            throw ("Mod file missing: $rel`n" +
                   "  This usually means the unzip was silently truncated by Windows' " +
                   "260-character path limit.`n" +
                   "  Move the unzipped folder somewhere short, like C:\Fixt, and run this again.")
        }
        $content[$rel] = [System.IO.File]::ReadAllBytes($srcPath)
    }

    $archive = [System.IO.Compression.ZipFile]::OpenRead($ctx.Dat)
    try {
        foreach ($rel in $ctx.Payload.patched) {
            $patchPath = Join-Path (Join-Path $ModDir 'patches') "$rel.lhpatch"
            if (-not (Test-Path -LiteralPath $patchPath)) {
                throw ("Patch missing: $rel`n" +
                       "  This usually means the unzip was silently truncated by Windows' " +
                       "260-character path limit.`n" +
                       "  Move the unzipped folder somewhere short, like C:\Fixt, and run this again.")
            }
            $delta = Get-Content -LiteralPath $patchPath -Raw | ConvertFrom-Json
            $source = Read-ZipEntry $archive $rel
            if (-not $source) { $source = @() }
            $rebuilt = Invoke-Delta $source $delta
            if (-not $rebuilt) {
                throw ("Could not rebuild $rel.`n" +
                       "  This mod patches your own game files rather than shipping copies of " +
                       "them, so it needs the original.`n" +
                       "  Your data.dat does not hold the version this patch expects -- usually " +
                       "because another mod changed it first.`n" +
                       "  Remove other mods or reinstall the game, then try again. " +
                       "Nothing has been changed.")
            }
            $content[$rel] = $rebuilt
            $originals[$rel] = $source        # kept so uninstall can put it back
        }
    } finally { $archive.Dispose() }

    # Save the originals we are about to overwrite, so uninstall needs no 1.6 GB backup.
    New-Item -ItemType Directory -Path $ctx.Backup -Force | Out-Null
    foreach ($rel in $originals.Keys) {
        $bak = Join-Path $ctx.Backup $rel
        New-Item -ItemType Directory -Path (Split-Path -Parent $bak) -Force | Out-Null
        [System.IO.File]::WriteAllBytes($bak, $originals[$rel])
    }

    Write-Step "Rebuilding data.dat -- this takes a few seconds"
    $stats = Write-Archive $ctx $content @()
    Write-Ok "$($stats.Written) entries rewritten, $($stats.Added) added."

    # A loose data\ directory shadows the archive for every path it holds, so leaving the
    # old copies there would make this whole rebuild invisible.
    $looseSynced = 0
    if (Test-Path -LiteralPath $ctx.Loose) {
        foreach ($rel in $m.files) {
            $dst = Join-Path $ctx.Loose $rel
            New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
            [System.IO.File]::WriteAllBytes($dst, $content[$rel])
            $looseSynced++
        }
        Write-Ok "$looseSynced file(s) also written to the loose data\ folder, which would otherwise shadow the archive."
    }

    $record = [pscustomobject]@{
        id          = $m.id
        name        = $m.name
        version     = $m.version
        installedAt = (Get-Date).ToString('s')
        replaced    = @($ctx.Payload.patched)
        added       = @($ctx.Payload.verbatim)
        looseSynced = $looseSynced
    }
    New-Item -ItemType Directory -Path $ctx.State -Force | Out-Null
    # Without a BOM: Set-Content -Encoding utf8 on PowerShell 5.1 always emits one, which
    # anything reading this as plain UTF-8 JSON chokes on.
    [System.IO.File]::WriteAllText($ctx.Record, ($record | ConvertTo-Json -Depth 5),
                                   (New-Object System.Text.UTF8Encoding($false)))

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
    Assert-DiskSpace $ctx

    $restore = New-Object 'System.Collections.Generic.Dictionary[string,byte[]]'
    $missing = @()
    foreach ($rel in $record.replaced) {
        $bak = Join-Path $ctx.Backup $rel
        if (Test-Path -LiteralPath $bak) {
            $restore[$rel] = [System.IO.File]::ReadAllBytes($bak)
        } else {
            $missing += $rel
        }
    }
    if ($missing.Count) {
        throw ("Cannot uninstall cleanly: the saved originals for $($missing.Count) file(s) are " +
               "missing from $($ctx.Backup).`nNothing has been changed.")
    }

    if (-not $Quiet) { Write-Step "Rebuilding data.dat -- this takes a few seconds" }
    $stats = Write-Archive $ctx $restore @($record.added)

    $looseCleaned = 0
    if (Test-Path -LiteralPath $ctx.Loose) {
        foreach ($rel in $record.replaced) {
            $dst = Join-Path $ctx.Loose $rel
            if (Test-Path -LiteralPath $dst) {
                [System.IO.File]::WriteAllBytes($dst, $restore[$rel]); $looseCleaned++
            }
        }
        foreach ($rel in $record.added) {
            $dst = Join-Path $ctx.Loose $rel
            if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force; $looseCleaned++ }
        }
    }

    Remove-Item -LiteralPath $ctx.State -Recurse -Force -ErrorAction SilentlyContinue

    if (-not $Quiet) {
        Write-Ok "$($restore.Count) file(s) restored, $(@($record.added).Count) removed from the archive."
        if ($looseCleaned) { Write-Ok "$looseCleaned file(s) also reverted in the loose data\ folder." }
        Write-Host ""
        Write-Host "  Done." -ForegroundColor Green
    }
}

# --- entry point ------------------------------------------------------------
try {
    Assert-GameClosed
    $ctx = Get-Context $GameDir
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
