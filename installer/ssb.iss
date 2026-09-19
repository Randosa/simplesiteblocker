#ifndef AppVersion
  #define AppVersion "1.2.1"
#endif
[Setup]
#ifdef PackagingTest
AppId=SSB-Packaging-Test
AppName=SSB Packaging Test
DefaultDirName={localappdata}\SSB-Packaging-Test
PrivilegesRequired=lowest
OutputBaseFilename=SSB-Packaging-Test-{#AppVersion}
AppMutex=SSB.PackagingTest
#else
AppId={{504BBC0B-9861-49D0-A993-BD5D9AE73455}
AppName=SSB — Simple Site Blocker
DefaultDirName={autopf}\SSB
PrivilegesRequired=admin
OutputBaseFilename=SSB-Setup-{#AppVersion}
AppMutex=Global\SSB.Manager
#endif
AppVersion={#AppVersion}
AppPublisher=Randosa
AppPublisherURL=https://github.com/Randosa/simplesiteblocker
AppUpdatesURL=https://github.com/Randosa/simplesiteblocker/releases
DisableDirPage=yes
UsePreviousAppDir=yes
DefaultGroupName=SSB
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\release
SetupIconFile=..\icons\ssb.ico
UninstallDisplayIcon={app}\SSB.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Dirs]
#ifndef PackagingTest
Name: "{app}\config"; Permissions: admins-full system-full
Name: "{app}\state"; Permissions: admins-full system-full
Name: "{app}\logs"; Permissions: admins-full system-full
#endif

[Files]
Source: "..\dist\SSB\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\defaults\list.json"; DestDir: "{app}\config"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "..\defaults\settings.json"; DestDir: "{app}\config"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
#ifdef PackagingTest
Name: "{autoprograms}\SSB Packaging Test"; Filename: "{app}\SSB.exe"; WorkingDir: "{app}"; AppUserModelID: "Randosa.SSB.PackagingTest"
#else
Name: "{autoprograms}\SSB — Simple Site Blocker"; Filename: "{app}\SSB.exe"; WorkingDir: "{app}"; AppUserModelID: "Randosa.SSB"
#endif

[Run]
#ifndef PackagingTest
Filename: "{app}\SSB.exe"; Description: "Open SSB"; Flags: postinstall nowait skipifsilent
#endif

[Code]
var
  IntegrationReady, UpdatePrepared, RemoveConfiguration: Boolean;

function RunSSB(const Arguments: String): Boolean;
var ExitCode: Integer;
begin
#ifdef PackagingTest
  { Exercise actual file installation without changing this machine's blocking. }
  Result := Exec(ExpandConstant('{app}\SSB.exe'), 'self-test',
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
#else
  Result := Exec(ExpandConstant('{app}\SSB.exe'), Arguments,
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
#endif
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  LegacyPath, Command: String;
begin
  Result := '';
#ifndef PackagingTest
  LegacyPath := ExpandConstant('{commonappdata}\CodexSiteBlocker\install-state.json');
  if FileExists(LegacyPath) then begin
    StringChangeEx(LegacyPath, '''', '''''', True);
    Command := '-NoProfile -NonInteractive -Command "$ErrorActionPreference=''Stop''; ' +
      '$s=Get-Content -LiteralPath ''' + LegacyPath + ''' -Raw | ConvertFrom-Json; ' +
      'if($s.installation_complete -and $s.app_version -notin @(''1.0.0'',''1.0.1'',''1.0.2'',''1.0.1-personal.1'')){exit 10}"';
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Command,
      '', SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then begin
      Result := 'An unsupported or unreadable old SSB installation was found. Uninstall that version first to restore its network settings, then run Setup again. Your files have not been replaced.';
      exit;
    end;
  end;
#endif
  if FileExists(ExpandConstant('{app}\SSB.exe')) then begin
    if not RunSSB('prepare-update') then begin
      Result := 'SSB could not prepare the update. Close SSB and try again.';
      exit;
    end;
    UpdatePrepared := True;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if not RunSSB('finish-update') then
      RaiseException('SSB files were installed, but Windows integration failed. See the SSB logs and rerun Setup to repair.');
    IntegrationReady := True;
  end;
end;

procedure DeinitializeSetup();
begin
  if UpdatePrepared and not IntegrationReady then
    RunSSB('finish-update');
end;

function InitializeUninstall(): Boolean;
var Attempt: Integer;
begin
  Result := False;
  for Attempt := 1 to 30 do begin
    if not CheckForMutexes('Global\SSB.Manager') then break;
    Sleep(100);
  end;
  if CheckForMutexes('Global\SSB.Manager') then begin
    MsgBox('Close the SSB window, then start uninstalling from Windows Installed Apps.', mbInformation, MB_OK);
    exit;
  end;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    { This event follows the uninstall confirmation. Failure aborts before files
      are deleted, so the installed program remains available for repair. }
    if not RunSSB('uninstall') then
      RaiseException('SSB could not restore its Windows settings. Removal has stopped; see its logs and repair the installation before retrying.');
    RemoveConfiguration := False;
    if not UninstallSilent then
      RemoveConfiguration := MsgBox('Also remove the saved website list, schedule, and logs? Choose No to keep them for reinstallation.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  end;
  if (CurUninstallStep = usPostUninstall) and RemoveConfiguration then begin
    DelTree(ExpandConstant('{app}\config'), True, True, True);
    DelTree(ExpandConstant('{app}\state'), True, True, True);
    DelTree(ExpandConstant('{app}\logs'), True, True, True);
  end;
end;

function GetCustomSetupExitCode(): Integer;
begin
  if IntegrationReady then Result := 0 else Result := 1;
end;
