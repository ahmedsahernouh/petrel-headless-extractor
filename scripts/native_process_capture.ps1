function Invoke-FVNativeCapture {
 param([Parameter(Mandatory=$true)][string]$Bat,[string[]]$Arguments,[Parameter(Mandatory=$true)][string]$Stdout,[Parameter(Mandatory=$true)][string]$Stderr)
 if(Test-Path -LiteralPath $Stdout){throw 'Preserve prior stdout log; choose a new attempt'}
 if(Test-Path -LiteralPath $Stderr){throw 'Preserve prior stderr log; choose a new attempt'}
 $parts=@($Bat)+@($Arguments)
 foreach($part in $parts){if($part -match '["\r\n%]'){throw 'Unsupported shell argument character in bounded VM procedure'}}
 $quoted=@($parts|ForEach-Object{'"'+$_+'"'})
 $command='/d /s /c "'+($quoted -join ' ')+'"'
 $started=[DateTimeOffset]::UtcNow.ToString('o')
 $process=Start-Process -FilePath $env:ComSpec -ArgumentList $command -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr
 if($null -eq $process.ExitCode){throw 'Native process exit code unavailable; do not infer zero'}
 [ordered]@{bat=$Bat;arguments=$Arguments;pid=$process.Id;started_utc=$started;finished_utc=[DateTimeOffset]::UtcNow.ToString('o');exit_code=$process.ExitCode;stdout=$Stdout;stderr=$Stderr}|ConvertTo-Json -Depth 5|Set-Content -LiteralPath ($Stdout+'.process.json') -Encoding utf8
 return [int]$process.ExitCode
}
