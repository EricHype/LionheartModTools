<#
Lionheart Mod Manager -- a window over the same core the command-line installer uses.

WinForms rather than anything richer, for one reason: it is already on every Windows
machine. A player installing a 60 KB mod should not download a 90 MB runtime to do it, and
an unsigned .exe is a harder thing to ask someone to trust than a script they can read.
Everything here is presentation; all the behaviour lives in lh-core.ps1, shared with
mod-installer.ps1 so the two front ends cannot disagree about what installing means.

Long operations run on the UI thread on purpose. A rebuild is a few seconds, and the
alternative -- runspaces, marshalling log lines back to the form -- is a great deal of
machinery and a new class of bug for a progress bar nobody will watch. The window is
disabled while work runs so a second click cannot start a concurrent rebuild.
#>
[CmdletBinding()]
param(
    [string]$GameDir = '',
    # Build the window, report what it would show, and exit. Lets the whole UI
    # construction path be exercised without a human clicking anything.
    [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSCommandPath) 'lh-core.ps1')

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# --- state ------------------------------------------------------------------
$state = [pscustomobject]@{ Game = $GameDir; Busy = $false }

# --- window -----------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Lionheart Mod Manager'
$form.Size = New-Object System.Drawing.Size(700, 560)
$form.MinimumSize = New-Object System.Drawing.Size(620, 480)
$form.StartPosition = 'CenterScreen'
$form.AllowDrop = $true

$font = New-Object System.Drawing.Font('Segoe UI', 9)
$form.Font = $font

# Game row -------------------------------------------------------------------
$gameGroup = New-Object System.Windows.Forms.GroupBox
$gameGroup.Text = 'Game'
$gameGroup.SetBounds(12, 8, 660, 78)
$gameGroup.Anchor = 'Top,Left,Right'
$form.Controls.Add($gameGroup)

$gamePath = New-Object System.Windows.Forms.TextBox
$gamePath.SetBounds(12, 24, 530, 22)
$gamePath.ReadOnly = $true
$gamePath.Anchor = 'Top,Left,Right'
$gameGroup.Controls.Add($gamePath)

$btnChange = New-Object System.Windows.Forms.Button
$btnChange.Text = 'Change...'
$btnChange.SetBounds(550, 23, 96, 24)
$btnChange.Anchor = 'Top,Right'
$gameGroup.Controls.Add($btnChange)

$gameStatus = New-Object System.Windows.Forms.Label
$gameStatus.SetBounds(14, 52, 630, 18)
$gameStatus.Anchor = 'Top,Left,Right'
$gameGroup.Controls.Add($gameStatus)

# Installed mods -------------------------------------------------------------
$modGroup = New-Object System.Windows.Forms.GroupBox
$modGroup.Text = 'Installed mods'
$modGroup.SetBounds(12, 94, 660, 150)
$modGroup.Anchor = 'Top,Left,Right'
$form.Controls.Add($modGroup)

$modList = New-Object System.Windows.Forms.ListView
$modList.SetBounds(12, 20, 634, 118)
$modList.View = 'Details'
$modList.FullRowSelect = $true
$modList.MultiSelect = $false
$modList.HideSelection = $false
$modList.Anchor = 'Top,Left,Right,Bottom'
[void]$modList.Columns.Add('Mod', 300)
[void]$modList.Columns.Add('Version', 110)
[void]$modList.Columns.Add('Installed by', 200)
$modGroup.Controls.Add($modList)

# Buttons --------------------------------------------------------------------
# A release ships this manager alongside the mod itself, so when it is launched from
# inside an unzipped release the mod to install is sitting right there. Offering it by
# name removes the silly step of browsing back to the archive you just unpacked.
$bundled = $null
$bundledDir = Split-Path -Parent $PSCommandPath
if (Test-Path -LiteralPath (Join-Path $bundledDir 'mod.json')) {
    try { $bundled = Get-Content -LiteralPath (Join-Path $bundledDir 'mod.json') -Raw | ConvertFrom-Json }
    catch { $bundled = $null }
}

$btnInstall = New-Object System.Windows.Forms.Button
$btnInstall.Text = if ($bundled) { "Install $($bundled.name) $($bundled.version)" } else { 'Install mod...' }
$btnInstall.SetBounds(12, 254, $(if ($bundled) { 250 } else { 130 }), 30)
$btnInstall.Anchor = 'Top,Left'
$form.Controls.Add($btnInstall)

$btnOther = New-Object System.Windows.Forms.Button
$btnOther.Text = 'Other mod...'
$btnOther.SetBounds($btnInstall.Right + 8, 254, 110, 30)
$btnOther.Anchor = 'Top,Left'
$btnOther.Visible = [bool]$bundled
$form.Controls.Add($btnOther)

$btnUninstall = New-Object System.Windows.Forms.Button
$btnUninstall.Text = 'Uninstall'
$btnUninstall.SetBounds($(if ($bundled) { $btnOther.Right + 8 } else { 150 }), 254, 110, 30)
$btnUninstall.Anchor = 'Top,Left'
$form.Controls.Add($btnUninstall)

$hint = New-Object System.Windows.Forms.Label
$hint.Text = 'or drop a release .zip onto this window'
$hint.SetBounds($btnUninstall.Right + 12, 261, 300, 18)
$hint.ForeColor = [System.Drawing.SystemColors]::GrayText
$hint.Anchor = 'Top,Left'
$form.Controls.Add($hint)

# Log ------------------------------------------------------------------------
$log = New-Object System.Windows.Forms.RichTextBox
$log.SetBounds(12, 294, 660, 190)
$log.ReadOnly = $true
$log.BackColor = [System.Drawing.Color]::White
$log.Font = New-Object System.Drawing.Font('Consolas', 9)
$log.Anchor = 'Top,Left,Right,Bottom'
$form.Controls.Add($log)

$status = New-Object System.Windows.Forms.Label
$status.SetBounds(14, 492, 660, 20)
$status.Anchor = 'Bottom,Left,Right'
$form.Controls.Add($status)

# --- log sink ---------------------------------------------------------------
# The core writes through this, so the window shows exactly what the console would.
$colours = @{
    Step = [System.Drawing.Color]::FromArgb(0, 90, 160)
    Ok   = [System.Drawing.Color]::FromArgb(0, 120, 40)
    Warn = [System.Drawing.Color]::FromArgb(160, 100, 0)
    Bad  = [System.Drawing.Color]::FromArgb(180, 30, 30)
    Info = [System.Drawing.Color]::FromArgb(40, 40, 40)
}

function Add-Log ($Message, $Level = 'Info') {
    $log.SelectionStart = $log.TextLength
    $log.SelectionLength = 0
    $log.SelectionColor = $colours[$Level]
    if (-not $log.SelectionColor) { $log.SelectionColor = $colours['Info'] }
    $log.AppendText("$Message`n")
    $log.SelectionStart = $log.TextLength
    $log.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

$script:LhLogSink = { param($m, $l) Add-Log $m $l }

# --- helpers ----------------------------------------------------------------
function Set-Busy ([bool]$busy, $message = '') {
    $state.Busy = $busy
    $btnInstall.Enabled = -not $busy
    $btnOther.Enabled = -not $busy
    $btnUninstall.Enabled = (-not $busy) -and ($modList.SelectedItems.Count -gt 0)
    $btnChange.Enabled = -not $busy
    $form.Cursor = if ($busy) { 'WaitCursor' } else { 'Default' }
    if ($message) { $status.Text = $message }
    [System.Windows.Forms.Application]::DoEvents()
}

function Get-InstalledMods {
    <#
    Two things can install a mod into this game, and the window has to show both or it
    will cheerfully report "no mods installed" on a modded game.

      mods\<id>\install-record.json   this manager, and mod-installer.ps1
      mods\installed\<id>\mod.json    modmanager.py, the developer command-line tool

    Only the first can be removed here: the command-line tool rebuilds data.dat from its
    own vanilla backup and its own notion of which mods are enabled, and second-guessing
    that from here would be a worse bug than sending the user back to it.
    #>
    $out = @()
    if (-not $state.Game) { return $out }
    $modsDir = Join-Path $state.Game 'mods'
    if (-not (Test-Path -LiteralPath $modsDir)) { return $out }

    foreach ($dir in Get-ChildItem -LiteralPath $modsDir -Directory -ErrorAction SilentlyContinue) {
        $rec = Join-Path $dir.FullName 'install-record.json'
        if (Test-Path -LiteralPath $rec) {
            $r = Get-Content -LiteralPath $rec -Raw | ConvertFrom-Json
            $out += [pscustomobject]@{
                id = $r.id; name = $r.name; version = $r.version
                installedAt = $r.installedAt; Removable = $true; Active = $true
                Source = 'this manager'
            }
        }
    }

    $cliDir = Join-Path $modsDir 'installed'
    if (Test-Path -LiteralPath $cliDir) {
        $enabled = @()
        $enabledJson = Join-Path $modsDir 'enabled.json'
        if (Test-Path -LiteralPath $enabledJson) {
            # Assign first, then wrap. ConvertFrom-Json emits a JSON array as ONE object
            # rather than enumerating it, so @(...) around the pipeline nests the array
            # inside another array and every -contains against it silently returns false.
            $parsed = Get-Content -LiteralPath $enabledJson -Raw | ConvertFrom-Json
            $enabled = @($parsed)
        }
        foreach ($dir in Get-ChildItem -LiteralPath $cliDir -Directory -ErrorAction SilentlyContinue) {
            $mj = Join-Path $dir.FullName 'mod.json'
            if (-not (Test-Path -LiteralPath $mj)) { continue }
            if ($out.id -contains $dir.Name) { continue }
            $m = Get-Content -LiteralPath $mj -Raw | ConvertFrom-Json
            $on = $enabled -contains $m.id
            $out += [pscustomobject]@{
                id = $m.id; name = $m.name; version = $m.version
                installedAt = ''
                Removable = $false; Active = $on
                Source = if ($on) { 'modmanager.py' } else { 'modmanager.py (disabled)' }
            }
        }
    }
    return $out
}

function Update-View {
    $gamePath.Text = if ($state.Game) { $state.Game } else { '(not found)' }
    $modList.Items.Clear()
    if (-not $state.Game) {
        $gameStatus.Text = 'No Lionheart installation found. Use Change... to point at the folder containing Lionheart.exe.'
        $gameStatus.ForeColor = $colours['Bad']
        $btnInstall.Enabled = $false
        $btnUninstall.Enabled = $false
        return
    }
    $btnInstall.Enabled = -not $state.Busy

    $mods = @(Get-InstalledMods)
    foreach ($m in $mods) {
        $item = New-Object System.Windows.Forms.ListViewItem($m.name)
        [void]$item.SubItems.Add([string]$m.version)
        [void]$item.SubItems.Add([string]$m.Source)
        if (-not $m.Active) { $item.ForeColor = [System.Drawing.SystemColors]::GrayText }
        $item.Tag = $m
        [void]$modList.Items.Add($item)
    }

    $dat = Join-Path $state.Game 'data.dat'
    $size = if (Test-Path -LiteralPath $dat) {
        '{0:N2} GB' -f ((Get-Item -LiteralPath $dat).Length / 1GB)
    } else { 'missing' }
    # Count what is actually in data.dat, not what is merely present on disk: a disabled
    # mod is installed but not applied, and conflating the two would misdescribe the game.
    $active = @($mods | Where-Object { $_.Active }).Count
    $idle = $mods.Count - $active
    if ($mods.Count -eq 0) {
        $gameStatus.Text = "data.dat $size  --  no mods installed"
        $gameStatus.ForeColor = $colours['Info']
    } else {
        $text = "data.dat $size  --  $active mod(s) active"
        if ($idle -gt 0) { $text += ", $idle installed but disabled" }
        $gameStatus.Text = $text
        $gameStatus.ForeColor = if ($active -gt 0) { $colours['Ok'] } else { $colours['Info'] }
    }
    $btnUninstall.Enabled = $false
}

function Invoke-Guarded ($Title, $Action) {
    if ($state.Busy) { return }
    Set-Busy $true "$Title..."
    try {
        Assert-GameClosed
        & $Action
        Set-Busy $false 'Ready.'
    } catch {
        Add-Log "FAILED: $($_.Exception.Message)" 'Bad'
        Set-Busy $false 'Failed -- see the log above.'
        [void][System.Windows.Forms.MessageBox]::Show(
            $_.Exception.Message, $Title,
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error)
    }
    Update-View
}

function Install-From ($Path) {
    Invoke-Guarded 'Install' {
        $folder = Resolve-ModFolder $Path
        $ctx = Get-Context $state.Game $folder
        if (-not (Test-GameDirWritable $ctx)) {
            throw ("Cannot write to $($ctx.Game).`n`nClose the game if it is running, or " +
                   "restart this manager as administrator (right-click, Run as administrator).")
        }
        Invoke-Install $ctx
    }
}

# --- events -----------------------------------------------------------------
$btnChange.Add_Click({
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = 'Select the folder containing Lionheart.exe'
    if ($state.Game) { $dlg.SelectedPath = $state.Game }
    if ($dlg.ShowDialog() -eq 'OK') {
        if (Test-GameDir $dlg.SelectedPath) {
            $state.Game = $dlg.SelectedPath
            Add-Log "Game folder set to $($dlg.SelectedPath)" 'Ok'
            Update-View
        } else {
            [void][System.Windows.Forms.MessageBox]::Show(
                "That folder has no Lionheart.exe and data.dat.", 'Not a Lionheart install',
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Warning)
        }
    }
})

function Show-InstallPicker {
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = 'Mod release (*.zip)|*.zip|All files (*.*)|*.*'
    $dlg.Title = 'Select a mod release'
    if ($dlg.ShowDialog() -eq 'OK') { Install-From $dlg.FileName }
}

$btnInstall.Add_Click({
    if ($bundled) { Install-From $bundledDir } else { Show-InstallPicker }
})

$btnOther.Add_Click({ Show-InstallPicker })

$btnUninstall.Add_Click({
    if ($modList.SelectedItems.Count -eq 0) { return }
    $rec = $modList.SelectedItems[0].Tag
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "Remove $($rec.name) $($rec.version)?`n`nYour data.dat will be rebuilt without it.",
        'Uninstall', [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question)
    if ($answer -ne 'Yes') { return }
    Invoke-Guarded 'Uninstall' {
        # Uninstall reads its saved originals from the game folder, not from the
        # release, so there is no manifest to load here.
        $ctx = New-ContextForId $state.Game $rec.id
        Invoke-Uninstall $ctx
    }
})

$modList.Add_SelectedIndexChanged({
    $sel = $modList.SelectedItems
    $btnUninstall.Enabled = (-not $state.Busy) -and ($sel.Count -gt 0) -and $sel[0].Tag.Removable
    if ($sel.Count -gt 0 -and -not $sel[0].Tag.Removable) {
        $status.Text = "$($sel[0].Text) was installed with modmanager.py -- remove it with that tool."
    }
})

$form.Add_DragEnter({
    if ($_.Data.GetDataPresent([System.Windows.Forms.DataFormats]::FileDrop) -and -not $state.Busy) {
        $_.Effect = [System.Windows.Forms.DragDropEffects]::Copy
    }
})
$form.Add_DragDrop({
    $files = $_.Data.GetData([System.Windows.Forms.DataFormats]::FileDrop)
    if ($files -and $files.Count -gt 0) { Install-From $files[0] }
})

# --- start ------------------------------------------------------------------
if (-not $state.Game) { $state.Game = Find-GameDir }
Update-View
if ($state.Game) {
    Add-Log "Found Lionheart at $($state.Game)" 'Ok'
    $status.Text = 'Ready.'
} else {
    $status.Text = 'Select your Lionheart folder to begin.'
}
Add-Log 'Installing a mod rebuilds data.dat. Remember to start a NEW GAME afterwards.' 'Info'

if ($SelfTest) {
    $rows = @()
    foreach ($item in $modList.Items) {
        $rows += "$($item.Text) $($item.SubItems[1].Text)"
    }
    Write-Output "SELFTEST title=$($form.Text)"
    Write-Output "SELFTEST controls=$($form.Controls.Count)"
    Write-Output "SELFTEST game=$($gamePath.Text)"
    Write-Output "SELFTEST status=$($gameStatus.Text)"
    Write-Output "SELFTEST mods=$($rows -join '; ')"
    # Report the decision, not $btnOther.Visible: WinForms returns the EFFECTIVE
    # visibility, which is false for every control while the form has never been
    # shown, so reading it here would say nothing about what was configured.
    Write-Output "SELFTEST bundled=$(if ($bundled) { "$($bundled.id) $($bundled.version)" } else { 'none' })"
    Write-Output "SELFTEST buttons=[$($btnInstall.Text)] [$($btnOther.Text)] [$($btnUninstall.Text)]"
    Write-Output "SELFTEST install_enabled=$($btnInstall.Enabled) uninstall_enabled=$($btnUninstall.Enabled)"
    Write-Output "SELFTEST logged=$($log.Lines.Count)"
    $form.Dispose()
    exit 0
}

[void]$form.ShowDialog()
