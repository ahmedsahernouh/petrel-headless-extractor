# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Exercise the actual view and link refresh without an extractor or user session.
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
function Assert([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
$root=Split-Path -Parent $PSScriptRoot
$source=[IO.File]::ReadAllText((Join-Path $root 'desktop/FieldViewer.Desktop.ps1'))
$script:form=New-Object Windows.Forms.Form
$form=$script:form; $meta=[pscustomobject]@{Version='layout test'}
$SmokeMinimum=$false; $SmokeScreenshot='layout-only'; $stateHome=$env:TEMP
$start=$source.IndexOf('$form.MinimumSize=')
$end=$source.IndexOf('function Start-FvRun')
. ([scriptblock]::Create($source.Substring($start,$end-$start)))
function Get-FvLinks { return $script:fixtureLinks }
$script:session=[pscustomobject]@{id='layout-only'}; $script:receipt=$null
try {
    $form.Show(); [Windows.Forms.Application]::DoEvents()
    foreach ($width in @(860,1040,1400)) {
        $form.Size=New-Object Drawing.Size($width,780)
        $form.PerformLayout(); [Windows.Forms.Application]::DoEvents()
        foreach ($control in @($projectBox,$outputBox,$projectBrowse,$outputBrowse,$reportChoice,$folderChoice,$modeBox,$runButton,$help)) {
            Assert ($control.Left -ge 0 -and $control.Right -le $control.Parent.ClientSize.Width) "Control clipped at width $width : $($control.AccessibleName) $($control.Text)"
        }
        Assert ($projectBox.Left -eq $outputBox.Left -and $projectBox.Width -eq $outputBox.Width) 'Path fields do not align'
        Assert ($projectBrowse.Left -eq $outputBrowse.Left) 'Browse controls do not align'
    }
    $script:fixtureLinks=@{reports=@{};folders=@{}}
    Update-FvLinks
    Assert (-not $reportOpen.Enabled -and -not $folderOpen.Enabled) 'Unavailable targets became actionable'
    $script:fixtureLinks=@{reports=@{'QC'='qc.json';'Main report'='report.html';'Detailed assessment'='assessment.html'};folders=@{'All exports'='exports';'Seismic'='seismic';'Custom category'='custom'}}
    Update-FvLinks
    Assert ($reportChoice.SelectedItem -eq 'Main report' -and $folderChoice.SelectedItem -eq 'All exports') 'Preferred targets not selected'
    Assert ($reportChoice.Items.Count -eq 3 -and $folderChoice.Items.Count -eq 3) 'Dynamic targets lost from dropdowns'
    $reportChoice.SelectedItem='Detailed assessment'; Update-FvLinks
    Assert ($reportChoice.SelectedItem -eq 'Detailed assessment') 'Refresh reset user selection'
    $script:fixtureLinks.reports.Remove('Detailed assessment'); Update-FvLinks
    Assert ($reportChoice.SelectedItem -eq 'Main report') 'Removed target did not fall back safely'
    Assert ($modeBox.Items.Count -eq 2 -and $modeBox.SelectedIndex -eq 0) 'Extraction mode default changed'
    Assert ($helpMenu.Items.Count -eq 2 -and -not $emailButton.Enabled) 'Help menu lost About or unavailable diagnostic action'
    'Desktop layout: PASS (three widths, alignment, availability, dynamic targets, selection preservation, modes and Help)'
} finally { $helpMenu.Dispose(); $form.Dispose() }
