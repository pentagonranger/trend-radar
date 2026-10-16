#!/usr/bin/env python3
"""
Trend Radar — ambil topik trending dari beberapa sumber lalu bikin dashboard HTML.

Sumber:
  - Google Trends (RSS, tanpa API key)
  - Google News Indonesia (RSS, tanpa API key)
  - Reddit r/indonesia (JSON publik, tanpa key)
  - YouTube Data API (butuh YOUTUBE_API_KEY, gratis)
  - X / Twitter API (opsional, butuh X_BEARER_TOKEN; biasanya paket berbayar)

Pakai:
  python fetch_trends.py                 # wilayah Indonesia (ID)
  python fetch_trends.py --geo US --lang en
  python fetch_trends.py --demo          # tanpa internet, pakai data contoh

Hanya butuh Python 3.8+ (library standar).
"""
import argparse
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TZ = timezone(timedelta(hours=7))  # WIB
UA = "Mozilla/5.0 (compatible; TrendRadar/1.0)"
X_WOEID = {"ID": 23424846, "US": 23424977, "GB": 23424975, "SG": 23424948, "MY": 23424901}

# ---------------------------------------------------------------- util
def http_get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def local(tag):
    return tag.split("}", 1)[-1]


def child_text(el, name):
    for c in el:
        if local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_traffic(s):
    """'200K+' -> 200000, '2M+' -> 2000000, '500+' -> 500"""
    m = re.match(r"\s*([\d.,]+)\s*([KkMmBb]?)", s or "")
    if not m:
        return 0
    num = float(m.group(1).replace(",", ""))
    mult = {"": 1, "k": 1e3, "m": 1e6, "b": 1e9}[m.group(2).lower()]
    return int(num * mult)


def to_wib_date(dt):
    return dt.astimezone(TZ).strftime("%Y-%m-%d")


def today():
    return datetime.now(TZ).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- kategori
CATS = [
    ("Olahraga", r"timnas|liga|piala|motogp|f1|badminton|sepak ?bola|gol\b|juara|medali|asian games|nba|nfl|mlb|ufc|tinju|persib|persija|vs\b|fc\b"),
    ("Politik", r"presiden|prabowo|jokowi|gibran|dpr|dpd|menteri|pemilu|pilkada|parlemen|partai|kabinet|istana|mk\b|mahkamah|reshuffle|election|senate|congress"),
    ("Hukum & Kriminal", r"polisi|polri|kapolri|kpk|kejaksaan|jaksa|hakim|sidang|tersangka|korupsi|narkoba|pembunuhan|penipuan|lapas|bareskrim|ditangkap|vonis"),
    ("Ekonomi & Bisnis", r"ihsg|saham|rupiah|dolar|emas|bbm|harga|inflasi|pajak|bank|bi rate|ekonomi|bisnis|investasi|ojol|umkm|stock|market|crypto|bitcoin"),
    ("Bencana & Lingkungan", r"gempa|banjir|longsor|erupsi|gunung|bmkg|cuaca|karhutla|kebakaran|asap|tsunami|kekeringan|hujan|badai"),
    ("Teknologi", r"iphone|android|samsung|openai|chatgpt|claude|gemini|\bai\b|aplikasi|update|ios|gadget|nvidia|tesla|google|apple|microsoft|startup"),
    ("Musik & Hiburan", r"lagu|album|konser|artis|film|drama|sinetron|netflix|comeback|trailer|mv\b|single|k-?pop|idol|selebriti|serial|episode"),
    ("Cek Fakta", r"hoaks|hoax|cek fakta|fact ?check|disinformasi"),
    ("Kesehatan", r"kesehatan|rumah sakit|vaksin|penyakit|virus|obat|bpjs|health"),
]
YT_CATS = {"1": "Film & Series", "2": "Otomotif", "10": "Musik & Hiburan", "15": "Hewan", "17": "Olahraga",
           "20": "Gaming", "22": "Vlog & Gaya Hidup", "23": "Komedi", "24": "Musik & Hiburan", "25": "Berita & Politik",
           "26": "Tips & Gaya Hidup", "27": "Edukasi", "28": "Teknologi"}


def classify(text):
    low = text.lower()
    for name, pat in CATS:
        if re.search(pat, low):
            return name
    return "Lainnya"


# ---------------------------------------------------------------- sumber
def parse_google_trends(xml_text):
    out = []
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        q = child_text(item, "title")
        if not q:
            continue
        traffic = parse_traffic(child_text(item, "approx_traffic"))
        news = []
        for c in item:
            if local(c.tag) == "news_item":
                news.append({"title": child_text(c, "news_item_title"),
                             "url": child_text(c, "news_item_url"),
                             "source": child_text(c, "news_item_source")})
        try:
            d = to_wib_date(parsedate_to_datetime(child_text(item, "pubDate")))
        except Exception:
            d = today()
        srcs = ["Google Trends"] + list(dict.fromkeys(n["source"] for n in news if n["source"]))[:2]
        text = q + " " + " ".join(n["title"] for n in news)
        out.append({
            "t": q[:1].upper() + q[1:], "q": q, "cat": classify(text), "reg": None,
            "ch": ["Google Trends"] + (["Portal Berita"] if news else []),
            "src": srcs, "d": d,
            "url": news[0]["url"] if news else "",
            "n": news[0]["title"] if news else "",
            "vol": traffic, "vu": "pencarian",
        })
    return out


def fetch_google_trends(geo):
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    return parse_google_trends(http_get(url))


def parse_google_news(xml_text, limit=25):
    out = []
    root = ET.fromstring(xml_text)
    for item in list(root.iter("item"))[:limit]:
        title = child_text(item, "title")
        link = child_text(item, "link")
        src = child_text(item, "source")
        if " - " in title:
            title, tail = title.rsplit(" - ", 1)
            src = src or tail
        try:
            d = to_wib_date(parsedate_to_datetime(child_text(item, "pubDate")))
        except Exception:
            d = today()
        out.append({"t": title, "q": title, "cat": classify(title), "reg": None,
                    "ch": ["Google Berita"], "src": [src or "Google News"], "d": d, "url": link, "n": ""})
    return out


def fetch_google_news(geo, lang):
    url = f"https://news.google.com/rss?hl={lang}&gl={geo}&ceid={geo}:{lang}"
    return parse_google_news(http_get(url))


def parse_reddit(json_text, sub, limit=15):
    out = []
    for ch in json.loads(json_text)["data"]["children"]:
        p = ch["data"]
        if p.get("stickied"):
            continue
        d = to_wib_date(datetime.fromtimestamp(p["created_utc"], tz=timezone.utc))
        out.append({"t": p["title"], "q": p["title"][:80], "cat": classify(p["title"]), "reg": None,
                    "ch": ["Reddit"], "src": [f"r/{sub}"], "d": d,
                    "url": "https://www.reddit.com" + p["permalink"],
                    "n": f'{p.get("num_comments", 0)} komentar',
                    "vol": int(p.get("score", 0)), "vu": "upvotes"})
        if len(out) >= limit:
            break
    return out


def fetch_reddit(sub):
    return parse_reddit(http_get(f"https://www.reddit.com/r/{sub}/hot.json?limit=30&raw_json=1"), sub)


def fetch_youtube(geo, key):
    params = urllib.parse.urlencode({"part": "snippet,statistics", "chart": "mostPopular",
                                     "regionCode": geo, "maxResults": 20, "key": key})
    data = json.loads(http_get("https://www.googleapis.com/youtube/v3/videos?" + params))
    out = []
    for v in data.get("items", []):
        sn, st = v["snippet"], v.get("statistics", {})
        out.append({"t": sn["title"], "q": sn["title"][:80],
                    "cat": YT_CATS.get(sn.get("categoryId"), classify(sn["title"])), "reg": None,
                    "ch": ["YouTube"], "src": [sn.get("channelTitle", "YouTube")], "d": today(),
                    "url": "https://www.youtube.com/watch?v=" + v["id"], "n": "",
                    "vol": int(st.get("viewCount", 0)), "vu": "views"})
    return out


def fetch_x(geo, token):
    woeid = X_WOEID.get(geo)
    if not woeid:
        raise RuntimeError(f"WOEID untuk {geo} belum ada di X_WOEID")
    data = json.loads(http_get(f"https://api.twitter.com/2/trends/by/woeid/{woeid}",
                               headers={"Authorization": "Bearer " + token}))
    out = []
    for tr in data.get("data", []):
        name = tr.get("trend_name") or tr.get("name", "")
        if not name:
            continue
        out.append({"t": name, "q": name, "cat": classify(name), "reg": None, "ch": ["X"], "src": ["X"],
                    "d": today(), "url": "https://x.com/search?q=" + urllib.parse.quote(name), "n": "",
                    "vol": int(tr.get("tweet_count", 0) or 0), "vu": "posts"})
    return out


# ---------------------------------------------------------------- gabung
def norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split()


def merge(records):
    """Gabungkan record yang sama topiknya. Anchor = record Google Trends / X (keyword pendek);
    record lain digabung jika semua kata keyword ada di judulnya."""
    anchors = [r for r in records if "Google Trends" in r["ch"] or "X" in r["ch"]]
    others = [r for r in records if r not in anchors]
    rest = []
    for o in others:
        words = set(norm(o["t"]))
        hit = None
        for a in anchors:
            kw = norm(a["q"])
            if kw and len(kw) <= 6 and all(w in words for w in kw):
                hit = a
                break
        if not hit:
            rest.append(o)
            continue
        hit["ch"] = list(dict.fromkeys(hit["ch"] + o["ch"]))
        hit["src"] = list(dict.fromkeys(hit["src"] + o["src"]))[:5]
        hit["url"] = hit["url"] or o["url"]
        hit["n"] = hit["n"] or o["t"]
        hit["d"] = max(hit["d"], o["d"])
    merged = anchors + rest
    # buang duplikat judul persis
    seen, uniq = set(), []
    for r in merged:
        k = " ".join(norm(r["t"]))[:80]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq


# ---------------------------------------------------------------- demo
DEMO_TRENDS = """<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>motogp mandalika</title><ht:approx_traffic>200K+</ht:approx_traffic><pubDate>Tue, 13 Oct 2026 10:00:00 +0700</pubDate>
<ht:news_item><ht:news_item_title>MotoGP Mandalika 2026 gerakkan ekonomi</ht:news_item_title><ht:news_item_url>https://example.com/a</ht:news_item_url><ht:news_item_source>Liputan6</ht:news_item_source></ht:news_item></item>
<item><title>ihsg</title><ht:approx_traffic>50K+</ht:approx_traffic><pubDate>Tue, 13 Oct 2026 09:00:00 +0700</pubDate>
<ht:news_item><ht:news_item_title>IHSG melemah, saham teknologi koreksi</ht:news_item_title><ht:news_item_url>https://example.com/b</ht:news_item_url><ht:news_item_source>Kompas</ht:news_item_source></ht:news_item></item>
</channel></rss>"""
DEMO_NEWS = """<?xml version="1.0"?><rss><channel>
<item><title>MotoGP Mandalika 2026 Diprediksi Gerakkan Ekonomi - Kompas.com</title><link>https://example.com/c</link><pubDate>Tue, 13 Oct 2026 08:00:00 +0700</pubDate></item>
<item><title>BMKG Prediksi Hujan Lebat 13-15 Oktober - Detik</title><link>https://example.com/d</link><pubDate>Tue, 13 Oct 2026 07:00:00 +0700</pubDate></item>
</channel></rss>"""
DEMO_REDDIT = json.dumps({"data": {"children": [{"data": {"title": "Kabut asap makin parah di Kalimantan", "stickied": False,
                          "created_utc": 1791849600, "permalink": "/r/indonesia/comments/x/y/", "num_comments": 312, "score": 4210}}]}})


def demo_records(geo):
    return (parse_google_trends(DEMO_TRENDS) + parse_google_news(DEMO_NEWS) + parse_reddit(DEMO_REDDIT, "indonesia"))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--geo", default="ID", help="kode negara, mis. ID, US, SG")
    ap.add_argument("--lang", default="id", help="bahasa Google News, mis. id, en")
    ap.add_argument("--subreddit", default=None, help="default: indonesia untuk ID, popular untuk lainnya")
    ap.add_argument("--out", default=os.path.join(HERE, "dashboard.html"))
    ap.add_argument("--demo", action="store_true", help="pakai data contoh (tanpa internet)")
    args = ap.parse_args()

    records, report = [], []

    def run(name, fn):
        try:
            rows = fn()
            records.extend(rows)
            report.append(f"  ✓ {name}: {len(rows)} topik")
        except Exception as e:  # satu sumber gagal tidak boleh menjatuhkan semuanya
            report.append(f"  ✗ {name}: gagal ({type(e).__name__}: {e})")

    if args.demo:
        run("Demo", lambda: demo_records(args.geo))
    else:
        run("Google Trends", lambda: fetch_google_trends(args.geo))
        run("Google News", lambda: fetch_google_news(args.geo, args.lang))
        run("Reddit", lambda: fetch_reddit(args.subreddit or ("indonesia" if args.geo == "ID" else "popular")))
        yt = os.environ.get("YOUTUBE_API_KEY")
        if yt:
            run("YouTube", lambda: fetch_youtube(args.geo, yt))
        else:
            report.append("  – YouTube dilewati (set YOUTUBE_API_KEY untuk mengaktifkan)")
        xt = os.environ.get("X_BEARER_TOKEN")
        if xt:
            run("X", lambda: fetch_x(args.geo, xt))
        else:
            report.append("  – X dilewati (set X_BEARER_TOKEN untuk mengaktifkan)")

    reg = "ID" if args.geo == "ID" else "Global"
    for r in records:
        r["reg"] = reg
        r.setdefault("vol", 0)
        if not r["vol"]:
            r.pop("vol")
            r.pop("vu", None)

    rows = merge(records)
    print("\n".join(report))
    if not rows:
        print("Tidak ada data yang berhasil diambil — dashboard tidak dibuat.")
        sys.exit(1)

    now = datetime.now(TZ)
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        html = f.read()
    # '</' di dalam <script> bisa menutup tag lebih awal, jadi di-escape
    payload = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    html = (html.replace("__DATA__", payload)
                .replace("__SNAP__", now.strftime("%Y-%m-%d"))
                .replace("__UPDATED__", now.strftime("%d %b %Y, %H:%M WIB")))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.splitext(args.out)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\nSelesai: {len(rows)} topik → {args.out}")


if __name__ == "__main__":
    main()
