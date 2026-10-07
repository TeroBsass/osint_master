; installer.iss — Inno Setup скрипт для Osint Master (mark.exe).
;
; Делает всё то же, что и раньше:
;   - ставит exe без прав администратора (PrivilegesRequired=lowest) в
;     пользовательскую папку {localappdata} — никакого UAC-запроса;
;   - кладёт .env рядом с exe ТОЛЬКО при первой установке (Flags: onlyifdoesntexist),
;     чтобы апдейт не затирал уже настроенный .env;
;   - при установке использует Restart Manager (CloseApplications), чтобы
;     корректно закрыть уже запущенный mark.exe перед заменой файла —
;     на стороне приложения за мгновенную реакцию на это отвечает
;     console_ctrl_handler.py;
;   - сам перезапускает mark.exe в конце через [Run] — именно поэтому
;     update.py больше НЕ передаёт /RESTARTAPPLICATIONS: если это сделает
;     ещё и сам Restart Manager, mark.exe запустится дважды подряд, и один
;     из двух запусков (от Restart Manager, без нормального рабочего
;     каталога) тут же схлопывается — это и была причина миллисекундной
;     вспышки консольного окна;
;   - небольшая пауза в начале компенсирует то, что update.py закрывает
;     старый mark.exe сам (os._exit) и тут же запускает установку, не
;     дожидаясь, пока ОС гарантированно снимет блокировку файла.
;
; Собирается через deploy.sh:
;   MSYS_NO_PATHCONV=1 ISCC.exe "/DMyAppVersion=1.4.3" installer.iss
; (если компилируете вручную без /D — ниже стоит запасное значение "0.0.0").

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "Osint Master"
#define MyAppExeName "mark.exe"

; ВАЖНО: этот GUID должен оставаться ОДНИМ И ТЕМ ЖЕ во всех последующих
; релизах — по нему Inno Setup (и "Установка и удаление программ") узнаёт,
; что это тот же самый продукт, а не новый. Если у вас уже был выпущен хотя
; бы один релиз с другим AppId — используйте именно его, а не этот, иначе
; апдейтнутые машины получат вторую запись в списке программ.
#define MyAppId "{8A2F06F8-FC4F-4110-A43E-2DF88351943C}"

[Setup]
AppId={{#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppName}
DefaultDirName={localappdata}\Programs\{#MyAppName}
; Папка установки намеренно ДРУГАЯ, чем папка с device.token
; ({localappdata}\Osint Master напрямую — см. _TOKEN_DIR в client_api.py):
; их развели специально, чтобы пересоздание/переустановка папки приложения
; при апдейте никогда не задевала токен устройства.
DisableProgramGroupPage=yes
DisableWelcomePage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=MarkSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

; Restart Manager: закрывать занятые файлом процессы можно (по умолчанию —
; при обычном GUI-запуске инсталлятора спросит), но НЕ перезапускать их
; своими силами — этим целиком занимается [Run] ниже. Именно сочетание
; "и Restart Manager перезапускает, и [Run] тоже запускает" и давало
; двойной запуск с миганием окна.
CloseApplications=yes
CloseApplicationsFilter={app}\{#MyAppExeName}
RestartApplications=no

[Files]
; onedir-сборка PyInstaller кладёт exe и весь его _internal/ (DLL, python-раннтайм
; и т.п.) в одну папку dist\mark\ — забираем её целиком, а не один файл, как
; раньше при onefile. recursesubdirs/createallsubdirs — чтобы _internal\ и всё,
; что внутри, тоже попало в {app} и корректно отслеживалось при удалении.
Source: "dist\mark\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: ".env"; DestDir: "{app}"; Flags: onlyifdoesntexist

[Run]
; Без "postinstall"/"skipifsilent" — запускается безусловно и в обычном
; GUI-режиме, и при /VERYSILENT (как раз тот случай, когда апдейт запущен
; из уже работающего приложения). "nowait" — Setup не ждёт, пока mark.exe
; завершится, это консольное приложение работает долго само по себе.
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Flags: nowait skipifdoesntexist

[Code]
function InitializeSetup(): Boolean;
begin
  // update.py закрывает старый mark.exe немедленно (os._exit из
  // console_ctrl_handler.py, без ожидания корректного WM_QUERYENDSESSION —
  // у голого консольного приложения физически нет окна, чтобы его
  // получить) и сразу запускает этот инсталлятор. ОС не всегда успевает
  // гарантированно снять блокировку файла exe к этому моменту — короткая
  // пауза здесь надёжнее, чем гонка с только что завершившимся процессом.
  Sleep(1500);
  Result := True;
end;