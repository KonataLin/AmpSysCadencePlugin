param(
    [string]$PluginRoot = "",
    [string]$EngineRoot = "",
    [string]$PyCmd = "py -3",
    [string]$Cdsinit = ""
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($PluginRoot)) {
    $PluginRoot = $scriptRoot
}
if ([string]::IsNullOrWhiteSpace($EngineRoot)) {
    $EngineRoot = $PluginRoot
}

$plugin = (Resolve-Path -LiteralPath $PluginRoot).Path
$engine = (Resolve-Path -LiteralPath $EngineRoot).Path

function Set-UserEnvNoBroadcast {
    param(
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)][string]$Value
    )
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment")
    try {
        $key.SetValue($Name, $Value, [Microsoft.Win32.RegistryValueKind]::String)
    } finally {
        $key.Close()
    }
    Set-Item -LiteralPath "Env:$Name" -Value $Value
}

Set-UserEnvNoBroadcast -Name "AMPSYS_PLUGIN_ROOT" -Value $plugin
Set-UserEnvNoBroadcast -Name "AMPSYS_ENGINE_ROOT" -Value $engine
Set-UserEnvNoBroadcast -Name "AMPSYS_PYCMD" -Value $PyCmd

Write-Host "[AmpSys] User environment variables set:"
Write-Host "  AMPSYS_PLUGIN_ROOT=$plugin"
Write-Host "  AMPSYS_ENGINE_ROOT=$engine"
Write-Host "  AMPSYS_PYCMD=$PyCmd"

if ($Cdsinit -ne "") {
    $snippet = @"

; --- AmpSys Cadence Plugin ---
load(strcat(getShellEnvVar("AMPSYS_PLUGIN_ROOT") "/skill/ampsys_init.il"))
"@
    if (Test-Path -LiteralPath $Cdsinit) {
        $text = Get-Content -LiteralPath $Cdsinit -Raw
        if ($text -notmatch "ampsys_init\.il") {
            Add-Content -LiteralPath $Cdsinit -Value $snippet
            Write-Host "[AmpSys] Added .cdsinit snippet to $Cdsinit"
        } else {
            Write-Host "[AmpSys] .cdsinit already contains AmpSys loader."
        }
    } else {
        New-Item -ItemType File -Path $Cdsinit -Force | Out-Null
        Add-Content -LiteralPath $Cdsinit -Value $snippet
        Write-Host "[AmpSys] Created $Cdsinit with AmpSys loader."
    }
}

Write-Host ""
Write-Host "Environment check:"
Write-Host "  py -3 `"$plugin\tools\check_environment.py`""
Write-Host ""
Write-Host "Manual GUI launch:"
Write-Host "  py -3 `"$plugin\cli\ampsys_gui.py`""
Write-Host ""
Write-Host "Cadence .cdsinit line:"
Write-Host "  load(strcat(getShellEnvVar(`"AMPSYS_PLUGIN_ROOT`") `"/skill/ampsys_init.il`"))"
