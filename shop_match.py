"""
Slice 2 of the shopping tool.

Searches Google Shopping (India), then for each product detects the dominant
colour of its image and scores how close it sits to Lorik's Autumn palette
(his flattering warm/earthy colours plus the neutrals he actually wears).
Ranks by best colour match and sends the top picks to Telegram as photos.

Size is still just a search hint (typed into the query) — confirm on-site.
"""

import os
import io
import requests
import numpy as np
from PIL import Image

# --- The target palette: Autumn signature colours + wearable neutrals --------
# Items whose dominant colour lands near any of these score high. Cool pastels,
# neons and icy tones aren't listed, so they naturally fall to the bottom.
PALETTE = {
    "rust":          "#B7410E",
    "terracotta":    "#C66B3D",
    "burnt orange":  "#CC5500",
    "mustard":       "#D4A017",
    "camel":         "#C19A6B",
    "olive":         "#6B7233",
    "forest green":  "#2E5E3A",
    "burgundy":      "#6E2233",
    "warm brown":    "#6F4E37",
    "cream":         "#EADDBF",
    "charcoal":      "#36393B",
    "navy":          "#262B38",
    "black":         "#1C1C1C",
    "tan":           "#C8A876",
}

MAX_PHOTOS = 6          # how many top matches to send as photos
DIST_ZERO = 60.0        # LAB distance treated as "0% match"


# --- colour maths -------------------------------------------------------------
def _srgb_to_lab(rgb):
    """Convert an (r,g,b) 0-255 colour to CIE-Lab (D65)."""
    rgb = np.asarray(rgb, dtype=float) / 255.0
    # inverse gamma
    rgb = np.where(rgb > 0.04045, ((rgb + 0.055) / 1.055) ** 2.4, rgb / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = m @ rgb
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])   # D65 white
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[1] - 16
    a = 500 * (f[0] - f[1])
    b = 200 * (f[1] - f[2])
    return np.array([L, a, b])


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# Pre-compute LAB for every palette colour once.
_PALETTE_LAB = {name: _srgb_to_lab(_hex_to_rgb(hx)) for name, hx in PALETTE.items()}


def dominant_color(img):
    """Find the main product colour, ignoring white/black background & shadow."""
    arr = np.asarray(img.convert("RGB").resize((64, 64))).reshape(-1, 3).astype(float)
    mask = ~(((arr > 235).all(axis=1)) | ((arr < 25).all(axis=1)))  # drop bg/shadow
    pix = arr[mask] if mask.sum() > 30 else arr
    buckets = (pix // 24 * 24).astype(int)                          # quantise
    vals, counts = np.unique(buckets, axis=0, return_counts=True)
    modal = vals[counts.argmax()]
    in_bucket = pix[(buckets == modal).all(axis=1)]                 # true centroid
    return in_bucket.mean(axis=0)


def match_score(rgb):
    """Return (match_percent, nearest_palette_name) for a colour."""
    lab = _srgb_to_lab(rgb)
    best_name, best_dist = None, 1e9
    for name, plab in _PALETTE_LAB.items():
        d = float(np.linalg.norm(lab - plab))
        if d < best_dist:
            best_dist, best_name = d, name
    pct = max(0, round(100 * (1 - min(best_dist, DIST_ZERO) / DIST_ZERO)))
    return pct, best_name


# --- data + delivery ----------------------------------------------------------
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
