from dis import pretty_flags
import json
from turtle import title
from typing import Type
import hwid, getpass, psycopg2, os, time, sys, random
from art import text2art
from colorama import Fore, Style
import dotenv
from psycopg2 import pool
import threading, subprocess, ctypes, hashlib, urllib.request
from ctypes import wintypes
dotenv.load_dotenv()

# версия текущей сборки — бампать вручную перед каждым релизом (git tag должен совпадать)
APP_VERSION = "1.3.7"
GITHUB_REPO = "TeroBsass/osint_master"
# version.json лежит в корне репозитория и отдаётся сырым через raw.githubusercontent.com
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


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
DATABASE_URL = os.environ["DATABASE_URL"]
connection_pool = None


# класс для работы с базой данных
class DB:
    def init_pool():
        global connection_pool
        connection_pool = psycopg2.pool.ThreadedConnectionPool(1, 15, DATABASE_URL, connect_timeout=5)


    def db_connect():
        return connection_pool.getconn()

    def release_connection(conn, broken=False):
        try:
            if broken:
                # соединение мёртвое — выкидываем из пула физически
                connection_pool.putconn(conn, close=True)
            else:
                connection_pool.putconn(conn)
        except Exception as e:
            pass

    def database():
        conn = DB.db_connect()
        broken = False
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        password TEXT NOT NULL,
                        hwid TEXT UNIQUE,
                        restart BOOLEAN DEFAULT FALSE,
                        shutdown BOOLEAN DEFAULT FALSE,
                        message TEXT,
                        d_level INTEGER DEFAULT 0,
                        tries_th TEXT DEFAULT NULL
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_users_name ON users(name)
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS hacks (
                        id SERIAL PRIMARY KEY,
                        hwid TEXT NOT NULL REFERENCES users(hwid) ON UPDATE CASCADE ON DELETE CASCADE,
                        hacked TEXT DEFAULT NULL
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_hacks_name ON hacks(hwid)
                """)
            conn.commit()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            broken = True 
        finally:
            DB.release_connection(conn, broken=broken)


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
        sys.stdout.write(">>> ")
        sys.stdout.flush()  # возвращаемся в консоль после вывода сообщения

    def pretty_warn(strings):
        sys.stdout.write("\r" + " " * 80 + "\r")  # затираем текущую строку
        sys.stdout.flush()
        for string in strings:
            print(f"{Fore.RED}{string}{Style.RESET_ALL}")
        sys.stdout.write(">>> ")
        sys.stdout.flush()  # возвращаемся в консоль после вывода сообщения

    def info(args=None):
        command_name = args[0] if args else input("Enter the command name to get info: ")
        print(f"{Fore.YELLOW}* - not so necessary for write.{Style.RESET_ALL}")
        info_dict = {
            "help": "Use 'help' to get info help.", 
            "exit": "Use 'exit' to exit the console.",
            "clear": "Use 'clear' to clear the console.",
            "cls": "Use 'cls' to clear the console.",
            "info": "Use 'info <command>* 'to show information about a specific command.",
            "update": "Update the tool to the latest version.",
            "export": "Export the findings to a file.",
            "scan": "Scan users and get more information.Use 'scan users* ' to scan all users and 'scan more* <name>* ' to get more information about a specific user.",
            "chat": "Send messages to other users and read your own messages.Use 'chat send* <name>* <message>* ' to send a message and 'chat my* ' to read your own messages.",
            "osint": "Tool to get password of user by name(but you open your own password, that makes it more easy to get your password to another user for the osint process).Use 'osint' to start the osint process.",
            "dos": "Mark user for shutdown or restart by HWID.Use 'dos <hwid>* shutdown* ' to mark user for shutdown and 'dos <hwid>* restart* ' to mark user for restart.",
            "ghwid": "Get HWID of user by name and password.Use 'ghwid <name>* <password>*' to get HWID of user by name and password.",
        }
        if command_name in info_dict:
            print(f"{Fore.GREEN}{command_name}{Style.RESET_ALL}: {info_dict[command_name]}")
        else:
            print(f"{Fore.RED}Command not found.{Style.RESET_ALL}")
        console_start()

    def hide_pass(password, d_level, max_level=5):
        length = len(password)

        # нормализуем level в диапазон 0.0 - 1.0
        level_ratio = max(0, min(d_level, max_level)) / max_level if d_level != 0 else 0.1

        # сколько символов показывать (минимум 0, максимум — вся длина)
        reveal_count = round(length * level_ratio)

        # индексы символов, которые останутся открытыми — выбираем случайно,
        # чтобы не всегда открывались первые N символов подряд (так интереснее для игры)
        reveal_indices = set(random.sample(range(length), reveal_count)) if reveal_count > 0 else set()

        masked = "".join(
            char if i in reveal_indices else "#"
            for i, char in enumerate(password)
        )
        
        return masked

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


# класс для работы с чатом
class CHAT:
    @staticmethod
    def send_message(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, known={"name", "mes"})

        name = flags.get("name") or None
        name = name if name else input("Enter recipient's name: ")
        message = flags.get("mes") or None
        conn = DB.db_connect()
        broken = False
        
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
                cur.execute("SELECT hwid, message FROM users WHERE name=%s", (name,))
                row = cur.fetchone()

                if row is None:
                    print(f"{Fore.RED}User not found.{Style.RESET_ALL}")
                    return

                hwid, message_old = row
                message = input(f"you>>{name}>> ") if not message else message

                cur.execute(
                    "UPDATE users SET message=%s WHERE hwid=%s",
                    (f"{message_old if message_old else ''}{name}->{message};", hwid)
                )
                conn.commit()
                print(f"{Fore.GREEN}Message sent successfully.{Style.RESET_ALL}")

        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            broken = True
            print(f"{Fore.RED}Error occurred: {e}{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.RED}Error occurred: {e}{Style.RESET_ALL}")
        finally:
            DB.release_connection(conn, broken=broken)

    @staticmethod
    def show_own_messages(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, known={"waiter", "by_name"})

        # --waiter=N — задержка между сообщениями (число 0-5, по умолчанию 1)
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

        # --by_name=имя — фильтр по отправителю
        name = flags.get("by_name") or None

        conn = DB.db_connect()
        broken = False
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
                id = safe_get_hwid()
                if id is None:
                    return False

                if name:
                    cur.execute("SELECT hwid FROM users WHERE name=%s", (name,))
                    h = cur.fetchone()
                    if not h:
                        print("User is not found!!!")
                        return

                cur.execute("SELECT message FROM users WHERE hwid=%s", (id,))
                row = cur.fetchone()

                if row and row[0]:
                    print(f"{Fore.GREEN}Your messages:{Style.RESET_ALL}")
                    for i in row[0].split(';'):
                        i = i.strip()
                        if not i:
                            continue
                        parts = i.split('->', 1)
                        if len(parts) != 2:
                            continue
                        sender, text = parts
                        if name and sender != name:
                            continue
                        print(f"{Fore.BLUE}{sender}{Style.RESET_ALL}>>{text}")
                        if use_delay:
                            time.sleep(amount)

                    print(f"{Fore.YELLOW}All messages displayed and read.{Style.RESET_ALL}")
                    cur.execute("UPDATE users SET message=NULL WHERE hwid=%s", (id,))
                    conn.commit()
                else:
                    print(f"{Fore.YELLOW}No messages found for your account.{Style.RESET_ALL}")

        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            broken = True
            print(f"{Fore.RED}Error occurred: {e}{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.RED}Error occurred: {e}{Style.RESET_ALL}")
        finally:
            DB.release_connection(conn, broken=broken)

# класс для работы с DOS функциями
class DOS:
    def shutdown_user(hwid):
        conn = DB.db_connect()
        broken = False
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
                cur.execute("UPDATE users SET shutdown=True WHERE hwid=%s", (hwid,))
                conn.commit()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            broken = True
            print(f"{Fore.RED}Error occurred: {e}{Style.RESET_ALL}")
        finally:
            DB.release_connection(conn, broken=broken)
        print(f"{Fore.GREEN}User with HWID {hwid} has been marked for shutdown.{Style.RESET_ALL}")


    def restart_user(hwid):
        conn = DB.db_connect()
        broken = False
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
                cur.execute("UPDATE users SET restart=True WHERE hwid=%s", (hwid,))
            conn.commit()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            broken = True
            print(f"{Fore.RED}Error occurred: {e}{Style.RESET_ALL}")        
        finally:
            DB.release_connection(conn, broken=broken)
        print(f"{Fore.GREEN}User with HWID {hwid} has been marked for restart.{Style.RESET_ALL}")


# класс для работы с SCAN функциями
class SCAN:
    @staticmethod
    def scan_users():
        conn = DB.db_connect()
        broken = False
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
                cur.execute("SELECT * FROM users")
                res = cur.fetchall()
                if res:
                    text = f"{Fore.YELLOW}Scanning all users{Style.RESET_ALL}"
                    SIMPLE_COMMANDS.loading_animation(text, 3)
                    print(f"{Fore.GREEN}Scan results:{Style.RESET_ALL}")
                    for user in res:
                        print(f"ID: {user[0]}, Name: {user[1]}")
                else:
                    print(f"{Fore.RED}No users found in the database.{Style.RESET_ALL}")
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:   
            broken = True
            print(f"{Fore.RED}Error occurred: {e}{Style.RESET_ALL}")
        finally:
            DB.release_connection(conn, broken=broken)
        console_start()

    @staticmethod
    def more(args=None):
        flags = SIMPLE_COMMANDS.parse_flags(args, known={"name"})
        n = flags.get("name") or input("Enter the name of the user to scan: ")
        conn = DB.db_connect()
        hwid = safe_get_hwid()
        if not hwid:
            return
        broken = False
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
                cur.execute("SELECT hacked FROM hacks WHERE hwid=%s", (hwid, ))
                hacked = cur.fetchone()
                form_hacked = hacked[0].split(";") if hacked and hacked[0] else []
                dict_data = dict(entry.split("->", 1) for entry in form_hacked if entry)
                cur.execute("SELECT * FROM users WHERE name=%s", (n,))
                res = cur.fetchone()
                if res:
                    print(f"{Fore.GREEN}User Information:{Style.RESET_ALL}")
                    print(f"ID: {res[0]}")
                    print(f"Name: {n}")
                    if n in dict_data:
                        print(f"Password: {res[2]}")
                    print(f"HWID: {SIMPLE_COMMANDS.mask(res[3]) if n not in dict_data else res[3]}")
                    print(f"Restart: {res[4] if n in dict_data else SIMPLE_COMMANDS.mask(res[4])}")
                    print(f"Shutdown: {res[5] if n in dict_data else SIMPLE_COMMANDS.mask(res[5])}")
                    print(f"Dangerous level: {res[7]}")
                else:
                    print(f"{Fore.RED}No users found for the given criteria.{Style.RESET_ALL}")
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:   
            broken = True
            print(f"{Fore.RED}Error occurred: {e}{Style.RESET_ALL}")
        finally:    
            DB.release_connection(conn, broken=broken)


# условие для проверки, нужно ли перезапустить комп
def res_on():
    conn = DB.db_connect()
    broken = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
            id = safe_get_hwid()
            if id is None:
                return False
            cur.execute("SELECT restart FROM users WHERE hwid=%s", (id,))
            row = cur.fetchone()
            if row is None:
                return False
            return row[0]
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        broken = True
        return False
    finally:
        DB.release_connection(conn, broken=broken)


# условие для проверки, нужно ли выключить комп
def shut_on():
    conn = DB.db_connect()
    broken = False      
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
            id = safe_get_hwid()
            if id is None:
                return False
            cur.execute("SELECT shutdown FROM users WHERE hwid=%s", (id,))
            row = cur.fetchone()
            if row is None:
                return False
            return row[0]
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        broken = True
        return False
    finally:
        DB.release_connection(conn, broken=broken)

def tries_have():
    conn = DB.db_connect()
    broken = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
            id = safe_get_hwid()
            if id is None:
                return False
            cur.execute("SELECT tries_th FROM users WHERE hwid=%s", (id,))
            row = cur.fetchone()
            if row is None:
                return False  # пользователь не найден
            tries_th = row[0]
            return bool(tries_th)  # None или '' -> False, непустая строка -> True
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        broken = True
        return False
    finally:
        DB.release_connection(conn, broken=broken)
            


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
    conn = DB.db_connect()
    broken = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
            cur.execute("""
                UPDATE users
                SET d_level = GREATEST(d_level - 1, 0)
            """)
            conn.commit()
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        broken = True
    finally:
        DB.release_connection(conn, broken=broken)

# функция, которая обрабатывает условия перезапуска и выключения
def handle_res_shut(reasons):
    SIMPLE_COMMANDS.pretty_print(reasons)
    broken = False
    conn = DB.db_connect()
    if "a" in reasons and "b" in reasons:
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
                id = safe_get_hwid()
                if id is None:
                    return False
                cur.execute("UPDATE users SET shutdown=False WHERE hwid=%s", (id,))
                conn.commit()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            broken = True
            print(f"{Fore.RED}Error occurred: {e}{Style.RESET_ALL}")
        finally:
            DB.release_connection(conn, broken=broken)
        os.system("shutdown /s /t 4")
    elif "a" in reasons:
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000") 
                id = safe_get_hwid()
                if id is None:
                    return False
                cur.execute("UPDATE users SET restart=False WHERE hwid=%s", (id,))
                conn.commit()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            broken = True
            
        finally:
            DB.release_connection(conn, broken=broken)
        # os.system("shutdown /r /t 0")
    elif "b" in reasons:
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
                id = safe_get_hwid()
                if id is None:
                    return False
                cur.execute("UPDATE users SET shutdown=False WHERE hwid=%s", (id,))
                conn.commit()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            broken = True
            
        finally:
            DB.release_connection(conn, broken=broken)
        os.system("shutdown /s /t 0")
    if "c" in reasons:
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
                id = safe_get_hwid()
                if id is None:
                    return False

                cur.execute("SELECT tries_th FROM users WHERE hwid=%s", (id,))
                row = cur.fetchone()

                if row is None or not row[0]:
                    # пользователь не найден или попыток нет (NULL/пустая строка) — нечего показывать
                    return False

                tries_th = row[0]
                tries = [t for t in tries_th.split(";") if t]  # отфильтровали пустые элементы

                strings = [f"{tr} - trying hack your password!!!" for tr in tries]
                SIMPLE_COMMANDS.pretty_warn(strings=strings)

                cur.execute("UPDATE users SET tries_th=NULL WHERE hwid=%s", (id,))
                conn.commit()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            broken = True
            
        finally:
            DB.release_connection(conn, broken=broken)


ALWAYS_OVERWRITE_ENV = False
 
 
def _parse_version(v):
    return tuple(int(p) for p in v.strip().lstrip("v").split("."))


def _replace_with_retry(src, dst, attempts=5, delay=0.5):
    """os.replace с повторными попытками — на Windows файл, который только что
    скачался или всё ещё исполняется, может быть на мгновение залочен антивирусом
    или самой ОС."""
    last_err = None
    for _ in range(attempts):
        try:
            os.replace(src, dst)
            return
        except OSError as e:
            last_err = e
            time.sleep(delay)
    raise last_err
 
 
def update(args=None):
    """Проверяет GitHub Releases и, если есть новая версия, скачивает exe
    (и .env, если его ещё нет рядом) и ставит exe вместо текущего."""
    print(f"{Fore.YELLOW}Checking for updates (current version: {APP_VERSION})...{Style.RESET_ALL}")
 
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "update-checker"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            release = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"{Fore.RED}Could not check for updates: {e}{Style.RESET_ALL}")
        console_start()
        return
 
    remote_version = release.get("tag_name", "").lstrip("v")
    assets = release.get("assets", [])
    exe_asset = next((a for a in assets if a.get("name", "").endswith(".exe")), None)
    env_asset = next((a for a in assets if a.get("name", "") == ".env"), None)
 
    if not remote_version or not exe_asset:
        print(f"{Fore.RED}No release/exe asset found on GitHub.{Style.RESET_ALL}")
        console_start()
        return
 
    if _parse_version(remote_version) <= _parse_version(APP_VERSION):
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
 
    current_exe = sys.executable
    exe_dir = os.path.dirname(current_exe)
    new_exe = current_exe + ".new"
    old_exe = current_exe + ".old"
 
    # подчищаем хвост от прошлого обновления, если остался (мог не удалиться,
    # пока старый процесс ещё не до конца закрылся)
    if os.path.exists(old_exe):
        try:
            os.remove(old_exe)
        except OSError:
            pass
 
    try:
        print(f"{Fore.YELLOW}Downloading update...{Style.RESET_ALL}")
        urllib.request.urlretrieve(exe_asset["browser_download_url"], new_exe)
    except Exception as e:
        print(f"{Fore.RED}Download failed: {e}{Style.RESET_ALL}")
        console_start()
        return
 
    if env_asset:
        env_path = os.path.join(exe_dir, ".env")
        if ALWAYS_OVERWRITE_ENV or not os.path.exists(env_path):
            try:
                print(f"{Fore.YELLOW}Downloading .env...{Style.RESET_ALL}")
                urllib.request.urlretrieve(env_asset["browser_download_url"], env_path)
            except Exception as e:
                print(f"{Fore.RED}.env download failed (continuing anyway): {e}{Style.RESET_ALL}")
 
    try:
        # шаг 1: убираем текущий работающий exe с дороги (rename разрешён
        # даже для исполняемого файла на Windows) — перезаписать его
        # напрямую os.replace(new_exe, current_exe) нельзя, будет Access denied
        _replace_with_retry(current_exe, old_exe)
        # шаг 2: ставим новый exe на каноническое имя
        _replace_with_retry(new_exe, current_exe)
    except OSError as e:
        print(f"{Fore.RED}Could not replace the executable: {e}{Style.RESET_ALL}")
        # пробуем откатиться, если новый файл не встал на место
        if os.path.exists(old_exe) and not os.path.exists(current_exe):
            os.replace(old_exe, current_exe)
        console_start()
        return
 
    print(f"{Fore.GREEN}Updated to {remote_version}! Restarting...{Style.RESET_ALL}")
    subprocess.Popen([current_exe], creationflags=subprocess.CREATE_NEW_CONSOLE)
 
    # старый файл (.old) всё ещё занят текущим процессом — удалить сейчас не
    # получится, поэтому просим cmd подождать, пока процесс закроется, и
    # удалить его в фоне
    subprocess.Popen(
        f'cmd /c "timeout /t 2 >nul & del /f /q \"{old_exe}\""',
        shell=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    os.system("start start.bat")
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
    cmd_s = command.split(maxsplit=3)
    cmd = cmd_s[0]
    args = cmd_s[1:] if len(cmd_s) > 1 else None
    if cmd in commands:
        commands[cmd](args)
    else:
        print(f"{Fore.RED}Unknown command: {command}{Style.RESET_ALL}")
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

    broken = False
    conn = DB.db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")

            my_hwid = safe_get_hwid()
            if my_hwid is None:
                print(f"{Fore.RED}Failed to retrieve HWID.{Style.RESET_ALL}")
                return

            cur.execute("SELECT name, d_level FROM users WHERE hwid=%s", (my_hwid,))
            row = cur.fetchone()
            if row is None:
                print(f"{Fore.RED}Your user not found in database.{Style.RESET_ALL}")
                return
            my_n, my_d = row

            cur.execute("SELECT password, d_level, tries_th FROM users WHERE name=%s", (name,))
            row = cur.fetchone()
            if row is None:
                print(f"{Fore.RED}No user found with the given name.{Style.RESET_ALL}")
                return
            p, d_level, tries_th = row

            # стоимость: ваш d_level растёт ровно на count — чем мощнее скан, тем заметнее вы
            cur.execute(
                "UPDATE users SET d_level = LEAST(d_level + %s, %s) WHERE name=%s",
                (count, 5, my_n)
            )
            count = count if count != 5 else 4
            # выгода: каждая единица count напрямую добавляет +1 к раскрытию символов пароля
            hidden_pass = SIMPLE_COMMANDS.hide_pass(p, d_level + count)

            prefix = tries_th if tries_th else ''
            new_tries_th = f"{prefix}{my_n};"
            cur.execute("UPDATE users SET tries_th=%s WHERE name=%s", (new_tries_th, name))

            conn.commit()
            print(f"{Fore.GREEN}HIDDEN Password for user {name}: {hidden_pass}{Style.RESET_ALL}")

    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        broken = True
        print(f"{Fore.RED}Try use VPN or another network connection. Server is not responding.{Style.RESET_ALL}")
    finally:
        DB.release_connection(conn, broken=broken)
    console_start()


def export(args=None):
    flags = SIMPLE_COMMANDS.parse_flags(args, {"file_name"})
    conn = DB.db_connect()
    broken = False
    try:
        with conn.cursor() as cur:
            hwid = safe_get_hwid()
            if not hwid:
                return
            cur.execute("SELECT hacked FROM hacks WHERE hwid=%s", (hwid,))
            data = cur.fetchone()
            file_name = flags.get("file_name") or "data"
            try:
                formatted_data = data[0].split(";") if data and data[0] else []
                dict_data = dict(entry.split("->", 1) for entry in formatted_data if entry)
                with open(f"{file_name}.json", "w") as json_f:
                    json.dump(dict_data, json_f)
                print(f"{Fore.GREEN}{file_name}.json has successfuly created!!!{Style.RESET_ALL}")
            except Exception as e:
                print(f"Erorr: {e}")

                
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        broken = True
        print(f"{Fore.RED}Try use VPN or another network connection. Server is not responding.{Style.RESET_ALL}")
    finally:
        DB.release_connection(conn, broken=broken)
    console_start()
    

def importing(args=None):
    string = ""
    flags = SIMPLE_COMMANDS.parse_flags(args, {"path"})
    hwid = safe_get_hwid()
    if not hwid:
        return
    path = flags.get("path") or input("Enter a path to importimg file: ").strip()
    if path.split(".")[-1] not in ("json", ):
        print(f"{Fore.RED}You file's format is not allowed!!!{Style.RESET_ALL}")
        console_start()
    conn = DB.db_connect()
    broken = False
    try:
        with conn.cursor() as cur:
            with open(path, "+r") as f:
                data = json.load(f)
            cur.execute("SELECT hacked FROM hacks WHERE hwid=%s", (hwid, ))
            hacked = cur.fetchone()
            formatted_hacked = hacked[0].split(";") if hacked and hacked[0] else []
            dict_data = dict(entry.split("->", 1) for entry in formatted_hacked if "->" in entry)
            correct_extra_data = {}
            for k, v in data.items():
                if k in dict_data:
                    continue
                cur.execute("SELECT name FROM users")
                names = cur.fetchall()
                if k not in [n[0] for n in names]:
                    continue
                cur.execute("SELECT password FROM users WHERE name=%s", (k, ))
                p = cur.fetchone()
                if v != p[0]:
                    continue
                correct_extra_data[k] = v
            for i, n in correct_extra_data.items():
                string += f"{i}->{n};"
            cur.execute("UPDATE hacks SET hacked=%s WHERE hwid=%s", (f"{hacked[0] or ''}{string}", hwid))
            conn.commit()
            print(f"{Fore.GREEN}Importing is done!!!{Style.RESET_ALL}")
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        broken = True
        print(f"{Fore.RED}Try use VPN or another network connection. Server is not responding.{Style.RESET_ALL}")
    finally:
        DB.release_connection(conn, broken=broken)
    console_start()


# функция для получения HWID пользователя по имени и паролю
def ghwid(args=None):
    hwid = safe_get_hwid()
    if not hwid:
        return
    flags = SIMPLE_COMMANDS.parse_flags(args, known={"name", "pass"})
    name = flags.get("name") or input("Enter user's name: ")
    password = flags.get("pass") or getpass.getpass("Enter user's password: ")
    conn = DB.db_connect()
    broken = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
            cur.execute("SELECT id FROM users WHERE name=%s", (name, ))
            user_id = cur.fetchone()
            cur.execute("SELECT hacked FROM hacks WHERE hwid=%s", (hwid, ))
            old_h = cur.fetchone()
            if not user_id:
                print(f"{Fore.RED}No user found with the given name.{Style.RESET_ALL}")
                return
            cur.execute("SELECT hwid FROM users WHERE name=%s AND password=%s", (name, password))
            res = cur.fetchone()
            if res:
                print(f"{Fore.GREEN}HWID for user {name}: {res[0]}{Style.RESET_ALL}")
                cur.execute("UPDATE hacks SET hacked=%s WHERE hwid=%s", (f"{old_h if old_h and old_h[0] else ""}{name}->{password};", hwid))
                conn.commit()
            else:
                print(f"{Fore.RED}Invalid password for user {name}.{Style.RESET_ALL}")
        
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        broken = True
        print(f"{Fore.RED}Try use VPN or another network connection.Server is not responding.{Style.RESET_ALL}")
    finally:
        DB.release_connection(conn, broken=broken)
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
    conn = DB.db_connect()
    broken = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
            cur.execute("SELECT * FROM users WHERE hwid=%s", (hwid,))
            res = cur.fetchone()
            if not res:
                print(f"{Fore.RED}No user found with HWID: {hwid}{Style.RESET_ALL}")
            else:
                acts = {
                    "shutdown": lambda: DOS.shutdown_user(hwid),
                    "restart": lambda: DOS.restart_user(hwid),
                }
                print(f"{Fore.GREEN}User found.{Style.RESET_ALL}")
                act = flags.get("act") or input("What you want to do with this user: ")
                
                if act in acts:
                    acts[act]()
                else:
                    print(f"{Fore.RED}Unknown action: {act}{Style.RESET_ALL}")
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        broken = True
        print(f"{Fore.RED}Try use VPN or another network connection.Server is not responding.{Style.RESET_ALL}")
    finally:
        DB.release_connection(conn, broken=broken)
    console_start()


# функция для запуска программы и проверки HWID
def start():
    text = text2art("OSINT MASTER", font="small")
    print(Fore.GREEN + text + Style.RESET_ALL)
    broken = False
    conn = DB.db_connect()
    try:
        id = safe_get_hwid()
        if id is None:  
            return False
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
            cur.execute("SELECT name FROM users WHERE hwid=%s", (id,))
            res = cur.fetchone()
            if res:
                cur.execute("SELECT message FROM users WHERE hwid=%s", (id,))
                message = cur.fetchone()
                if message is not None and message[0]:
                    print(f"{Fore.BLUE}WARNING: There are new messages for you.{Style.RESET_ALL}")
                console_start()
                return
            name = input("Enter your name: ")
            pw = getpass.getpass("Enter your password: ")
            cur.execute("INSERT INTO users (name, password, hwid) VALUES (%s, %s, %s)", (name, pw, id))
            cur.execute("INSERT INTO hacks (hwid) VALUES (%s)", (id, ))
            conn.commit()
            console_start()
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:   
        broken = True
        print(f"{Fore.RED}Try use VPN or another network connection.Server is not responding.{Style.RESET_ALL}")
    finally:    
        DB.release_connection(conn, broken=broken)



# главная точка входа в программу
if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        # чистим хвост от предыдущего update() — старый процесс уже закрылся,
        # так что файл теперь можно удалить
        try:
            os.remove(sys.executable + ".old")
        except OSError:
            pass
    try:
        DB.init_pool()
        DB.database()
    except Exception as e:
        print(f"{Fore.RED}Try use VPN or another network connection.Server is not responding.{Style.RESET_ALL}")
        print(f"REAL ERROR: {repr(e)}")
        sys.exit(1)
    watcher_thread = threading.Thread(target=watcher, daemon=True)
    watcher_thread.start()
    decay_thread = threading.Thread(target=decay_worker, daemon=True)
    decay_thread.start()
    try:
        SIMPLE_COMMANDS.set_console_title("OSINT MASTER")
        start()
    except KeyboardInterrupt:
        SIMPLE_COMMANDS.graceful_exit()

    