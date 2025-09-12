# popmart_stock_watcher.py
# Run in cloud (e.g., Render) as a Background Worker.
# Env vars (Render → Environment):
#   PUSHOVER_TOKEN = a8zxubz4ndhfdne8i5ro54n6r8fzib
#   PUSHOVER_USER  = u7ekcakhmez4v7riffbhjscps8vecs
#   CHECK_EVERY_SECONDS = 60           (recommended 45–90)
#   SEND_TEST_PUSH_ON_START = 0 or 1   (optional one-time test on boot)

import asyncio
import re
import sys
import os
import requests
from datetime import datetime
from playwright.async_api import async_playwright

# === CONFIG ===
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

CHECK_EVERY_SECONDS = int(os.getenv("CHECK_EVERY_SECONDS", "60"))  # cloud-friendly default
OPEN_PAGE_WHEN_IN_STOCK = False  # no GUI in cloud

# Pushover via env vars (safer). Your values will be injected by Render.
PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", "")
PUSHOVER_USER  = os.getenv("PUSHOVER_USER", "")

# Optional: set 1 to send a one-time push on startup (good for testing)
SEND_TEST_PUSH_ON_START = os.getenv("SEND_TEST_PUSH_ON_START", "0") == "1"

# Texts used by shops
CANDIDATE_ADD_TEXTS = [
    "Add to Cart", "Add To Cart", "Add to Bag", "Add To Bag",
    "Add to Basket", "Add To Basket", "Buy Now", "Purchase",
]
SOLD_OUT_TEXTS = ["Sold Out", "Out of Stock", "Out Of Stock", "Unavailable"]
NOTIFY_ME_TEXTS = ["Notify Me", "Email Me When Available", "Back in Stock"]

def log(msg: str) -> None:
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{stamp}] {msg}", flush=True)

def iphone_push(title: str, message: str, url: str | None = None):
    """Send a push to your iPhone via Pushover."""
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

async def click_if_exists(page, role_name_regex_list):
    """Try clicking common cookie/consent/close buttons if they appear."""
    for pat in role_name_regex_list:
        try:
            locator = page.get_by_role("button", name=re.compile(pat, re.I))
            if await locator.count() > 0:
                btn = locator.nth(0)
                if await btn.is_visible():
                    await btn.click(timeout=2000)
        except Exception:
            pass

async def dismiss_overlays(page):
    await click_if_exists(page, [
        r"Accept All", r"Accept", r"Agree", r"OK", r"Got it",
        r"Close", r"Continue", r"I Understand", r"Allow all",
    ])

async def page_has_any_text(page, texts):
    try:
        html = await page.content()
        lower = html.lower()
        return any(t.lower() in lower for t in texts)
    except Exception:
        return False

async def find_enabled_buy_button(page):
    for t in CANDIDATE_ADD_TEXTS:
        try:
            locator = page.get_by_role("button", name=re.compile(t, re.I))
            if await locator.count() > 0:
                btn = locator.nth(0)
                if await btn.is_visible() and await btn.is_enabled():
                    return True
        except Exception:
            continue
    return False

async def is_in_stock(page):
    if await find_enabled_buy_button(page):
        return True
    if await page_has_any_text(page, SOLD_OUT_TEXTS):
        return False
    if await page_has_any_text(page, NOTIFY_ME_TEXTS):
        return False
    return False

async def enable_light_mode(context):
    """
    Block heavy resources to cut CPU/RAM/network.
    """
    async def route_block(route):
        req = route.request
        if req.resource_type in ("image", "media", "font", "stylesheet"):
            return await route.abort()
        return await route.continue_()
    await context.route("**/*", route_block)

async def main():
    log("Starting POP MART stock watcher (cloud, light mode)…")
    async with async_playwright() as p:
        # Launch headless Chromium with low-memory flags
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",   # use /tmp instead of /dev/shm
                "--single-process",          # fewer processes (saves RAM)
                "--disable-gpu",
                "--no-zygote",
            ]
        )

        # Create a lightweight context
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1024, "height": 700},
            java_script_enabled=True,
            accept_downloads=False,
        )

        # Block heavy resources
        await enable_light_mode(context)

        page = await context.new_page()
        page.set_default_navigation_timeout(60000)
        page.set_default_timeout(20000)

        # Optional one-time test push at boot
        if SEND_TEST_PUSH_ON_START:
            iphone_push("Pushover Test", "Cloud watcher started OK.", PRODUCT_URLS[0])

        # Track previous state to avoid duplicate alerts
        last_seen_instock = {url: False for url in PRODUCT_URLS}

        LOOP_RESTART = 200  # recycle the browser every N loops to avoid leaks
        loop_count = 0

        try:
            while True:
                loop_count += 1
                for url in PRODUCT_URLS:
                    try:
                        await page.goto(url)  # simplified (networkidle/domcontentloaded can be finicky under blocking)
                    except Exception as e:
                        log(f"[{url}] Navigation failed (skipping): {e}")
                        continue

                    try:
                        await dismiss_overlays(page)
                        in_stock = await is_in_stock(page)
                        log(f"[{url}] In stock? {in_stock}")

                        was = last_seen_instock.get(url, False)
                        if in_stock and not was:
                            title = "POP MART Stock Alert"
                            msg = f"In stock: {url}"
                            iphone_push(title, msg, url)

                        last_seen_instock[url] = in_stock

                    except Exception as e:
                        log(f"[{url}] Check failed: {e}")

                # Recycle browser/context periodically (helps memory on small instances)
                if loop_count % LOOP_RESTART == 0:
                    try:
                        await page.close()
                        await context.close()
                    except Exception:
                        pass
                    browser = await p.chromium.launch(
                        headless=True,
                        args=[
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-dev-shm-usage",
                            "--single-process",
                            "--disable-gpu",
                            "--no-zygote",
                        ]
                    )
                    context = await browser.new_context(
                        user_agent=(
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        viewport={"width": 1024, "height": 700},
                        java_script_enabled=True,
                        accept_downloads=False,
                    )
                    await enable_light_mode(context)
                    page = await context.new_page()
                    page.set_default_navigation_timeout(60000)
                    page.set_default_timeout(20000)
                    log("Recycled browser/context to keep memory low.")

                await asyncio.sleep(CHECK_EVERY_SECONDS)
        finally:
            try:
                await page.close()
                await context.close()
                await browser.close()
            except Exception:
                pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)