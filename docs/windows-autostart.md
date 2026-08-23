# Windows: Auto-start the service at login

Run the service silently in the background on every login, without a terminal window or manual start.

## 1. Clone the repo somewhere permanent

Replace `<path-to-clone>` below with wherever you want the repo to live (e.g. `C:\ai-watermark-remover` or `C:\Users\<you>\ai-watermark-remover`). Use the same path consistently in every step below.

```powershell
git clone https://github.com/mohityadav8/ai-watermark-remover.git <path-to-clone>
```

## 2. Create a silent launcher script

Save as `<path-to-clone>\start-service.vbs`:

```vbscript
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "python service\scripts\server.py --host 127.0.0.1 --port 8765", 0, False
```

## 3. Register a scheduled task

This does **not** require Administrator privileges — `-AtLogOn` with a user-level trigger runs under your own account, so a regular PowerShell window is enough.

```powershell
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument '"<path-to-clone>\start-service.vbs"' -WorkingDirectory "<path-to-clone>"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "WatermarksRemoverService" -Action $action -Trigger $trigger -Settings $settings -Description "Auto-starts the ai-watermark-remover HTTP service at login"
```

## 4. Start it immediately (no reboot needed)

```powershell
Start-ScheduledTask -TaskName "WatermarksRemoverService"
```

## 5. Verify

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

Should return `{"ok": true, "version": "..."}`.

## Notes

- Requires Python 3.10+ on PATH.
- The scheduled task runs at every login going forward — no manual start needed.
- To stop auto-starting: `Unregister-ScheduledTask -TaskName "WatermarksRemoverService"`
