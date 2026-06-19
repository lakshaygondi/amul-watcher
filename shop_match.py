"""
Slice 2 of the shopping tool (tiered palette).

Searches Google Shopping (India), detects each product's dominant colour,
and scores it against Lorik's Autumn palette. Warm signature colours can hit
100%; cool-dark neutrals (black/navy/charcoal/grey) are wearable but capped
so they don't crowd out the statement colours. Ranks by match, sends the top
picks to Telegram as photos.

Size is still just a search hint (typed into the query) — confirm on-site.
"""

import os
import io
import requests
import numpy as np
from PIL import Image

# Warm Autumn signature colours — full weight, can reach 100%.
WARM = {
    "rust":          "#B7410E",
    "terracotta":    "#C66B3D",
    "burnt orange":  "#CC5500",
    "mustard":       "#D4A017",
    "camel":         "#C19A6B",
    "olive":         "#6B7233",
    "forest green":  "#2E5E3A",
    "burgundy":      "#6E2233",
    "warm brown":    "#6F4E37",
    "tan":           "#C8A876",
    "cream":         "#EADDBF",
}

# Cool / dark neutrals — wearable basics, but capped so they rank below warms.
NEUTRAL = {
    "charcoal": "#36393B",
    "navy":     "#262B38",
    "black":    "#1C1C1C",
    "grey":     "#8A8D8F",
}

MAX_PHOTOS = 6
DIST_ZERO = 60.0       # LAB distance treated as "0% match"
NEUTRAL_CAP = 75       # neutrals can't score above this


def _srgb_to_lab(rgb):
    rgb = np.asarray(rgb, dtype=float) / 255.0
    rgb = np.where(rgb > 0.04045, ((rgb + 0.055) / 1.055) ** 2.4, rgb / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = (m @ rgb) / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


_WARM_LAB = {n: _srgb_to_lab(_hex_to_rgb(h)) for n, h in WARM.items()}
_NEUT_LAB = {n: _srgb_to_lab(_hex_to_rgb(h)) for n, h in NEUTRAL.items()}


def dominant_color(img):
    arr = np.asarray(img.convert("RGB").resize((64, 64))).reshape(-1, 3).astype(float)
    mask = ~(((arr > 235).all(axis=1)) | ((arr < 25).all(axis=1)))
    pix = arr[mask] if mask.sum() > 30 else arr
    buckets = (pix // 24 * 24).astype(int)
    vals, counts = np.unique(buckets, axis=0, return_counts=True)
    modal = vals[counts.argmax()]
    return pix[(buckets == modal).all(axis=1)].mean(axis=0)


def _nearest(lab, table):
    name, dist = min(((n, float(np.linalg.norm(lab - l))) for n, l in table.items()),
                     key=lambda x: x[1])
    pct = max(0, round(100 * (1 - min(dist, DIST_ZERO) / DIST_ZERO)))
    return pct, name


def match_score(rgb):
    """Best warm match (full) vs best neutral match (capped); higher wins."""
    lab = _srgb_to_lab(rgb)
    wpct, wname = _nearest(lab, _WARM_LAB)
    npct, nname = _nearest(lab, _NEUT_LAB)
    npct = min(npct, NEUTRAL_CAP)
    return (wpct, wname) if wpct >= npct else (npct, nname)


def search_shopping(query, key):
    params = {"engine": "google_shopping", "q": query, "gl": "in",
              "hl": "en", "google_domain": "google.co.in", "api_key": key}
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("shopping_results") or []


def send_message(token, chat, text):
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                  json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
                  timeout=20)


def send_photo(token, chat, photo_url, caption):
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                      data={"chat_id": chat, "photo": photo_url, "caption": caption[:1024]},
                      timeout=30)
    except Exception as e:
        print("photo send failed:", e)


def main():
    key   = os.environ["SERPAPI_KEY"]
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat  = os.environ["TELEGRAM_CHAT_ID"]
    query = os.environ.get("QUERY", "men's casual shirt").strip()

    products = search_shopping(query, key)
    if not products:
        send_message(token, chat, f"No results for '{query}'.")
        return

    scored = []
    for p in products:
        thumb = p.get("thumbnail")
        if not thumb:
            continue
        try:
            img = Image.open(io.BytesIO(requests.get(thumb, timeout=20).content))
            pct, near = match_score(dominant_color(img))
        except Exception as e:
            print("skip (image error):", e)
            continue
        scored.append({
            "title": (p.get("title") or "")[:90],
            "price": p.get("price") or "?",
            "source": p.get("source") or "?",
            "link": p.get("link") or p.get("product_link") or "",
            "thumb": thumb, "pct": pct, "near": near,
        })

    scored.sort(key=lambda x: x["pct"], reverse=True)

    send_message(token, chat,
                 f"Top Autumn-palette matches for: {query}\n"
                 f"(scored {len(scored)} products, showing best {min(MAX_PHOTOS, len(scored))})")

    for i, it in enumerate(scored[:MAX_PHOTOS], 1):
        caption = (f"{i}. {it['title']}\n"
                   f"{it['price']} - {it['source']}\n"
                   f"Colour: reads as {it['near']} ({it['pct']}% palette match)\n"
                   f"{it['link']}")
        send_photo(token, chat, it["thumb"], caption)
        print(f"{i}. {it['pct']}% {it['near']} - {it['title']}")


if __name__ == "__main__":
    main()
