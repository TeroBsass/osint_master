[Setup]
AppId={{8A2F06F8-FC4F-4110-A43E-2DF88351943C}
AppName=Osint Master
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\Osint Master
CloseApplications=yes
CloseApplicationsFilter=mark.exe
RestartApplications=yes
PrivilegesRequired=admin
OutputBaseFilename=MarkSetup

[Files]
Source: "mark.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall
