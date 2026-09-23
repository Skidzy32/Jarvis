; ============================================================
; jarvis.iss -- 4.4.0: the recipe for Jarvis-Setup.exe (Inno Setup 6).
;
; You don't build this yourself: pushing a version tag to GitHub
; (python make_share.py, then git push --follow-tags in jarvis-share) makes
; GitHub build it (.github/workflows/build-installer.yml) and publish
; Jarvis-Setup.exe under Releases. It expects the SHARED layout (the
; program in ..\app, Python's installer in deps\), so it only builds in
; jarvis-share, never in your own Jarvis folder -- nothing personal can
; get in, and the [Files] exclusions below are a second guard.
;
; What Setup does, all without a console window:
;   - the usual wizard: licence, shortcuts (Start menu ticked, Desktop
;     not -- people choose), which browser Jarvis opens in, Install;
;   - installs for this user only (no admin prompt) into
;     %LOCALAPPDATA%\Programs\Jarvis;
;   - if there's no suitable Python (3.9+, with Tk), installs Jarvis's own
;     private copy inside that folder (not on PATH, no py launcher, no file
;     associations) from the python.org installer bundled into Setup;
;   - offers to open Jarvis at the end.
; Uninstall (Settings > Apps, or Jarvis's own Settings > Uninstall...)
; asks whether to keep your notes (default: keep) and removes the private
; Python too.
;
; NOTE: without a code-signing certificate, Windows shows "Windows protected
; your PC" the first time: More info -> Run anyway.
; ============================================================

#define AppVersion "4.4.1"
#define PyVersion "3.13.15"
#define PySetup "python-" + PyVersion + "-amd64.exe"

[Setup]
AppId={{8C1B7A44-2F7E-4E0E-9B0B-5A1C4D3E7F21}
AppName=Jarvis
AppVersion={#AppVersion}
AppVerName=Jarvis {#AppVersion}
AppPublisher=Jarvis
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Jarvis Setup
DefaultDirName={localappdata}\Programs\Jarvis
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
OutputDir=Output
OutputBaseFilename=Jarvis-Setup
SetupIconFile=..\app\assets\jarvis.ico
WizardImageFile=wizard-large.bmp
WizardSmallImageFile=wizard-small.bmp
UninstallDisplayIcon={app}\assets\jarvis.ico
UninstallDisplayName=Jarvis
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=no

[Messages]
WelcomeLabel2=This will install Jarvis {#AppVersion} on your computer.%n%nJarvis turns your notes into a 3D galaxy you can talk to. It runs on your computer, and your notes stay here.%n%nNo admin rights needed, and nothing else on your computer is changed.
FinishedLabel=Jarvis is installed. Open it any time from the Start menu (type Jarvis).
FinishedLabelNoIcons=Jarvis is installed.

[Tasks]
Name: "startmenu"; Description: "Add Jarvis to the &Start menu"; GroupDescription: "Shortcuts:"
Name: "desktopicon"; Description: "Add a Jarvis icon to my &Desktop"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\app\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "\.git\*,\notes\*,\usage\*,\logs\*,\config.json,\focus_ledger.json,\review_settings.json,\overlay_position.json,\viewer\graph-data.js,__pycache__,*.pyc,*.log,\browser_launchers\jarvis-profiles\*,_testrun.py"
; Python, only when this computer has no suitable one. Kept as python-setup.exe
; so uninstalling Jarvis can remove that private Python cleanly.
Source: "deps\{#PySetup}"; DestDir: "{app}"; DestName: "python-setup.exe"; Flags: ignoreversion; Check: NeedPython; AfterInstall: InstallPython

[Icons]
Name: "{userprograms}\Jarvis"; Filename: "{code:PythonW}"; Parameters: """{app}\jarvis_launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\jarvis.ico"; Comment: "Your notes as a galaxy you can talk to"; Tasks: startmenu
Name: "{userdesktop}\Jarvis"; Filename: "{code:PythonW}"; Parameters: """{app}\jarvis_launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\jarvis.ico"; Comment: "Your notes as a galaxy you can talk to"; Tasks: desktopicon

[Run]
; records the browser chosen in the wizard (in config.json; your key is never touched)
Filename: "{code:PythonW}"; Parameters: """{app}\installer.py"" --in-place --browser={code:BrowserKey}"; WorkingDir: "{app}"; StatusMsg: "Saving your choices..."; Flags: runhidden waituntilterminated
Filename: "{code:PythonW}"; Parameters: """{app}\jarvis_launcher.pyw"""; WorkingDir: "{app}"; Description: "Open Jarvis now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; stop Jarvis if it's running, so nothing is in use while files are removed
Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -Command ""try {{ Invoke-WebRequest -UseBasicParsing -Method Post -Uri http://localhost:4700/system/quit -Body '{{}}' -ContentType 'application/json' -TimeoutSec 2 | Out-Null; Start-Sleep 2 }} catch {{}}"""; Flags: runhidden waituntilterminated; RunOnceId: "StopJarvis"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
var
  BrowserPage: TInputOptionWizardPage;
  BrowserKeys: array of String;
  FoundPython: String;
  DeleteNotes: Boolean;

{ ---- Python: a real one (3.9+, windowless pythonw, with Tk), or ours ---- }

function GoodPython(const Dir: String): String;
begin
  Result := '';
  if (Dir <> '') and (Pos('WindowsApps', Dir) = 0)
     and FileExists(Dir + '\pythonw.exe') and FileExists(Dir + '\Lib\tkinter\__init__.py') then
    Result := Dir + '\pythonw.exe';
end;

function PythonIn(Root: Integer; const Base: String; var Best: Integer): String;
var
  Names: TArrayOfString;
  I, Minor: Integer;
  V, P, W: String;
begin
  Result := '';
  if not RegGetSubkeyNames(Root, Base, Names) then exit;
  for I := 0 to GetArrayLength(Names) - 1 do
  begin
    V := Names[I];
    if (Pos('3.', V) = 1) and (Pos('-', V) = 0) then
    begin
      Minor := StrToIntDef(Copy(V, 3, Length(V)), -1);      { '3.13t' and the like -> -1, skipped }
      if (Minor >= 9) and (Minor > Best)
         and RegQueryStringValue(Root, Base + '\' + V + '\InstallPath', '', P) then
      begin
        W := GoodPython(RemoveBackslashUnlessRoot(P));
        if W <> '' then begin Result := W; Best := Minor; end;
      end;
    end;
  end;
end;

function FindPython(): String;
var
  Best: Integer;
  W: String;
begin
  { Jarvis's own Python from an earlier install comes first }
  Result := GoodPython(ExpandConstant('{localappdata}\Programs\Jarvis\python'));
  if Result <> '' then exit;
  Best := -1;
  W := PythonIn(HKCU, 'Software\Python\PythonCore', Best); if W <> '' then Result := W;
  W := PythonIn(HKLM64, 'Software\Python\PythonCore', Best); if W <> '' then Result := W;
  W := PythonIn(HKLM32, 'Software\Python\PythonCore', Best); if W <> '' then Result := W;
end;

function NeedPython(): Boolean;
begin
  Result := FoundPython = '';
end;

function PythonW(Param: String): String;
begin
  if FoundPython <> '' then Result := FoundPython
  else Result := ExpandConstant('{app}\python\pythonw.exe');
end;

procedure InstallPython();
var
  RC: Integer;
begin
  WizardForm.StatusLabel.Caption := 'Installing Jarvis''s own copy of Python (about a minute)...';
  Exec(ExpandConstant('{app}\python-setup.exe'),
       ExpandConstant('/quiet InstallAllUsers=0 TargetDir="{app}\python" Include_launcher=0 InstallLauncherAllUsers=0 ' +
                      'PrependPath=0 AssociateFiles=0 Shortcuts=0 Include_doc=0 Include_test=0 Include_dev=0 ' +
                      'Include_pip=0 Include_tcltk=1'),
       '', SW_HIDE, ewWaitUntilTerminated, RC);
  FoundPython := FindPython();
  if FoundPython = '' then
    MsgBox('Python could not be installed (code ' + IntToStr(RC) + '), so Jarvis won''t be able to start yet.' + #13#10#13#10 +
           'Run Jarvis-Setup.exe again, or install Python from python.org and then run Setup again.',
           mbError, MB_OK);
end;

{ ---- the browser page ---- }

function Found(const A, B, C: String): Boolean;
begin
  Result := ((A <> '') and FileExists(ExpandConstant(A))) or ((B <> '') and FileExists(ExpandConstant(B)))
            or ((C <> '') and FileExists(ExpandConstant(C)));
end;

procedure AddBrowser(const Key, Name, How: String; IsThere: Boolean);
var
  N: Integer;
begin
  if not IsThere then exit;
  N := GetArrayLength(BrowserKeys);
  SetArrayLength(BrowserKeys, N + 1);
  BrowserKeys[N] := Key;
  BrowserPage.Add(Name + ' -- ' + How);
end;

procedure InitializeWizard();
begin
  BrowserPage := CreateInputOptionPage(wpSelectTasks, 'Which browser should Jarvis open in?',
    'Jarvis opens in its own window, like an app.',
    'Chrome, Edge or Brave give Jarvis a clean window with no tabs or address bar. Any of the four lets focus ' +
    'sessions see which site you''re on. You can change this later in Jarvis''s Settings.',
    True, False);
  AddBrowser('chrome', 'Chrome', 'its own clean window, like an app', Found('{commonpf64}\Google\Chrome\Application\chrome.exe',
    '{commonpf32}\Google\Chrome\Application\chrome.exe', '{localappdata}\Google\Chrome\Application\chrome.exe'));
  AddBrowser('edge', 'Microsoft Edge', 'its own clean window, like an app', Found('{commonpf32}\Microsoft\Edge\Application\msedge.exe',
    '{commonpf64}\Microsoft\Edge\Application\msedge.exe', ''));
  AddBrowser('brave', 'Brave', 'its own clean window, like an app', Found('{commonpf64}\BraveSoftware\Brave-Browser\Application\brave.exe',
    '{commonpf32}\BraveSoftware\Brave-Browser\Application\brave.exe', '{localappdata}\BraveSoftware\Brave-Browser\Application\brave.exe'));
  AddBrowser('opera', 'Opera GX', 'a separate Opera GX window', Found('{localappdata}\Programs\Opera GX\opera.exe',
    '{localappdata}\Programs\Opera GX\launcher.exe', ''));
  BrowserPage.Add('My normal browser -- as a tab');
  BrowserPage.SelectedValueIndex := 0;
end;

function BrowserKey(Param: String): String;
var
  I: Integer;
begin
  I := BrowserPage.SelectedValueIndex;
  if (I >= 0) and (I < GetArrayLength(BrowserKeys)) then Result := BrowserKeys[I]
  else Result := 'default';
end;

{ ---- install ---- }

function InitializeSetup(): Boolean;
begin
  FoundPython := FindPython();
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RC: Integer;
begin
  { updating: stop a running Jarvis first, so its files aren't in use }
  Exec('powershell.exe', '-NoProfile -WindowStyle Hidden -Command "try { Invoke-WebRequest -UseBasicParsing -Method Post ' +
       '-Uri http://localhost:4700/system/quit -Body ''{}'' -ContentType ''application/json'' -TimeoutSec 2 | Out-Null; ' +
       'Start-Sleep 2 } catch {}"', '', SW_HIDE, ewWaitUntilTerminated, RC);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { an install made by "Install Jarvis.bat" had its own Settings > Apps
    entry; Setup's entry replaces it, so there's only ever one }
  if CurStep = ssPostInstall then
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis');
end;

{ ---- uninstall ---- }

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  RC: Integer;
  App: String;
begin
  App := ExpandConstant('{app}');
  if CurUninstallStep = usUninstall then
  begin
    DeleteNotes := False;
    if not UninstallSilent then
      DeleteNotes := MsgBox('Delete your notes and settings too?' + #13#10#13#10 +
        'Choose No to keep them (they stay in ' + App + '), so if you install Jarvis again it carries on where you left off.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    { Jarvis's own private Python goes too (a Python you installed yourself is never touched) }
    if FileExists(App + '\python-setup.exe') and DirExists(App + '\python') then
      Exec(App + '\python-setup.exe', '/quiet /uninstall', '', SW_HIDE, ewWaitUntilTerminated, RC);
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    DelTree(App + '\python', True, True, True);
    if DeleteNotes then
      DelTree(App, True, True, True);
  end;
end;
