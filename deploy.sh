#!/usr/bin/env bash
# Полный релизный цикл: пуш кода в репозиторий + сборка exe + сборка
# инсталлятора Inno Setup + GitHub Release с этим инсталлятором.
#
# .env в репозиторий всё так же не коммитим (секреты в git не кладём), но
# он подхватывается инсталлятором напрямую из .iss-скрипта (Source: ".env" ...
# Flags: onlyifdoesntexist) — то есть один раз попадает внутрь инсталлятора
# и разворачивается рядом с mark.exe только при первой установке, не
# перетирая уже настроенный .env при обновлении. Отдельным downloadable
# asset'ом в релизе он больше не нужен — updater его больше не скачивает.
#
# Использование:
#   ./deploy.sh v1.3.0 "Что изменилось в этой версии"
set -euo pipefail

VERSION="${1:-}"
NOTES="${2:-Update $VERSION}"
ENTRY_SCRIPT="mark.py"          # <-- замените на имя вашего главного .py файла
ENV_FILE=".env"                 # <-- путь до вашего локального .env
ISS_SCRIPT="MarkSetup.iss"      # <-- путь до вашего .iss-скрипта Inno Setup
INSTALLER_NAME="MarkSetup.exe"  # <-- должно совпадать с OutputBaseFilename в .iss

# Путь к компилятору Inno Setup. Можно переопределить снаружи:
#   INNO_COMPILER="/c/Program Files/Inno Setup 6/ISCC.exe" ./deploy.sh v1.3.0
INNO_COMPILER="${INNO_COMPILER:-/c/Program Files (x86)/Inno Setup 6/ISCC.exe}"

if [ -z "$VERSION" ]; then
  echo -e "\e[31mUsage: ./deploy.sh vX.Y.Z \"changelog text\"\e[0m"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo -e "\e[31mНе найден $ENV_FILE рядом со скриптом — нечего вкладывать в инсталлятор.\e[0m"
  exit 1
fi

if [ ! -f "$ISS_SCRIPT" ]; then
  echo -e "\e[31mНе найден $ISS_SCRIPT — нужен .iss-скрипт Inno Setup рядом со скриптом.\e[0m"
  exit 1
fi

if [ ! -f "$INNO_COMPILER" ]; then
  echo -e "\e[31mНе найден компилятор Inno Setup по пути: $INNO_COMPILER\e[0m"
  echo -e "\e[31mУкажите верный путь через переменную окружения INNO_COMPILER.\e[0m"
  exit 1
fi

# Версия без ведущей "v" — именно так её сравнивает _parse_version() в update.py,
# и её же передаём в .iss как AppVersion, чтобы в "Установка и удаление программ"
# показывалась корректная версия и Inno Setup сам понимал, что это апгрейд.
VERSION_NUM="${VERSION#v}"

echo -e "\e[34m== 1. Пуш кода в репозиторий (без .env — он в .gitignore) ==\e[0m"
git add -A
git commit -m "Release $VERSION" || echo "(нечего коммитить, идём дальше)"
git push origin main

echo -e "\e[34m== 2. Чистая пересборка exe ==\e[0m"
# Удаляем всё, что осталось от прошлого релиза, ДО сборки — иначе при сбое
# компиляции (тихом или нет) в Output/ может остаться старый файл от прошлой
# версии, который потом безо всякой ошибки уйдёт в релиз под новым тегом
# (именно так один раз в релиз 1.6.x улетел installer от 1.5.0).
rm -rf dist build Output
python -m PyInstaller --onefile "$ENTRY_SCRIPT"
# .env НЕ передаётся через --add-data — он не должен попасть внутрь самого exe,
# в инсталлятор он подкладывается отдельно, см. [Files] в $ISS_SCRIPT

echo -e "\e[34m== 3. Сборка инсталлятора Inno Setup ==\e[0m"
# MSYS_NO_PATHCONV=1 — иначе Git Bash сам "исправит" /DMyAppVersion=... как будто
# это unix-путь, ISCC перестанет узнавать в нём ключ /D и решит, что ему дали
# два файла скрипта сразу ("You may not specify more than one script filename").
MSYS_NO_PATHCONV=1 "$INNO_COMPILER" "/DMyAppVersion=$VERSION_NUM" "$ISS_SCRIPT"

BUILT_INSTALLER="Output/$INSTALLER_NAME"
if [ ! -f "$BUILT_INSTALLER" ]; then
  echo -e "\e[31mИнсталлятор не найден там, где ожидался: $BUILT_INSTALLER\e[0m"
  echo -e "\e[31mПроверьте OutputDir/OutputBaseFilename в $ISS_SCRIPT.\e[0m"
  exit 1
fi

# Доп. страховка: раз мы только что удалили Output/ перед сборкой, файл
# физически не может быть старше нескольких секунд. Если он вдруг "старый" —
# что-то пошло совсем не так (например, ISCC отработал из кеша/не там,
# где ожидалось) — лучше остановиться, чем залить в релиз не то.
BUILT_AGE=$(( $(date +%s) - $(date -r "$BUILT_INSTALLER" +%s) ))
if [ "$BUILT_AGE" -gt 120 ]; then
  echo -e "\e[31mСобранный $BUILT_INSTALLER выглядит старым (${BUILT_AGE}s) — похоже, это не свежая сборка. Останавливаюсь.\e[0m"
  exit 1
fi

echo -e "\e[34m== 4. Тег версии ==\e[0m"
git tag "$VERSION"
git push origin "$VERSION"

echo -e "\e[34m== 5. GitHub Release: инсталлятор как единственный asset ==\e[0m"
gh release create "$VERSION" "$BUILT_INSTALLER" \
  --title "$VERSION" \
  --notes "$NOTES"

echo -e "\e[32mГотово: код запушен в main, тег $VERSION создан, релиз опубликован с $INSTALLER_NAME.\e[0m"