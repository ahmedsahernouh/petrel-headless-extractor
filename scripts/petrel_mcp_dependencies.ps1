# Petrel Headless Extractor - Ahmed Saher Nouh / SaherLabs
# Website: https://saherlabs.dev/
# GitHub: https://github.com/ahmedsahernouh
# Repository: https://github.com/ahmedsahernouh/petrel-headless-extractor

Set-StrictMode -Version Latest

function Get-PetrelMcpLastExitCode {
    $variable = Get-Variable -Name LASTEXITCODE -ErrorAction SilentlyContinue
    if ($null -eq $variable -or $null -eq $variable.Value) {
        return 0
    }
    return [int]$variable.Value
}

function Test-PetrelMcpExecutable {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string[]]$VersionArguments = @("--version")
    )

    try {
        $null = & $Path @VersionArguments 2>&1
        return ((Get-PetrelMcpLastExitCode) -eq 0)
    } catch {
        return $false
    }
}

function Resolve-PetrelMcpExecutable {
    param(
        [string]$ExplicitPath = "",
        [string[]]$EnvironmentVariableNames = @(),
        [string[]]$CandidatePaths = @(),
        [string[]]$CommandNames = @(),
        [Parameter(Mandatory = $true)][string]$Label,
        [string[]]$VersionArguments = @("--version")
    )

    $candidates = New-Object System.Collections.Generic.List[string]
    $hasExplicitPath = -not [string]::IsNullOrWhiteSpace($ExplicitPath)
    if ($hasExplicitPath) {
        $candidates.Add($ExplicitPath.Trim().Trim('"')) | Out-Null
    } else {
        foreach ($name in $EnvironmentVariableNames) {
            $value = [Environment]::GetEnvironmentVariable($name)
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                $candidates.Add($value.Trim().Trim('"')) | Out-Null
            }
        }
        foreach ($path in $CandidatePaths) {
            if (-not [string]::IsNullOrWhiteSpace($path)) {
                $candidates.Add($path.Trim().Trim('"')) | Out-Null
            }
        }
        foreach ($commandName in $CommandNames) {
            $command = Get-Command $commandName -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
                $candidates.Add($command.Source) | Out-Null
            }
        }
    }

    $checked = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        $resolved = $candidate
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $resolved = (Resolve-Path -LiteralPath $candidate).ProviderPath
        } elseif ($candidate -match '[\\/]') {
            $checked.Add($candidate) | Out-Null
            continue
        }

        if ($checked -contains $resolved) {
            continue
        }
        $checked.Add($resolved) | Out-Null
        if (Test-PetrelMcpExecutable -Path $resolved -VersionArguments $VersionArguments) {
            return $resolved
        }
    }

    $checkedText = if ($checked.Count -gt 0) { $checked -join "; " } else { "(none)" }
    throw "$Label was not found or could not be executed. Checked: $checkedText."
}

function Resolve-PetrelMcpPython {
    param(
        [string]$ExplicitPath = "",
        [string]$ProjectRoot = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($ProjectRoot) -and (Test-Path -LiteralPath (Join-Path $ProjectRoot "STANDALONE.txt") -PathType Leaf)) {
        $bundledPython = Join-Path $ProjectRoot "runtime\python.exe"
        if (-not (Test-Path -LiteralPath $bundledPython -PathType Leaf)) {
            throw "Standalone runtime missing; extract the complete ZIP. No system-Python fallback is allowed."
        }
        return Resolve-PetrelMcpExecutable -ExplicitPath $bundledPython -Label "Bundled standalone Python" -VersionArguments @("--version")
    }

    $candidatePaths = @()
    if (-not [string]::IsNullOrWhiteSpace($ProjectRoot)) {
        $candidatePaths += (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
    }

    return Resolve-PetrelMcpExecutable `
        -ExplicitPath $ExplicitPath `
        -EnvironmentVariableNames @("PETREL_MCP_PYTHON", "PYTHON") `
        -CandidatePaths $candidatePaths `
        -CommandNames @("python.exe", "python", "py.exe", "py") `
        -Label "Python for Petrel MCP" `
        -VersionArguments @("--version")
}

function Resolve-PetrelMcpTesseract {
    param([string]$ExplicitPath = "")

    return Resolve-PetrelMcpExecutable `
        -ExplicitPath $ExplicitPath `
        -EnvironmentVariableNames @("PETREL_TESSERACT_PATH", "TESSERACT_PATH") `
        -CandidatePaths @(
            "C:\Program Files\Tesseract-OCR\tesseract.exe",
            "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
        ) `
        -CommandNames @("tesseract.exe", "tesseract") `
        -Label "Tesseract OCR for Petrel MCP" `
        -VersionArguments @("--version")
}
