[Setup]
AppId={{8A2F06F8-FC4F-4110-A43E-2DF88351943C}
AppName=Osint Master
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Osint Master
CloseApplications=no
PrivilegesRequired=lowest
OutputBaseFilename=MarkSetup

[Code]
const
  MaxLockAttempts = 60; // 60 * 500ms = 30 секунд

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
  while IsFileLocked(ExePath) and (Attempts < MaxLockAttempts) do
  begin
    Sleep(500);
    Attempts := Attempts + 1;
  end;

  if IsFileLocked(ExePath) then
    Result := 'Не удалось завершить работу Osint Master (файл mark.exe занят). ' +
               'Закройте приложение вручную и запустите установку снова.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  EnvPath: string;
  Lines, NewLines: TArrayOfString;
  Found: Boolean;
  I: Integer;
  ApiLine: string;
begin
  if CurStep = ssPostInstall then
  begin
    ApiLine := 'API_BASE_URL=https://back-osint.onrender.com';
    EnvPath := ExpandConstant('{app}\.env');

    if not FileExists(EnvPath) then
    begin
      SaveStringToFile(EnvPath, ApiLine + #13#10, False);
      Exit;
    end;

    LoadStringsFromFile(EnvPath, Lines);
    SetArrayLength(NewLines, GetArrayLength(Lines));
    Found := False;

    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      if Pos('API_BASE_URL=', Lines[I]) = 1 then
      begin
        NewLines[I] := ApiLine;
        Found := True;
      end
      else
        NewLines[I] := Lines[I];
    end;

    if not Found then
    begin
      SetArrayLength(NewLines, GetArrayLength(NewLines) + 1);
      NewLines[GetArrayLength(NewLines) - 1] := ApiLine;
    end;

    SaveStringsToFile(EnvPath, NewLines, False);
  end;
end;

[Icons]
Name: "{autodesktop}\Osint Master"; Filename: "{app}\mark.exe"

[Files]
Source: "dist\mark.exe"; DestDir: "{app}"; Flags: ignoreversion restartreplace
Source: ".env"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall

[Run]
; Вариант без чекбоксов: запускается всегда и при обычной, и при тихой установке
Filename: "{cmd}"; Parameters: "/c start "" ""{app}\mark.exe"""; WorkingDir: "{app}"; Flags: nowait