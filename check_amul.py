"""
Amul stock watcher -> Telegram alert.

It logs into the Amul shop API for YOUR pincode, checks whether the
product(s) you care about are buyable, and sends you a Telegram message
the moment one flips from "Sold Out" to "In Stock".

You do not need to understand this file. All the bits you change live in
GitHub "Secrets" (set up in SETUP.md), not in here.
"""

import os
import re
import json
import time
import random
import hashlib
import pathlib
import requests

# ---------------------------------------------------------------------------
# Settings come from environment variables (GitHub Secrets). Nothing secret
# is written in this file.
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]      # from BotFather
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]        # your numeric chat id
PINCODE   = os.environ["PINCODE"]                 # your delivery pincode, e.g. 121001

# Which products to watch. Comma-separated "aliases" (the bit at the end of the
# product URL). Defaults to the Rose Lassi you asked about. Add more if you like.
ALIASES = os.environ.get(
    "PRODUCT_ALIASES",
    "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
).split(",")
ALIASES = [a.strip() for a in ALIASES if a.strip()]

BASE = "https://shop.amul.com"
STORE_ID = "62fa94df8c13af2e242eba16"   # Amul's storefront id (constant)
STATE_FILE = pathlib.Path("state.json")  # remembers last status to avoid spam

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")


def tid_header(session_tid):
    """Amul requires a signed 'tid' header on API calls. This reproduces it."""
    ts = str(int(time.time() * 1000))
    rand = str(random.randint(0, 1000))
    digest = hashlib.sha256(f"{STORE_ID}:{ts}:{rand}:{session_tid}".encode()).hexdigest()
    return f"{ts}:{rand}:{digest}"


def open_session_for_pincode(pincode):
    """Set up a session locked to the given pincode. Returns (session, substore_id)."""
    s = requests.Session()
    base_headers = {
        "user-agent": UA,
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "origin": BASE,
        "referer": BASE + "/en/browse/protein",
        "base_url": BASE + "/en/browse/protein",
        "frontend": "1",
        "x-amul-b2c-access-key": "shop.amul.com",
    }

    # 1. Touch the site so we get session cookies.
    s.get(BASE + "/en/browse/protein", headers=base_headers, timeout=15)

    # 2. Look up which "substore" serves this pincode.
    h = dict(base_headers, referer=BASE + "/", tid=tid_header("dummy"))
    pin = s.get(
        BASE + "/entity/pincode",
        headers=h,
        params={
            "limit": 50,
            "filters[0][field]": "pincode",
            "filters[0][value]": str(pincode),
            "filters[0][operator]": "regex",
            "cf_cache": "1h",
        },
        timeout=15,
    ).json()
    records = pin.get("records", [])
    if not records:
        raise SystemExit(f"Amul does not deliver to pincode {pincode} (no substore found).")
    raw_substore = records[0]["substore"]

    # 3. Tell the session to use that substore.
    ph = dict(base_headers,
              **{"content-type": "application/json",
                 "x-requested-with": "XMLHttpRequest",
                 "tid": tid_header("dummy")})
    s.put(BASE + "/entity/ms.settings/_/setPreferences",
          headers=ph,
          data=json.dumps({"data": {"store": raw_substore}}),
          timeout=15)

    # 4. Read back the live session token + substore id.
    info = s.get(BASE + f"/user/info.js?_v={int(time.time()*1000)}",
                 headers=base_headers, timeout=15).text
    m = re.search(r"session\s*=\s*(\{.*\})", info, re.DOTALL)
    if not m:
        raise SystemExit("Could not read session token from Amul (site may have changed).")
    sess = json.loads(m.group(1))
    session_tid = sess.get("tid")
    substore_id = sess.get("substore_id") or sess.get("substore", {}).get("_id")
    if not session_tid or not substore_id:
        raise SystemExit("Amul session missing tid/substore_id.")

    s._amul_tid = session_tid          # stash for later calls
    return s, substore_id


def check_product(s, substore_id, alias):
    """Return (in_stock: bool, name: str, qty: int) for one product alias."""
    h = {
        "user-agent": UA,
        "accept": "application/json, text/plain, */*",
        "referer": BASE + "/en/browse/protein",
        "base_url": BASE + "/en/browse/protein",
        "origin": BASE,
        "frontend": "1",
        "x-amul-b2c-access-key": "shop.amul.com",
        "tid": tid_header(s._amul_tid),
    }
    params = {
        "fields[name]": 1,
        "fields[alias]": 1,
        "fields[available]": 1,
        "fields[inventory_quantity]": 1,
        "fields[seller_substore_ids]": 1,
        "filters[0][field]": "alias",
        "filters[0][value]": alias,
        "filters[0][operator]": "eq",
        "limit": 1,
        "substore": substore_id,
    }
    data = s.get(BASE + "/api/1/entity/ms.products",
                 headers=h, params=params, timeout=15).json().get("data", [])
    if not data:
        return False, alias, 0
    p = data[0]
    name = p.get("name", alias)
    try:
        available = int(p.get("available", 0) or 0)
    except (TypeError, ValueError):
        available = 0
    try:
        qty = int(p.get("inventory_quantity", 0) or 0)
    except (TypeError, ValueError):
        qty = 0
    seller_ids = p.get("seller_substore_ids", []) or []
    in_stock = available == 1 and substore_id in seller_ids
    return in_stock, name, qty


def telegram(text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text,
              "parse_mode": "Markdown", "disable_web_page_preview": False},
        timeout=15,
    )


def main():
    # Load what we saw last time so we only ping on a fresh restock.
    try:
        last = json.loads(STATE_FILE.read_text())
    except Exception:
        last = {}

    s, substore_id = open_session_for_pincode(PINCODE)

    new_state = {}
    for alias in ALIASES:
        try:
            in_stock, name, qty = check_product(s, substore_id, alias)
        except Exception as e:
            print(f"[warn] could not check {alias}: {e}")
            new_state[alias] = last.get(alias, "Unknown")
            continue

        status = "In Stock" if in_stock else "Sold Out"
        print(f"{name}: {status} (qty {qty})")
        new_state[alias] = status

        # Notify only when it changes INTO 'In Stock'.
        if in_stock and last.get(alias) != "In Stock":
            url = f"{BASE}/en/product/{alias}"
            telegram(
                f"\U0001F7E2 *Back in stock!*\n*{name}* is now buyable "
                f"for pincode {PINCODE}.\n\n[Open product page]({url})"
            )

    STATE_FILE.write_text(json.dumps(new_state, indent=2))


if __name__ == "__main__":
    main()
