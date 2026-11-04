#!/usr/bin/env python3
"""
Збирає 8 товарних фідів Meta для yapiko.ua (4 міста × Сети / Асорті).

Для кожного міста відкривається чистий браузер (без cookies), на сайті обирається місто,
після чого з карток товарів беруться назва, ціни, наявність, фото, склад і id.
Ціни перевіряються з Google Shopping фідом цього міста (там, де товар є в обох).

    python build_feeds.py            # зібрати фіди в feeds/
    python build_feeds.py --debug    # додатково зберегти HTML/скріни/cookies у debug/
"""
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from playwright.sync_api import sync_playwright

BASE = "https://yapiko.ua"
OUT = Path("feeds")
DEBUG = Path("debug")
G = "{http://base.google.com/ns/1.0}"

CITIES = {
    "harkiv": "Харків",
    "kyiv": "Київ",
    "odesa": "Одеса",
    "poltava": "Полтава",
}

WEEKLY = "WEEKLY"  # Сет тижня: шукається на сторінці сетів міста
FEEDS = {
    "sety": [
        (WEEKLY, "Сети"),
        ("set-haruko", "Сети"),
        ("pica-kurka-gribi-ta-bekon", "Піца"),
        ("set-shinobu", "Сети"),
        ("mini-set", "Сети"),
        ("set-5-filadel-fij", "Сети"),
    ],
    "asorti": [
        (WEEKLY, "Сети"),
        ("pica-kurka-gribi-ta-bekon", "Піца"),
        ("sup-tom-jam", "Супи"),
        ("sushi-burger-z-krevetkoju", "Бургери"),
        ("party-kombo-miks", "Party combo"),
    ],
}

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍⬀-⯿]")


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = EMOJI.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_title(s):
    s = clean(s)
    letters = re.sub(r"[^\w]|\d|_", "", s)
    if len(letters) > 3 and letters.upper() == letters:
        s = s.title()
    return s


def money(v):
    return f"{float(v):.2f} UAH"


def original_image(url):
    """Прибирає /cache/<hash>/ з адреси фото Magento, щоб отримати оригінал."""
    return re.sub(r"/media/catalog/product/cache/[0-9a-f]+/", "/media/catalog/product/", url or "")


def google_feed(city):
    """{url_key: price} з Google Shopping фіду міста — для звірки цін."""
    url = f"{BASE}/media/googleshopping/googleshopping-{city}-2.xml"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as r:
            root = ET.fromstring(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"  ! Google-фід {city} не прочитано: {e}")
        return {}
    res = {}
    for it in root.iter("item"):
        link = (it.findtext(G + "link") or "").rstrip("/")
        key = link.rsplit("/", 1)[-1]
        price = it.findtext(G + "sale_price") or it.findtext(G + "price") or ""
        try:
            p = float(price.split()[0])
        except (ValueError, IndexError):
            continue
        res.setdefault(key, []).append(p)
    return res


# ---------------------------------------------------------------- браузер

JS_PRODUCT = r"""
() => {
  const q = s => document.querySelector(s);
  const meta = p => { const m = q(`meta[property="${p}"],meta[name="${p}"]`); return m ? m.content : null; };
  const box = q('.product-info-main .price-box, .price-box[data-product-id], .price-box');
  const amt = t => { const e = box && box.querySelector(`[data-price-type="${t}"]`); return e ? e.getAttribute('data-price-amount') : null; };
  const img = q('.fotorama__stage__frame img, .gallery-placeholder img, .product.media img, img.gallery-placeholder__image');
  const info = q('.product-info-main') || document.body;
  const sku = q('[itemprop="sku"], .product.attribute.sku .value');
  return {
    title: (q('h1') || {}).innerText || meta('og:title'),
    final: amt('finalPrice') || meta('product:price:amount'),
    old: amt('oldPrice'),
    availability: meta('product:availability'),
    stockText: (q('.stock') || {}).innerText || '',
    productId: box ? box.getAttribute('data-product-id') : null,
    sku: sku ? sku.innerText.trim() : null,
    image: (img && (img.getAttribute('src') || img.getAttribute('data-src'))) || meta('og:image'),
    canonical: (q('link[rel="canonical"]') || {}).href || location.href,
    infoText: info.innerText,
    ogDescription: meta('og:description') || meta('description'),
    url: location.href,
  };
}
"""


def extract_description(info_text):
    """З тексту картки: «32 шт, 1060 г. Склад: …»."""
    t = info_text or ""
    pcs = re.search(r"(\d+)\s*шт\.?(?!\))", t)
    wt = re.search(r"(\d[\d\s]*)\s*г\b", t)
    lines = [l.strip(" ;•-") for l in t.splitlines()]
    comp = [l for l in lines if re.search(r"\(\d+\s*шт", l) or l.lower().startswith("склад")]
    if not comp:
        m = re.search(r"Склад[:\s]+(.+)", t)
        comp = [m.group(1)] if m else []
    head = []
    if pcs:
        head.append(f"{pcs.group(1)} шт")
    if wt:
        head.append(f"{wt.group(1).replace(' ', '')} г")
    out = ", ".join(head)
    if comp:
        c = clean("; ".join(comp))
        c = re.sub(r"^склад[:\s]*", "", c, flags=re.I)
        out += (". " if out else "") + "Склад: " + c
    return out


def select_city(page, city, label, dbg):
    """Обирає місто на сайті. Повертає опис способу, яким це вдалося."""
    page.goto(f"{BASE}/ua/{city}/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    if dbg:
        page.screenshot(path=str(dbg / "01_home.png"))
        (dbg / "01_home.html").write_text(page.content(), encoding="utf-8")
    # Спроба: відкрити перемикач «Ваше місто» і клікнути назву міста
    tried = []
    for opener in ["text=Ваше місто", "[class*=city] >> visible=true", "[data-city]", "header >> text=/Харків|Київ|Одеса|Полтава/"]:
        try:
            el = page.locator(opener).first
            if el.count() and el.is_visible():
                el.click(timeout=3000)
                page.wait_for_timeout(1000)
                tried.append(opener)
                break
        except Exception:  # noqa: BLE001
            continue
    if dbg:
        page.screenshot(path=str(dbg / "02_switcher.png"))
        (dbg / "02_switcher.html").write_text(page.content(), encoding="utf-8")
    for sel in [f"[role=dialog] >> text='{label}'", f".modal >> text='{label}'", f"text='{label}'", f"text={label}"]:
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                el.click(timeout=3000)
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(2500)
                tried.append(sel)
                break
        except Exception:  # noqa: BLE001
            continue
    if dbg:
        page.screenshot(path=str(dbg / "03_after.png"))
        (dbg / "cookies.json").write_text(json.dumps(page.context.cookies(), ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            ls = page.evaluate("() => Object.fromEntries(Object.entries(localStorage))")
        except Exception:  # noqa: BLE001
            ls = {}
        (dbg / "localStorage.json").write_text(json.dumps(ls, ensure_ascii=False, indent=1)[:200000], encoding="utf-8")
    return " → ".join(tried) or "лише /ua/{city}/"


def find_weekly(page, city):
    for path in (f"/ua/{city}/sushi/set/", "/ua/sushi/set/"):
        page.goto(BASE + path, wait_until="domcontentloaded", timeout=60000)
        hrefs = page.eval_on_selector_all("a[href*='set-tizhnja']", "els => els.map(e => e.href)")
        if hrefs:
            return hrefs[0].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return None


def scrape_product(page, url_key, dbg):
    page.goto(f"{BASE}/ua/{url_key}/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    data = page.evaluate(JS_PRODUCT)
    try:
        dl = page.evaluate("() => JSON.stringify(window.dataLayer || [])")
    except Exception:  # noqa: BLE001
        dl = "[]"
    data["dataLayer"] = dl
    if dbg:
        (dbg / f"p_{url_key}.html").write_text(page.content(), encoding="utf-8")
        (dbg / f"p_{url_key}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def pixel_id(data):
    """content_ids з dataLayer (якщо сайт їх туди кладе), інакше data-product-id."""
    for pat in (r'"content_ids"\s*:\s*\[\s*"?([^",\]]+)', r'"item_id"\s*:\s*"([^"]+)"', r'"id"\s*:\s*"?(\d+)"?'):
        m = re.search(pat, data.get("dataLayer") or "")
        if m:
            return m.group(1)
    return data.get("productId") or data.get("sku") or ""


def to_item(data, url_key, ptype, city, feed, weekly):
    final = float(data["final"]) if data.get("final") else 0.0
    old = float(data["old"]) if data.get("old") else 0.0
    price, sale = (old, final) if old > final > 0 else (final, None)
    av = (data.get("availability") or "").lower()
    if av not in ("in stock", "out of stock"):
        av = "out of stock" if re.search(r"немає|out of stock|недоступ", data.get("stockText", ""), re.I) else "in stock"
    return {
        "id": "set-tyzhnya" if weekly else pixel_id(data),
        "title": clean_title(data.get("title") or ""),
        "description": extract_description(data.get("infoText")) or clean(data.get("ogDescription")),
        "availability": av,
        "condition": "new",
        "price": money(price),
        "sale_price": money(sale) if sale else "",
        "link": f"{BASE}/ua/{url_key}/",
        "image_link": original_image(data.get("image") or ""),
        "brand": "ЯПІКО",
        "google_product_category": "422",
        "product_type": ptype,
        "custom_label_0": city,
        "custom_label_1": feed,
    }


def render(items, city, feed):
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">', "  <channel>",
           f"    <title>ЯПІКО {city} {feed}</title>",
           f"    <link>{BASE}/ua/</link>",
           f"    <description>Каталог Meta: {city} / {feed}. Оновлено {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC</description>"]
    for it in items:
        out.append("    <item>")
        for k, v in it.items():
            if k == "sale_price" and not v:
                continue
            out.append(f"      <g:{k}>{escape(str(v))}</g:{k}>")
        out.append("    </item>")
    out += ["  </channel>", "</rss>", ""]
    return "\n".join(out)


def main():
    debug = "--debug" in sys.argv
    OUT.mkdir(exist_ok=True)
    report = []
    ok_all = True
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for city, label in CITIES.items():
            print(f"== {city}")
            dbg = DEBUG / city if debug else None
            if dbg:
                dbg.mkdir(parents=True, exist_ok=True)
            ctx = browser.new_context(locale="uk-UA", viewport={"width": 1366, "height": 900})
            page = ctx.new_page()
            how = select_city(page, city, label, dbg)
            print(f"  місто обрано: {how}")
            gfeed = google_feed(city)
            weekly_key = find_weekly(page, city)
            print(f"  Сет тижня: {weekly_key}")
            cache = {}
            for feed, slots in FEEDS.items():
                items, errs = [], []
                for key, ptype in slots:
                    weekly = key == WEEKLY
                    url_key = weekly_key if weekly else key
                    if not url_key:
                        errs.append("Сет тижня не знайдено")
                        continue
                    if url_key not in cache:
                        cache[url_key] = scrape_product(page, url_key, dbg)
                    data = cache[url_key]
                    if not data.get("final"):
                        errs.append(f"{url_key}: немає ціни")
                        continue
                    it = to_item(data, url_key, ptype, city, feed, weekly)
                    items.append(it)
                    eff = float((it["sale_price"] or it["price"]).split()[0])
                    gp = gfeed.get(url_key)
                    check = ""
                    if gp:
                        check = "✓ Google" if any(abs(eff - p) < 0.01 for p in gp) else f"✗ Google {gp}"
                        if check.startswith("✗"):
                            errs.append(f"{url_key}: ціна {eff} ≠ Google-фід {gp}")
                    report.append(f"{city:8} {feed:6} {it['id']:>12}  {it['title'][:34]:34} {it['price']:>12} {it['sale_price']:>12}  {it['availability']:12} {check}")
                if len(items) == len(slots) and not errs:
                    (OUT / f"{city}_{feed}.xml").write_text(render(items, city, feed), encoding="utf-8")
                    print(f"  OK {city}_{feed}.xml")
                else:
                    ok_all = False
                    print(f"  FAIL {city}_{feed}.xml: {'; '.join(errs)}  (попередня версія залишається)")
                    if not (OUT / f"{city}_{feed}.xml").exists() and items:
                        (OUT / f"{city}_{feed}.xml").write_text(render(items, city, feed), encoding="utf-8")
            ctx.close()
        browser.close()
    (OUT / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
