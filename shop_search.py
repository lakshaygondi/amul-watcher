"""
Slice 1 of the shopping tool.

Searches Google Shopping (India) for whatever term you type, then sends the
top results to your Telegram — plus a tally of which retailers showed up.
This is just reconnaissance: no size filtering or colour-scoring yet. We're
checking what real data comes back from Amazon / Myntra / Ajio before we
build anything on top.
"""

import os
import requests

SERPAPI_KEY = os.environ["SERPAPI_KEY"]
BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID     = os.environ["TELEGRAM_CHAT_ID"]
QUERY       = os.environ.get("QUERY", "men's casual shirt").strip()

MAX_ITEMS = 12  # how many products to list in the Telegram message


def search_shopping(query):
    """Call SerpApi's Google Shopping engine, India locale."""
    params = {
        "engine": "google_shopping",
        "q": query,
        "gl": "in",                       # country: India
        "hl": "en",                       # language: English
        "google_domain": "google.co.in",
        "api_key": SERPAPI_KEY,
    }
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def telegram(text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )


def main():
    data = search_shopping(QUERY)
    results = data.get("shopping_results") or []

    if not results:
        err = data.get("error", "no shopping_results returned")
        telegram(f"Search for '{QUERY}' returned nothing. ({err})")
        print("No results:", err)
        return

    # Tally which retailers appeared — the key thing we want to learn here.
    sources = {}
    for p in results:
        src = p.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1

    lines = [f"Results for: {QUERY}", f"({len(results)} found)\n"]
    for i, p in enumerate(results[:MAX_ITEMS], 1):
        title = (p.get("title") or "")[:80]
        price = p.get("price") or "?"
        src   = p.get("source") or "?"
        link  = p.get("link") or p.get("product_link") or ""
        lines.append(f"{i}. {title}\n   {price} - {src}\n   {link}")

    summary = "Retailers seen: " + ", ".join(
        f"{k} ({v})" for k, v in sorted(sources.items(), key=lambda x: -x[1])
    )
    lines.append("\n" + summary)

    msg = "\n".join(lines)
    if len(msg) > 4000:                   # Telegram's per-message limit is ~4096
        msg = msg[:4000] + "\n...(truncated)"
    telegram(msg)

    for line in lines:                    # also dump to the Actions log
        print(line)


if __name__ == "__main__":
    main()
