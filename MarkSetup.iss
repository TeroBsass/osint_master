[Setup]
AppId={{8A2F06F8-FC4F-4110-A43E-2DF88351943C}
AppName=Osint Master
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\Osint Master
CloseApplications=no
PrivilegesRequired=admin
OutputBaseFilename=MarkSetup

[Code]
function InitializeSetup(): Boolean;
begin
  // небольшой запас по времени, чтобы наш процесс (который сам себя закрывает
  // сразу после запуска этого инсталлятора) гарантированно успел отпустить
  // файл mark.exe до того, как Setup попробует его перезаписать
  Sleep(1500);
  Result := True;
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

[Files]
Source: "dist/mark.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall

[Run]
Filename: "{app}\mark.exe"; Flags: nowait runasoriginaluser; Description: "Run Osint Master";