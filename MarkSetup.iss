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

[Files]
Source: "mark.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall

[Run]
Filename: "{app}\mark.exe"; Flags: nowait
