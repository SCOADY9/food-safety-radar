#!/usr/bin/env python3
"""Food Safety Radar collector.

Pulls food-safety signals from regulators, RSS feeds, news (GDELT) and weather
(Open-Meteo), normalises them into one schema, tags hazard / region / severity,
and writes site/data/alerts.json for the dashboard.

Standard library only, so it runs anywhere (laptop, GitHub Actions, a server).
    python collector/collect.py            # fetch everything
    python collector/collect.py --offline  # re-tag the existing file only
"""
import hashlib, json, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data" / "alerts.json"
STATE = Path(__file__).parent / "state" / "seen_links.json"   # page-watch memory, committed with the data
SOURCES = json.loads((Path(__file__).parent / "sources.json").read_text())
KEEP_DAYS = 180          # alerts
KEEP_DAYS_COMPETITOR = 365
UA = {"User-Agent": "FoodSafetyRadar/0.1 (contact: you@example.com)"}

# ---------------------------------------------------------------- regions
REGIONS = {
    "UK & Ireland": ["united kingdom", "uk", "england", "scotland", "wales", "northern ireland", "ireland", "great britain"],
    "Europe": ["european union", "eu", "france", "germany", "spain", "italy", "netherlands", "belgium", "poland", "portugal",
               "denmark", "sweden", "norway", "finland", "iceland", "austria", "switzerland", "bulgaria", "romania", "greece",
               "czech republic", "czechia", "hungary", "croatia", "ukraine", "türkiye", "turkey", "russia", "serbia", "albania",
               "estonia", "latvia", "lithuania", "luxembourg", "slovakia", "slovenia", "portugal", "malta", "cyprus"],
    "North America": ["united states", "usa", "us", "canada", "mexico"],
    "Latin America": ["brazil", "argentina", "chile", "peru", "colombia", "ecuador", "guatemala", "honduras", "costa rica", "venezuela",
                      "uruguay", "paraguay", "bolivia", "el salvador", "dominican republic", "panama", "jamaica"],
    "Asia-Pacific": ["china", "japan", "india", "vietnam", "viet nam", "thailand", "indonesia", "malaysia", "philippines", "pakistan",
                     "south korea", "korea", "australia", "new zealand", "singapore", "hong kong", "taiwan", "bangladesh", "sri lanka"],
    "Middle East & Africa": ["côte d'ivoire", "ivory coast", "nigeria", "ghana", "kenya", "south africa", "egypt", "morocco", "iran",
                             "israel", "saudi arabia", "united arab emirates", "uae", "ethiopia", "madagascar", "tunisia",
                             "democratic republic of congo", "drc", "mauritania", "mozambique", "algeria", "senegal", "uganda", "tanzania"],
}
COUNTRY_REGION = {c: r for r, cs in REGIONS.items() for c in cs}

def country_in(text):
    """Find a country named in a headline, so trade-press and news stories land on the map."""
    t = f" {(text or '').lower()} "
    for c in sorted(COUNTRY_REGION, key=len, reverse=True):
        if len(c) > 3 and re.search(rf"[^a-z]{re.escape(c)}[^a-z]", t):
            return c.title()
    for tag, c in ((" uk ", "United Kingdom"), (" u.s. ", "United States"), (" us ", "United States"), (" eu ", "European Union"),
                   (" fda ", "United States"), (" usda", "United States"), (" cdc ", "United States"), (" fsis", "United States"),
                   (" fsa ", "United Kingdom"), (" ukhsa", "United Kingdom"), (" fss ", "United Kingdom"), (" fsai", "Ireland"),
                   (" efsa", "European Union"), (" ecdc", "European Union"), (" rasff", "European Union"),
                   (" cfia", "Canada"), (" fsanz", "Australia"), (" anvisa", "Brazil"), (" sfa ", "Singapore")):
        if tag in t.replace("’", " ").replace("'", " "):
            return c
    return None

def region_for(country):
    return COUNTRY_REGION.get((country or "").strip().lower(), "Global")

# ---------------------------------------------------------------- hazards
HAZARDS = [  # (label, severity, keywords) - first match wins, so order matters
    ("Listeria", "high", ["listeria", "listérias", "listerien"]),
    ("Salmonella", "high", ["salmonell"]),
    ("E. coli / STEC", "high", ["e. coli", "e.coli", "stec", "o157", "shiga"]),
    ("Botulism", "high", ["botulism", "botulinum", "clostridium"]),
    ("Cronobacter / B. cereus", "high", ["cronobacter", "cereulide", "bacillus cereus"]),
    ("Viral (noro / hep A)", "high", ["norovirus", "hepatitis a"]),
    ("Animal disease", "medium", ["avian influenza", "avian flu", "bird flu", "hpai", "swine fever", "foot and mouth", "foot-and-mouth"]),
    ("Pathogen (unspecified)", "high", ["pathogen", "krankheitserreger", "microbiolog", "bacteri", "microbial contamination"]),
    ("Allergen", "medium", ["undeclared", "allergène", "allergeen", "alérgeno", "allerg", "gluten", "peanut", "sesame", "mustard", "celery", "sulphite", "sulfite",
                            "lupin", "soya", "soy ", "milk", "egg", "tree nut", "almond", "wheat"]),
    ("Chemical / contaminant", "medium", ["lead", "pesticide", "aflatoxin", "mycotoxin", "ochratoxin", "ethylene oxide",
                                          "cadmium", "mercury", "histamine", "residue"]),
    ("Foreign body", "medium", ["plastic", "metal", "glass", "stone", "pellet", "foreign", "rubber", "wood"]),
    ("Temperature / process", "medium", ["temperature", "under-processed", "underprocessed", "seal", "pasteur"]),
    ("Pests / spoilage", "low", ["beetle", "insect", "rodent", "mould", "mold", "spoil"]),
    ("Plant pest", "medium", ["locust", "pest report", "fall armyworm", "xylella", "fruit fly", "plant pest", "phytosanitary"]),
    ("Weather / climate", "medium", ["drought", "heatwave", "flood", "el niño", "el nino", "frost", "storm"]),
    ("Regulation / guidance", "low", ["guidance", "consultation", "legislation", "food law", "food code", "regulation",
                                      "roadmap", "sps agreement", "import rule", "standards", "code of practice"]),
    ("Markets / prices", "low", ["price index", "futures", "balance sheet", "supply and demand", "wasde", "commodity price"]),
    ("Trade / policy", "low", ["tariff", "import ban", "export ban", "regulation", "trade deal", "sanction"]),
]

COMPETITOR_TYPES = [
    ("M&A", ["acquire", "acquisition", "merger", "divest", "sale of", "to buy"]),
    ("Validation / approval", ["aoac", "microval", "afnor", "nf validation", "certif", "validation", "approval"]),
    ("Product launch", ["launch", "introduc", "new product", "unveil", "release"]),
    ("Partnership / distribution", ["partner", "distribution agreement", "distributor", "collaborat"]),
    ("Regulatory / legal", ["warning letter", "recall", "lawsuit", "class action", "fda"]),
    ("Financials", ["results", "revenue", "quarter", "earnings", "guidance", "investor"]),
    ("People", ["appoint", "ceo", "names", "hire"]),
]

# ---------------------------------------------------------------- relevance filters
# Farm to fork: animal feed and primary production through processing, packaging and finished food & drink.
FOOD_CHAIN = ["food", "feed", "drink", "beverage", "dairy", "milk", "cheese", "yogurt", "yoghurt", "butter", "cream", "meat",
              "poultry", "chicken", "beef", "pork", "egg", "fish", "seafood", "shellfish", "salmon", "produce", "fruit",
              "vegetable", "salad", "sprout", "grain", "cereal", "flour", "bread", "bakery", "biscuit", "cake", "confection",
              "chocolate", "sweet", "snack", "crisp", "sauce", "soup", "spice", "herb", "tea", "coffee", "juice", "water",
              "wine", "beer", "infant formula", "formula", "supplement", "pet food", "pet treat", "livestock", "cattle",
              "farm", "abattoir", "slaughter", "game", "hygiene", "allergen", "allerg", "recall", "withdrawal", "listeria",
              "salmonella", "e. coli", "botulism", "norovirus", "contamina", "label", "manufactur", "processing",
              "packaging", "food contact", "import", "export", "sps", "traceab", "haccp", "sanitation", "inspection",
              "audit", "brcgs", "salsa", "mycotoxin", "aflatoxin", "ochratoxin", "pesticide", "residue", "novel food",
              "cell-cultivated", "precision fermentation", "kitchen", "restaurant", "takeaway", "caterer", "outbreak",
              "ingredient", "nut", "peanut", "sesame", "gluten", "wheat",
              # other languages (titles from national regulators)
              "lebensmittel", "rückruf", "rueckruf", "warnung", "wurst", "käse", "kaese", "fleisch", "salmonellen", "listerien",
              "rappel", "aliment", "fromage", "viande", "lait", "terugroep", "voedsel", "levensmiddel", "alerta", "alimento",
              "alimentaria", "alimentar", "retirada", "recolhimento", "carne", "leche", "leite", "queso", "queijo", "richiamo",
              "tilbagekald", "fødevare", "tilbakekall", "återkall", "livsmedel", "elintarvike", "takaisinveto", "matvæli",
              "innköllun", "żywność", "zywnosc", "wycofanie", "ostrzeżenie", "食品", "召回", "回收", "回収", "リコール", "식품",
              "회수", "อาหาร", "makanan", "penarikan"]
NOT_FOOD_CHAIN = ["workforce management", "board meeting", "recruitment", "vacanc", "freedom of information",
                  "accessibility statement", "cookie", "privacy", "sign in", "subscribe", "contact us", "careers",
                  "drug", "medical device", "cosmetic", "tobacco", "vaccine", "biologic"]

CROP_WEATHER = ["crop", "harvest", "yield", "drought", "rain", "flood", "heat", "frost", "el niño", "el nino", "la niña",
                "enso", "weather", "climate", "grain", "cereal", "wheat", "barley", "maize", "corn", "soy", "rice", "sugar",
                "cocoa", "coffee", "oilseed", "rapeseed", "dairy", "milk", "livestock", "beef", "pork", "lamb", "poultry",
                "feed", "fertiliser", "fertilizer", "price index", "food price", "food security", "sowing", "planting",
                "balance sheet", "supply and demand", "market report", "wasde", "crop monitor", "mars bulletin",
                "locust", "pest", "cyclone", "storm", "food insecurity", "famine", "epidemic", "cholera"]

OUTBREAK = ["outbreak", "cholera", "typhoid", "anthrax", "avian", "influenza", "zoonot", "foodborne", "food poisoning",
            "listeria", "salmonella", "e. coli", "botulism", "hepatitis a", "norovirus", "brucell", "epidemiological alert"]

def outbreak_rel(text):
    t = (text or "").lower()
    return any(k in t for k in OUTBREAK)

def crop_weather(text):
    t = (text or "").lower()
    return any(k in t for k in CROP_WEATHER) and not any(k in t for k in NOT_FOOD_CHAIN)

def food_chain(text):
    t = (text or "").lower()
    return any(k in t for k in FOOD_CHAIN) and not any(k in t for k in NOT_FOOD_CHAIN)

# Competitor relevance: for diversified groups keep only food, beverage, hygiene and industrial-microbiology items.
COMP_FOOD = ["food", "beverage", "dairy", "milk", "meat", "poultry", "water testing", "hygiene", "sanitation", "atp",
             "allergen", "mycotoxin", "pathogen", "listeria", "salmonella", "e. coli", "coliform", "environmental monitoring",
             "industrial microbiology", "microbial", "microbiology", "culture media", "petrifilm", "aoac", "microval",
             "afnor", "nf validation", "rapid micro", "quality control", "spoilage", "food safety", "feed", "livestock"]
ANNOUNCE = ["launch", "introduc", "new ", "partner", "working with", "distribut", "approv", "aoac", "certif", "validat",
            "award", "acquir", "acquisition", "merger", "appoint", "agreement", "expands", "opens", "unveil", "release"]

def competitor_relevant(text, scope):
    t = (text or "").lower()
    if scope == "food_only":
        return any(k in t for k in COMP_FOOD)
    if scope == "announce_only":
        return any(k in t for k in ANNOUNCE)
    return True

def tag_competitor(text):
    t = (text or "").lower()
    for label, kws in COMPETITOR_TYPES:
        if any(k in t for k in kws):
            return label
    return "News"

def tag_hazard(text):
    t = (text or "").lower()
    for label, sev, kws in HAZARDS:
        if any(k in t for k in kws):
            return label, sev
    return "Other", "low"

# ---------------------------------------------------------------- helpers
def get(url, as_json=True, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body) if as_json else body

def iso(value):
    """Best-effort parse of the many date formats regulators use."""
    if not value:
        return None
    v = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S %Z", "%Y%m%dT%H%M%SZ", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.search(r"\d{4}-\d{2}-\d{2}", v)
    return m.group(0) if m else None

def record(source, title, url, date, country, summary="", signal="recall", hazard_text=None, severity=None, company=None):
    hz, sev = tag_hazard(f"{title} {summary} {hazard_text or ''}")
    if signal == "weather":
        hz = "Weather / climate"
    if signal == "competitor":
        hz, sev = tag_competitor(f"{title} {summary}"), "low"
    r = {
        "id": hashlib.sha1(f"{source}|{url}|{title}".encode()).hexdigest()[:16],
        "title": re.sub(r"\s+", " ", title or "").strip()[:240],
        "summary": re.sub(r"<[^>]+>|\s+", " ", summary or "").strip()[:400],
        "source": source, "signal": signal, "hazard": hz, "severity": severity or sev,
        "country": country, "region": region_for(country), "date": date, "url": url,
    }
    if company:
        r["company"] = company
    if signal in ("news", "trade_press", "outbreak") and r["region"] == "Global":
        found = country_in(title)
        if found:
            r["country"], r["region"] = found, region_for(found)
    return r

# ---------------------------------------------------------------- handlers
def h_openfda(src):
    for r in get(src["url"]).get("results", []):
        sev = {"Class I": "high", "Class II": "medium", "Class III": "low"}.get(r.get("classification"))
        firm, prod = r.get("recalling_firm", ""), (r.get("product_description") or "")[:90]
        yield record(src["name"], f"{firm}: {prod}", f"https://www.accessdata.fda.gov/scripts/ires/?Product={r.get('recall_number','')}",
                     iso(r.get("report_date")), r.get("country") or src["country"],
                     f"{r.get('reason_for_recall','')} Distribution: {r.get('distribution_pattern','')}", severity=sev)

def h_fsa(src):
    for a in get(src["url"]).get("items", []):
        kinds = " ".join(str(t) for t in (a.get("type") or []))
        signal = "allergen" if "AA" in kinds or "AA" in (a.get("notation") or "") else "recall"
        yield record(src["name"], a.get("title"), a.get("alertURL") or a.get("@id"), iso(a.get("created")),
                     src["country"], a.get("shortTitle", ""), signal=signal)

def h_fsis(src):
    for r in get(src["url"]):
        yield record(src["name"], r.get("field_title"), r.get("field_recall_url") or "https://www.fsis.usda.gov/recalls",
                     iso(r.get("field_recall_date")), src["country"],
                     f"{r.get('field_recall_reason','')} {r.get('field_summary','')}")

def h_canada(src):
    data = get(src["url"])
    for r in (data if isinstance(data, list) else data.get("results", []))[:300]:
        if "food" not in str(r.get("Organization", r.get("Category", ""))).lower():
            continue
        yield record(src["name"], r.get("Title"), r.get("URL"), iso(r.get("Last updated") or r.get("Date published")),
                     src["country"], r.get("Issue", ""))

def h_rss_filtered(src):
    for r in h_rss(src):
        text = f"{r['title']} {r['summary']}"
        f = src.get("filter")
        if f == "gdacs":
            # keep orange/red alerts, plus every flood, drought and tropical cyclone (they hit crops and supply chains)
            t = text.lower()
            if not (re.search(r"\b(orange|red)\b", t) or any(k in t for k in ("flood", "drought", "cyclone"))):
                continue
            found = country_in(r["title"])
            if found:
                r["country"], r["region"] = found, region_for(found)
            r["hazard"] = "Weather / climate"
            yield r
        elif f == "food_chain" and not food_chain(text):
            continue
        elif f == "crop_weather" and not crop_weather(text):
            continue
        else:
            yield r

def h_oecd(src):
    """OECD GlobalRecalls: food & beverage segment (GS1 50000000), ~50 countries. Same data call the portal makes."""
    for r in get(src["url"]).get("results", []):
        title = r.get("product.name") or ""
        if not food_chain(title + " food"):   # segment also holds tobacco & lighters
            continue
        if any(k in title.lower() for k in ("lighter", "tobacco", "cigar", "vape", "e-liquid")):
            continue
        yield record(src["name"], title, r.get("extUrl") or "https://globalrecalls.oecd.org", r.get("date"),
                     r.get("countryName") or "Global", "Via OECD GlobalRecalls.", signal="recall")

def h_rss(src):
    root = ET.fromstring(get(src["url"], as_json=False))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//a:entry", ns)
    for it in items[:60]:
        f = lambda tag: (it.findtext(tag) or it.findtext(f"a:{tag}", namespaces=ns) or "")
        link = f("link") or ((it.find("a:link", ns) is not None and it.find("a:link", ns).get("href")) or "")
        yield record(src["name"], f("title"), link, iso(f("pubDate") or f("updated") or f("published")),
                     src["country"], f("description") or f("summary"), signal=src.get("signal", "recall"))

def h_gnews(q, company=None):
    """Google News RSS search: covers agencies and countries that have no feed of their own.
    Check Google's terms before any commercial use; swap for a paid news API at that point."""
    hl, gl = q.get("hl", "en-GB"), q.get("gl", "GB")
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q["q"] + " when:14d", "hl": hl, "gl": gl, "ceid": f"{gl}:{hl.split('-')[0]}"})
    root = ET.fromstring(get(url, as_json=False))
    for it in root.findall(".//item")[:40]:
        title = it.findtext("title") or ""
        pub = it.findtext("source") or ""
        if pub and title.endswith(" - " + pub):
            title = title[: -len(pub) - 3]
        yield record(f"News · {pub}" if pub else "Google News", title, it.findtext("link"), iso(it.findtext("pubDate")),
                     q.get("country", "Global"), signal=q.get("signal", "news"), company=company)

def h_trade(t):
    """One query per trade publication (site: search). Source is labelled with the publication name."""
    for r in h_gnews({"q": t["q"], "country": "Global", "signal": "trade_press", "hl": t.get("hl", "en-GB"), "gl": t.get("gl", "GB")}):
        r["source"] = t["name"]
        yield r

def h_competitor(c):
    for r in h_gnews({"q": c["q"], "country": c.get("hq", "Global"), "signal": "competitor"}, company=c["name"]):
        if competitor_relevant(r["title"], c.get("scope", "all")):
            yield r

SEEN = json.loads(STATE.read_text()) if STATE.exists() else {}

def h_doc(d):
    """Watch a single document (weekly PDF bulletin, monthly outlook page). When its Last-Modified date or
    content fingerprint changes, emit one item linking to the new edition, with an optional extracted summary."""
    req = urllib.request.Request(d["url"], headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        body, modified = r.read(), r.headers.get("Last-Modified", "")
    stamp = modified or hashlib.sha1(body).hexdigest()
    key = "doc:" + d["url"]
    if SEEN.get(key) == [stamp]:
        return
    first = key not in SEEN
    SEEN[key] = [stamp]
    summary = ""
    if d.get("extract"):
        text = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "ignore"))
        m = re.search(d["extract"], text, flags=re.S)
        summary = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    try:
        date = datetime.strptime(modified, "%a, %d %b %Y %H:%M:%S %Z").date().isoformat()
    except ValueError:
        date = datetime.now(timezone.utc).date().isoformat()
    title = f"{d['name']}: new edition" + (f" ({date})" if modified else "")
    if not first or summary:
        r = record(d["name"], title, d["url"], date, d.get("country", "Global"), summary, signal=d.get("signal", "weather"))
        r["hazard"] = "Weather / climate"
        yield r

def h_page(w):
    """Watch a web page that has no feed. Each run, links not seen before become new items.
    On the first run for a page, everything is just remembered (no flood of old links), except
    links whose URL carries a date. Pages built by JavaScript return nothing; use a news query instead."""
    html = get(w["url"], as_json=False).decode("utf-8", "ignore")
    first_run = w["url"] not in SEEN
    seen = set(SEEN.get(w["url"], []))
    host = urllib.parse.urlparse(w["url"]).netloc
    today = datetime.now(timezone.utc).date().isoformat()
    for href, inner in re.findall(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', html, flags=re.S | re.I):
        title = re.sub(r"<[^>]+>|\s+", " ", inner).strip()
        url = urllib.parse.urljoin(w["url"], href)
        if w.get("title_prefix"):
            title = w["title_prefix"] + title
        if len(title) < w.get("min_len", 25) + len(w.get("title_prefix", "")) or url in seen or url.rstrip("/") == w["url"].rstrip("/"):
            continue
        if w.get("external_only") and urllib.parse.urlparse(url).netloc == host:
            continue
        if w.get("link_pattern") and not re.search(w["link_pattern"], url + " " + title, flags=re.I):
            continue
        seen.add(url)
        m = re.search(r"(20\d\d)[/-](\d\d)[/-](\d\d)", url) or re.search(r"/(2\d)(0[1-9]|1[0-2])([0-3]\d)_", url)
        if m:
            y = m.group(1) if len(m.group(1)) == 4 else "20" + m.group(1)
            date = f"{y}-{m.group(2)}-{m.group(3)}"
        else:
            date = None if first_run else today
        if not date:
            continue
        if w["signal"] == "competitor":
            if not competitor_relevant(title, w.get("scope", "all")):
                continue
        elif w.get("filter") == "food_chain" and not food_chain(title):
            continue
        elif w.get("filter") == "crop_weather" and not crop_weather(title):
            continue
        elif w.get("filter") == "outbreak" and not outbreak_rel(title):
            continue
        yield record(w["name"], title, url, date, w.get("country", "Global"), signal=w["signal"], company=w.get("company"))
    SEEN[w["url"]] = sorted(seen)[-3000:]

def h_gdelt(q):
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(
        {"query": q["q"], "mode": "artlist", "format": "json", "maxrecords": 75, "timespan": "3d", "sort": "datedesc"})
    for a in get(url).get("articles", []):
        yield record(f"News · {a.get('domain','')}", a.get("title"), a.get("url"), iso(a.get("seendate")),
                     a.get("sourcecountry") or "Global", signal=q["signal"])

def h_weather(spots):
    for s in spots:
        url = ("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": s["lat"], "longitude": s["lon"], "forecast_days": 7, "timezone": "UTC",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum"}))
        d = get(url)["daily"]
        tmax, tmin, rain = max(d["temperature_2m_max"]), min(d["temperature_2m_min"]), d["precipitation_sum"]
        flags = []
        if tmax >= 38: flags.append(f"heat stress, highs to {tmax:.0f}°C")
        if max(rain) >= 50: flags.append(f"heavy rain, up to {max(rain):.0f} mm in a day")
        if tmin <= -2 and s["lat"] < 45: flags.append(f"frost risk, lows to {tmin:.0f}°C")
        if sum(rain) < 1 and tmax >= 32: flags.append("dry and hot all week")
        if flags:
            yield record("Open-Meteo forecast", f"{s['name']}: {', '.join(flags)}", "https://open-meteo.com/",
                         d["time"][0], s["country"], "7-day forecast flag for a key growing region.",
                         signal="weather", severity="medium")

# ---------------------------------------------------------------- main
def main(offline=False):
    old = json.loads(OUT.read_text()) if OUT.exists() else {"items": []}
    items = {i["id"]: i for i in old.get("items", [])}
    status = []
    jobs = []
    if not offline:
        handlers = {"openfda": h_openfda, "fsa": h_fsa, "fsis": h_fsis, "canada": h_canada}
        jobs += [(s["name"], handlers[s["handler"]], s) for s in SOURCES["regulators"] if s["enabled"]]
        jobs += [(s["name"], h_rss_filtered, s) for s in SOURCES["rss"] if s["enabled"]]
        if SOURCES.get("oecd"):
            jobs += [(SOURCES["oecd"]["name"], h_oecd, SOURCES["oecd"])]
        jobs += [(f"Page watch: {w['name']}", h_page, w) for w in SOURCES.get("page_watch", [])]
        jobs += [(f"Document watch: {d['name']}", h_doc, d) for d in SOURCES.get("doc_watch", [])]
        jobs += [(f"Google News {q['id']}", h_gnews, q) for q in SOURCES.get("google_news", [])]
        jobs += [(f"Trade press: {t['name']}", h_trade, t) for t in SOURCES.get("trade_press", [])]
        jobs += [(f"Competitor: {c['name']}", h_competitor, c) for c in SOURCES.get("competitors", [])]
        if SOURCES.get("method_approvals"):
            jobs += [("Method approvals", h_gnews, SOURCES["method_approvals"])]
        jobs += [(f"GDELT {q['id']}", h_gdelt, q) for q in SOURCES["news_queries"]]
        jobs += [("Open-Meteo weather", h_weather, SOURCES["weather_hotspots"])]
    for name, fn, arg in jobs:
        try:
            got = [r for r in fn(arg) if r["title"]]
            for r in got:
                if r["signal"] == "competitor":
                    r["region"] = "Global"
            for r in got:
                items.setdefault(r["id"], r)
            status.append({"source": name, "ok": True, "count": len(got)})
        except Exception as e:  # one bad feed must never stop the run
            status.append({"source": name, "ok": False, "error": str(e)[:160]})
        time.sleep(1)  # be polite to public APIs
    today = datetime.now(timezone.utc)
    cut = {False: (today - timedelta(days=KEEP_DAYS)).date().isoformat(),
           True: (today - timedelta(days=KEEP_DAYS_COMPETITOR)).date().isoformat()}
    keep, seen = [], set()
    for i in sorted(items.values(), key=lambda i: i.get("date") or "", reverse=True):
        if (i.get("date") or "9999") < cut[i.get("signal") == "competitor"]:
            continue
        key = re.sub(r"[^a-z0-9]", "", i["title"].lower())[:70]   # same story from several outlets
        if key in seen:
            continue
        seen.add(key)
        keep.append(i)
    keep = keep[:4000]
    out = {"generated_at": today.isoformat(timespec="minutes"),
           "sources": status or old.get("sources", []), "items": keep,
           "directory": SOURCES.get("directory", [])}
    if offline and old.get("sample"):
        out["sample"], out["generated_at"] = True, old.get("generated_at")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    if not offline:
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(SEEN, indent=0))
    for s in status:
        print(("OK  " if s["ok"] else "FAIL"), s["source"], s.get("count", s.get("error")))
    print(f"wrote {len(keep)} items -> {OUT}")

if __name__ == "__main__":
    main(offline="--offline" in sys.argv)
