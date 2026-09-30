# 注册 SoraV3Battery 的「开机自启 + 自愈」计划任务（无需管理员）。
# 用法：powershell -ExecutionPolicy Bypass -File tools/install-task.ps1
$ErrorActionPreference = 'Stop'

$TaskName = 'SoraV3Battery'
$dir = Join-Path $env:LOCALAPPDATA 'SoraV3Battery'
$exe = Join-Path $dir 'SoraV3Battery.exe'
if (-not (Test-Path $exe)) { throw "未找到 $exe，请先打包并复制到该目录" }

$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$action = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $dir
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $me
# 每 10 分钟触发一次：已在运行时会被程序的单实例互斥体挡掉，被终止后能自动回来
$keepAlive = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 3650)
# ExecutionTimeLimit 必须是不限时(Zero)，否则计划程序会在默认 3 天后杀掉常驻进程
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $atLogon, $keepAlive -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "已注册并启动计划任务 $TaskName"
