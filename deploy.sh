#!/usr/bin/env bash
# Полный релизный цикл: пуш кода в репозиторий + сборка exe + GitHub Release
# с exe И .env как отдельными файлами (в репозиторий .env всё равно не коммитим —
# секреты в git не кладём, но в сам релиз он попадает как downloadable asset).
# Использование:
#   ./deploy.sh v1.3.0 "Что изменилось в этой версии"
set -euo pipefail

VERSION="${1:-}"
NOTES="${2:-Update $VERSION}"
ENTRY_SCRIPT="mark.py"   # <-- замените на имя вашего главного .py файла
EXE_NAME="mark.exe"             # <-- имя exe, которое собирает PyInstaller
ENV_FILE=".env"                 # <-- путь до вашего локального .env, который прикладываем к релизу

if [ -z "$VERSION" ]; then
  echo -e "\e[31mUsage: ./deploy.sh vX.Y.Z \"changelog text\"\e[0m"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo -e "\e[31mНе найден $ENV_FILE рядом со скриптом — нечего прикладывать к релизу.\e[0m"
  exit 1
fi

echo -e "\e[34m== 1. Пуш кода в репозиторий (без .env — он в .gitignore) ==\e[0m"
git add -A
git commit -m "Release $VERSION" || echo "(нечего коммитить, идём дальше)"
git push origin main

echo -e "\e[34m== 2. Сборка exe ==\e[0m"
python -m PyInstaller --onefile "$ENTRY_SCRIPT"
# .env НЕ передаётся через --add-data — он не должен попасть внутрь самого exe,
# он идёт отдельным файлом рядом

echo -e "\e[34m== 3. Тег версии ==\e[0m"
git tag "$VERSION"
git push origin "$VERSION"

echo -e "\e[34m== 4. GitHub Release: exe + .env отдельными файлами ==\e[0m"
gh release create "$VERSION" "dist/$EXE_NAME" "$ENV_FILE" \
  --title "$VERSION" \
  --notes "$NOTES"

echo -e "\e[32mГотово: код запушен в main, тег $VERSION создан, релиз опубликован с $EXE_NAME и $ENV_FILE.\e[0m"
