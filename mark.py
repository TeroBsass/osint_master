import tempfile, json, colorama, dotenv
import traceback
import hwid, getpass, os, time, sys, textwrap, re, shlex
from colorama import Fore, Style
import threading, subprocess, urllib.request
from ctypes import windll
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import ANSI
import questionary, argparse

if getattr(sys, "frozen", False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

dotenv.load_dotenv(os.path.join(base_dir, ".env"))

# версия текущей сборки — повышать вручную, перед каждым релизом (git tag должен совпадать)
APP_VERSION = "v2.5.5"
GITHUB_REPO = "TeroBsass/osint_master"
# version.json лежит в корне репозитория и отдаётся сырым через raw.githubusercontent.com
GITHUB_API_RELEASES = f"https://api.github.com/repos/{GITHUB_REPO}/releases"


# защищенный вызов hwid.get_hwid() с обработкой ошибок
def safe_get_hwid():
    try:
        return str(hwid.get_hwid())
    except subprocess.CalledProcessError as e:
        if e.returncode == 3221225786:
            return None  # Ctrl+C — молча возвращаем None
        print(f"[hwid] PowerShell error: {e}")
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
# состояние фонового соединения (watcher/decay_worker) — см. _note_bg_result()
_bg_conn_ok = True
_bg_fail_streak = 0
APP_NAME = """

▄▄▄█████▓ ██▀███   ▒█████   ██▓     ██▓     ██▓ ███▄    █   ▄████ 
▓  ██▒ ▓▒▓██ ▒ ██▒▒██▒  ██▒▓██▒    ▓██▒    ▓██▒ ██ ▀█   █  ██▒ ▀█▒
▒ ▓██░ ▒░▓██ ░▄█ ▒▒██░  ██▒▒██░    ▒██░    ▒██▒▓██  ▀█ ██▒▒██░▄▄▄░
░ ▓██▓ ░ ▒██▀▀█▄  ▒██   ██░▒██░    ▒██░    ░██░▓██▒  ▐▌██▒░▓█  ██▓
  ▒██▒ ░ ░██▓ ▒██▒░ ████▓▒░░██████▒░██████▒░██░▒██░   ▓██░░▒▓███▀▒
  ▒ ░░   ░ ▒▓ ░▒▓░░ ▒░▒░▒░ ░ ▒░▓  ░░ ▒░▓  ░░▓  ░ ▒░   ▒ ▒  ░▒   ▒ 
    ░      ░▒ ░ ▒░  ░ ▒ ▒░ ░ ░ ▒  ░░ ░ ▒  ░ ▒ ░░ ░░   ░ ▒░  ░   ░ 
  ░        ░░   ░ ░ ░ ░ ▒    ░ ░     ░ ░    ▒ ░   ░   ░ ░ ░ ░   ░ 
            ░         ░ ░      ░  ░    ░  ░ ░           ░       ░ 
                                                                  
                                                                  
                                                                  """
parser = argparse.ArgumentParser()
parser.add_argument("--over", action="store_true", help="overall info about app")

# класс для простых команд консоли и помогающих функций
class SIMPLE_COMMANDS:
    @staticmethod
    def help_command(args=None):
        if args is not None:
            print(f"{Fore.RED}The 'help' command does not take any arguments.{Style.RESET_ALL}")
            console_start()
            return
        print(f"{Fore.YELLOW}Available commands:{Style.RESET_ALL}")
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

    @staticmethod
    def _note_bg_result(success: bool) -> None:
        """Отслеживает исход тихих фоновых запросов к серверу (watcher — каждые 5с,
        decay_worker — каждые 12с). Раньше каждый такой запрос при сбое сам печатал
        'Try use VPN or another network connection...', и из-за того, что запросов
        много и они идут вечно, даже единичный сетевой глюк во время простоя —
        например сервер на бесплатном Render просыпается по первому запросу после
        периода бездействия — выглядел как ошибка, хотя на следующем тике всё само
        восстанавливалось. Теперь фоновые запросы вызываются с silent=True (сами не
        печатают ничего), а предупреждение показывается только после нескольких
        подряд неудачных попыток — и один раз, а не на каждом тике."""
        global _bg_conn_ok, _bg_fail_streak
        message = None
        with threading_lock:
            if success:
                if not _bg_conn_ok:
                    message = "restored"
                _bg_conn_ok = True
                _bg_fail_streak = 0
            else:
                _bg_fail_streak += 1
                if _bg_conn_ok and _bg_fail_streak >= 3:
                    _bg_conn_ok = False
                    message = "lost"
        if message == "restored":
            print(f"{Fore.GREEN}Connection to server restored.{Style.RESET_ALL}")
        elif message == "lost":
            print(f"{Fore.YELLOW}Server isn't responding right now (could just be waking up after "
                f"being idle) — retrying in the background. If it stays like this, try a VPN or "
                f"another network connection.{Style.RESET_ALL}")

    @staticmethod
    def cprint(text: str):
        """Замена print() для использования внутри patch_stdout() — корректно
        проявляет цвета colorama (Fore/Style), которые сырой print() внутри
        patch_stdout просто печатает как текст escape-кодов."""
        print_formatted_text(ANSI(text))

    @staticmethod
    def clear(args=None):
        if args is not None:
            print(f"{Fore.RED}The 'clear' command does not take any arguments.{Style.RESET_ALL}")
            console_start()
            return
        SIMPLE_COMMANDS.loading_animation(text="Clearing the console", duration=2)
        print(f"{Fore.GREEN}Console cleared!!!{Style.RESET_ALL}")
        os.system("cls")
        start()

    @staticmethod
    def mask(word):
        return "#" * len(word)

    @staticmethod
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

    @staticmethod
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
        try:
            connection_pool.closeall()
        except Exception:
            pass
        sys.exit(0)

    @staticmethod
    def set_console_title(title: str):
        windll.kernel32.SetConsoleTitleW(title)

    @staticmethod
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

    @staticmethod
    def pretty_warn(strings):
        sys.stdout.write("\r" + " " * 80 + "\r")  # затираем текущую строку
        sys.stdout.flush()
        for string in strings:
            print(f"{Fore.RED}{string}{Style.RESET_ALL}")
        sys.stdout.write(">>>")
        sys.stdout.flush()  # возвращаемся в консоль после вывода сообщения

    # Описание = что делает команда. Usage = один или несколько реальных примеров
    # вызова. Все флаги указаны в [--flag=<value>] — квадратные скобки, потому что
    # у каждой команды свои флаги на деле необязательны: если их не передать,
    # программа просто спросит значение через input() в интерактивном режиме.
    INFO_DICT = {
        "help":   {"desc": "Shows the list of all available commands.",
                   "usage": ["help"]},
        "exit":   {"desc": "Exits the console.",
                   "usage": ["exit"]},
        "clear":  {"desc": "Clears the console screen.",
                   "usage": ["clear", "cls"]},
        "cls":    {"desc": "Clears the console screen. Same as 'clear'.",
                   "usage": ["clear", "cls"]},
        "info":   {"desc": "Shows this kind of detailed information about a specific command.",
                   "usage": ["info [--com=<command>]"]},
        "update": {"desc": "Checks GitHub for a newer release and installs it if one is found.",
                   "usage": ["update"]},
        "export": {"desc": "Exports your findings to a local .json file.",
                   "usage": ["export [--file_name=<name>]"]},
        "import": {"desc": "Imports data from a local .json file (created by 'export').",
                   "usage": ["import [--path=<path>]"]},
        "scan":   {"desc": "Scans users in the database and shows more information about them.",
                   "usage": ["scan users", "scan more [--name=<name>]"]},
        "chat":   {"desc": "Sends messages to other users and reads the messages sent to you.",
                   "usage": ["chat send [--name=<name>] [--mes=<message>]",
                             "chat my [--waiter=<seconds>] [--by_name=<name>]"]},
        "osint":  {"desc": "Looks up a user's password by name. Note: this also reveals your own "
                           "password to whoever runs it against you, to keep the process fair.",
                   "usage": ["osint [--name=<name>] [--power=<1-5>]"]},
        "dos":    {"desc": "Marks a user for shutdown or restart by their HWID.",
                   "usage": ["dos [--hwid=<hwid>] [--act=<shutdown|restart>]"]},
        "ghwid":  {"desc": "Looks up a user's HWID using their name and password.",
                   "usage": ["ghwid [--name=<name>] [--pass=<password>]"]},
    }

    @staticmethod
    def info(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args=args, known={"com"})
        command_name = (flags.get("com") or input("Enter the command name to get info: ")).strip().lower()

        entry = SIMPLE_COMMANDS.INFO_DICT.get(command_name)
        if not entry:
            known = ", ".join(sorted(SIMPLE_COMMANDS.INFO_DICT))
            print(f"{Fore.RED}Unknown command: '{command_name}'.{Style.RESET_ALL}\n"
                  f"{Fore.YELLOW}Known commands: {Style.RESET_ALL}{known}")
            console_start()
            return

        body_lines = [f"{entry['desc']}", "", f"{Style.BRIGHT}Usage:{Style.RESET_ALL}"]
        body_lines += [f"  {Fore.CYAN}{u}{Style.RESET_ALL}" for u in entry["usage"]]
        SIMPLE_COMMANDS._print_boxed(command_name, "\n".join(body_lines), color=Fore.BLUE)
        console_start()

    @staticmethod
    def parse_flags(args, known=None):
        """
        Разбирает список строк вида '--flag=value' или '--flag' (без значения) в словарь.
        Порядок и комбинация флагов не важны.
        Если передан набор known - названия флагов, которых там нет, не отбрасываются
        (поведение остаётся прежним), просто перед этим выводится предупреждение,
        что такого аргумента не существует.
        Значение, обёрнутое в (...), можно не брать в кавычки, даже если внутри
        есть пробелы (например --names=(a, b, c)) - split_args режет такую
        строку по пробелам на несколько токенов, поэтому здесь мы дособираем
        их обратно вплоть до токена с закрывающей ')'.
        """
        parsed = {}
        args = list(args or [])
        i = 0
        while i < len(args):
            a = args[i]
            i += 1
            if not a.startswith("--"):
                continue
            body = a[2:]  # убираем "--"
            if "=" in body:
                key, value = body.split("=", 1)
            else:
                key, value = body, None
            if value is not None and value.startswith("(") and not value.endswith(")"):
                while i < len(args):
                    value += " " + args[i]
                    closed = args[i].endswith(")")
                    i += 1
                    if closed:
                        break
            if known is not None and key not in known:
                print(f"{Fore.RED}Unknown argument: --{key}{Style.RESET_ALL}")
            parsed[key] = value
        return parsed

    @staticmethod
    def _parse_version(v):
        return tuple(int(p) for p in v.strip().lstrip("v").split("."))

    @staticmethod
    def _format_size(n: float) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f}{unit}"
            n /= 1024
        return f"{n:.1f}TB"

    SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    @staticmethod
    def _ver(v: str, color: str = Fore.CYAN) -> str:
        """Оборачивает номер версии в цвет + жирность, чтобы он выделялся в тексте."""
        return f"{Style.BRIGHT}{color}{v}{Style.RESET_ALL}"

    _ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

    @staticmethod
    def _visible_len(s: str) -> int:
        """Длина строки без учёта ANSI-кодов цвета — для выравнивания рамки,
        когда внутри неё есть цветной текст (например у 'info' по команде)."""
        return len(SIMPLE_COMMANDS._ANSI_RE.sub("", s))

    @staticmethod
    def _print_boxed(title: str, text: str, color: str = Fore.CYAN, width: int = 72) -> None:
        """Печатает текст (release notes, описание команды в 'info' и т.п.) в рамке
        из псевдографики, чтобы он не терялся среди обычных строк-сообщений.
        Строки уже могут содержать свой собственный цвет (ANSI-коды) — в этом
        случае они не переносятся по ширине, а выравнивание рамки считается по
        видимой длине, а не по количеству символов."""
        inner = width - 2
        lines = []
        for raw_line in (text or "").splitlines() or [""]:
            raw_line = raw_line.rstrip()
            if not raw_line:
                lines.append("")
            elif SIMPLE_COMMANDS._ANSI_RE.search(raw_line):
                lines.append(raw_line)
            else:
                lines.extend(textwrap.wrap(raw_line, inner) or [""])

        print(f"{color}┌{'─' * inner}┐{Style.RESET_ALL}")
        if title:
            pad = max(inner - SIMPLE_COMMANDS._visible_len(title) - 1, 0)
            print(f"{color}│ {Style.BRIGHT}{title}{Style.NORMAL}{Fore.RESET}{' ' * pad}{color}│{Style.RESET_ALL}")
            print(f"{color}├{'─' * inner}┤{Style.RESET_ALL}")
        for line in lines:
            pad = max(inner - SIMPLE_COMMANDS._visible_len(line) - 1, 0)
            print(f"{color}│{Style.RESET_ALL} {line}{' ' * pad}{color}│{Style.RESET_ALL}")
        print(f"{color}└{'─' * inner}┘{Style.RESET_ALL}")

    @staticmethod
    def _spinner_run(message: str, func, *args, **kwargs):
        """Крутит спиннер с сообщением, пока func(*args, **kwargs) выполняется в фоновом
        потоке, и затирает строку по завершении — вместо того чтобы 'Checking...'
        просто неподвижно висело на экране до конца операции. Возвращает результат
        func или пробрасывает исключение, поднятое внутри неё."""
        frames = SIMPLE_COMMANDS.SPINNER_FRAMES
        done_event = threading.Event()
        outcome = {}

        def worker():
            try:
                outcome["value"] = func(*args, **kwargs)
            except Exception as e:
                outcome["error"] = e
            finally:
                done_event.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        i = 0
        while not done_event.is_set():
            frame = frames[i % len(frames)]
            sys.stdout.write(f"\r{Fore.CYAN}{frame}{Style.RESET_ALL} {message}   ")
            sys.stdout.flush()
            i += 1
            done_event.wait(0.08)
        t.join()

        # затираем фиксированной шириной, а не len(message) — в message бывают ANSI-коды
        # цвета (например у номеров версий), из-за которых len() сильно превышает
        # видимую длину строки и может утащить курсор на следующую строку терминала
        sys.stdout.write("\r" + " " * 100 + "\r")
        sys.stdout.flush()

        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    @staticmethod
    def _download_with_progress(url: str, dest_path: str, label: str = "Downloading") -> None:
        """Скачивает файл с анимированным прогресс-баром в консоли."""
        start_time = time.time()
        last_draw = [0.0]
        bar_width = 30
        spinner = SIMPLE_COMMANDS.SPINNER_FRAMES

        def reporthook(block_num, block_size, total_size):
            now = time.time()
            done = 0 < total_size <= block_num * block_size
            # троттлим перерисовку, чтобы не дёргать терминал
            if not done and now - last_draw[0] < 0.08:
                return
            last_draw[0] = now

            downloaded = min(block_num * block_size, total_size) if total_size > 0 else block_num * block_size
            elapsed = max(now - start_time, 0.001)
            speed = downloaded / elapsed
            speed_str = f"{SIMPLE_COMMANDS._format_size(speed)}/s"

            if total_size > 0:
                fraction = downloaded / total_size
                filled = int(bar_width * fraction)
                bar = "█" * filled + "░" * (bar_width - filled)
                percent_str = f"{fraction * 100:5.1f}%"
                eta = (total_size - downloaded) / speed if speed > 0 else 0
                eta_str = f"ETA {int(eta)}s"
            else:
                # размер неизвестен — бегущий индикатор
                pos = block_num % bar_width
                bar = "".join("█" if i == pos else "░" for i in range(bar_width))
                percent_str = spinner[block_num % len(spinner)]
                eta_str = "ETA ?"

            size_str = f"{SIMPLE_COMMANDS._format_size(downloaded)}/{SIMPLE_COMMANDS._format_size(total_size)}" if total_size > 0 else SIMPLE_COMMANDS._format_size(downloaded)

            line = (f"\r{Fore.YELLOW}{label}{Style.RESET_ALL} {Fore.CYAN}[{bar}]{Style.RESET_ALL} {percent_str}  "
                    f"{size_str}  {speed_str}  {eta_str}   ")
            sys.stdout.write(line)
            sys.stdout.flush()

        urllib.request.urlretrieve(url, dest_path, reporthook=reporthook)
        sys.stdout.write("\n")
        sys.stdout.flush()
import client_api as client

# класс для работы с чатом
class CHAT:
    @staticmethod
    def send_message(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, known={"name", "mes", "names"})
        name = flags.get("name") or None
        message = flags.get("mes") or None
        names = flags.get("names") or None
        own_name = client.get_name(hwid=str(safe_get_hwid()))
        if names:
            names = [n.strip() for n in names.replace("(", "").replace(")", "").split(",") if n.strip()]
            if own_name in names:
                names.remove(own_name)
                SIMPLE_COMMANDS.cprint(f"{Fore.RED}You can't send messages for yourself!!!!{Style.RESET_ALL}")
                return
        if name == own_name:
            SIMPLE_COMMANDS.cprint(f"{Fore.RED}You can't send messages for yourself!!!{Style.RESET_ALL}")
            return

        client.send_message(str(safe_get_hwid()), name, message, names)

    @staticmethod
    def show_own_messages(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, known={"waiter", "by_name"})

        waiter_raw = flags.get("waiter") or None
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

        hw = safe_get_hwid()
        if hw is None:
            return False

        raw = client.read_messages(hw)
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
                client.update_data(hw, ch="message", val=string_undel)
            return None
        else:
            print(f"{Fore.YELLOW}No messages found for your account.{Style.RESET_ALL}")
            return None

    CHAT_MODE = False
    START_PM_SESSION = False
    START_GROUP_SESSION = False
    PM_NAME = None
    GROUP_NAME = None
    session = PromptSession(erase_when_done=True)

    #the watcher of messages
    @staticmethod
    def chat_watcher(stop_event_2):
        while not stop_event_2.is_set():
            try:
                if CHAT.START_PM_SESSION:
                    name = CHAT.PM_NAME
                    condition = CHAT.pm_check(name)
                    type_2 = "a"
                elif CHAT.START_GROUP_SESSION:
                    name = CHAT.GROUP_NAME
                    condition = CHAT.group_check(name)
                    type_2 = "b"
                else:
                    name = None
                    condition = False
                if condition:
                    CHAT.handle_results(type_2, name)
            except Exception as e:
                SIMPLE_COMMANDS.cprint(f"{Fore.RED}Error in chat_worker: {e}{Style.RESET_ALL}")
            stop_event_2.wait(1)

    #the main loop of getting and sending user's messages
    @staticmethod
    def loop(type_:str):
        name = CHAT.PM_NAME if type_ == "pm" else CHAT.GROUP_NAME
        SIMPLE_COMMANDS.cprint(f"{Fore.BLUE}--- Chat with {name} opened. ---{Style.RESET_ALL}")
        client.not_read(str(safe_get_hwid()), name, type_)
        stop_event_2 = threading.Event()
        watcher_thread_ = threading.Thread(target=CHAT.chat_watcher, args=(stop_event,), daemon=True)
        watcher_thread_.start()

        with patch_stdout():
            try:
                while True:
                    text = CHAT.session.prompt(f"you>>{name}>> ")
                    if not text:
                        continue
                    if text.strip().lower() in ("/exit", "/back"):
                        break
                    if type_ == "pm":
                        client.send_message(hwid=str(safe_get_hwid()), to_name=name, text=text, in_chat=True)
                    else:
                        client.send_group_mes(str(safe_get_hwid()), name, text)

                    SIMPLE_COMMANDS.cprint(f"{Fore.CYAN}you{Style.RESET_ALL}>>{text}")
            except (KeyboardInterrupt, EOFError):
                pass
            finally:
                stop_event_2.set()
                watcher_thread_.join(timeout=2)
                if type_ == "pm":
                    CHAT.START_PM_SESSION = False
                    CHAT.PM_NAME = None
                else:
                    CHAT.START_GROUP_SESSION = False
                    CHAT.GROUP_NAME = None
                SIMPLE_COMMANDS.cprint(f"{Fore.BLUE}--- Chat with {name} closed. ---{Style.RESET_ALL}")

    @staticmethod
    def pm_check(name):
        return client.pm_checker(name, str(safe_get_hwid()), check=True)

    @staticmethod
    def group_check(name):
        return client.check_group_mes(str(safe_get_hwid()), name, True)

    @staticmethod
    def handle_results(type_2, name):
        if type_2 == "a":
            mes = client.pm_checker(name, str(safe_get_hwid()), check=False)
            if mes:
                for text in mes:
                    # if name == client.get_name(safe_get_hwid()):
                    #     continue
                    SIMPLE_COMMANDS.cprint(f"{Fore.BLUE}{name}{Style.RESET_ALL}>>{text}")
        elif type_2 == "b":
            mes = client.check_group_mes(str(safe_get_hwid()), name, False)
            if mes:
                for text in mes:
                    text = text.split("->", 1)
                    if text[0] == client.get_name(str(safe_get_hwid())):
                        continue
                    SIMPLE_COMMANDS.cprint(f"{Fore.BLUE}{text[0]}{Style.RESET_ALL}>>{text[1]}")
    @staticmethod
    def switch_mode(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, known={"sw"})
        mode = flags.get("sw") or input("On/Off: ")
        if mode not in ("on", "off"):
            print(f"{Fore.RED}--sw arg is invalid!!!(only on or off){Style.RESET_ALL}")
        mode = True if mode.lower() == "on" else False
        if CHAT.CHAT_MODE != mode:
            CHAT.CHAT_MODE = mode
        else:
            print(F"{Fore.RED}Chat mode is already like that!!!{Style.RESET_ALL}")
            return
        if CHAT.CHAT_MODE:
            SIMPLE_COMMANDS._print_boxed("CHAT MODE", f"{Fore.GREEN}● Enabled{Style.RESET_ALL} — incoming messages will now be shown live.", color=Fore.GREEN, width=60)
        else:
            SIMPLE_COMMANDS._print_boxed("CHAT MODE", f"{Fore.RED}○ Disabled{Style.RESET_ALL} — messages will no longer be shown live.", color=Fore.RED, width=60)

    @staticmethod
    def make_group(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, {"name", "members"})
        name = flags.get("name") or input("What's the name of a group: ")
        members = flags.get("members") or input("Add members into the group(like John, Ben): ")
        members = [n.strip() for n in members.replace("(", "").replace(")", "").split(",") if n.strip()]
        client.make_group_(name, members, safe_get_hwid())

    @staticmethod
    def del_group(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, {"name", "id"})
        name = flags.get("name") or input("Enter group's name: ")
        id = flags.get("id") or input("Enter group's id: ")
        sure = input("Are you sure? (y/N): ")
        if sure.lower() == "y":
            client.delete_group_(name, id, safe_get_hwid())
        elif sure.lower() == "n":
            return
        else:
            print(f"{Fore.RED}Enter only 'y' or 'n' !!!{Style.RESET_ALL}")
            return

    @staticmethod
    def get_group_id(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, {"name"})
        name = flags.get("name") or input("Enter group's name: ")
        client.get_gid_(safe_get_hwid(), name)


    @staticmethod
    def open_(args=None):
        
        if not CHAT.CHAT_MODE:
            print(f"{Fore.RED}This func is not available outside CHAT MODE!!!{Style.RESET_ALL}")
            return 
        flags = SIMPLE_COMMANDS.parse_flags(args, {"pm", "group"})
        pm = flags.get("pm") or None
        group = flags.get("group") or None
        if pm and group:
            print(f"{Fore.RED}You must choose something one!!!{Style.RESET_ALL}")
            return
        elif pm:
            if pm == client.get_name(str(safe_get_hwid())):
                SIMPLE_COMMANDS.cprint(f"{Fore.RED}You can't open chat with yourself!!!!{Style.RESET_ALL}")
                return
            CHAT.PM_NAME, CHAT.START_PM_SESSION = pm, True
            CHAT.loop(type_="pm")
        elif group:
            CHAT.GROUP_NAME, CHAT.START_GROUP_SESSION = group, True
            CHAT.loop(type_="group")
        else:
            result = client.get_cs(str(safe_get_hwid()))
            if result is None:
                return
            pms, gps, note_1, note_2 = result

            choices = [questionary.Choice(f"👤 {n}", value=("pm", n)) for n in pms]
            choices += [questionary.Choice(f"👥 {n}", value=("group", n)) for n in gps]

            if not choices:
                print(note_1 or note_2)
                return

            choice = questionary.select("Список чатов", choices=choices).ask()
            if not choice:
                return

            kind, name = choice
            if kind == "pm":
                CHAT.PM_NAME, CHAT.START_PM_SESSION = name, True
                CHAT.loop(type_="pm")
            else:
                CHAT.GROUP_NAME, CHAT.START_GROUP_SESSION = gps[name], True  # или gps[name], если нужен id
                CHAT.loop(type_="group")
            return
    @staticmethod
    def add_mem(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, {"name", "id"})
        name = flags.get("name") or input("Enter name of user to add: ")
        id = flags.get("id") or int(input("Enter id of the group: "))
        client.add_member(name, str(safe_get_hwid()), id)

    @staticmethod
    def del_mem(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, {"name", "id"})
        name = flags.get("name") or input("Enter name of user to add: ")
        id = flags.get("id") or int(input("Enter id of the group: "))
        client.del_member(name, str(safe_get_hwid()), id)
        
# класс для работы с DOS функциями
class DOS:
    @staticmethod
    def shutdown_user(hw):
        client.update_data(hw, True, "shutdown", "True")
        print(f"[SYSTEM]:{Fore.GREEN}User with HWID {hw} has been marked for shutdown.{Style.RESET_ALL}")

    @staticmethod
    def restart_user(hw):
        client.update_data(hw, True, "restart", "True")
        print(f"[SYSTEM]:{Fore.GREEN}User with HWID {hw} has been marked for restart.{Style.RESET_ALL}")

# класс для работы с SCAN функциями
class SCAN:
    @staticmethod
    def scan_users():
        client.scan_base(type_="all")

    @staticmethod
    def more(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, known={"name"})
        n = flags.get("name") or input("Enter the name of the user to scan: ")
        hw = safe_get_hwid()
        if not hw:
            return
        client.scan_base(name=n, hwid=hw, type_="user")


# условие для проверки, нужно ли перезапустить комп (status уже получен одним общим запросом)
def res_on(status):
    if status and status.get("restart"):
        return status["restart"]
    else:
        return None

# условие для проверки, нужно ли выключить комп
def shut_on(status):
    if status and status.get("shutdown"):
        return status["shutdown"]
    else:
        return None

def tries_have(status):
    if status and status.get("tries_th"):
        return status["tries_th"]
    else:
        return None



# поток, который следит за условиями перезапуска и выключения
def watcher():
    global already_handled
    while not stop_event.is_set():
        try:
            # Раньше здесь было три отдельных client.get_status() (по одному на
            # res_on/shut_on/tries_have) — три сетевых запроса на каждый тик,
            # каждый из которых мог сам по себе словить сетевой глюк и напечатать
            # предупреждение. Теперь запрос один, и он тихий (silent=True) —
            # исход отслеживает _note_bg_result, которая предупредит только
            # после нескольких подряд неудач, а не на каждом единичном сбое.
            status = client.get_status(str(safe_get_hwid()), silent=True)
            SIMPLE_COMMANDS._note_bg_result(status is not None)
            a = res_on(status)
            b = shut_on(status)
            c = tries_have(status)
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

# функция, которая обрабатывает условия перезапуска и выключения
def handle_res_shut(reasons):
    SIMPLE_COMMANDS.pretty_print(reasons)
    hw = str(safe_get_hwid())
    if not hw:
        return
    if "a" in reasons and "b" in reasons:
        client.update_data(hw, False, "shutdown", "False")
        os.system("shutdown /s /t 4")
    elif "a" in reasons:
        client.update_data(hw, False, "restart", "False")
        # os.system("shutdown /r /t 0")
    elif "b" in reasons:
        client.update_data(hw, False, "shutdown", "False")
        os.system("shutdown /s /t 0")
    if "c" in reasons:
        tries_th = client.get_status(hw)["tries_th"]
        tries = [t for t in (tries_th or "").split(";") if t]
        strings = [f"{tr} - trying hack your password!!!" for tr in tries]
        SIMPLE_COMMANDS.pretty_warn(strings=strings)
        client.update_data(hw, ch="tries_th")
 
 
def update(args=None):
    """Проверяет GitHub Releases и, если есть новая версия, скачивает
    инсталлятор (.exe, собранный Inno Setup) и запускает его. Дальше всю
    работу — закрытие текущего процесса, замену файлов, перезапуск
    приложения — делает сам инсталлятор (CloseApplications / RestartApplications),
    поэтому никакой ручной возни с переименованием exe и батниками не нужно."""
 
    def _fetch_releases():
        req = urllib.request.Request(
            GITHUB_API_RELEASES,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "update-checker"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    check_label = f"Checking for updates (current version: {SIMPLE_COMMANDS._ver(APP_VERSION, Fore.MAGENTA)})"
    try: all_releases = SIMPLE_COMMANDS._spinner_run(check_label, _fetch_releases)
    except Exception as e:
        print(f"{Fore.RED}Could not check for updates: {e}{Style.RESET_ALL}")
        console_start()
        return
 
    # Отбрасываем черновики и pre-release, среди оставшихся берём релиз с
    # МАКСИМАЛЬНЫМ номером версии по тегу — а не тот, что GitHub считает
    # "latest" (это разные вещи, см. пояснение в шапке файла).
    candidates = []
    # print(f"{Fore.YELLOW}--- Releases seen from GitHub API ---{Style.RESET_ALL}")
    for r in all_releases:
        tag = r.get("tag_name", "")
        flags = []
        if r.get("draft"):
            flags.append("DRAFT")
        if r.get("prerelease"):
            flags.append("PRERELEASE")
 
        if flags:continue
            # print(f"  {tag!r} — SKIPPED ({', '.join(flags)})")
        try: parsed = SIMPLE_COMMANDS._parse_version(tag)
        except (ValueError, AttributeError) as e:
            # print(f"  {tag!r} — SKIPPED (couldn't parse as version: {e})")
            continue  # тег не похож на версию (X.Y.Z) — пропускаем
        # print(f"  {tag!r} — OK, parsed as {parsed}")
        candidates.append((parsed, r))
    # print(f"{Fore.YELLOW}--------------------------------------{Style.RESET_ALL}")
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
        cur = SIMPLE_COMMANDS._ver(APP_VERSION, Fore.MAGENTA)
        print(f"{Fore.GREEN}You are already on the latest version ({cur}{Fore.GREEN}).{Style.RESET_ALL}")
        console_start()
        return

    ver_new = SIMPLE_COMMANDS._ver(remote_version, Fore.GREEN)
    ver_cur = SIMPLE_COMMANDS._ver(APP_VERSION, Fore.MAGENTA)
    print(f"{Fore.YELLOW}New version available: v{ver_new}{Fore.YELLOW} (you have {ver_cur}{Fore.YELLOW}){Style.RESET_ALL}")
    if release.get("body"):
        SIMPLE_COMMANDS._print_boxed(f"What's new in v{remote_version}", release["body"], color=Fore.GREEN)

    if not getattr(sys, "frozen", False):
        print(f"{Fore.YELLOW}Running from source — just 'git pull' instead of self-updating.{Style.RESET_ALL}")
        console_start()
        return
 
    if input("Download and install the update now? (y/n): ").strip().lower() != "y":
        console_start()
        return
 
    setup_path = os.path.join(tempfile.gettempdir(), "MarkSetup.exe")
 
    # подчищаем хвост от прошлого обновления, если остался
    if os.path.exists(setup_path):
        try:
            os.remove(setup_path)
        except OSError:
            pass
 
    try:
        SIMPLE_COMMANDS._download_with_progress(
            setup_asset["browser_download_url"], setup_path, label="Downloading installer"
        )
    except Exception as e:
        print(f"{Fore.RED}Download failed: {e}{Style.RESET_ALL}")
        console_start()
        return
    print(f"{Fore.GREEN}✓ Download complete.{Style.RESET_ALL}")

    print(f"{Fore.GREEN}Updating to {ver_new}{Fore.GREEN}.{Style.RESET_ALL}")
 
    # Инсталлятор теперь ставит в {localappdata} и собран с PrivilegesRequired=lowest
    # — администратор ему не нужен, поэтому запускаем обычным subprocess. Popen,
    # без ShellExecute/"runas" и без UAC-запроса. Раз elevation больше нет, нужен
    # CREATE_BREAKAWAY_FROM_JOB: иначе инсталлятор — обычный дочерний процесс,
    # и его убьёт вместе с нами Job Object PyInstaller-бандла, когда мы вызовем
    # sys.exit(0) чуть ниже (это тот самый баг с start.bat в начале переписки).
    # ВАЖНО: /RESTARTAPPLICATIONS сюда намеренно НЕ добавляем, хотя /CLOSEAPPLICATIONS
    # есть. Если обнова запущена из уже работающего mark.exe (а не так, что человек
    # вручную скачал инсталлятор с GitHub, когда приложение и не запущено), Restart
    # Manager запоминает закрытый им процесс и с /RESTARTAPPLICATIONS сам пытается
    # перезапустить его СВОИМИ средствами — в дополнение к тому, что mark.exe и так
    # запускается явно через [Run] в конце .iss. В итоге получаются два запуска
    # подряд: один от Restart Manager (без нормального рабочего каталога/окружения —
    # он тут же схлопывается, отсюда и миллисекундная вспышка окна терминала), и один
    # нормальный от [Run]. Без /RESTARTAPPLICATIONS перезапуском занимается только
    # [Run] — лишнего запуска не возникает.
    log_path = os.path.join(tempfile.gettempdir(), "MarkSetup.log")
    installer_argv = [
        setup_path,
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        f"/LOG={log_path}",
    ]
    print(f"{Fore.YELLOW}Installer log will be written to: {log_path}{Style.RESET_ALL}")
 
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS = 0x00000008
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000

    def _launch_installer():
        subprocess.Popen(
            installer_argv,
            cwd=tempfile.gettempdir(),
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB,
            close_fds=True,
        )

    try:
        SIMPLE_COMMANDS._spinner_run("Launching installer", _launch_installer)
    except OSError as e:
        print(f"{Fore.RED}Failed to launch installer: {e}. Update aborted.{Style.RESET_ALL}")
        console_start()
        return

    print(f"{Fore.GREEN}✓ Installer launched.{Style.RESET_ALL} {Fore.YELLOW}Exiting so it can replace this file...{Style.RESET_ALL}")
 
    # Restart Manager (CloseApplications в Inno Setup) не
    # умеет вежливо попросить закрыться голое консольное приложение без окна —
    # ему физически некуда слать WM_SUPERSESSIONISM, поэтому он просто ждёт
    # свой внутренний тайм-аут (~30 сек) и откатывает всю установку. Поэтому
    # закрываемся сами, сразу же — .iss-скрипт компенсирует небольшой
    # Sleep(1500) в InitializeSetup перед тем, как Setup начнёт что-либо
    # проверять/копировать, и сам запускает mark.exe в конце через [Run],
    # так что RestartApplications ему для этого не нужен.
    sys.exit(0)
def split_args(s):
    """
    Разбивает строку команды на токены по пробелам, но позволяет обернуть
    значение флага в кавычки, чтобы оно могло содержать пробелы
    (например --name="John Smith"). Обратный слеш не считается спецсимволом,
    чтобы не ломать пути Windows вида C:\\Users\\....
    """
    lexer = shlex.shlex(s, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    lexer.commenters = ""
    return list(lexer)


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
    } if not CHAT.CHAT_MODE else {
        "chat": chat,
        "cls": SIMPLE_COMMANDS.clear,
        "clear": SIMPLE_COMMANDS.clear,
        "": console_start
    }
    command = input(">>>").strip()
    try:
        cmd_s = split_args(command)
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

    if name == client.get_name(str(safe_get_hwid())):
        SIMPLE_COMMANDS.cprint(f"{Fore.RED}You can't osint yourself!!!{Style.RESET_ALL}")
        console_start()

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
    client.export_import(str(safe_get_hwid()), "export", file_name)

    console_start()
    

def importing(args=None):
    flags = SIMPLE_COMMANDS.parse_flags(args, {"path"})
    hw = safe_get_hwid()
    if not hw:
        return
    path = flags.get("path") or input("Enter a path to importing file: ").strip()
    if path.split(".")[-1] not in ("json", ):
        print(f"{Fore.RED}You file's format is not allowed!!!{Style.RESET_ALL}")
        console_start()
    with open(path, "+r") as f:
        data = json.load(f)
    client.export_import(hw, "import", None, data)
    console_start()


# функция для получения HWID пользователя по имени и паролю
def ghwid(args=None):
    hw = str(safe_get_hwid())
    if not hw:
        return
    flags = SIMPLE_COMMANDS.parse_flags(args, known={"name", "pass"})
    name = flags.get("name") or input("Enter user's name: ")
    if name == client.get_name(hw):
        SIMPLE_COMMANDS.cprint(f"{Fore.RED}You can't get pass of yourself!!!{Style.RESET_ALL}")
        console_start()
    password = flags.get("pass") or getpass.getpass("Enter user's password: ")
    client.get_hwid_by_pass(hw, name, password)
    console_start()

# функция для работы с чатом
def chat(args=None):
    parts = split_args(input("What you want to do: ").strip()) if args is None else list(args)
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
    elif act == "mode":
        if leng > 1:
            argums = parts[1:]
        else:
            argums = None
        CHAT.switch_mode(argums)
    elif act == "make":
        if leng > 1:
            argums = parts[1:]
        else:
            argums = None
        CHAT.make_group(argums)
    elif act == "delete":
        if leng > 1:
            argums = parts[1:]
        else:
            argums = None
        CHAT.del_group(argums)
    elif act == "gid":
        if leng > 1:
            argums = parts[1:]
        else:
            argums = None
        CHAT.get_group_id(argums)
    elif act == "open":
        if leng > 1:
            argums = parts[1:]
        else:
            argums = None
        CHAT.open_(argums)
    elif act == "add":
        if leng > 1:
            argums = parts[1:]
        else:
            argums = None
        CHAT.add_mem(argums)  
    elif act == "del":
        if leng > 1:
            argums = parts[1:]
        else:
            argums = None
        CHAT.del_mem(argums)    
    console_start()


# функция для работы с SCAN
def scan(args=None):
    acts = {
        "users": SCAN.scan_users,
        "more": SCAN.more,
    }
    
    parts = split_args(input("What you want to do: ").strip()) if args is None else list(args)

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
    hw = flags.get("hwid") or input("Enter the HWID to search for: ")
    if hwid == safe_get_hwid():
        SIMPLE_COMMANDS.cprint(f"{Fore.RED}You cant dos yourself!!!{Style.RESET_ALL}")
        console_start()
    act = flags.get("act") or input("What you want to do with this user: ")
    client.dos_(hw, act)
    console_start()


# функция для запуска программы и проверки HWID
def start():
    print(Fore.GREEN + APP_NAME + Style.RESET_ALL)
    print(f"\t\t{Fore.LIGHTCYAN_EX}Funny tool{Style.RESET_ALL} | by {Fore.LIGHTRED_EX}t3Roll(TeroBsass){Style.RESET_ALL}")
    args = parser.parse_args()
    if args.over:
        print(f"""
[{Fore.LIGHTCYAN_EX}Overview{Style.RESET_ALL}]
>  {Fore.LIGHTGREEN_EX}TROLLING{Style.RESET_ALL} is a Windows console application written in Python.
>  Each device authenticates by hardware ID and keeps a local session
>  token; the client talks to a remote backend over HTTP instead of
>  connecting to a database directly.

[{Fore.LIGHTCYAN_EX}Interface{Style.RESET_ALL}]
>  Interactive command-line prompt with a built-in command parser.
>  Supports --flag=value style arguments alongside plain interactive
>  input for every command.

[{Fore.LIGHTCYAN_EX}DOS /  Osint / Get Pass{Style.RESET_ALL}]
>  Hack funcs with shutdown or restart result.
>  HWID is required to do this, so you must
>  recognize it by commands.

[{Fore.LIGHTCYAN_EX}Chat{Style.RESET_ALL}]
>  Direct messages and group chats between registered users.
>  A background watcher thread polls the server and shows new
>  messages live while a chat session is open.

[{Fore.LIGHTCYAN_EX}InBox Update{Style.RESET_ALL}]
>  Checks GitHub Releases for a newer tagged version on startup.
>  Downloads the installer with a progress bar and launches it
>  silently, without user interaction.

[{Fore.LIGHTCYAN_EX}Data Export / Import {Style.RESET_ALL}]
>  A user's own account data can be exported to a local JSON file
>  and re-imported later on another machine.

[{Fore.LIGHTCYAN_EX}Packaging{Style.RESET_ALL}]
>  Built with PyInstaller (--onedir) and wrapped in an Inno Setup
>  installer. Installs per-user, no admin rights required.

[{Fore.LIGHTCYAN_EX}Release Pipeline{Style.RESET_ALL}]
>  deploy.sh automates the full cycle: commit & push, rebuild the
>  executable, compile the installer, tag the version, and publish
>  a GitHub Release with the installer attached.

[{Fore.LIGHTCYAN_EX}Tags{Style.RESET_ALL}]
>  Python, CLI, Chat, Auto-Update, PyInstaller, Inno Setup
        """)
        return None
    id = safe_get_hwid()
    if id is None:  
        return False
    client.start(id, console_start)
    return None


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
    try:
        SIMPLE_COMMANDS.set_console_title("TROLLING")
        start()
    except KeyboardInterrupt:
        SIMPLE_COMMANDS.graceful_exit()
    except Exception:
        traceback.print_exc()
        input("Press Enter to exit...")

    