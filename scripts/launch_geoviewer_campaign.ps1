# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Website: https://saherlabs.dev/
# Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
param(
    [Parameter(Position=0)][string]$List = '',
    [string]$ExtractorDirectory = '',
    [string]$Config = '',
    [ValidateSet('all','scan','run','collect')][string]$Mode = 'scan',
    [string]$Resume = '',
    [switch]$RetryFailed,
    [int]$MaxProjects = 0
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    if (-not $Config) { $Config = Join-Path $PSScriptRoot 'campaign_config.json' }
    if ($List -and $Resume) { throw 'Specify a saved list or -Resume, not both.' }
    if ($List -and $Mode -notin @('run','collect')) { throw 'A saved list is accepted only by run/collect mode.' }
    if ($Mode -eq 'scan' -and $Resume) { throw 'The crawler creates a new list. Use run mode to read a saved list.' }
    if ($Mode -in @('run','collect')) {
        if ($List) { $Resume = $List }
        if (-not $Resume) {
            $pointer = Join-Path (Split-Path -Parent ([System.IO.Path]::GetFullPath($Config))) 'LATEST_CRAWL.json'
            if (-not (Test-Path -LiteralPath $pointer -PathType Leaf)) {
                throw 'No completed crawler list is selected. Run GeoViewer_crawl_projects.bat first, or pass -List "D:\path\CAMPAIGN.json".'
            }
            $Resume = (Get-Content -LiteralPath $pointer -Raw | ConvertFrom-Json).campaign
        }
        $Resume = (Resolve-Path -LiteralPath $Resume).ProviderPath
        if (Test-Path -LiteralPath $Resume -PathType Leaf) {
            if ((Split-Path -Leaf $Resume) -notin @('CAMPAIGN.json','PROJECTS.csv')) {
                throw 'Select CAMPAIGN.json, PROJECTS.csv or their campaign directory.'
            }
            $Resume = Split-Path -Parent $Resume
        }
        $saved = Get-Content -LiteralPath (Join-Path $Resume 'CAMPAIGN.json') -Raw | ConvertFrom-Json
        if ($saved.schema -ne 'geoviewer.campaign/1' -or (-not $saved.discovery_complete -and $Mode -ne 'collect')) {
            throw 'This list has no completed discovery. An incomplete crawl cannot start extraction.'
        }
        Write-Output ('Saved project list: ' + (Join-Path $Resume 'CAMPAIGN.json'))
        Write-Output 'The runner reads this list only; it does not search connected drives.'
        if (-not $ExtractorDirectory) { $ExtractorDirectory = $saved.config.extractor_directory }
    }
    $settings = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
    if (-not $ExtractorDirectory -and $settings.PSObject.Properties.Name -contains 'extractor_directory') {
        $ExtractorDirectory = $settings.extractor_directory
    }
    if (-not $ExtractorDirectory) { $ExtractorDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) 'GeoViewer' }
    $ExtractorDirectory = (Resolve-Path -LiteralPath $ExtractorDirectory).ProviderPath
    if (-not (Test-Path -LiteralPath (Join-Path $ExtractorDirectory 'STANDALONE.txt'))) {
        throw 'Select the GeoViewer folder of a complete standalone extractor package using -ExtractorDirectory.'
    }
    Write-Output 'Website: https://saherlabs.dev/'
    Write-Output 'Project: https://github.com/ahmedsahernouh/petrel-headless-extractor'
    Write-Output ('GeoViewer batch trials - mode: ' + $Mode)
    Write-Output 'Sources remain read-only. No logs are uploaded.'
    if ($Mode -eq 'scan') { Write-Output 'CRAWLER ONLY: saves the findings and duplicate decisions, then stops. No extraction.' }
    elseif ($Mode -eq 'run') { Write-Output 'RUNNER ONLY: extracts eligible saved-list projects one at a time.' }
    # Invoke the same checked bootstrap as the normal BAT in a child host. Its
    # exit statement must not terminate this outer campaign launcher.
    $launcher = Join-Path $ExtractorDirectory 'scripts\launch_standalone_petrel.ps1'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $launcher -Check -NoPause
    if ($LASTEXITCODE -ne 0) { throw 'Extractor bootstrap/preflight failed; campaign was not started.' }
    $python = Join-Path $ExtractorDirectory 'runtime\python.exe'
    $arguments = @('-B', (Join-Path $PSScriptRoot 'geoviewer_campaign.py'), '--config', $Config,
        '--extractor-directory', $ExtractorDirectory, '--mode', $Mode.ToLowerInvariant())
    if ($Resume) { $arguments += @('--resume', $Resume) }
    if ($RetryFailed) { $arguments += '--retry-failed' }
    if ($MaxProjects -gt 0) { $arguments += @('--max-projects', [string]$MaxProjects) }
    & $python @arguments
    exit $LASTEXITCODE
} catch {
    Write-Output ('ERROR: ' + $_.Exception.Message)
    exit 1
}
