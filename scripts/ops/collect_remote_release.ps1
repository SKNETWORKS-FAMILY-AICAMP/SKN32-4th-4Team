param(
    # Format: A=user@host or A=user@host|C:\repo\path;B=user@host|C:\repo\path
    [Parameter(Mandatory = $true)]
    [string]$Node,
    [string]$RepoPath = "",
    [string]$KeyFile = "",
    [int]$Port = 22,
    [ValidateSet("powershell", "bash")]
    [string]$RemoteShell = "powershell",
    [string]$PythonExecutable = "python",
    [switch]$DryRun,
    [switch]$Strict,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

function Parse-Node([string]$Value) {
    $separator = $Value.IndexOf("=")
    if ($separator -lt 1) {
        throw "Invalid node '$Value'. Use NAME=user@host or NAME=user@host|repo-path."
    }
    $name = $Value.Substring(0, $separator)
    $spec = $Value.Substring($separator + 1)
    $pathSeparator = $spec.IndexOf("|")
    if ($pathSeparator -ge 0) {
        return [pscustomobject]@{
            Name = $name
            Target = $spec.Substring(0, $pathSeparator)
            Path = $spec.Substring($pathSeparator + 1)
        }
    }
    return [pscustomobject]@{ Name = $name; Target = $spec; Path = $RepoPath }
}

function New-RemoteCommand([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "A repository path is required globally or per node."
    }
    $escaped = $Path.Replace("'", "''")
    $strictArg = if ($Strict) { " --strict" } else { "" }
    if ($RemoteShell -eq "bash") {
        return "cd '$escaped' && $PythonExecutable -m scripts.ops.verify_release$strictArg --json"
    }
    return "Set-Location -LiteralPath '$escaped'; $PythonExecutable -m scripts.ops.verify_release$strictArg --json"
}

$results = @()
$nodeValues = @($Node -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
foreach ($rawNode in $nodeValues) {
    $nodeInfo = Parse-Node $rawNode
    $remoteCommand = New-RemoteCommand $nodeInfo.Path
    if ($RemoteShell -eq "bash") {
        $sshCommand = "bash -lc `"$remoteCommand`""
        $displayCommand = "ssh $($nodeInfo.Target) $sshCommand"
        $sshArgs = @("-p", $Port)
        if (-not [string]::IsNullOrWhiteSpace($KeyFile)) { $sshArgs += @("-i", $KeyFile) }
        $sshArgs += @($nodeInfo.Target, "bash", "-lc", $remoteCommand)
    } else {
        $sshCommand = "powershell -NoProfile -Command `"$remoteCommand`""
        $displayCommand = "ssh $($nodeInfo.Target) $sshCommand"
        $sshArgs = @("-p", $Port)
        if (-not [string]::IsNullOrWhiteSpace($KeyFile)) { $sshArgs += @("-i", $KeyFile) }
        $sshArgs += @($nodeInfo.Target, "powershell", "-NoProfile", "-Command", $remoteCommand)
    }

    if ($DryRun) {
        $results += [pscustomobject]@{
            node = $nodeInfo.Name
            target = $nodeInfo.Target
            command = $displayCommand
            checked = $false
            ok = $null
        }
        continue
    }

    try {
        $rawOutput = (& ssh @sshArgs 2>&1 | Out-String).Trim()
        $exitCode = $LASTEXITCODE
        $remoteReport = $null
        if ($rawOutput) {
            try { $remoteReport = $rawOutput | ConvertFrom-Json } catch { }
        }
        $results += [pscustomobject]@{
            node = $nodeInfo.Name
            target = $nodeInfo.Target
            checked = ($exitCode -eq 0 -and $null -ne $remoteReport)
            ok = ($exitCode -eq 0 -and $null -ne $remoteReport -and $remoteReport.ok -eq $true)
            exit_code = $exitCode
            report = $remoteReport
            raw_output = if ($null -eq $remoteReport) { $rawOutput } else { $null }
        }
    } catch {
        $results += [pscustomobject]@{
            node = $nodeInfo.Name
            target = $nodeInfo.Target
            checked = $false
            ok = $false
            reason = $_.Exception.Message
        }
    }
}

$overallOk = ($results.Count -gt 0 -and (@($results | Where-Object { $_.ok -ne $true }).Count -eq 0))
if ($Json) {
    [pscustomobject]@{
        read_only = $true
        checked_at = [DateTime]::UtcNow.ToString("o")
        overall_ok = ($overallOk -or $DryRun)
        nodes = $results
    } | ConvertTo-Json -Depth 12
} else {
    $results | Format-Table -AutoSize
}

if ($DryRun -or $overallOk) { exit 0 }
exit 1
