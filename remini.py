import base64
import hashlib
import json
import os
import random
import sys
import threading
import time
import uuid
import tempfile
import asyncio
import logging
from queue import Queue
import requests
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ==========================================
# 1. الجزء الخاص بـ Remini (السكربت الأصلي)
# ==========================================

ORACLE = "https://api.remini.ai/v1/mobile/oracle"
API = "https://a.android.api.remini.ai/v1/mobile"
API2 = "https://a.android.api.remini.ai/v2/mobile"
UA = "okhttp/4.12.0"

DEVICES = [
    ("Samsung", "SM-G998B", "6.8", "33"),
    ("Google", "Pixel 7 Pro", "6.7", "33"),
    ("Xiaomi", "2201116SG", "6.67", "32"),
    ("OPPO", "CPH2211", "6.5", "31"),
    ("INFINIX", "Infinix X669", "6.6", "31"),
]

MIMES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".bmp": "image/bmp"}

MULTITOOL = {"type": "multi-tool", "pipelines": [{"bokeh": {"aperture_radius": "0.30", "apply_front_bokeh": "false", "group_picture": "true", "highlights": "0.25", "rescale_kernel_for_small_images": "true", "vivid": "0.75"}, "color_enhance": {"model": "prism-blend"}, "auto_balance": {"auto_contrast": {"contrast_intensity_coefficient": "1.5"}}, "background_enhance": {"model": "shiba-strong-tensorrt", "remove_color_shift": "true"}, "face_lifting": {"model": "pinko_bigger_dataset-style"}, "dodge_burn": {"intensity": 0.5, "model": "dodge_burn_residuals_big"}, "clip_extractor": {}, "face_enhance": {"model": "recommender_entire_dataset", "pre_blur": "0.6"}, "jpeg_quality": "80", "interpolation": "bicubic", "max_output_resolution": "3480", "enlarge_small_images_to": "2048", "smart_subject_selection": "true", "smart_subject_selection_factor": "1.5"}]}

DEFAULT_MAGIC_PRESET = "magic_video_wan_distill_lora_v2"

class Identity:
    def __init__(self, proxy=None):
        self.s = requests.Session()
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}
        self.proxy = proxy
        self.token = None
        self.android_id = uuid.uuid4().hex[:16]
        self.aaid = str(uuid.uuid4())
        self.backend_id = str(uuid.uuid4())
        man, model, dtype, osv = random.choice(DEVICES)
        self.base = {
            "bsp-id": "com.bigwinepot.nwdn.international.android",
            "build-number": "202526404",
            "build-version": "3.7.1477",
            "country": "US",
            "device-manufacturer": man,
            "device-model": model,
            "device-type": dtype,
            "language": "en",
            "locale": "en_US",
            "os-version": osv,
            "platform": "Android",
            "timezone": "America/New_York",
            "android-id": self.android_id,
            "aaid": self.aaid,
            "user-agent": UA,
        }

    def headers(self, extra=None):
        h = dict(self.base)
        if self.token:
            h["identity-token"] = self.token
        if extra:
            h.update(extra)
        return h

    def auth(self):
        h = self.headers({
            "first-install-timestamp": str(int(time.time())) + "E9",
            "backup-persistent-id": self.android_id + "_com.bigwinepot.nwdn.international",
            "non-backup-persistent-id": self.backend_id,
            "environment": "Production",
            "settings-response-version": "v2",
            "is-app-running-in-background": "false",
            "is-old-user": "true",
            "app-set-id": str(uuid.uuid4()),
        })
        r = self.s.get(ORACLE + "/setup", headers=h, timeout=40)
        r.raise_for_status()
        self.token = r.json()["settings"]["__identity__"]["token"]
        self.s.get(API + "/users/@me", headers=self.headers(), timeout=40)
        return self

class Farm:
    def __init__(self, proxies=None):
        self.proxies = proxies or []
        self.lock = threading.Lock()
        self.count = 0

    def next_proxy(self):
        with self.lock:
            self.count += 1
            if not self.proxies:
                return None
            return self.proxies[self.count % len(self.proxies)]

    def identity(self):
        return Identity(self.next_proxy()).auth()

def read_image(path):
    content = open(path, "rb").read()
    md5 = base64.b64encode(hashlib.md5(content).digest()).decode()
    mime = MIMES.get(os.path.splitext(path)[1].lower(), "image/jpeg")
    w = h = None
    try:
        im = Image.open(path)
        w, h = im.size
    except Exception:
        pass
    return content, md5, mime, w, h

def gcs_put(url, headers, content):
    uh = dict(headers)
    uh["Content-Length"] = str(len(content))
    uh["User-Agent"] = UA
    requests.put(url, headers=uh, data=content, timeout=180).raise_for_status()

def poll_v1(ident, task_id, max_wait=420):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        time.sleep(4)
        r = ident.s.get(f"{API}/tasks/{task_id}", headers=ident.headers(), timeout=40)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        d = r.json()
        st = d.get("status")
        if st == "completed":
            outs = (d.get("result") or {}).get("outputs") or []
            if outs:
                return outs[0]["url"]
            raise RuntimeError("completed without outputs")
        if st in ("failed", "error"):
            raise RuntimeError("task failed: " + json.dumps(d)[:200])
    raise TimeoutError("poll timeout")

def poll_v2(ident, task_id, max_wait=600):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        time.sleep(6)
        r = ident.s.get(f"{API2}/multi-avatar/tasks/{task_id}", headers=ident.headers(), timeout=40)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        d = r.json()
        st = d.get("status")
        if st == "completed":
            res = d.get("results") or []
            if res:
                return res[0]["uri"]
            raise RuntimeError("completed without results")
        if st in ("failed", "error"):
            raise RuntimeError("task failed: " + json.dumps(d)[:200])
    raise TimeoutError("poll timeout")

def download(url, out_path):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=300)
    r.raise_for_status()
    open(out_path, "wb").write(r.content)
    return len(r.content)

def enhance(farm, image, out, multitool=False):
    ident = farm.identity()
    content, md5, mime, w, h = read_image(image)
    feature = MULTITOOL if multitool else {"type": "enhance", "models": []}
    body = {"image_content_type": mime, "image_md5": md5, "image_size": len(content), "feature": feature}
    if multitool:
        body["image_resolution_width"] = w or 640
        body["image_resolution_height"] = h or 640
    else:
        body["metadata"] = {"size": len(content)}
        body["options"] = {"high_quality_output": False, "save_input": True}
    r = ident.s.post(API + "/tasks", headers=ident.headers({"content-type": "application/json; charset=UTF-8"}), json=body, timeout=40)
    r.raise_for_status()
    d = r.json()
    gcs_put(d["upload_url"], d["upload_headers"], content)
    ident.s.post(f"{API}/tasks/{d['task_id']}/process", headers=ident.headers({"content-length": "0"}), timeout=40).raise_for_status()
    url = poll_v1(ident, d["task_id"])
    size = download(url, out)
    return out, size

def magic_video(farm, face_image, out, preset=DEFAULT_MAGIC_PRESET):
    ident = farm.identity()
    content, md5, mime, w, h = read_image(face_image)
    h2 = ident.headers({"content-type": "application/json; charset=UTF-8"})
    r = ident.s.post(API2 + "/multi-avatar/multi-images/models", headers=h2, json={"subjects": [{"images": [{"content_type": mime, "md5": md5}]}]}, timeout=40)
    r.raise_for_status()
    subj = r.json()["subjects"][0]
    gcs_put(subj["images"][0]["upload_uri"], subj["images"][0]["upload_headers"], content)
    body = {"subjects": [{"photo_model_id": subj["photo_model_id"]}], "preset_ids": [preset], "n_output_images": 1,
            "ai_generation_config": {"free": {"generation": {"config": {"job_type": "multiavatar"}, "handler_type": "multiavatar"}},
                                     "premium": {"generation": {"config": {"job_type": "multiavatar"}, "handler_type": "multiavatar"}}},
            "input_people": 1}
    r = ident.s.post(API2 + "/multi-avatar/tasks", headers=h2, json=body, timeout=40)
    r.raise_for_status()
    tid = r.json()["photo_task_id"]
    url = poll_v2(ident, tid)
    size = download(url, out)
    return out, size

def stylize(farm, image, out, style="toon"):
    ident = farm.identity()
    content, md5, mime, w, h = read_image(image)
    body = {"image_content_type": mime, "image_md5": md5, "image_size": len(content), "feature": {"type": "stylization-v2", "id": style}}
    r = ident.s.post(API + "/tasks", headers=ident.headers({"content-type": "application/json; charset=UTF-8"}), json=body, timeout=40)
    r.raise_for_status()
    d = r.json()
    gcs_put(d["upload_url"], d["upload_headers"], content)
    ident.s.post(f"{API}/tasks/{d['task_id']}/process", headers=ident.headers({"content-length": "0"}), timeout=40).raise_for_status()
    url = poll_v1(ident, d["task_id"])
    size = download(url, out)
    return out, size

def ai_sticker(farm, image, out, preset="ai_sticker_geemoji"):
    ident = farm.identity()
    content, md5, mime, w, h = read_image(image)
    H = ident.headers({"content-type": "application/json; charset=UTF-8"})
    r = ident.s.post(API2 + "/ai-sticker/input-photos", headers=H, json={"input_photos": [{"content_type": mime, "md5": md5}]}, timeout=40)
    r.raise_for_status()
    d = r.json()
    up = d["input_photos"][0]
    gcs_put(up["upload_uri"], up["upload_headers"], content)
    r = ident.s.post(API2 + "/ai-sticker/tasks", headers=H, json={"input_photo_group_id": d["input_photo_group_id"], "preset_ids": [preset]}, timeout=40)
    r.raise_for_status()
    tid = r.json()["task_id"]
    t0 = time.time()
    while time.time() - t0 < 1800:
        time.sleep(10)
        p = ident.s.get(f"{API2}/ai-sticker/tasks/{tid}", headers=ident.headers(), timeout=40).json()
        st = p.get("status")
        if st == "completed":
            res = p.get("results") or []
            if not res:
                raise RuntimeError("completed without results")
            size = download(res[0].get("uri") or res[0].get("url"), out)
            return out, size
        if st in ("failed", "error"):
            raise RuntimeError("task failed: " + json.dumps(p)[:200])
    raise TimeoutError("sticker poll timeout")

# ==========================================
# 2. الجزء الخاص ببوت تيليجرام
# ==========================================

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

farm = Farm()

MODES = {
    "enhance": {"label": "✨ تحسين الجودة (HD)", "func": "enhance"},
    "multitool": {"label": "🎨 أدوات متعددة (Multi-Tool)", "func": "enhance"},
    "stylize": {"label": "🎭 تحويل لكرتون (Toon)", "func": "stylize", "style": "toon"},
    "sticker": {"label": "🤡 ملصق ذكي (Sticker)", "func": "ai_sticker", "preset": "ai_sticker_geemoji"},
    "magicvideo": {"label": "🎬 فيديو سحري (Magic Video)", "func": "magic_video", "preset": "magic_video_wan_distill_lora_v2"}
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(m["label"], callback_data=mode)] for mode, m in MODES.items()]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🌟 **بوت Remini المدفوع مجاناً**\n\nاختر الميزة التي تريد استخدامها:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode = query.data
    
    if mode not in MODES:
        await query.edit_message_text("❌ خيار غير صالح.")
        return
        
    context.user_data["mode"] = mode
    await query.edit_message_text(
        f"✅ اخترت: **{MODES[mode]['label']}**\n\n📤 الآن أرسل الصورة التي تريد معالجتها.",
        parse_mode="Markdown"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode")
    if not mode:
        await update.message.reply_text("⚠️ الرجاء اختيار الميزة أولاً باستخدام /start")
        return
    
    msg = await update.message.reply_text("⏳ جاري معالجة الصورة... (قد يستغرق الفيديو بضع دقائق)")
    
    photo_file = await update.message.photo[-1].get_file()
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        await photo_file.download_to_drive(f.name)
        input_path = f.name
        
    ext = ".mp4" if mode == "magicvideo" else ".jpg"
    output_path = tempfile.mktemp(suffix=ext)
    
    try:
        if mode == "enhance":
            _, size = await asyncio.to_thread(enhance, farm, input_path, output_path, multitool=False)
        elif mode == "multitool":
            _, size = await asyncio.to_thread(enhance, farm, input_path, output_path, multitool=True)
        elif mode == "stylize":
            _, size = await asyncio.to_thread(stylize, farm, input_path, output_path, style=MODES["stylize"]["style"])
        elif mode == "sticker":
            _, size = await asyncio.to_thread(ai_sticker, farm, input_path, output_path, preset=MODES["sticker"]["preset"])
        elif mode == "magicvideo":
            _, size = await asyncio.to_thread(magic_video, farm, input_path, output_path, preset=MODES["magicvideo"]["preset"])
        else:
            await msg.edit_text("❌ وضع غير مدعوم.")
            return
        
        with open(output_path, "rb") as f:
            if mode == "magicvideo":
                await update.message.reply_video(video=f, caption=f"✅ تم إنشاء الفيديو بنجاح!\n📦 الحجم: {size / 1024:.1f} KB")
            else:
                await update.message.reply_photo(photo=f, caption=f"✅ تمت المعالجة بنجاح!\n📦 الحجم: {size / 1024:.1f} KB")
        await msg.delete()
        
    except Exception as e:
        logger.error(f"Error processing {mode}: {e}")
        await msg.edit_text(f"❌ حدث خطأ أثناء المعالجة:\n`{str(e)[:200]}`")
    finally:
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📖 استخدم /start لاختيار الميزة، ثم أرسل الصورة. للفيديو السحري، يفضل إرسال صورة وجه واضحة."
    await update.message.reply_text(text)

def main():
    # ⚠️ ضع توكن البوت هنا
    TOKEN = "8947371808:AAH7k_kTGFMfbRU_pqhiqgI8lCr6gBERQNg"
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    logger.info("🚀 Remini Premium Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()