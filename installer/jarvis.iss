; ============================================================
; jarvis.iss -- builds Jarvis-Setup-<version>.exe with Inno Setup (free:
; https://jrsoftware.org/isinfo.php).
;
; HOW TO BUILD (on Windows, once Inno Setup is installed):
;   open this file in Inno Setup Compiler -> Build -> Compile.
;   The installer appears in installer\Output\. Build it from the
;   jarvis-share folder (the clean copy make_share.py makes), so nothing
;   personal can get in -- the [Files] exclusions below are a second guard.
;
; What Setup.exe does: the usual Next/Next/Finish wizard; installs for this
; user only (no admin prompt) into %LOCALAPPDATA%\Programs\Jarvis; then runs
; "Install Jarvis.bat /inplace", which installs Python if it's missing and
; makes the Desktop/Start menu icons; offers to start Jarvis at the end.
; Uninstall is in Settings > Apps; it leaves your notes folder in place.
;
; NOTE: without a code-signing certificate, Windows shows "Windows protected
; your PC" the first time: More info -> Run anyway.
; ============================================================

#define AppVersion "4.3.0"

[Setup]
AppId={{8C1B7A44-2F7E-4E0E-9B0B-5A1C4D3E7F21}
AppName=Jarvis
AppVersion={#AppVersion}
AppVerName=Jarvis {#AppVersion}
AppPublisher=Jarvis
DefaultDirName={localappdata}\Programs\Jarvis
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=Jarvis-Setup-{#AppVersion}
SetupIconFile=..\assets\jarvis.ico
UninstallDisplayIcon={app}\assets\jarvis.ico
UninstallDisplayName=Jarvis
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=no

[Messages]
WelcomeLabel2=This will install Jarvis on your computer.%n%nJarvis turns your notes into a 3D galaxy you can talk to. It runs on your computer; your notes stay here.%n%nIf Python isn't installed yet, Setup installs it for you (from Microsoft's winget).
FinishedLabel=Jarvis is installed. Open it any time from the Jarvis icon on your Desktop or in the Start menu.

[Files]
Source: "..\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; \
  Excludes: "\.git\*,\notes\*,\usage\*,\logs\*,\config.json,\focus_ledger.json,\review_settings.json,\overlay_position.json,\viewer\graph-data.js,__pycache__,*.pyc,*.log,\installer\Output\*,\browser_launchers\jarvis-profiles\*,_testrun.py"

[Run]
Filename: "{cmd}"; Parameters: "/c ""{app}\Install Jarvis.bat"" /inplace"; WorkingDir: "{app}"; \
  StatusMsg: "Setting up Python and the Jarvis icons (this can take a minute)..."; Flags: runhidden waituntilterminated

[UninstallRun]
; stop Jarvis if it's running, so nothing is in use while files are removed
Filename: "powershell.exe"; Parameters: "-NoProfile -Command ""try {{ Invoke-WebRequest -UseBasicParsing -Method Post -Uri http://localhost:4700/system/quit -Body '{{}}' -ContentType 'application/json' -TimeoutSec 2 | Out-Null }} catch {{}}"""; \
  Flags: runhidden; RunOnceId: "StopJarvis"

[UninstallDelete]
Type: files; Name: "{userdesktop}\Jarvis.lnk"
Type: files; Name: "{userprograms}\Jarvis.lnk"
Type: files; Name: "{app}\Jarvis.lnk"
Type: filesandordirs; Name: "{app}\__pycache__"
