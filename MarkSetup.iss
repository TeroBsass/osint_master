[Setup]
AppId={{8A2F06F8-FC4F-4110-A43E-2DF88351943C}
AppName=Osint Master
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Osint Master
CloseApplications=no
PrivilegesRequired=lowest
OutputBaseFilename=MarkSetup

[Code]
function IsFileLocked(FileName: string): Boolean;
var
  TempName: string;
begin
  Result := True;
  if not FileExists(FileName) then
  begin
    Result := False;
    Exit;
  end;
  TempName := FileName + '.lock_check';
  if RenameFile(FileName, TempName) then
  begin
    RenameFile(TempName, FileName);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExePath: string;
  Attempts: Integer;
begin
  Result := '';
  ExePath := ExpandConstant('{app}\mark.exe');
  Attempts := 0;
  while IsFileLocked(ExePath) and (Attempts < 20) do
  begin
    Sleep(500);
    Attempts := Attempts + 1;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  EnvPath: string;
  Lines: TArrayOfString;
  Found: Boolean;
  I: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    EnvPath := ExpandConstant('{app}\.env');
    if not FileExists(EnvPath) then
      SaveStringToFile(EnvPath, 'API_BASE_URL=https://back-osint.onrender.com' + #13#10, False)
    else
    begin
      LoadStringsFromFile(EnvPath, Lines);
      Found := False;
      for I := 0 to GetArrayLength(Lines) - 1 do
        if Pos('API_BASE_URL=', Lines[I]) = 1 then
          Found := True;
      if not Found then
        SaveStringToFile(EnvPath, 'API_BASE_URL=https://back-osint.onrender.com' + #13#10, True);
    end;
  end;
end;

[Icons]
Name: "{autodesktop}\Osint Master"; Filename: "{app}\mark.exe"

[Files]
Source: "dist\mark.exe"; DestDir: "{app}"; Flags: ignoreversion restartreplace
Source: ".env"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall

[Run]
Filename: "{app}\mark.exe"; Flags: nowait runasoriginaluser