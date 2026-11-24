# Food Safety Radar

A regional "situation board" for food manufacturers: recalls, allergen alerts, pathogen
outbreaks (UKHSA, CDC, ECDC and equivalents), animal disease, weather risk in key growing
regions, trade/policy news and a competitor watch (Neogen, Kikkoman Biochemifa, Charm,
bioMérieux, Bio-Rad, R-Biopharm and more), pulled from public sources and grouped by region.

```
collector/collect.py    pulls every source, tags hazard / severity / region, writes site/data/alerts.json
collector/sources.json  the list of sources (add or switch off feeds here, no code changes)
site/index.html         the dashboard (reads data/alerts.json)
.github/workflows/      runs the collector every hour and publishes the site (free on GitHub)
```

No paid services and no Python packages needed (standard library only).

## Run it on your laptop (10 minutes)

1. Install Python 3.10+ from python.org.
2. In this folder: `python collector/collect.py` - it prints OK / FAIL per source.
3. `cd site && python -m http.server 8000`, then open http://localhost:8000

## Put it online for free (GitHub Pages)

1. Create a free GitHub account and a new repository; upload this folder.
2. Repository **Settings > Pages > Source: GitHub Actions**.
3. **Actions** tab > "Collect food safety signals" > **Run workflow**.
4. Your site appears at `https://<username>.github.io/<repo>/` and refreshes hourly.

Make the repository private (or move to a paid host) if you don't want the site public.

## Sources wired in

| Signal | Source | Region | Status |
|---|---|---|---|
| Recalls (Class I/II/III) | openFDA food enforcement API | North America | verified API |
| Allergy alerts, PRINs, FAFAs | UK FSA food alerts API | UK & Ireland | verified API |
| Meat & poultry recalls | USDA FSIS recall API | North America | check URL |
| Recalls | Health Canada / CFIA open data | North America | check URL |
| Recalls & allergen alerts | FSAI RSS | UK & Ireland | check URL |
| Recalls | FSANZ RSS | Asia-Pacific | check URL |
| Science / risk news | EFSA RSS, Food Safety News RSS | Europe / Global | check / verified |
| Food-safety, trade-policy, animal-disease news | GDELT news API (global, 3-day window) | by source country | verified API |
| Weather flags (heat, heavy rain, frost, dry spell) | Open-Meteo 7-day forecast for 9 growing hotspots | by hotspot | verified API |

"Check URL" means the endpoint wasn't testable from where this was built - run the collector
once and fix any line that prints FAIL (usually just the feed address).

**Not included yet, and why:** RASFF has no public API (the RASFF Window is a web app) -
options are its weekly email or scraping the Window, which needs checking against its terms.
Dataminr-style social-media monitoring needs paid data access (X/Twitter API etc.).

## Coverage (v2)

- **80 sources** in the on-site directory; 63 are pulled automatically.
- **Public health agencies:** UKHSA, Public Health Scotland, Public Health Wales, PHA (NI), HPSC (IE),
  CDC, FDA outbreak table, PHAC, ECDC, WHO Disease Outbreak News.
- **Regulators by country:** FSA, FSS, FSAI, FDA, USDA FSIS, CFIA, COFEPRIS, RappelConso, Lebensmittelwarnung,
  NVWA, AESAN, BLV, Nordic agencies, ANVISA, FSANZ, MPI, SFA, CFS Hong Kong, FSSAI, NCC (South Africa), NAFDAC, SFDA.
- Agencies without a feed of their own are covered with **Google News RSS searches** (`google_news` in
  sources.json, one per agency/country, in the local language where it matters). Fine for a pilot; check
  Google's terms before using it commercially, then switch to a paid news API (e.g. NewsAPI, Event Registry).
- **Competitor watch:** `competitors` in sources.json. Each company has a news query plus its press-release
  page. Updates are auto-sorted into Product launch, Validation / approval, M&A, Partnership / distribution,
  Regulatory / legal, Financials, People. There's also a general AOAC / MicroVal / AFNOR approvals query.
  Add a company by copying one block. Watch for name clashes: the query uses "Charm Sciences", not "Charm",
  because plain "Charm" pulls in an unrelated biotech.
- **Trade press:** `trade_press` in sources.json, one feed per publication: Food Manufacture, Food Safety
  Magazine, Food Safety News, New Food, FoodNavigator (EU + USA), Just Food, Food Quality & Safety,
  Food Engineering, Food Processing, Food Ingredients First, The Grocer, Agriland, Farmers Weekly and
  Irish Examiner. General outlets are filtered to food-safety keywords. If a headline names a country or
  agency (e.g. "FSAI", "UKHSA", "Kenya"), the story is placed in that region on the map.
- Near-duplicate headlines (one story carried by several outlets) are merged.

## v4: your source list

| Source | How it's pulled | Filter |
|---|---|---|
| FSA on GOV.UK (organisation page + GOV.UK Atom search) | Atom feed + page watch | farm-to-fork keywords; drops workforce/board/admin items |
| FDA recalls, market withdrawals & safety alerts | FDA RSS + page watch | food, beverage, animal feed, pet food, supplements; drops drugs, devices, cosmetics |
| FoodReady recall tracker | page watch (links out to FDA notices) | farm to fork |
| supplement.ge food alert archive (RASFF, FSAI, FSA, FSANZ, CFIA, FSIS) | page watch | farm to fork |
| Food Safety News | RSS (foodsafetynews.com/rss/) | none |
| Smart Food Safe, Food Compliance International, Food Factory Fairbanks | page watch + news search | farm to fork |
| FSA incidents/withdrawals guidance | directory only (it's guidance, not a feed) | n/a |
| BBC News topic page | **not pulled** (blocked to the tools used to build this) | n/a |

**Competitor pages** are watched with three relevance rules:
- `all`: food-focused firms where every announcement matters (Neogen, Kikkoman test kits, Charm news, R-Biopharm food, NEMIS).
- `food_only`: diversified groups, where only food, beverage, hygiene and microbiology items are kept (bioMérieux, Bio-Rad, IDEXX, Thermo Fisher, Merck/Sigma-Aldrich).
- `announce_only`: blogs that are mostly education, where only launches, partnerships, approvals, awards and deals are kept (FreshCheck, Charm blog).

Neogen's and Bio-Rad's investor pages load their lists with JavaScript, so they can't be page-watched. They're covered by
news searches instead (incl. Business Wire, where both publish their releases).

**How page watching works:** on the first run the collector only remembers the links on each page (so you don't get a
flood of old posts). From then on, any new link that passes the filter becomes an item, dated the day it first appeared.
That memory is kept in `collector/state/seen_links.json`, which the workflow commits along with the data.

## v5: weather & crops

The "Weather & crops" stream combines:
- **Met Office** blog (food and farming posts); the food security research page and Food Insecurity index are reference links.
- **USDA agricultural weather (WAOB)**: the Weekly Weather and Crop Bulletin and the U.S. Agriculture in Drought report are
  *document-watched*: when a new edition is posted, an item appears linking to it.
- **NOAA CPC El Niño/La Niña discussion** (monthly): document-watched, and the synopsis line is pulled into the item.
- **GEOGLAM Crop Monitor** (monthly, global crop conditions), **JRC MARS Bulletin** (monthly EU crop and yield forecasts),
  **FAO** (Food Price Index, GIEWS crop prospects), **AMIS Market Monitor**, **AHDB** (UK crops, balance sheets, markets).
- **Open-Meteo** 7-day forecast flags for 9 key growing regions (heat, heavy rain, frost, dry spells).
- Reference only: Copernicus Drought Observatory, FEWS NET.

Watched pages use a crop/weather keyword filter (`CROP_WEATHER` in collect.py), so staff news, events and
unrelated research are dropped.

## v6: global aggregators and national regulators

- **OECD GlobalRecalls**: pulled from the portal's own search endpoint (`/ws/search.xqy`, GS1 food & beverage
  segment 50000000). Mostly consumer-product recalls, so the food segment is small; lighters and tobacco are dropped.
- **GDACS**: RSS; keeps orange/red alerts plus every flood, drought and tropical cyclone, placed on the map by country.
- **ReliefWeb**: RSS, filtered to crop/weather/epidemic items. **FAO Locust Watch**, **IPPC pest reports**,
  **Africa CDC**, **PAHO**: page watch (outbreak or crop filters).
- **WTO SPS / ePing**, **EMPRES-i**, **HealthMap**: these are search tools or JavaScript apps (EMPRES-i is a
  Flutter app with no public feed), so they're covered by news searches and listed in the directory.
- **28 national regulators** (Europe, Asia-Pacific, Middle East & Africa, Latin America): page watch with a
  `link_pattern` per site that keeps only alert/recall links, plus a local-language news search as backup.
  The food filter now understands food/recall words in about 15 languages. Titles stay in the original language.
- **Trade press**: DairyReporter, FoodNavigator-Asia and -LATAM (RSS), Global Meat News (news search; its
  homepage currently shows archive content), Food Safety Asia (page watch).

Most regulator URLs and link patterns are marked `"status": "check"`. Run the collector once and fix any
that FAIL or return nothing.

## Roadmap

1. **Live feeds** - run the collector, fix FAIL lines, add FSAI allergen RSS, Food Standards Scotland, BfR/RappelConso (France), NVWA (NL).
2. **Alerts** - email / Teams message when a high-severity signal hits a region or keyword you watch (e.g. "Listeria" + "UK & Ireland").
3. **Smarter tagging** - swap keyword tagging for an AI classifier that also writes a one-line "why it matters to a manufacturer".
4. **Deduplication** - the same recall appears via regulator + news; cluster near-duplicate titles.
5. **Map & history** - proper world map, trend lines per hazard, 12-month archive.
6. **Accounts** - logins and saved watchlists (needs a small backend, e.g. Supabase or Firebase).

## Notes

- Hazard and severity are tagged by keyword and can be wrong; the feed always links to the original notice.
- Respect each source's terms of use; the collector identifies itself and pauses between requests. Put a real contact email in `UA` in collect.py.
