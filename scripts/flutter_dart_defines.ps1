# Shared --dart-define flags from gitignored .env (never echo values).
function Get-FlutterDartDefines {
    param([string]$RepoRoot)
    $envFile = Join-Path $RepoRoot ".env"
    $defines = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path $envFile)) { return @() }
    foreach ($raw in Get-Content $envFile) {
        $line = $raw.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith("#")) { continue }
        $eq = $line.IndexOf("=")
        if ($eq -le 0) { continue }
        $key = $line.Substring(0, $eq).Trim()
        if ($key -notin @("SERPER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY")) {
            continue
        }
        $value = $line.Substring($eq + 1).Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ([string]::IsNullOrWhiteSpace($value)) { continue }
        $defines.Add("--dart-define=$key=$value")
    }
    return $defines.ToArray()
}
