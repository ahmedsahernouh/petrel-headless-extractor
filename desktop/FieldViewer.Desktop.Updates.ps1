# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
# Dot-source this file in a worker runspace. This module performs read-only public checks.

function ConvertTo-FvSemVer([string]$Version) {
    if ([string]::IsNullOrWhiteSpace($Version) -or $Version.Length -gt 128) { return $null }
    $match = [regex]::Match($Version, '^[vV]?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$')
    if (-not $match.Success) { return $null }
    $pre = [string]$match.Groups[4].Value
    foreach ($part in @($pre.Split('.'))) {
        if ($part -match '^[0-9]+$' -and $part.Length -gt 1 -and $part[0] -eq '0') { return $null }
    }
    $preParts = [string[]]@()
    if ($pre) { $preParts = [string[]]$pre.Split('.') }
    return [pscustomobject]@{
        Text = $Version
        Core = @($match.Groups[1].Value, $match.Groups[2].Value, $match.Groups[3].Value)
        Pre = $preParts
        IsPrerelease = [bool]$pre
    }
}

function Compare-FvDecimal([string]$Left, [string]$Right) {
    if ($Left.Length -lt $Right.Length) { return -1 }
    if ($Left.Length -gt $Right.Length) { return 1 }
    return [Math]::Sign([string]::CompareOrdinal($Left, $Right))
}

function Compare-FvSemVer([string]$Left, [string]$Right) {
    $a = ConvertTo-FvSemVer $Left; $b = ConvertTo-FvSemVer $Right
    if ($null -eq $a -or $null -eq $b) { throw 'Invalid semantic version.' }
    for ($i=0; $i -lt 3; $i++) {
        $comparison = Compare-FvDecimal $a.Core[$i] $b.Core[$i]
        if ($comparison) { return $comparison }
    }
    if (-not $a.IsPrerelease -and -not $b.IsPrerelease) { return 0 }
    if (-not $a.IsPrerelease) { return 1 }
    if (-not $b.IsPrerelease) { return -1 }
    for ($i=0; $i -lt [Math]::Min($a.Pre.Count, $b.Pre.Count); $i++) {
        $x = [string]$a.Pre[$i]; $y = [string]$b.Pre[$i]
        $xNumeric = $x -match '^[0-9]+$'; $yNumeric = $y -match '^[0-9]+$'
        if ($xNumeric -and $yNumeric) { $comparison = Compare-FvDecimal $x $y }
        elseif ($xNumeric) { $comparison = -1 }
        elseif ($yNumeric) { $comparison = 1 }
        else { $comparison = [Math]::Sign([string]::CompareOrdinal($x, $y)) }
        if ($comparison) { return $comparison }
    }
    return [Math]::Sign($a.Pre.Count - $b.Pre.Count)
}

function New-FvUpdateResult([string]$Status,[string]$CurrentVersion,[string]$LatestVersion,[string]$ReleaseUrl,[string]$Message) {
    return [pscustomobject]@{ Status=$Status; CurrentVersion=$CurrentVersion; LatestVersion=$LatestVersion; ReleaseUrl=$ReleaseUrl; Message=$Message }
}

function ConvertFrom-FvReleaseResponse([string]$Json) {
    if ([string]::IsNullOrWhiteSpace($Json) -or $Json.TrimStart() -notmatch '^\[') { throw 'Unexpected GitHub release response.' }
    $items = ConvertFrom-Json -InputObject $Json -ErrorAction Stop
    if ($items -isnot [array]) { throw 'Unexpected GitHub release response.' }
    return ,$items
}

function Select-FvUpdateRelease([string]$CurrentVersion, $Releases) {
    $current = ConvertTo-FvSemVer $CurrentVersion
    if ($null -eq $current) {
        return New-FvUpdateResult 'unavailable' $CurrentVersion $null $null 'The installed version could not be compared with GitHub releases.'
    }
    if ($null -eq $Releases -or $Releases -isnot [array]) {
        return New-FvUpdateResult 'unavailable' $CurrentVersion $null $null 'GitHub returned an unexpected release list.'
    }
    $best = $null
    foreach ($release in $Releases) {
        if ($null -eq $release -or $release -isnot [pscustomobject]) { continue }
        $draftProperty = $release.PSObject.Properties['draft']
        $preProperty = $release.PSObject.Properties['prerelease']
        $publishedProperty = $release.PSObject.Properties['published_at']
        $tagProperty = $release.PSObject.Properties['tag_name']
        if ($null -eq $draftProperty -or $draftProperty.Value -isnot [bool] -or $draftProperty.Value) { continue }
        if ($null -eq $preProperty -or $preProperty.Value -isnot [bool]) { continue }
        if ($null -eq $publishedProperty -or $publishedProperty.Value -isnot [string] -or [string]::IsNullOrWhiteSpace($publishedProperty.Value)) { continue }
        if ($null -eq $tagProperty -or $tagProperty.Value -isnot [string]) { continue }
        $published = [datetimeoffset]::MinValue
        if (-not [datetimeoffset]::TryParse($publishedProperty.Value, [ref]$published)) { continue }
        $tag = $tagProperty.Value
        $parsed = ConvertTo-FvSemVer $tag
        if ($null -eq $parsed) { continue }
        if (-not $current.IsPrerelease -and ($preProperty.Value -or $parsed.IsPrerelease)) { continue }
        # Do not promote a release whose GitHub flag and version disagree.
        if ($preProperty.Value -ne $parsed.IsPrerelease) { continue }
        if ($null -eq $best -or (Compare-FvSemVer $tag $best.Tag) -gt 0) {
            $best = [pscustomobject]@{ Tag=$tag; Version=$parsed }
        }
    }
    if ($null -eq $best) {
        return New-FvUpdateResult 'no_releases' $CurrentVersion $null $null 'No eligible published GitHub release was found for this version.'
    }
    $url = 'https://github.com/ahmedsahernouh/petrel-headless-extractor/releases/tag/' + [uri]::EscapeDataString($best.Tag)
    $comparison = Compare-FvSemVer $CurrentVersion $best.Tag
    if ($comparison -lt 0) {
        return New-FvUpdateResult 'update_available' $CurrentVersion $best.Tag $url "A newer version ($($best.Tag)) is available. You have $CurrentVersion."
    }
    if ($comparison -gt 0) {
        return New-FvUpdateResult 'local_ahead' $CurrentVersion $best.Tag $url "Your installed version ($CurrentVersion) is newer than the latest eligible public release ($($best.Tag))."
    }
    return New-FvUpdateResult 'up_to_date' $CurrentVersion $best.Tag $url "You have the latest eligible public release ($CurrentVersion)."
}

function Get-FvUpdateCheck([string]$CurrentVersion) {
    if ($null -eq (ConvertTo-FvSemVer $CurrentVersion)) {
        return New-FvUpdateResult 'unavailable' $CurrentVersion $null $null 'The installed version could not be compared with GitHub releases.'
    }
    $client = $null; $handler = $null; $cancel = $null
    try {
        Add-Type -AssemblyName System.Net.Http
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $handler = New-Object Net.Http.HttpClientHandler
        $handler.AllowAutoRedirect = $false
        $handler.UseCookies = $false
        $handler.UseDefaultCredentials = $false
        $client = New-Object Net.Http.HttpClient($handler)
        $client.DefaultRequestHeaders.UserAgent.ParseAdd('FieldViewer-Desktop-UpdateCheck/1.0')
        $client.DefaultRequestHeaders.Accept.ParseAdd('application/vnd.github+json')
        $cancel = New-Object Threading.CancellationTokenSource
        $cancel.CancelAfter(12000)
        $all = New-Object 'System.Collections.Generic.List[object]'
        $totalBytes = 0
        for ($page=1; $page -le 3; $page++) {
            $uri = "https://api.github.com/repos/ahmedsahernouh/petrel-headless-extractor/releases?per_page=100&page=$page"
            $response = $client.GetAsync($uri, [Net.Http.HttpCompletionOption]::ResponseHeadersRead, $cancel.Token).GetAwaiter().GetResult()
            try {
                if (-not $response.IsSuccessStatusCode) { throw "GitHub HTTP $([int]$response.StatusCode)" }
                if ($response.Content.Headers.ContentLength -gt 2097152) { throw 'Release response too large.' }
                $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
                try {
                    $buffer = New-Object byte[] 32768
                    $memory = New-Object IO.MemoryStream
                    try {
                        while ($true) {
                            $n = $stream.ReadAsync($buffer,0,$buffer.Length,$cancel.Token).GetAwaiter().GetResult()
                            if ($n -le 0) { break }
                            if ($totalBytes + $n -gt 2097152) { throw 'Release response too large.' }
                            $memory.Write($buffer,0,$n)
                            $totalBytes += $n
                        }
                        $json = [Text.Encoding]::UTF8.GetString($memory.ToArray())
                    } finally { $memory.Dispose() }
                } finally { $stream.Dispose() }
                $items = ConvertFrom-FvReleaseResponse $json
                foreach ($item in $items) { $all.Add($item) }
                if ($items.Count -lt 100) { break }
                if ($page -eq 3) { throw 'Release listing exceeded the page limit.' }
            } finally { $response.Dispose() }
        }
        return Select-FvUpdateRelease $CurrentVersion $all.ToArray()
    } catch {
        return New-FvUpdateResult 'unavailable' $CurrentVersion $null $null 'Unable to check GitHub releases right now. Check your connection and try again.'
    } finally {
        if ($cancel) { $cancel.Dispose() }
        if ($client) { $client.Dispose() }
        if ($handler) { $handler.Dispose() }
    }
}
