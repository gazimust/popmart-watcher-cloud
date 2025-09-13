# popmart_stock_watcher.py
# Two-stage, memory-friendly watcher for Render (512MB):
#  1) Cheap HTML requests every cycle.
#  2) Launch Playwright ONLY to confirm a "maybe in stock" URL, then close immediately.
#
# Env vars (Render → Environment):
#   PUSHOVER_TOKEN, PUSHOVER_USER   (required)
#   CHECK_EVERY_SECONDS = 60..120   (recommend 90)
#   SEND_TEST_PUSH_ON_START = 0|1   (optional)
#   CART_URL = https://www.popmart.com/gb/cart  (optional)

import asyncio
import os
import re
import sys
from datetime import datetime

import requests
from playwright.async_api import async_playwright

# ===== CONFIG =====
PRODUCT_URLS = [
    "https://www.popmart.com/gb/products/1265/THE-MONSTERS-Big-into-Energy-Series-ROCK-THE-UNIVERSE-Vinyl-Plush-Doll",
    "https://www.popmart.com/gb/products/1270/THE-MONSTERS-Pin-for-Love-Series-Vinyl-Plush-Pendant-Blind-Box-(N-Z)",
    "https://www.popmart.com/gb/products/1269/THE-MONSTER-PIN-FOR-LOVE-SERIES---Vinyl-Plush-Pendant-Blind-Box-(A-M)",
    "https://www.popmart.com/gb/products/925/THE-MONSTERS-Let's-Checkmate-Series-Vinyl-Plush-Hanging-Card",
    "https://www.popmart.com/gb/products/909/LABUBU-%C3%97-PRONOUNCE---WINGS-OF-FORTUNE-Vinyl-Plush-Hanging-Card",
    "https://www.popmart.com/gb/products/820/THE-MONSTERS---ANGEL-IN-CLOUDS-Vinyl-Face-Doll",
    "https://www.popmart.com/gb/products/757/THE-MONSTERS---I-FOUND-YOU-Vinyl-Face-Doll",
    "https://www.popmart.com/gb/products/753/Happy-Halloween-Party-Series-Sitting-Pumpkin-Vinyl-Plush-Pendant",
    "https://www.popmart.com/gb/products/733/LABUBU-Time-to-Chill-Vinyl-Plush-Doll",
]

CHECK_EVERY_SECONDS = int(os.getenv("CHECK_EVERY_SECONDS", "90"))
CART_URL = os.getenv("CART_URL", "https://www.popmart.com/gb/cart")

PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", "")
PUSHOVER_USER = os.getenv("PUSHOVER_USER", "")
SEND_TEST_PUSH_ON_START = os.getenv("SEND_TEST_PUSH_ON_START", "0") == "1"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Text patterns
BTN_PATTERNS = re.compile(r"(Add\s+to\s+(Cart|Bag|Basket)|Buy\s+Now|Purchase)", re.I)
SOLD_OUT_PATTERNS = re.compile(r"(Sold\s*Out|Out\s*of\s*Stock|Unavailable)", re.I)
NOTIFY_PATTERNS = re.compile(r"(Notify\s*Me|Email\s*Me\s*When\s*Available|Back\s*in\s*Stock)", re.I)

def log(msg: str) -> None:
    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}] {msg}", flush=True)

def iphone_push(title: str, message: str, url: str | None = None):
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        log("Pushover not configured; skipping push.")
        return
    try:
        data = {"token": PUSHOVER_TOKEN, "user": PUSHOVER_USER, "title": title, "message": message}
        if url:
            data["url"] = url
        r = requests.post("https://api.pushover.net/1/messages.json", data=data, timeout=15)
        if r.status_code != 200:
            log(f"Pushover error: {r.status_code} {r.text}")
    except Exception as e:
        log(f"Pushover exception: {e}")

def cheap_html_check(url: str) -> tuple[bool, str]:
    """
    Return (maybe_in_stock, reason). Uses plain HTML (no JS).
    'maybe_in_stock' means we found buy-ish text and did NOT find sold-out/notify text.
    """
    try:
        resp = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=20)
        resp.raise_for_status()
        html = resp.text
        has_buy = bool(BTN_PATTERNS.search(html))
        has_sold = bool(SOLD_OUT_PATTERNS.search(html))
        has_notify = bool(NOTIFY_PATTERNS.search(html))
        if has_buy and not (has_sold or has_notify):
            return True, "buy-text-without-sold/notify"
        return False, ("sold/notify" if (has_sold or has_notify) else "buy-not-found")
    except Exception as e:
        return False, f"http-error: {e}"

async def confirm_with_playwright(maybe_url: str) -> bool:
    """
    Launch a tiny headless Chromium, block heavy resources, and confirm stock for ONE url.
    Immediately close everything afterward to keep memory low.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--single-process",
                "--disable-gpu",
                "--no-zygote",
            ],
        )
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1024, "height": 700},
            java_script_enabled=True,
            accept_downloads=False,
        )

        # Block heavy assets
        async def route_block(route):
            r = route.request
            if r.resource_type in ("image", "media", "font", "stylesheet"):
                return await route.abort()
            return await route.continue_()
        await context.route("**/*", route_block)

        page = await context.new_page()
        page.set_default_navigation_timeout(60000)
        page.set_default_timeout(20000)

        try:
            await page.goto(maybe_url)
            # Try button detection
            try:
                for pat in ("Add to Cart", "Add To Cart", "Add to Bag", "Add To Bag",
                            "Add to Basket", "Add To Basket", "Buy Now", "Purchase"):
                    loc = page.get_by_role("button", name=re.compile(pat, re.I))
                    if await loc.count() > 0:
                        if await loc.nth(0).is_visible() and await loc.nth(0).is_enabled():
                            return True
            except Exception:
                pass

            # Fallback: text scan
            html = await page.content()
            if BTN_PATTERNS.search(html) and not (SOLD_OUT_PATTERNS.search(html) or NOTIFY_PATTERNS.search(html)):
                return True
            return False
        finally:
            try:
                await page.close()
                await context.close()
                await browser.close()
            except Exception:
                pass

async def main():
    log("Starting POP MART stock watcher (Two-stage / low memory)…")

    if SEND_TEST_PUSH_ON_START:
        iphone_push("Pushover Test", "Cloud watcher started OK.", PRODUCT_URLS[0])

    # Track last status to avoid duplicate pings
    last_seen_instock = {u: False for u in PRODUCT_URLS}

    while True:
        # 1) Cheap pass over all URLs
        candidates: list[str] = []
        for url in PRODUCT_URLS:
            maybe, reason = cheap_html_check(url)
            log(f"[cheap] {url} → maybe={maybe} ({reason})")
            if maybe:
                candidates.append(url)

        # 2) Confirm each candidate with a short Playwright session (one by one)
        for url in candidates:
            log(f"[confirm] Launching Chromium briefly for: {url}")
            try:
                is_real = await confirm_with_playwright(url)
                log(f"[confirm] {url} → in_stock={is_real}")
                was = last_seen_instock.get(url, False)
                if is_real and not was:
                    title = "POP MART Stock Alert"
                    msg = f"In stock:\n{url}\n\nCart (open after adding):\n{CART_URL}"
                    iphone_push(title, msg, url)
                last_seen_instock[url] = is_real
            except Exception as e:
                log(f"[confirm] error for {url}: {e}")

        # 3) Sleep until next cycle
        await asyncio.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
