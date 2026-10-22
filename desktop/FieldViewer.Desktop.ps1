# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
param([string]$PackageRoot='', [string]$SmokeScreenshot='', [switch]$SmokeMinimum, [ValidateSet('full','report','rescue','crs')][string]$SmokeMode='full', [string]$SmokeSessionFile='')
$ErrorActionPreference='Stop'
if (-not [Environment]::UserInteractive) {
    throw 'Field Viewer Desktop requires an interactive Windows desktop. Use FieldViewer_data_extractor.bat for SSH or automation.'
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
. (Join-Path $PSScriptRoot 'FieldViewer.Desktop.Core.ps1')
. (Join-Path $PSScriptRoot 'FieldViewer.Desktop.Mail.ps1')
if (-not $PackageRoot) { $PackageRoot=Split-Path -Parent $PSScriptRoot }
$PackageRoot=[IO.Path]::GetFullPath($PackageRoot)
try { $meta=Get-FvMetadata $PackageRoot } catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'Field Viewer'); exit 1 }
$stateHome=Get-FvStateHome $PackageRoot; [IO.Directory]::CreateDirectory($stateHome) | Out-Null
$mutexName='Local\FieldViewerDesktop_'+[IO.Path]::GetFileName($stateHome)
$created=$false; $mutex=New-Object Threading.Mutex($true,$mutexName,[ref]$created)
if (-not $created -and -not $SmokeScreenshot) {
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class FvWindow { [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); }'
    try {
        $owner=Read-FvJson (Join-Path $stateHome 'gui_owner.json') 8192
        if ($owner -and $owner.package_root -ieq $PackageRoot) {
            $existing=[Diagnostics.Process]::GetProcessById([int]$owner.pid)
            if ($existing.StartTime.ToUniversalTime().Ticks -eq [long]$owner.start_ticks -and $existing.MainWindowHandle -ne [IntPtr]::Zero) { [void][FvWindow]::SetForegroundWindow($existing.MainWindowHandle) }
        }
    } catch {}
    $mutex.Dispose(); exit 0
}

$script:session=$null; $script:sessionLoadError=$null; $script:receipt=$null; $script:receiptStamp=''; $script:eventOffset=[long]0
$script:events=New-Object 'System.Collections.Generic.List[object]'; $script:startedMonotonic=$null; $script:progressState=$null; $script:recordedStages=@()
$script:scanJobs=@{}; $script:lastScan=@{}; $script:scanScopes=@{}; $script:lastState=''; $script:links=$null; $script:terminalRefreshPending=$false
$script:updateJob=$null
$script:form=New-Object Windows.Forms.Form
$form=$script:form; $form.Text='Field Viewer Data Extractor '+$meta.ReleaseVersion+' ('+$meta.PreparationRevision+')'
$form.MinimumSize=New-Object Drawing.Size(860,700); $form.Size=New-Object Drawing.Size(1040,780)
if ($SmokeMinimum) { $form.Size=$form.MinimumSize }
$form.StartPosition='CenterScreen'; $form.AutoScaleMode='Dpi'; $form.Font=New-Object Drawing.Font('Segoe UI',10)
if ($SmokeScreenshot) { $form.StartPosition='Manual'; $form.Location=New-Object Drawing.Point(-32000,-32000) }
$ownerPath=Join-Path $stateHome 'gui_owner.json'
if (-not $SmokeScreenshot) { Write-FvJson $ownerPath ([ordered]@{ package_root=$PackageRoot; pid=$PID; start_ticks=(Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks }) }
$form.BackColor=[Drawing.Color]::FromArgb(248,250,252)
$main=New-Object Windows.Forms.TableLayoutPanel; $main.Dock='Fill'; $main.ColumnCount=1; $main.RowCount=7
$main.Padding=New-Object Windows.Forms.Padding(20); $main.AutoScroll=$true
foreach ($h in @(76,48,104,52,148,80)) { [void]$main.RowStyles.Add((New-Object Windows.Forms.RowStyle([Windows.Forms.SizeType]::Absolute,$h))) }
[void]$main.RowStyles.Add((New-Object Windows.Forms.RowStyle([Windows.Forms.SizeType]::Percent,100)))
$form.Controls.Add($main)
function New-FvLabel([string]$Text,[int]$Size=10,[bool]$Bold=$false) {
    $l=New-Object Windows.Forms.Label; $l.Text=$Text; $l.AutoSize=$true
    $l.Font=New-Object Drawing.Font('Segoe UI',$Size,$(if ($Bold) { [Drawing.FontStyle]::Bold } else { [Drawing.FontStyle]::Regular }))
    $l.Margin=New-Object Windows.Forms.Padding(4,3,4,3); $l.Anchor='Left'; return $l
}
function New-FvButton([string]$Text,[scriptblock]$Action) {
    $b=New-Object Windows.Forms.Button; $b.Text=$Text; $b.AutoSize=$true; $b.MinimumSize=New-Object Drawing.Size(88,32)
    $b.FlatStyle='System'; $b.Anchor='Left,Right'; $b.Add_Click($Action); return $b
}
function New-FvFlow { $p=New-Object Windows.Forms.FlowLayoutPanel; $p.Dock='Fill'; $p.WrapContents=$true; $p.AutoScroll=$false; return $p }
function New-FvChoice([string]$Name) {
    $box=New-Object Windows.Forms.ComboBox; $box.DropDownStyle='DropDownList'; $box.Dock='Fill'; $box.Anchor='Left,Right'
    $box.Margin=New-Object Windows.Forms.Padding(4,8,8,4); $box.AccessibleName=$Name; $box.DropDownWidth=280
    return $box
}
$header=New-Object Windows.Forms.TableLayoutPanel; $header.Dock='Fill'; $header.ColumnCount=3; $header.RowCount=1
foreach ($w in @(76,0,100)) { [void]$header.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle([Windows.Forms.SizeType]::Absolute,$w))) }
$header.ColumnStyles[1].SizeType='Percent'; $header.ColumnStyles[1].Width=100
# Render the existing docs/assets/fv-mark.svg design at native 64px dimensions.
$logo=New-Object Windows.Forms.Panel; $logo.Size=New-Object Drawing.Size(64,64); $logo.AccessibleName='Field Viewer FV logo'
$logo.Add_Paint({ param($sender,$e)
    $e.Graphics.SmoothingMode=[Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $brush=New-Object Drawing.Drawing2D.LinearGradientBrush((New-Object Drawing.Rectangle(0,0,64,64)),[Drawing.Color]::FromArgb(55,138,221),[Drawing.Color]::FromArgb(29,158,117),45)
    $shape=New-Object Drawing.Drawing2D.GraphicsPath
    $shape.AddArc(0,0,32,32,180,90); $shape.AddArc(32,0,32,32,270,90); $shape.AddArc(32,32,32,32,0,90); $shape.AddArc(0,32,32,32,90,90); $shape.CloseFigure()
    $e.Graphics.FillPath($brush,$shape); $shape.Dispose(); $brush.Dispose()
    $font=New-Object Drawing.Font('Arial',32,[Drawing.FontStyle]::Bold,[Drawing.GraphicsUnit]::Pixel)
    $format=New-Object Drawing.StringFormat; $format.Alignment='Center'; $format.LineAlignment='Center'
    $e.Graphics.DrawString('FV',$font,[Drawing.Brushes]::White,(New-Object Drawing.RectangleF(0,1,64,64)),$format); $format.Dispose(); $font.Dispose()
})
$header.Controls.Add($logo,0,0)
$title=New-FvLabel "Field Viewer`r`nData Extractor" 17 $true; $header.Controls.Add($title,1,0)
$helpMenu=New-Object Windows.Forms.ContextMenuStrip
$aboutItem=$helpMenu.Items.Add('About Field Viewer'); $aboutItem.Add_Click({ Show-FvAbout })
$emailButton=$helpMenu.Items.Add('Email last log...'); $emailButton.Enabled=$false; $emailButton.Add_Click({ Show-FvEmail })
$updateItem=$helpMenu.Items.Add('Check for updates...'); $updateItem.Add_Click({ Start-FvUpdateCheck })
$help=New-FvButton '&Help...' { $helpMenu.Show($help,0,$help.Height) }; $header.Controls.Add($help,2,0)
$main.Controls.Add($header,0,0)
# Two selectors replace the report/folder button strips. Only available targets appear.
$linkRow=New-Object Windows.Forms.TableLayoutPanel; $linkRow.Dock='Fill'; $linkRow.ColumnCount=6; $linkRow.RowCount=1
foreach ($w in @(64,0,88,64,0,88)) { [void]$linkRow.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle([Windows.Forms.SizeType]::Absolute,$w))) }
foreach ($i in @(1,4)) { $linkRow.ColumnStyles[$i].SizeType='Percent'; $linkRow.ColumnStyles[$i].Width=50 }
$reportChoice=New-FvChoice 'Reports'; $folderChoice=New-FvChoice 'Extracted data folders'
$reportOpen=New-FvButton '&Open' { if ($reportChoice.SelectedIndex -ge 0) { Open-FvLink 'reports' ([string]$reportChoice.SelectedItem) } }
$folderOpen=New-FvButton 'O&pen' { if ($folderChoice.SelectedIndex -ge 0) { Open-FvLink 'folders' ([string]$folderChoice.SelectedItem) } }
$reportOpen.AccessibleName='Open selected report'; $folderOpen.AccessibleName='Open selected folder'
$reportChoice.Enabled=$false; $folderChoice.Enabled=$false; $reportOpen.Enabled=$false; $folderOpen.Enabled=$false
$linkRow.Controls.Add((New-FvLabel 'Reports'),0,0); $linkRow.Controls.Add($reportChoice,1,0); $linkRow.Controls.Add($reportOpen,2,0)
$linkRow.Controls.Add((New-FvLabel 'Folders'),3,0); $linkRow.Controls.Add($folderChoice,4,0); $linkRow.Controls.Add($folderOpen,5,0)
$main.Controls.Add($linkRow,0,1)
$paths=New-Object Windows.Forms.TableLayoutPanel; $paths.Dock='Fill'; $paths.ColumnCount=3; $paths.RowCount=2
[void]$paths.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle([Windows.Forms.SizeType]::Absolute,108)))
[void]$paths.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle([Windows.Forms.SizeType]::Percent,100)))
[void]$paths.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle([Windows.Forms.SizeType]::Absolute,104)))
foreach ($i in 0..1) { [void]$paths.RowStyles.Add((New-Object Windows.Forms.RowStyle([Windows.Forms.SizeType]::Percent,50))) }
$projectBox=New-Object Windows.Forms.TextBox; $projectBox.Dock='Fill'; $projectBox.Anchor='Left,Right'; $projectBox.AccessibleName='Petrel project path'
$outputBox=New-Object Windows.Forms.TextBox; $outputBox.Dock='Fill'; $outputBox.Anchor='Left,Right'; $outputBox.AccessibleName='Output folder path'
$projectBox.Add_TextChanged({ $script:lastScan.Remove('input'); $sizeLabel.Text='Input exact .pet + matching .ptd: Calculating...' })
$outputBox.Add_TextChanged({ $script:lastScan.Remove('selected'); $sizeLabel.Text='Selected output total: Calculating...' })
$projectBrowse=New-FvButton '&Browse...' {
    $dialog=New-Object Windows.Forms.OpenFileDialog; $dialog.Filter='Petrel project (*.pet)|*.pet'; $dialog.CheckFileExists=$true
    try { if ($dialog.ShowDialog() -eq 'OK') { $projectBox.Text=$dialog.FileName; Start-FvScan 'input' } } finally { $dialog.Dispose() }
}
$outputBrowse=New-FvButton 'B&rowse...' {
    $dialog=New-Object Windows.Forms.FolderBrowserDialog; $dialog.Description='Select an output folder outside the source project directory'
    try { if ($dialog.ShowDialog() -eq 'OK') { $outputBox.Text=$dialog.SelectedPath; Start-FvScan 'selected' } } finally { $dialog.Dispose() }
}
$paths.Controls.Add((New-FvLabel 'Petrel project' 10 $true),0,0); $paths.Controls.Add($projectBox,1,0); $paths.Controls.Add($projectBrowse,2,0)
$paths.Controls.Add((New-FvLabel 'Output folder' 10 $true),0,1); $paths.Controls.Add($outputBox,1,1); $paths.Controls.Add($outputBrowse,2,1)
$main.Controls.Add($paths,0,2)
$controlRow=New-Object Windows.Forms.TableLayoutPanel; $controlRow.Dock='Fill'; $controlRow.ColumnCount=4; $controlRow.RowCount=1
foreach ($w in @(108,220,0,120)) { [void]$controlRow.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle([Windows.Forms.SizeType]::Absolute,$w))) }
$controlRow.ColumnStyles[2].SizeType='Percent'; $controlRow.ColumnStyles[2].Width=100
$modeBox=New-FvChoice 'Extraction mode'; [void]$modeBox.Items.Add('Full extraction'); [void]$modeBox.Items.Add('Report only'); [void]$modeBox.Items.Add('RESCUE (experimental)'); [void]$modeBox.Items.Add('CRS and datum only'); $modeBox.SelectedIndex=0
if ($SmokeScreenshot) { $modeBox.SelectedIndex=@('full','report','rescue','crs').IndexOf($SmokeMode) }
$runButton=New-FvButton '&Run' { Start-FvRun }; $runButton.BackColor=[Drawing.Color]::FromArgb(29,105,172); $runButton.ForeColor=[Drawing.Color]::White; $runButton.FlatStyle='Flat'
$controlRow.Controls.Add((New-FvLabel 'Mode' 10 $true),0,0); $controlRow.Controls.Add($modeBox,1,0); $controlRow.Controls.Add($runButton,3,0)
$main.Controls.Add($controlRow,0,3)
$progressPanel=New-Object Windows.Forms.TableLayoutPanel; $progressPanel.Dock='Fill'; $progressPanel.ColumnCount=1; $progressPanel.RowCount=5
foreach ($h in @(30,28,28,24,30)) { [void]$progressPanel.RowStyles.Add((New-Object Windows.Forms.RowStyle([Windows.Forms.SizeType]::Absolute,$h))) }
$statusLabel=New-FvLabel 'Ready. Choose a project and an output folder.' 10 $true
$timerLabel=New-FvLabel 'Elapsed 00:00:00  |  Overall progress: not available  |  Last activity: --' 9 $false
$stageLabel=New-FvLabel 'Stage: awaiting run' 9 $false
$stageStrip=New-FvLabel 'Recorded stages: awaiting first event' 9 $false
foreach ($label in @($statusLabel,$timerLabel,$stageLabel,$stageStrip)) { $label.AutoSize=$false; $label.Dock='Fill'; $label.AutoEllipsis=$true }
$bar=New-Object Windows.Forms.ProgressBar; $bar.Dock='Fill'; $bar.Style='Marquee'; $bar.MarqueeAnimationSpeed=0
$progressPanel.Controls.Add($statusLabel,0,0); $progressPanel.Controls.Add($timerLabel,0,1)
$progressPanel.Controls.Add($stageLabel,0,2); $progressPanel.Controls.Add($bar,0,3); $progressPanel.Controls.Add($stageStrip,0,4)
$main.Controls.Add($progressPanel,0,4)
$sizeLabel=New-FvLabel 'Input project: --  |  Output this run: --  |  Output folder total: --' 9 $false
$sizeLabel.AutoSize=$false; $sizeLabel.Dock='Fill'; $main.Controls.Add($sizeLabel,0,5)
$detail=New-Object Windows.Forms.TextBox; $detail.Multiline=$true; $detail.ReadOnly=$true; $detail.ScrollBars='Vertical'; $detail.Dock='Fill'; $detail.MinimumSize=New-Object Drawing.Size(0,64)
$detail.Text='Choose the exact .pet file with its matching .ptd folder. A new FV_<ID> folder will be created in the output folder. Prefer a short output path, such as C:\FV.'
if (-not (Test-Path -LiteralPath (Join-Path $meta.EngineRoot 'runtime/python.exe') -PathType Leaf)) {
    $detail.Text+="`r`nFirst use: bundled Python will be prepared automatically in the background when you press Run. Preparation may take a minute or two; progress stays in this window."
}
$main.Controls.Add($detail,0,6)

function Open-FvLink([string]$Group,[string]$Name) {
    if (-not $script:links -or -not $script:session) { return }
    $target=$script:links[$Group][$Name]
    if (-not $target) { return }
    if ($Group -eq 'reports') { $target=Resolve-FvArtifact $script:session $target }
    elseif ($Name -eq 'Selected output') { if (-not (Test-Path -LiteralPath $target -PathType Container) -or -not (Test-FvNoReparse $target)) { $target=$null } }
    else { $target=Resolve-FvArtifact $script:session $target 'dir' }
    if (-not $target) { [Windows.Forms.MessageBox]::Show('This run-bound target is no longer available.','Open result') | Out-Null; Update-FvLinks; return }
    try { Start-Process -FilePath $target | Out-Null } catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'Open result') | Out-Null }
}
function Update-FvLinks {
    if (-not $script:session) { return }
    $script:links=Get-FvLinks $script:session $script:receipt
    foreach ($group in @('reports','folders')) {
        $choice=$(if ($group -eq 'reports') { $reportChoice } else { $folderChoice })
        $open=$(if ($group -eq 'reports') { $reportOpen } else { $folderOpen })
        $selected=[string]$choice.SelectedItem
        $preferred=$(if ($group -eq 'reports') { 'Main report' } else { 'All exports' })
        $keys=@($script:links[$group].Keys | Where-Object { $script:links[$group][$_] })
        $ordered=@($keys | Sort-Object @{Expression={ if ($_ -eq $preferred) { 0 } else { 1 } }}, @{Expression={$_}})
        # Avoid resetting an open dropdown during the one-second dashboard refresh.
        if (($ordered -join "`n") -ne (@($choice.Items) -join "`n")) {
            $choice.BeginUpdate()
            try {
                $choice.Items.Clear()
                foreach ($key in $ordered) { [void]$choice.Items.Add($key) }
                if ($choice.Items.Contains($selected)) { $choice.SelectedItem=$selected }
                elseif ($choice.Items.Count) { $choice.SelectedIndex=0 }
            } finally { $choice.EndUpdate() }
        }
        $choice.Enabled=($choice.Items.Count -gt 0); $open.Enabled=$choice.Enabled
    }
}

function Start-FvRun {
    $launched=$false
    $createdSession=$false
    try {
        if ($script:sessionLoadError) { throw $script:sessionLoadError }
        if ($modeBox.SelectedIndex -eq 2 -and -not $meta.RescueExportAvailable) { throw 'Static-model RESCUE export is not included in public beta3. Full extraction and CRS modes remain available; use the CLI inventory-only option for grid metadata.' }
        if ($script:session -and (Get-FvSessionState $script:session) -notin @('finished','launch_failed')) { throw 'The previous run is active or its process identity is unverified. New work is disabled until a terminal supervisor record exists.' }
        $script:session=New-FvSession $PackageRoot $projectBox.Text $outputBox.Text ($modeBox.SelectedIndex -eq 1) ($modeBox.SelectedIndex -eq 2) ($modeBox.SelectedIndex -eq 3)
        $createdSession=$true
        $script:receipt=$null; $script:receiptStamp=''; $script:eventOffset=0; $script:events.Clear(); $script:progressState=$null; $script:recordedStages=@(); $script:startedMonotonic=[Diagnostics.Stopwatch]::StartNew(); $script:lastScan.Remove('run'); $script:lastScan.Remove('selected'); $script:terminalRefreshPending=$false
        $detail.Text=$(if ($modeBox.SelectedIndex -eq 3) { "Run folder: $($script:session.output)`r`nReading CRS and datum declarations from PET/Model metadata only. No 3D model is required. No data or seismic extraction, and no coordinate transformation." } elseif ($modeBox.SelectedIndex -eq 2) { "Run folder: $($script:session.output)`r`nExperimental RESCUE requires the exact tested static-model profile. Other projects produce diagnostics and no RESCUE model. For metadata without a static model, choose CRS and datum only." } else { "Run folder: $($script:session.output)`r`nFull hashing is enabled. Backend bootstrap and doctor run inside the existing launcher." })
        $runButton.Enabled=$false; $projectBox.Enabled=$false; $outputBox.Enabled=$false; $projectBrowse.Enabled=$false; $outputBrowse.Enabled=$false; $modeBox.Enabled=$false
        Update-FvLinks
        $args=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'FieldViewer.Desktop.Supervisor.ps1'),'-SessionFile',$script:session.session_file)
        Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList (Join-FvArgs $args) -WindowStyle Hidden | Out-Null
        $launched=$true
        Start-FvScan 'input'; Start-FvScan 'selected'; Start-FvScan 'run'
    } catch {
        if ($createdSession -and -not $launched -and $script:session -and $script:session.state -eq 'starting') {
            $script:session.state='launch_failed'; $script:session.ended_utc=[DateTime]::UtcNow.ToString('o')
            $script:session | Add-Member -NotePropertyName launch_error -NotePropertyValue $_.Exception.Message -Force
            try { Write-FvJson $script:session.session_file $script:session } catch {}
        }
        [Windows.Forms.MessageBox]::Show($_.Exception.Message,'Cannot start run') | Out-Null
        $editable=(-not $script:sessionLoadError -and (-not $script:session -or (Get-FvSessionState $script:session) -in @('finished','launch_failed')))
        $runButton.Enabled=$editable; $projectBox.Enabled=$editable; $outputBox.Enabled=$editable; $projectBrowse.Enabled=$editable; $outputBrowse.Enabled=$editable; $modeBox.Enabled=$editable
    }
}
function Start-FvScan([string]$Kind) {
    try {
        if ($SmokeScreenshot) { return }
        if ($modeBox.SelectedIndex -eq 3 -and $Kind -in @('input','selected')) { return }
        if ($Kind -eq 'input') {
            if (-not (Test-Path -LiteralPath $projectBox.Text -PathType Leaf)) { return }
            $pet=[IO.Path]::GetFullPath($projectBox.Text); $root=[IO.Path]::ChangeExtension($pet,'.ptd')
            if (-not (Test-Path -LiteralPath $root -PathType Container)) { return }
            $exact=$pet
        } elseif ($Kind -eq 'selected') { $root=$outputBox.Text; $exact='' }
        else { if (-not $script:session) { return }; $root=$script:session.output; $exact='' }
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { return }
        $scope=[IO.Path]::GetFullPath($root)+'|'+$exact
        if ($script:scanScopes[$Kind] -ne $scope) { $script:scanScopes[$Kind]=$scope; $script:lastScan.Remove($Kind) }
        if ($script:scanJobs.ContainsKey($Kind) -and -not $script:scanJobs[$Kind].HasExited) { return }
        if ($script:lastScan.ContainsKey($Kind) -and ([DateTime]::UtcNow-$script:lastScan[$Kind]).TotalSeconds -lt 15) { return }
        $script:lastScan[$Kind]=[DateTime]::UtcNow
        $path=Join-Path $stateHome ($Kind+'_size.json'); if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
        $args=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'FieldViewer.Desktop.SizeScan.ps1'),'-Root',$root,'-ResultPath',$path)
        if ($exact) { $args+=@('-ExactFile',$exact) }
        $script:scanJobs[$Kind]=Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList (Join-FvArgs $args) -WindowStyle Hidden -PassThru
    } catch {}
}
function Format-FvScan([string]$Kind) {
    if ($modeBox.SelectedIndex -eq 3 -and $Kind -eq 'input') { return 'PET/Model metadata only; complete project size not scanned' }
    if ($modeBox.SelectedIndex -eq 3 -and $Kind -eq 'selected') { return 'Not scanned in CRS-only mode' }
    $r=Read-FvJson (Join-Path $stateHome ($Kind+'_size.json')) 16384
    if (-not $r) { return 'Calculating...' }
    try {
        if ($Kind -eq 'input') { $expected=[IO.Path]::ChangeExtension([IO.Path]::GetFullPath($projectBox.Text),'.ptd'); $exact=[IO.Path]::GetFullPath($projectBox.Text) }
        elseif ($Kind -eq 'selected') { $expected=[IO.Path]::GetFullPath($outputBox.Text); $exact='' }
        else { if (-not $script:session) { return 'Calculating...' }; $expected=$script:session.output; $exact='' }
        if ($r.root -ine $expected -or [string]$r.exact_file -ine $exact) { return 'Calculating...' }
    } catch { return 'Unavailable' }
    $prefix=$(if ($r.partial) { 'Partial ' } else { '' })
    return ($prefix+(Format-FvBytes ([long]$r.logical_path_sum_bytes))+" logical size ($($r.files) files; aliases included)")
}
function New-FvAboutDialog {
    $dialog=New-Object Windows.Forms.Form; $dialog.Text='About Field Viewer'; $dialog.Size=New-Object Drawing.Size(740,540); $dialog.StartPosition='CenterParent'; $dialog.AutoScaleMode='Dpi'
    $layout=New-Object Windows.Forms.FlowLayoutPanel; $layout.Dock='Fill'; $layout.FlowDirection='TopDown'; $layout.WrapContents=$false; $layout.AutoScroll=$true; $layout.Padding=New-Object Windows.Forms.Padding(14); $dialog.Controls.Add($layout)
    $info=New-FvLabel "Field Viewer Data Extractor`r`nRelease: $($meta.ReleaseVersion)`r`nPreparation: $($meta.PreparationRevision)`r`nPackage folder: $($meta.PackageRoot)`r`nBase engine: $($meta.Version), $($meta.Build)`r`nCurrent scripts: $($meta.SourceRevision)`r`nGUI revision: $($meta.GuiRevision)`r`nEdition: $($meta.Edition)" 10 $false
    $info.MaximumSize=New-Object Drawing.Size(625,0); $layout.Controls.Add($info)
    foreach ($entry in @(
        @{Label='GitHub project';Target='https://github.com/ahmedsahernouh/petrel-headless-extractor'},
        @{Label='Ahmed Saher Nouh on LinkedIn';Target='https://www.linkedin.com/in/ahmedsahernouh'},
        @{Label='SaherLabs website';Target='https://saherlabs.dev/'},
        @{Label='Support email';Target='mailto:ahmed-saher@hotmail.com'})) {
        $link=New-Object Windows.Forms.LinkLabel; $link.Text=$entry.Label+' — '+$entry.Target; $link.AutoSize=$true; $link.MaximumSize=New-Object Drawing.Size(680,0); $link.Tag=$entry.Target
        $link.Add_LinkClicked({ Start-Process -FilePath ([string]$this.Tag) | Out-Null }); $layout.Controls.Add($link)
    }
    foreach ($entry in @(@{Label='Apache-2.0 LICENSE';Name='LICENSE'},@{Label='Author NOTICE';Name='NOTICE'},@{Label='Third-party notices';Name='THIRD_PARTY_NOTICES.md'})) {
        $path=Join-Path $meta.EngineRoot $entry.Name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { $path=Join-Path $PackageRoot $entry.Name }
        $button=New-FvButton $entry.Label { try { Start-Process -FilePath ([string]$this.Tag) | Out-Null } catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'Open notice') | Out-Null } }
        $button.Tag=$path; $button.Enabled=(Test-Path -LiteralPath $path -PathType Leaf); $layout.Controls.Add($button)
    }
    $close=New-FvButton '&Close' { $this.FindForm().Close() }; $layout.Controls.Add($close)
    return $dialog
}
function Show-FvAbout {
    $dialog=New-FvAboutDialog
    try { [void]$dialog.ShowDialog($form) } finally { $dialog.Dispose() }
}
function New-FvUpdateDialog($Result) {
    $dialog=New-Object Windows.Forms.Form; $dialog.Text='Check for updates'; $dialog.Size=New-Object Drawing.Size(670,330); $dialog.StartPosition='CenterParent'; $dialog.AutoScaleMode='Dpi'
    $panel=New-Object Windows.Forms.FlowLayoutPanel; $panel.Dock='Fill'; $panel.FlowDirection='TopDown'; $panel.WrapContents=$false; $panel.AutoScroll=$true; $panel.Padding=New-Object Windows.Forms.Padding(18); $dialog.Controls.Add($panel)
    $heading=New-FvLabel 'Field Viewer updates' 14 $true; $panel.Controls.Add($heading)
    $message=New-FvLabel ([string]$Result.Message) 10; $message.MaximumSize=New-Object Drawing.Size(600,0); $panel.Controls.Add($message)
    $privacy=New-FvLabel 'Checks public GitHub releases only. No project data is sent. Updates are downloaded manually.' 9; $privacy.MaximumSize=New-Object Drawing.Size(600,0); $panel.Controls.Add($privacy)
    # Only the checker-created URL under this fixed repository can be opened.
    if ($Result.Status -eq 'update_available' -and [string]$Result.ReleaseUrl -match '^https://github\.com/ahmedsahernouh/petrel-headless-extractor/releases/tag/[A-Za-z0-9.%+_-]+$') {
        $link=New-Object Windows.Forms.LinkLabel; $link.Text='Open the newer release on GitHub'; $link.Tag=[string]$Result.ReleaseUrl; $link.AutoSize=$true
        $link.Add_LinkClicked({ try { Start-Process -FilePath ([string]$this.Tag) | Out-Null } catch { [Windows.Forms.MessageBox]::Show('Could not open the browser. Visit the project releases page manually.','Check for updates') | Out-Null } }); $panel.Controls.Add($link)
    }
    $close=New-FvButton '&Close' { $this.FindForm().Close() }; $panel.Controls.Add($close); $dialog.AcceptButton=$close; $dialog.CancelButton=$close
    return $dialog
}
function Show-FvUpdateResult($Result) {
    $dialog=New-FvUpdateDialog $Result
    try { [void]$dialog.ShowDialog($form) } finally { $dialog.Dispose() }
}
function Start-FvUpdateCheck {
    if ($script:updateJob) { return }
    $worker=$null
    try {
        $helper=Join-Path $PSScriptRoot 'FieldViewer.Desktop.Updates.ps1'
        if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'Update checker is unavailable.' }
        $worker=[PowerShell]::Create()
        [void]$worker.AddScript('param($helper,$version) . $helper; Get-FvUpdateCheck -CurrentVersion $version').AddArgument($helper).AddArgument($meta.ReleaseVersion)
        $handle=$worker.BeginInvoke()
        $script:updateJob=[pscustomobject]@{Worker=$worker;Handle=$handle}
        $updateItem.Enabled=$false; $updateItem.Text='Checking GitHub...'
    } catch {
        if ($worker) { $worker.Dispose() }
        Show-FvUpdateResult ([pscustomobject]@{Status='unavailable';CurrentVersion=$meta.ReleaseVersion;LatestVersion='';ReleaseUrl='';Message='Could not check for updates. Check your connection and try again. Your installed version is '+$meta.ReleaseVersion+'.'})
    }
}
function Complete-FvUpdateCheck {
    if (-not $script:updateJob -or -not $script:updateJob.Handle.IsCompleted) { return }
    $job=$script:updateJob; $script:updateJob=$null
    try {
        $results=@($job.Worker.EndInvoke($job.Handle))
        if ($job.Worker.HadErrors -or $results.Count -ne 1) { throw 'Update checker failed.' }
        $result=$results[0]
    } catch {
        $result=[pscustomobject]@{Status='unavailable';CurrentVersion=$meta.ReleaseVersion;LatestVersion='';ReleaseUrl='';Message='Could not check for updates. Check your connection and try again. Your installed version is '+$meta.ReleaseVersion+'.'}
    } finally {
        $job.Worker.Dispose(); $updateItem.Text='Check for updates...'; $updateItem.Enabled=$true
    }
    Show-FvUpdateResult $result
}
function Show-FvEmail {
    if (-not $script:session) { return }
    $r=$script:receipt
    if (-not $r) {
        $logs=@(Get-ChildItem -LiteralPath $script:session.output -File -Filter '*_LOG.txt' -ErrorAction SilentlyContinue)
        if ($logs.Count -ne 1) { [Windows.Forms.MessageBox]::Show('This run has no exact log yet.','Email last log') | Out-Null; return }
        $r=[pscustomobject]@{ process_log=$logs[0].FullName }
    }
    $log=Resolve-FvArtifact $script:session (Get-FvProp $r 'process_log')
    if (-not $log) { return }
    $candidate=Get-FvRedactedLogMember $script:session $log
    $dialog=New-Object Windows.Forms.Form; $dialog.Text='Review diagnostic email'; $dialog.Size=New-Object Drawing.Size(680,475); $dialog.StartPosition='CenterParent'; $dialog.AutoScaleMode='Dpi'
    $panel=New-Object Windows.Forms.FlowLayoutPanel; $panel.Dock='Fill'; $panel.FlowDirection='TopDown'; $panel.WrapContents=$false; $panel.AutoScroll=$true; $panel.Padding=New-Object Windows.Forms.Padding(12); $dialog.Controls.Add($panel)
    $state=Get-FvSessionState $script:session
    $choice=$(if ($candidate) { "Best-effort redacted member $($candidate.Member) from $([IO.Path]::GetFileName($candidate.Zip))" } else { 'No complete mapped redacted log is available.' })
    $info=New-FvLabel "Run: $($script:session.id) | Date: $($script:session.started_utc) | Status: $state`r`nRecipient: ahmed-saher@hotmail.com`r`nLog: $([IO.Path]::GetFileName($log)) ($((Get-Item -LiteralPath $log).Length) bytes)`r`n$choice`r`nReview paths, names and free text; identifier replacement is not anonymity." 9 $false
    $info.MaximumSize=New-Object Drawing.Size(610,0); $panel.Controls.Add($info)
    $unredacted=New-Object Windows.Forms.CheckBox; $unredacted.Text='Use explicitly UNREDACTED closed log copy if redacted member unavailable'; $unredacted.AutoSize=$true; $unredacted.Enabled=(-not [bool]$candidate); $panel.Controls.Add($unredacted)
    $omit=New-Object Windows.Forms.CheckBox; $omit.Text='Omit attachment'; $omit.AutoSize=$true; $panel.Controls.Add($omit)
    $preparedLabel=New-FvLabel 'Prepared attachment: none. Prepare a closed copy and review its exact path and size.' 9 $false
    $preparedLabel.MaximumSize=New-Object Drawing.Size(610,0); $panel.Controls.Add($preparedLabel)
    $unredacted.Add_CheckedChanged({ $dialog.Tag=$null; $preparedLabel.Text='Prepared attachment: none. Prepare a new closed copy.' })
    $omit.Add_CheckedChanged({ $dialog.Tag=$null; $preparedLabel.Text=$(if ($omit.Checked) { 'No attachment selected.' } else { 'Prepared attachment: none. Prepare a closed copy.' }) })
    $prepareCopy=New-FvButton '&Prepare closed copy' {
        try {
            if ($omit.Checked) { $preparedLabel.Text='No attachment selected.'; return }
            $copy=New-FvClosedLogCopy $script:session $r $unredacted.Checked
            if (-not $copy.Path) { [Windows.Forms.MessageBox]::Show('Choose UNREDACTED explicitly or omit the attachment.','Review required') | Out-Null; return }
            $dialog.Tag=$copy
            $size=(Get-Item -LiteralPath $copy.Path).Length
            $preparedLabel.Text="Prepared attachment: $($copy.Path)`r`n$size bytes | $($copy.Privacy) | Captured $($copy.CapturedUtc)$(if (-not $copy.Complete) { ' | INCOMPLETE SNAPSHOT' })"
        } catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'Attachment preparation failed') | Out-Null }
    }; $panel.Controls.Add($prepareCopy)
    $panel.Controls.Add((New-FvLabel 'Optional explanation:' 9 $true))
    $explain=New-Object Windows.Forms.TextBox; $explain.Multiline=$true; $explain.Width=600; $explain.Height=120; $panel.Controls.Add($explain)
    $prepare=New-FvButton 'Open reviewed &draft' {
        try {
            $attachment=''; $privacy='No attachment'
            if (-not $omit.Checked) {
                $copy=$dialog.Tag
                if (-not $copy -or -not $copy.Path -or -not (Test-Path -LiteralPath $copy.Path -PathType Leaf)) { [Windows.Forms.MessageBox]::Show('Prepare the exact attachment and review its path and size first.','Review required') | Out-Null; return }
                $attachment=$copy.Path; $privacy=$copy.Privacy
            }
            $subject="Field Viewer $($meta.Version) ($($meta.EngineTag)) run $($script:session.id)"
            $body="Run: $($script:session.id)`r`nDate: $($script:session.started_utc)`r`nStatus: $state`r`nAttachment: $attachment ($privacy)`r`n"
            if ($state -ne 'finished') { $body+="Snapshot captured while running; incomplete.`r`n" }
            $body+="`r`n$($explain.Text)"
            $outcome=Invoke-FvMailDraft 'ahmed-saher@hotmail.com' $subject $body $attachment
            [Windows.Forms.MessageBox]::Show($outcome,'Diagnostic draft') | Out-Null
            $dialog.Close()
        } catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'Diagnostic draft failed') | Out-Null }
    }; $panel.Controls.Add($prepare)
    [void]$dialog.ShowDialog($form); $dialog.Dispose()
}
function Update-FvDashboard {
    if (-not $script:session) {
        Start-FvScan 'input'; Start-FvScan 'selected'
        $sizeLabel.Text="Input exact .pet + matching .ptd: $(Format-FvScan 'input')`r`nOutput this run: --`r`nSelected output folder total: $(Format-FvScan 'selected')"
        return
    }
    try {
        $current=Read-FvJson $script:session.session_file 65536
        if ($current -and $current.id -eq $script:session.id) { $script:session=$current }
        $state=Get-FvSessionState $script:session
        if ($state -ne $script:lastState) { $script:lastState=$state; Update-FvLinks }
        elseif ($state -eq 'running') { Update-FvLinks }
        if ($state -eq 'finished' -and -not $script:receipt) {
            $candidate=@(if ((Get-FvProp $script:session 'export_mode') -eq 'crs') { Join-Path (Join-Path $script:session.output 'CRS') 'CRS_EXPORT.json' | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } } elseif ((Get-FvProp $script:session 'export_mode') -eq 'rescue') { Join-Path (Join-Path $script:session.output 'RESCUE') 'RESCUE_EXPORT.json' | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } } else { Get-ChildItem -LiteralPath $script:session.output -Directory -Filter '*_data' -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName 'RUN_RESULT.json' } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } })
            $stamp=$(if ($candidate.Count -eq 1) { (Get-Item -LiteralPath $candidate[0]).LastWriteTimeUtc.Ticks.ToString() } else { 'none' })
            if ($stamp -ne $script:receiptStamp) { $script:receiptStamp=$stamp; $script:receipt=Get-FvReceipt $script:session; Update-FvLinks }
        }
        $seconds=0.0
        if ($state -in @('finished','launch_failed') -and $script:session.ended_utc) { $seconds=([DateTime]::Parse($script:session.ended_utc)-[DateTime]::Parse($script:session.started_utc)).TotalSeconds }
        elseif ($script:startedMonotonic) { $seconds=$script:startedMonotonic.Elapsed.TotalSeconds }
        else { $seconds=([DateTime]::UtcNow-[DateTime]::Parse($script:session.started_utc)).TotalSeconds }
        $eventsPath=$null
        if ($script:receipt) { $eventsPath=Resolve-FvArtifact $script:session (Get-FvProp $script:receipt 'structured_events') }
        if (-not $eventsPath -and (Get-FvProp $script:session 'export_mode') -eq 'rescue') { $eventsPath=Resolve-FvArtifact $script:session (Join-Path (Join-Path $script:session.output 'RESCUE') 'EVENTS.jsonl') }
        if (-not $eventsPath -and (Get-FvProp $script:session 'export_mode') -eq 'crs') { $eventsPath=Resolve-FvArtifact $script:session (Join-Path (Join-Path $script:session.output 'CRS') 'EVENTS.jsonl') }
        if (-not $eventsPath) { $eventFiles=@(Get-ChildItem -LiteralPath $script:session.output -File -Filter '*_EVENTS.jsonl' -ErrorAction SilentlyContinue); if ($eventFiles.Count -eq 1) { $eventsPath=$eventFiles[0].FullName } }
        if ($eventsPath) {
            $slice=Read-FvEventSlice $eventsPath $script:eventOffset
            $script:eventOffset=$slice.offset
            foreach ($e in $slice.events) {
                if ((Get-FvProp $e 'event') -eq 'stage') {
                    $n=Get-FvProp $e 'number'
                    if ($null -ne $n -and ($script:recordedStages.Count -eq 0 -or $script:recordedStages[-1] -ne $n)) { $script:recordedStages+= $n }
                }
            }
            $script:progressState=Get-FvProgress $slice.events $script:progressState
        }
        if (-not $script:progressState) { $script:progressState=Get-FvProgress @() }
        $progress=$script:progressState
        $timerLabel.Text="Elapsed $(Format-FvDuration $seconds)  |  Overall progress: not available  |  Last activity: $(if ($progress.LastActivityUtc) { $progress.LastActivityUtc } else { '--' })"
        $stageLabel.Text="Stage: $($progress.Stage)  |  $($progress.Activity)  |  $(if ($null -ne $progress.Percent) { "$($progress.Percent)% $($progress.Unit)" } else { 'Stage percentage unavailable' })"
        if ($null -ne $progress.Percent) { $bar.Style='Continuous'; $bar.Value=$progress.Percent } else { $bar.Style='Marquee'; $bar.MarqueeAnimationSpeed=$(if ($state -eq 'running') { 25 } else { 0 }) }
        if ($state -in @('running','starting')) {
            $statusLabel.Text="$(if ($state -eq 'running') { 'Running' } else { 'Starting' }) | Output this run: $($script:session.output)"
            if (-not $eventsPath) {
                $stageLabel.Text=$(if (Test-Path -LiteralPath (Join-Path $meta.EngineRoot 'runtime/python.exe') -PathType Leaf) { 'Preparing package dependencies and checking the selected extraction mode...' } else { 'Preparing bundled Python for first use. This may take a minute or two.' })
            }
        } elseif ($state -eq 'finished') {
            $exit=[int]$script:session.exit_code; $receiptStatus=Get-FvProp $script:receipt 'status'
            $outcome=Get-FvOutcome $script:session $script:receipt
            if ($outcome -eq 'completed') { $statusLabel.Text='Completed | receipt validated for this run' }
            elseif ($outcome -eq 'experimental_export_verified') { $statusLabel.Text='Experimental RESCUE created | qualification pending' }
            elseif ($outcome -eq 'completed_with_gaps') { $statusLabel.Text='Completed with gaps | read report for missing or unsupported metadata' }
            elseif ($receiptStatus -eq 'unsupported_profile') { $statusLabel.Text='No RESCUE model created | project is outside the tested static-model profile; CRS-only mode is available' }
            elseif ($outcome -eq 'cancelled') { $statusLabel.Text='Backend cancelled (exit 130) | partial evidence may remain' }
            else { $statusLabel.Text="Backend failed or receipt missing/mismatched (exit $exit; receipt $receiptStatus). $(if (-not $script:receipt) { Get-FvReceiptProblem $script:session })" }
        } elseif ($state -eq 'launch_failed') { $statusLabel.Text="Launcher failed: $(Get-FvProp $script:session 'launch_error')" }
        else { $statusLabel.Text='Recorded process identity is stale or unverified; no restart or success assumed.' }
        if ($script:recordedStages.Count) {
            $recent=@($script:recordedStages | Select-Object -Last 5)
            $parts=@(); for ($i=0;$i -lt $recent.Count;$i++) {
                $label=$(if ($i -lt $recent.Count-1) { 'earlier' } elseif ($state -eq 'finished') { $(if ((Get-FvOutcome $script:session $script:receipt) -eq 'experimental_export_verified') { 'experimental' } elseif ((Get-FvOutcome $script:session $script:receipt) -eq 'completed_with_gaps') { 'gaps' } elseif ((Get-FvOutcome $script:session $script:receipt) -eq 'completed') { 'completed' } else { 'failed' }) } else { 'running' })
                $parts+= "Stage $($recent[$i]) $label"
            }
            $stageStrip.Text='Recorded stages: '+($parts -join '  |  ')
        }
        if ($state -in @('finished','launch_failed')) {
            $runButton.Enabled=$true; $projectBox.Enabled=$true; $outputBox.Enabled=$true; $projectBrowse.Enabled=$true; $outputBrowse.Enabled=$true; $modeBox.Enabled=$true
            if ($script:startedMonotonic) { $script:startedMonotonic.Stop() }
        } else { $runButton.Enabled=$false; $projectBox.Enabled=$false; $outputBox.Enabled=$false; $projectBrowse.Enabled=$false; $outputBrowse.Enabled=$false; $modeBox.Enabled=$false }
        if ($state -eq 'running' -and (-not $script:lastScan.ContainsKey('run') -or ([DateTime]::UtcNow-$script:lastScan['run']).TotalSeconds -ge 30)) { Start-FvScan 'run' }
        if ($state -eq 'finished' -and -not $script:terminalRefreshPending) { $script:terminalRefreshPending=$true; $script:lastScan.Remove('run'); $script:lastScan.Remove('selected') }
        if ($script:terminalRefreshPending) {
            if (-not $script:scanJobs.ContainsKey('run') -or $script:scanJobs['run'].HasExited) { Start-FvScan 'run' }
            if (-not $script:scanJobs.ContainsKey('selected') -or $script:scanJobs['selected'].HasExited) { Start-FvScan 'selected' }
            if ($script:lastScan.ContainsKey('run') -and ($modeBox.SelectedIndex -eq 3 -or $script:lastScan.ContainsKey('selected'))) { $script:terminalRefreshPending=$false }
        }
        if (-not $script:session -or $state -notin @('running','starting')) { Start-FvScan 'input'; Start-FvScan 'selected' }
        $sizeLabel.Text="Input exact .pet + matching .ptd: $(Format-FvScan 'input')`r`nOutput this run: $(Format-FvScan 'run')`r`nSelected output folder total (may include other runs): $(Format-FvScan 'selected')"
        $emailButton.Enabled=($state -ne 'starting' -and [bool]$eventsPath)
        if ($script:session.stdout -and (Test-Path -LiteralPath $script:session.stdout -PathType Leaf)) { $detail.Text="Run folder: $($script:session.output)`r`nExternal references: size unknown and excluded from the exact-project count.`r`nLauncher output tail (max 8 KiB):`r`n"+(Read-FvTail $script:session.stdout 8192) }
    } catch { $statusLabel.Text='Display error: '+$_.Exception.Message }
}
$timer=New-Object Windows.Forms.Timer; $timer.Interval=1000; $timer.Add_Tick({ Complete-FvUpdateCheck; Update-FvDashboard }); $timer.Start()
$form.Add_FormClosing({
    if ($script:session -and (Get-FvSessionState $script:session) -in @('starting','running')) {
        $answer=[Windows.Forms.MessageBox]::Show('Extraction continues independently. Minimize this window and keep watching? Choose No to close the window while extraction continues.','Extraction running',[Windows.Forms.MessageBoxButtons]::YesNoCancel)
        if ($answer -eq 'Yes') { $_.Cancel=$true; $form.WindowState='Minimized' }
        elseif ($answer -eq 'Cancel') { $_.Cancel=$true }
    }
})
try {
    if (-not $SmokeScreenshot) {
        try { $script:session=Get-FvLatestSession $PackageRoot }
        catch { $script:sessionLoadError=$_.Exception.Message; $runButton.Enabled=$false; $statusLabel.Text='Saved session could not be verified; new work is blocked.'; $detail.Text=$script:sessionLoadError }
        if ($script:session) {
            $projectBox.Text=$script:session.project; $outputBox.Text=$script:session.selected_output; $modeBox.SelectedIndex=$(if ((Get-FvProp $script:session 'export_mode') -eq 'crs') { 3 } elseif ((Get-FvProp $script:session 'export_mode') -eq 'rescue') { 2 } elseif ($script:session.report_only) { 1 } else { 0 })
            Start-FvScan 'input'; Start-FvScan 'selected'; Update-FvDashboard
        }
        [void]$form.ShowDialog()
    } else {
        if ($SmokeSessionFile) {
            $script:session=Read-FvJson $SmokeSessionFile 65536
            if (-not $script:session -or $script:session.state -ne 'finished') { throw 'Smoke session must be a saved completed backend run.' }
            $projectBox.Text=$script:session.project; $outputBox.Text=$script:session.selected_output
            $modeBox.SelectedIndex=$(if ((Get-FvProp $script:session 'export_mode') -eq 'crs') { 3 } elseif ((Get-FvProp $script:session 'export_mode') -eq 'rescue') { 2 } elseif ($script:session.report_only) { 1 } else { 0 })
            Update-FvDashboard
        }
        $form.Add_Shown({ $bitmap=New-Object Drawing.Bitmap($form.Width,$form.Height); $form.DrawToBitmap($bitmap,(New-Object Drawing.Rectangle(0,0,$form.Width,$form.Height))); $bitmap.Save($SmokeScreenshot,[Drawing.Imaging.ImageFormat]::Png); $bitmap.Dispose(); Write-FvJson ($SmokeScreenshot+'.json') @{title=$form.Text;release=$meta.ReleaseVersion;preparation=$meta.PreparationRevision;runtime_prepared=(Test-Path -LiteralPath (Join-Path $meta.EngineRoot 'runtime/python.exe') -PathType Leaf);status_text=$statusLabel.Text;timer_text=$timerLabel.Text;run_enabled=$runButton.Enabled;receipt_admitted=($null -ne $script:receipt);reports=@($reportChoice.Items);mode=[string]$modeBox.SelectedItem;modes=@($modeBox.Items);help_items=@($helpMenu.Items | ForEach-Object { $_.Text });detail_text=$detail.Text}; $form.Close() })
        [void]$form.ShowDialog()
    }
} finally { $timer.Stop(); if ($script:updateJob) { $script:updateJob.Worker.Stop(); $script:updateJob.Worker.Dispose(); $script:updateJob=$null }; $form.Dispose(); if ($created) { $mutex.ReleaseMutex() }; $mutex.Dispose(); if (-not $SmokeScreenshot -and (Test-Path -LiteralPath $ownerPath)) { try { $owner=Read-FvJson $ownerPath 8192; if ($owner.pid -eq $PID) { Remove-Item -LiteralPath $ownerPath -Force } } catch {} } }
