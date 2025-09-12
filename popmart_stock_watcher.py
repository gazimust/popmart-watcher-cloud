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

CHECK_EVERY_SECONDS = int(os.getenv("CHECK_EVERY_SECONDS", "45"))  # cloud-friendly
OPEN_PAGE_WHEN_IN_STOCK = False  # no GUI in cloud

# Pushover via env vars (safer). You already have these values.
PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", "a8zxubz4ndhfdne8i5ro54n6r8fzib")
PUSHOVER_USER  = os.getenv("PUSHOVER_USER",  "u7ekcakhmez4v7riffbhjscps8vecs")

# Optional: set SEND_TEST_PUSH_ON_START=1 in Render to get a one-time test push at boot
SEND_TEST_PUSH_ON_START = os.getenv("SEND_TEST_PUSH_ON_START", "0") == "1"

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

async def main():
    log("Starting POP MART stock watcher (cloud)…")
    async with async_playwright() as p:
        # ✅ FIX: launch a browser, then create a context
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ))
        page = await context.new_page()

        if SEND_TEST_PUSH_ON_START:
            iphone_push("Pushover Test", "Cloud watcher started OK.", PRODUCT_URLS[0])

        last_seen_instock = {url: False for url in PRODUCT_URLS}

        try:
            while True:
                for url in PRODUCT_URLS:
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
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

                await asyncio.sleep(CHECK_EVERY_SECONDS)
        finally:
            await context.close()
            await browser.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
