# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Website: https://saherlabs.dev/
# Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
param(
    [ValidateSet('prepare','profile','extract','collect')][string]$Mode='prepare',
    [Parameter(Position=0)][string]$List='',
    [string]$Plan='',
    [string]$ExtractorDirectory='',
    [string]$Config='',
    [int]$MaxProjects=0,
    [switch]$RetryFailed,
    [switch]$RunToCompletion
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
try {
    if ($Mode -ne 'prepare') {
        if ($List -and -not $Plan) { $Plan=$List }
        if (-not $Plan) {
            $Plan=(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'LATEST_TEST_PLAN.json') -Raw | ConvertFrom-Json).plan
        }
        if (Test-Path -LiteralPath $Plan -PathType Leaf) { $Plan=Split-Path -Parent $Plan }
        $saved=Get-Content -LiteralPath (Join-Path $Plan 'CAMPAIGN.json') -Raw | ConvertFrom-Json
        if (-not $ExtractorDirectory) { $ExtractorDirectory=$saved.config.extractor_directory }
    } else {
        if (-not $List) { throw 'Pass the saved crawler CAMPAIGN.json or drag it onto GeoViewer_prepare_tests.bat.' }
        $catalogue=if(Test-Path -LiteralPath $List -PathType Leaf){Split-Path -Parent $List}else{$List}
        $saved=Get-Content -LiteralPath (Join-Path $catalogue 'CAMPAIGN.json') -Raw | ConvertFrom-Json
        if (-not $ExtractorDirectory) { $ExtractorDirectory=$saved.config.extractor_directory }
    }
    $python=Join-Path $ExtractorDirectory 'runtime\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'The existing bundled runtime is missing. Run FieldViewer_data_extractor.bat --check -NoPause once to repair it.'
    }
    Write-Output 'Website: https://saherlabs.dev/'
    Write-Output 'Project: https://github.com/ahmedsahernouh/petrel-headless-extractor'
    Write-Output ('Field Viewer 1.0.0 dedicated testing runner - ' + $Mode)
    $argsList=@('-B',(Join-Path $PSScriptRoot 'geoviewer_test_lab.py'),$Mode)
    if ($Mode -eq 'prepare') { $argsList+=@('--list',$List,'--toolkit',$ExtractorDirectory) }
    if ($Plan) { $argsList+=@('--plan',$Plan) }
    if ($Config) { $argsList+=@('--config',$Config) }
    if ($MaxProjects -gt 0) { $argsList+=@('--max-projects',[string]$MaxProjects) }
    if ($RetryFailed) { $argsList+='--retry-failed' }
    if ($RunToCompletion) { $argsList+='--run-to-completion' }
    & $python @argsList
    exit $LASTEXITCODE
} catch {
    Write-Output ('TEST RUNNER ERROR: ' + $_.Exception.Message)
    exit 1
}
