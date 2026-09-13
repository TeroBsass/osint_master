import tempfile, json, colorama, dotenv
import hwid, getpass, os, time, sys
from art import text2art
from colorama import Fore, Style
import threading, subprocess, ctypes, urllib.request
dotenv.load_dotenv()
import client_api as client


# версия текущей сборки — бампать вручную перед каждым релизом (git tag должен совпадать)
APP_VERSION = "1.8.9"
GITHUB_REPO = "TeroBsass/osint_master"
# version.json лежит в корне репозитория и отдаётся сырым через raw.githubusercontent.com
GITHUB_API_RELEASES = f"https://api.github.com/repos/{GITHUB_REPO}/releases"


# защищенный вызов hwid.get_hwi d() с обработкой ошибок
def safe_get_hwid():
    try:
        return hwid.get_hwid()
    except subprocess.CalledProcessError as e:
        if e.returncode == 3221225786:
            return None  # Ctrl+C — молча возвращаем None
        print(f"[hwid] powershell error: {e}")
        return None


# обработка исключений, чтобы корректно завершать программу при Ctrl+C
def custom_excepthook(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        SIMPLE_COMMANDS.graceful_exit()
    else:
        sys.__excepthook__(exc_type, exc_value, exc_traceback)



# глобальные переменные и объекты
sys.excepthook = custom_excepthook
stop_event = threading.Event()
watcher_thread = None
threading_lock = threading.Lock()
already_handled = False
connection_pool = None


# класс для простых команд консоли и помощных функций
class SIMPLE_COMMANDS:
    def help_command(args=None):
        if args is not None:
            print(f"{Fore.RED}The 'help' command does not take any arguments.{Style.RESET_ALL}")
            console_start()
            return
        print(f"{Fore.RED}Available commands:{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}help{Style.RESET_ALL} - Show this help message")
        print(f"{Fore.YELLOW}exit{Style.RESET_ALL} - Exit the console")
        print(f"{Fore.YELLOW}clear(or cls){Style.RESET_ALL} - Clear the console")
        print(f"{Fore.YELLOW}osint{Style.RESET_ALL} - Start OSINT tools")
        print(f"{Fore.YELLOW}scan{Style.RESET_ALL} - Start scanning tools")
        print(f"{Fore.YELLOW}info{Style.RESET_ALL} - Show information about the tool")
        print(f"{Fore.YELLOW}update{Style.RESET_ALL} - Update the tool to the latest version")
        print(f"{Fore.YELLOW}export{Style.RESET_ALL} - Export the findings to a file")
        print(f"{Fore.YELLOW}import{Style.RESET_ALL} - Import data from a file")
        print(f"{Fore.YELLOW}chat{Style.RESET_ALL} - Start chat tools")
        print(f"{Fore.YELLOW}dos{Style.RESET_ALL} - Mark user for shutdown or restart by HWID")
        print(f"{Fore.YELLOW}ghwid{Style.RESET_ALL} - Get HWID of user by name and password")
        console_start()


    def clear(args=None):
        if args is not None:
            print(f"{Fore.RED}The 'clear' command does not take any arguments.{Style.RESET_ALL}")
            console_start()
            return
        SIMPLE_COMMANDS.loading_animation("Clearing the console", 2)
        print(f"{Fore.GREEN}Console cleared!!!{Style.RESET_ALL}")
        os.system("cls")
        start()


    def mask(word):
        return "#" * len(word)

    def loading_animation(text="text", duration=3):
        end_time = time.time() + duration
        dots_cycle = ["", ".", "..", "..."]
        i = 0
        while time.time() < end_time:
            dots = dots_cycle[i % len(dots_cycle)]
            # \r возвращает курсор в начало строки, чтобы перезаписать её
            # пробелы в конце нужны, чтобы затереть более длинный предыдущий текст
            sys.stdout.write(f"\r{Fore.RED}{text}{dots}{Style.RESET_ALL}   ")
            sys.stdout.flush()
            time.sleep(0.4)
            i += 1
        # очищаем строку после завершения
        sys.stdout.write("\r" + " " * (len(text) + 10) + "\r")
        sys.stdout.flush()

    def graceful_exit(args=None):
        global watcher_thread
        """Общая функция завершения — используйте её и для команды exit, и для Ctrl+C"""
        flags = SIMPLE_COMMANDS.parse_flags(args, known={"cls"})
        stop_all = flags.get("cls") or None
        if stop_all and bool(stop_all):
            os.system("cls")

        stop_event.set()
        if watcher_thread is not None:
            watcher_thread.join(timeout=3)# ждём, пока поток реально закончит текущую итерацию
        if decay_thread is not None:
            decay_thread.join(timeout=3)  
        try:
            connection_pool.closeall()
        except Exception:
            pass
        sys.exit(0)

    def set_console_title(title: str):
        ctypes.windll.kernel32.SetConsoleTitleW(title)


    def pretty_print(reasons):
        sys.stdout.write("\r" + " " * 80 + "\r")  # затираем текущую строку
        sys.stdout.flush()
        if "a" in reasons and "b" in reasons:
            print(f"{Fore.YELLOW}Your account has been marked for restart and shutdown.(Firstly shutdown will be performed, then in the next start it will be restarted){Style.RESET_ALL}")
        elif "a" in reasons:
            print(f"{Fore.YELLOW}Your account has been marked for restart.{Style.RESET_ALL}")
        elif "b" in reasons:
            print(f"{Fore.RED}Your account has been marked for shutdown.{Style.RESET_ALL}")
        sys.stdout.write(">>>")
        sys.stdout.flush()  # возвращаемся в консоль после вывода сообщения

    def pretty_warn(strings):
        sys.stdout.write("\r" + " " * 80 + "\r")  # затираем текущую строку
        sys.stdout.flush()
        for string in strings:
            print(f"{Fore.RED}{string}{Style.RESET_ALL}")
        sys.stdout.write(">>>")
        sys.stdout.flush()  # возвращаемся в консоль после вывода сообщения

    def info(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args=args, known={"com"})
        command_name = flags.get("com") or input("Enter the command name to get info: ")
        info_dict = {
            "help": f"{Fore.BLUE}Use 'help'{Style.RESET_ALL} to get info help.", 
            "exit": f"{Fore.BLUE}Use 'exit'{Style.RESET_ALL} to exit the console.",
            "clear": f"{Fore.BLUE}Use 'clear'{Style.RESET_ALL} to clear the console.",
            "cls": f"{Fore.BLUE}Use 'cls'{Style.RESET_ALL} to clear the console.",
            "info": f"{Fore.BLUE}Use 'info --com=<command>* '{Style.RESET_ALL}to show information about a specific command.",
            "update": f"Update the tool to the latest version.\n{Fore.BLUE}Use 'update --path=<path>* '{Style.RESET_ALL}.",
            "export": f"Export the findings to a file.\n{Fore.BLUE}Use export --file_name=<name>* {Style.RESET_ALL}",
            "scan": f"Scan users and get more information\n{Fore.BLUE}.Use 'scan users* '{Style.RESET_ALL} to scan all users and {Fore.BLUE}'scan more* --name=<name>* '{Style.RESET_ALL} to get more information about a specific user.",
            "chat": f"Send messages to other users and read your own messages.\n{Fore.BLUE}Use 'chat send* --name=<name>* --mes=<message>* '{Style.RESET_ALL} to send a message and {Fore.BLUE}'chat my* --waiter=<time>* --by_name=<name>* '{Style.RESET_ALL} to read your own messages.",
            "osint": f"Tool to get password of user by name(but you open your own password, that makes it more easy to get it to another user for the osint process).{Fore.BLUE}Use 'osint --name=<name>* --power=<power>* '{Style.RESET_ALL} to start the osint process.",
            "dos": f"Mark user for shutdown or restart by HWID.\n{Fore.BLUE}Use dos --hwid=<hwid>* --act=<act>* {Style.RESET_ALL}",
            "ghwid": f"Get HWID of user by name and password.\n{Fore.BLUE}Use 'ghwid --name=<name>* --pass=<password>* '{Style.RESET_ALL} to get HWID of user by name and password.",
        }
        if command_name in info_dict:
            print(f"{Fore.GREEN}{command_name}{Style.RESET_ALL}: {info_dict[command_name]}")
        else:
            print(f"{Fore.RED}Command not found.{Style.RESET_ALL}")
        console_start()

    def parse_flags(args, known=None):
        """
        Разбирает список строк вида '--flag=value' или '--flag' (без значения) в словарь.
        Порядок и комбинация флагов не важны.
        Если передан набор known - названия флагов, которых там нет, не отбрасываются
        (поведение остаётся прежним), просто перед этим выводится предупреждение,
        что такого аргумента не существует.
        """
        parsed = {}
        for a in (args or []):
            if not a.startswith("--"):
                continue
            body = a[2:]  # убираем "--"
            if "=" in body:
                key, value = body.split("=", 1)
            else:
                key, value = body, None
            if known is not None and key not in known:
                print(f"{Fore.RED}Unknown argument: --{key}{Style.RESET_ALL}")
            parsed[key] = value
        return parsed
    def _parse_version(v):
        return tuple(int(p) for p in v.strip().lstrip("v").split("."))


# класс для работы с чатом
class CHAT:
    @staticmethod
    def send_message(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, known={"name", "mes"})
        name = flags.get("name") or None
        message = flags.get("mes") or None
        client.send_message(safe_get_hwid(), name, message)

    @staticmethod
    def show_own_messages(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, known={"waiter", "by_name"})

        waiter_raw = flags.get("waiter")
        if "waiter" in flags:
            try:
                amount = int(waiter_raw) if waiter_raw is not None else 1
            except (ValueError, TypeError):
                amount = 1
            if amount > 5 or amount < 0:
                amount = 1
            use_delay = True
        else:
            amount = 0
            use_delay = False

        name = flags.get("by_name") or None

        hwid = safe_get_hwid()
        if hwid is None:
            return False

        raw = client.read_messages(hwid)
        string_undel = ""
        if raw:
            print(f"{Fore.GREEN}Your messages:{Style.RESET_ALL}")
            for i in raw.split(';'):
                i = i.strip()
                if not i:
                    continue
                parts = i.split('->', 1)
                if len(parts) != 2:
                    continue
                sender, text = parts
                if name and sender != name:
                   string_undel += f"{sender}->{text};"
                   continue
                print(f"{Fore.BLUE}{sender}{Style.RESET_ALL}>>{text}")
                if use_delay:
                    time.sleep(amount)
            print(f"{Fore.YELLOW}All messages displayed and read.{Style.RESET_ALL}")
            if name:
                client.update_data(hwid, "message", string_undel)
        else:
            print(f"{Fore.YELLOW}No messages found for your account.{Style.RESET_ALL}")

# класс для работы с DOS функциями
class DOS:
    def shutdown_user(hwid):
        client.update_data(safe_get_hwid(), "shutdown", "True")
        print(f"{Fore.GREEN}User with HWID {hwid} has been marked for shutdown.{Style.RESET_ALL}")


    def restart_user(hwid):
        client.update_data(safe_get_hwid(), "restart", "True")
        print(f"{Fore.GREEN}User with HWID {hwid} has been marked for restart.{Style.RESET_ALL}")


# класс для работы с SCAN функциями
class SCAN:
    @staticmethod
    def scan_users():
        client.scan_base(type="all")

    @staticmethod
    def more(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, known={"name"})
        n = flags.get("name") or input("Enter the name of the user to scan: ")
        hwid = safe_get_hwid()
        if not hwid:
            return
        client.scan_base(name=n, hwid=hwid, type="user")


# условие для проверки, нужно ли перезапустить комп
def res_on():
    status = client.get_status(safe_get_hwid())
    if status and status["restart"]:
        return status["restart"] 
    else:
        return

# условие для проверки, нужно ли выключить комп
def shut_on():
    status = client.get_status(safe_get_hwid())
    if status and status["shutdown"]:
        return status["shutdown"] 
    else:
        return

def tries_have():
    status = client.get_status(safe_get_hwid())
    if status and status["tries_th"]:
       return status["tries_th"] 
    else:
        return
            


# поток, который следит за условиями перезапуска и выключения
def watcher():
    global already_handled
    while not stop_event.is_set():
        try:
            a = res_on()
            b = shut_on()
            c = tries_have()
            if (a or b or c) and not already_handled:
                with threading_lock:
                    already_handled = True
                reasons = []
                if a:reasons.append("a")
                if b:reasons.append("b")
                if c:reasons.append("c")
                handle_res_shut(reasons)
            elif not (a or b or c) and already_handled:
                with threading_lock:
                    already_handled = False   # сброс, чтобы можно было сработать снова
        except Exception as e:
            print(f"{Fore.RED}Error in watcher: {e}{Style.RESET_ALL}")
        stop_event.wait(5)  # проверяем каждые 5 секунд  



def decay_worker():
    while not stop_event.is_set():
        try:
            decrease_dangerous_level()
        except Exception as e:
            print(f"{Fore.RED}Error in decay_worker: {e}{Style.RESET_ALL}")
        stop_event.wait(12)


def decrease_dangerous_level():
    client.update_data(safe_get_hwid(), "d_level_decr")

# функция, которая обрабатывает условия перезапуска и выключения
def handle_res_shut(reasons):
    SIMPLE_COMMANDS.pretty_print(reasons)
    hwid = safe_get_hwid()
    if not hwid:
        return
    if "a" in reasons and "b" in reasons:
        client.update_data(hwid, "shutdown", "False")
        os.system("shutdown /s /t 4")
    elif "a" in reasons:
        client.update_data(hwid, "restart", "False")
        # os.system("shutdown /r /t 0")
    elif "b" in reasons:
        client.update_data(hwid, "shutdown", "False")
        os.system("shutdown /s /t 0")
    if "c" in reasons:

        tries_th = client.get_status(hwid)["tries_th"]
        tries = [t for t in (tries_th or "").split(";") if t]
        strings = [f"{tr} - trying hack your password!!!" for tr in tries]
        SIMPLE_COMMANDS.pretty_warn(strings=strings)
        client.update_data(hwid, "tries_th")
 
 
def update(args=None):
    """Проверяет GitHub Releases и, если есть новая версия, скачивает
    инсталлятор (.exe, собранный Inno Setup) и запускает его. Дальше всю
    работу — замену файлов и перезапуск приложения — делает сам инсталлятор
    через [Run], поэтому никакой ручной возни с переименованием exe и
    батниками не нужно."""

    print(f"{Fore.YELLOW}Checking for updates (current version: {APP_VERSION})...{Style.RESET_ALL}")

    try:
        req = urllib.request.Request(
            GITHUB_API_RELEASES,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "update-checker"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            all_releases = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"{Fore.RED}Could not check for updates: {e}{Style.RESET_ALL}")
        console_start()
        return

    # Отбрасываем черновики и pre-release, среди оставшихся берём релиз с
    # МАКСИМАЛЬНЫМ номером версии по тегу — а не тот, что GitHub считает
    # "latest" (это разные вещи).
    candidates = []
    for r in all_releases:
        tag = r.get("tag_name", "")
        if r.get("draft") or r.get("prerelease"):
            continue
        try:
            parsed = SIMPLE_COMMANDS._parse_version(tag)
        except (ValueError, AttributeError):
            continue
        candidates.append((parsed, r))

    if not candidates:
        print(f"{Fore.RED}No valid published releases found on GitHub.{Style.RESET_ALL}")
        console_start()
        return

    candidates.sort(key=lambda item: item[0])
    _, release = candidates[-1]

    remote_version = release.get("tag_name", "").lstrip("v")
    assets = release.get("assets", [])
    setup_asset = next((a for a in assets if a.get("name", "").endswith("Setup.exe")), None)

    if not remote_version or not setup_asset:
        print(f"{Fore.RED}No release/installer asset found on GitHub.{Style.RESET_ALL}")
        console_start()
        return

    if SIMPLE_COMMANDS._parse_version(remote_version) <= SIMPLE_COMMANDS._parse_version(APP_VERSION):
        print(f"{Fore.GREEN}You are already on the latest version ({APP_VERSION}).{Style.RESET_ALL}")
        console_start()
        return

    print(f"{Fore.YELLOW}New version available: {remote_version} (you have {APP_VERSION}){Style.RESET_ALL}")
    if release.get("body"):
        print(release["body"])

    if not getattr(sys, "frozen", False):
        print(f"{Fore.YELLOW}Running from source — just 'git pull' instead of self-updating.{Style.RESET_ALL}")
        console_start()
        return

    if input("Download and install the update now? (y/n): ").strip().lower() != "y":
        console_start()
        return

    setup_path = os.path.join(tempfile.gettempdir(), "MarkSetup.exe")

    if os.path.exists(setup_path):
        try:
            os.remove(setup_path)
        except OSError:
            pass

    try:
        print(f"{Fore.YELLOW}Downloading installer...{Style.RESET_ALL}")
        urllib.request.urlretrieve(setup_asset["browser_download_url"], setup_path)
    except Exception as e:
        print(f"{Fore.RED}Download failed: {e}{Style.RESET_ALL}")
        console_start()
        return

    print(f"{Fore.GREEN}Updating to {remote_version}. Launching installer...{Style.RESET_ALL}")

    # Обычный subprocess.Popen, БЕЗ ShellExecuteW/"runas" — установщик теперь
    # PrivilegesRequired=lowest, админ ему не нужен, а искусственная элевация
    # только возвращала UAC-запрос и (при запуске от админа) ломала цвета
    # colorama. Флаги breakaway нужны, чтобы установщик пережил наш
    # sys.exit() ниже и не был убит вместе с процессом Job Object'ом
    # PyInstaller-бандла.
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS = 0x00000008
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000

    log_path = os.path.join(tempfile.gettempdir(), "MarkSetup.log")
    # Без /CLOSEAPPLICATIONS и /RESTARTAPPLICATIONS — они бы перебили
    # CloseApplications=no из .iss и снова включили Restart Manager,
    # который не умеет закрывать консольные приложения и роняет установку
    # по таймауту ~30 сек.
    installer_argv = [
        setup_path,
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f"/LOG={log_path}",
    ]
    print(f"{Fore.YELLOW}Installer log will be written to: {log_path}{Style.RESET_ALL}")

    try:
        subprocess.Popen(
            installer_argv,
            cwd=tempfile.gettempdir(),
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB,
            close_fds=True,
        )
    except OSError as e:
        print(f"{Fore.RED}Failed to launch installer: {e}{Style.RESET_ALL}")
        console_start()
        return

    print(f"{Fore.YELLOW}Installer launched. Exiting so it can replace this file...{Style.RESET_ALL}")

    # Restart Manager нам не нужен (CloseApplications=no) — закрываемся сами,
    # сразу же. .iss компенсирует это через InitializeSetup: реально ждёт,
    # пока mark.exe освободит файл (через IsFileLocked), прежде чем начать
    # копирование, и сам запускает новую версию в конце через [Run].
    sys.exit(0)

# функция для запуска консоли и обработки команд
def console_start(args=None):
    commands = {
        "help": SIMPLE_COMMANDS.help_command,
        "exit": SIMPLE_COMMANDS.graceful_exit,
        "clear": SIMPLE_COMMANDS.clear,
        "dos": dos,
        "ghwid": ghwid,
        "scan": scan,
        "chat": chat,
        "osint": osint,
        "cls": SIMPLE_COMMANDS.clear,
        "info": SIMPLE_COMMANDS.info,
        "update": update,
        "export": export,
        "import": importing,
        "": console_start
    }
    command = input(">>>").strip()
    try:
        cmd_s = command.split(maxsplit=3)
        cmd = cmd_s[0]
        args = cmd_s[1:] if len(cmd_s) > 1 else None
        if cmd in commands:
            commands[cmd](args)
        else:
            print(f"{Fore.RED}Unknown command: {command}{Style.RESET_ALL}")
            console_start()
    except Exception:
        console_start()


def osint(args=None):
    flags = SIMPLE_COMMANDS.parse_flags(args, known={"name", "power"})

    name = flags.get("name") or input("Enter user's name: ")
    count_raw = flags.get("power")

    if "power" in flags:
        try:
            count = int(count_raw)
        except (ValueError, TypeError):
            print(f"{Fore.RED}Power must be a number.{Style.RESET_ALL}")
            return

        if count not in (1, 2, 3, 4, 5):
            print(f"{Fore.RED}Power must be between 1 and 5.{Style.RESET_ALL}")
            return
    else:
        count = 1

    client.osint_user(name=name, hwid=safe_get_hwid(), count=count)
    console_start()


def export(args=None):
    flags = SIMPLE_COMMANDS.parse_flags(args, {"file_name"})
    file_name = flags.get("file_name") or "data"
    client.export_import(safe_get_hwid(), "export", file_name)

    console_start()
    

def importing(args=None):
    flags = SIMPLE_COMMANDS.parse_flags(args, {"path"})
    hwid = safe_get_hwid()
    if not hwid:
        return
    path = flags.get("path") or input("Enter a path to importimg file: ").strip()
    if path.split(".")[-1] not in ("json", ):
        print(f"{Fore.RED}You file's format is not allowed!!!{Style.RESET_ALL}")
        console_start()
    with open(path, "+r") as f:
        data = json.load(f)
    client.export_import(hwid, "import", None, data)
    console_start()


# функция для получения HWID пользователя по имени и паролю
def ghwid(args=None):
    hwid = safe_get_hwid()
    if not hwid:
        return
    flags = SIMPLE_COMMANDS.parse_flags(args, known={"name", "pass"})
    name = flags.get("name") or input("Enter user's name: ")
    password = flags.get("pass") or getpass.getpass("Enter user's password: ")
    client.get_hwid_by_pass(hwid, name, password)
    console_start()

# функция для работы с чатом
def chat(args=None):
    raw = input("What you want to do: ").strip() if args is None else " ".join(args)
    parts = raw.split(maxsplit=2)  # команда, имя, сообщение
    leng = len(parts)
    if not parts:
        console_start()
        return

    act = parts[0]

    if act == "send":
        if leng > 1:
            argums = parts[1:]
        else:
            argums = None

        CHAT.send_message(argums)

    elif act == "my":
        if leng > 1:
            argums = parts[1:]
        else:
            argums = None
        CHAT.show_own_messages(argums)

    console_start()


# функция для работы с SCAN
def scan(args=None):
    acts = {
        "users": SCAN.scan_users,
        "more": SCAN.more,
    }
    
    raw = input("What you want to do: ").strip() if args is None else " ".join(args)
    parts = raw.split(maxsplit=1)  # разделяем на команду и остальное
    
    if not parts:
        console_start()
        return
    
    act = parts[0]
    args = parts[1:] if len(parts) > 1 else None
    
    if act in acts:
        if args:
            acts[act](args)
        else:
            acts[act]()
    
    console_start()


# функция для работы с DOS
def dos(args=None):
    flags = SIMPLE_COMMANDS.parse_flags(args, known={"hwid", "act"})
    hwid = flags.get("hwid") or input("Enter the HWID to search for: ") 
    act = flags.get("act") or input("What you want to do with this user: ")
    client.dos_(hwid, act)
    console_start()


# функция для запуска программы и проверки HWID
def start():
    text = text2art("OSINT MASTER", font="small")
    print(Fore.GREEN + text + Style.RESET_ALL)
    id = safe_get_hwid()
    if id is None:  
        return False
    client.start(id, console_start)
    



# главная точка входа в программу
if __name__ == "__main__":
    colorama.init(convert=True, strip=False)
    if getattr(sys, "frozen", False):
        # чистим хвост от предыдущего update() — старый процесс уже закрылся,
        # так что файл теперь можно удалить
        try:
            os.remove(sys.executable + ".old")
        except OSError:
            pass
    watcher_thread = threading.Thread(target=watcher, daemon=True)
    watcher_thread.start()
    decay_thread = threading.Thread(target=decay_worker, daemon=True)
    decay_thread.start()
    try:
        SIMPLE_COMMANDS.set_console_title("OSINT MASTER")
        start()
    except KeyboardInterrupt:
        SIMPLE_COMMANDS.graceful_exit()

    