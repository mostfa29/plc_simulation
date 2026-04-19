# Windows hardening for 24/7 industrial operation — MASTER_CONTEXT §8.2
# Run as Administrator on the rig PC before bringing the service online.

# 1. High Performance power plan — disable sleep / hibernate / monitor blanking
$powerPlan = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"  # High Performance GUID
powercfg /setactive $powerPlan
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
powercfg /change monitor-timeout-ac 0

# 2. Windows Defender exclusions (reduce scan-induced jitter)
Add-MpPreference -ExclusionPath "C:\hxi_optimizer"
Add-MpPreference -ExclusionPath "C:\hxi_optimizer\hxi_optimizer\logs"
Add-MpPreference -ExclusionProcess "python.exe"

# 3. Defer Windows Update auto-restart (Pro/Enterprise)
$wuKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
if (-not (Test-Path $wuKey)) { New-Item -Path $wuKey -Force | Out-Null }
Set-ItemProperty -Path $wuKey -Name "NoAutoUpdate" -Value 1 -Type DWord
Set-ItemProperty -Path $wuKey -Name "AUOptions"   -Value 2 -Type DWord  # Notify only

# 4. Disable USB selective suspend (avoid eWon adapter dropouts)
powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0

Write-Host "Windows hardening complete. A reboot is recommended before service install."
