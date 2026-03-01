#!/usr/bin/env python3
"""
Photonics M&A Data Scraper — v2

Scrapes M&A transaction data from the EPIC Photonics Index
(https://photonics-index.org/ma) and produces the exact data.json
format consumed by the Cloudberry VC dashboard.

The EPIC page uses a WordPress TablePress table (#tablepress-10) with
DataTables client-side pagination.  All ~2 000 rows are present in the
initial HTML, so a simple requests + BeautifulSoup approach works.

Table columns (indices 0-8):
  0  Year
  1  Month
  2  Type           (Acquisition | Merger — sometimes with typos / <br> tags)
  3  Mother Company (acquirer name)
  4  HQ Country     (acquirer country — may contain <br>, multi-country)
  5  Company Acquired (target name)
  6  HQ Country     (target country — same caveats)
  7  Value of Transaction (various formats: $110 million, €570 million,
                           £9.3 million, $9.9 billion, Not disclosed, …)
  8  Website Links

Blocked deals are maintained in a separate JSON file (blocked_deals.json)
that lives next to this script.  Any deal matching an entry there by
acquirer+target is excluded from all aggregates and placed in a dedicated
blockedDeals array in the output.
"""

import argparse
import json
import logging
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Logging ────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ── Region classifications ─────────────────────────────────────────────
REGION_MAP = {
    "USA": {
        "US", "USA", "United States", "United States of America",
    },
    "Europe": {
        "Austria", "Belgium", "Bosnia", "Bulgaria", "Croatia", "Cyprus",
        "Czech Republic", "Denmark", "England", "Estonia", "Finland",
        "France", "Germany", "Greece", "Hungary", "Iceland", "Ireland",
        "Italy", "Latvia", "Liechtenstein", "Lithuania", "Luxembourg",
        "Malta", "Moldova", "Montenegro", "Netherlands", "North Macedonia",
        "Norway", "Poland", "Portugal", "Romania", "Russia", "Scotland",
        "Serbia", "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland",
        "UK", "United Kingdom", "Ukraine", "Wales", "Belarus", "Albania",
    },
    "Asia": {
        "Australia", "China", "Hong Kong", "India", "Indonesia", "Japan",
        "Malaysia", "New Zealand", "Philippines", "Singapore", "South Korea",
        "Korea", "Taiwan", "Thailand", "Vietnam",
    },
}


def classify_region(country: str) -> str:
    """Map a (cleaned) country string to USA / Europe / Asia / Other."""
    if not country:
        return "Other"
    c = country.strip()
    for region, countries in REGION_MAP.items():
        if c in countries:
            return region
    return "Other"


# ── Value parsing ──────────────────────────────────────────────────────
# Approximate FX rates (good enough for M&A order-of-magnitude work)
FX_TO_USD = {"$": 1.0, "€": 1.08, "£": 1.27, "¥": 0.0067}

_VALUE_RE = re.compile(
    r"""
    (?P<currency>[€£$¥])          # currency symbol
    \s*(?P<number>[\d,.]+)        # numeric part
    \s*(?P<unit>billion|million|trillion)?  # magnitude
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_value(raw: str) -> float | None:
    """
    Parse a deal-value string into millions USD.
    Returns None for undisclosed / unparseable values.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text or "not disclosed" in text.lower() or text.lower() in {"n/a", "-", "—", ""}:
        return None

    m = _VALUE_RE.search(text)
    if not m:
        return None

    fx = FX_TO_USD.get(m.group("currency"), 1.0)
    try:
        number = float(m.group("number").replace(",", ""))
    except ValueError:
        return None

    unit = (m.group("unit") or "").lower()
    if unit == "billion":
        number *= 1_000
    elif unit == "trillion":
        number *= 1_000_000
    # "million" → already in millions (no multiply needed)

    return round(number * fx)


# ── HTML / text cleanup helpers ────────────────────────────────────────
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    """Strip HTML tags, normalise whitespace."""
    text = _BR_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    return " ".join(text.split()).strip()


def clean_country(raw: str) -> str:
    """
    Normalise country strings.
    Handles <br> artifacts, multi-country ("Denmark/USA"), and aliases.
    """
    c = clean(raw)
    # Take the first country if there are separators
    for sep in ["/", " and ", ",", "&"]:
        if sep in c:
            c = c.split(sep)[0].strip()
    # Aliases
    aliases = {"England": "United Kingdom", "Scotland": "United Kingdom", "Wales": "United Kingdom"}
    return aliases.get(c, c)


# ── Scraping ───────────────────────────────────────────────────────────
EPIC_URL = "https://photonics-index.org/ma/"


def scrape(url: str = EPIC_URL) -> list[dict]:
    """
    Fetch the EPIC M&A page and return a list of parsed transactions.
    Each dict has keys: year, month, type, acquirer, acquirer_country,
    acquirer_region, target, target_country, target_region, value, link.
    """
    log.info("Fetching %s …", url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    log.info("Fetched OK (%d bytes)", len(resp.content))

    soup = BeautifulSoup(resp.content, "html.parser")
    table = soup.find("table", id="tablepress-10") or soup.find("table")
    if not table:
        log.error("No table found on page — structure may have changed.")
        return []

    rows = table.find_all("tr")
    log.info("Found %d rows (including header)", len(rows))

    transactions = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 8:
            continue

        raw = [c.decode_contents() for c in cells]  # preserves inner HTML
        try:
            year = int(clean(raw[0]))
        except (ValueError, IndexError):
            continue

        txn = {
            "year": year,
            "month": clean(raw[1]),
            "type": clean(raw[2]).rstrip().replace("Acquistion", "Acquisition"),
            "acquirer": clean(raw[3]),
            "acquirer_country": clean_country(raw[4]),
            "target": clean(raw[5]),
            "target_country": clean_country(raw[6]),
            "value": parse_value(raw[7]),
            "link": clean(raw[8]) if len(raw) > 8 else "",
        }
        txn["acquirer_region"] = classify_region(txn["acquirer_country"])
        txn["target_region"] = classify_region(txn["target_country"])
        transactions.append(txn)

    log.info("Parsed %d transactions", len(transactions))
    return transactions


# ── Blocked-deal handling ──────────────────────────────────────────────
BLOCKED_FILE = Path(__file__).parent / "blocked_deals.json"


def load_blocked_deals() -> list[dict]:
    """Load the blocked-deals registry (lives next to this script)."""
    if not BLOCKED_FILE.exists():
        log.warning("No blocked_deals.json found — no deals will be excluded.")
        return []
    with open(BLOCKED_FILE) as f:
        deals = json.load(f)
    log.info("Loaded %d blocked deal(s) from %s", len(deals), BLOCKED_FILE)
    return deals


def is_blocked(txn: dict, blocked: list[dict]) -> dict | None:
    """Return the matching blocked-deal entry if txn is blocked, else None."""
    for bd in blocked:
        # Match on acquirer + target (case-insensitive, substring)
        if (bd["acq"].lower() in txn["acquirer"].lower()
                and bd["tgt"].lower() in txn["target"].lower()):
            return bd
    return None


# ── Statistics computation ─────────────────────────────────────────────

def compute_stats(transactions: list[dict], blocked_deals: list[dict]) -> dict:
    """
    Compute all dashboard statistics.
    Blocked deals are separated out and excluded from aggregates.
    Returns a dict ready to be serialised as data.json.
    """

    # Partition into active and blocked
    active = []
    matched_blocked = []
    for txn in transactions:
        bd = is_blocked(txn, blocked_deals)
        if bd:
            matched_blocked.append({**bd, "_txn": txn})
        else:
            active.append(txn)

    log.info("Active transactions: %d, blocked: %d", len(active), len(matched_blocked))

    # ── Aggregate counters ──
    total = len(active)
    targets_by_region = {"USA": 0, "Europe": 0, "Asia": 0, "Other": 0}
    acquirers_by_region = {"USA": 0, "Europe": 0, "Asia": 0, "Other": 0}
    eu_by_acq_region = {"USA": 0, "Europe": 0, "Asia": 0, "Other": 0}
    eu_size_values = {"USA": [], "Europe": [], "Asia": []}  # lists of values for median
    eu_targets_total = 0
    usa_buy_eu = 0
    eu_buy_usa = 0
    eu_buy_eu = 0
    asia_buy_eu = 0
    disclosed_value = 0
    usa_buy_eu_value = 0
    eu_buy_usa_value = 0

    year_counts: dict[int, dict] = {}    # year → {USA, Europe, Asia, Other}
    year_values: dict[int, dict] = {}    # year → {USA, Europe, Asia}
    usa_eu_per_year: dict[int, int] = {}

    usa_eu_deals = []   # for usaBuyEU list
    eu_usa_deals = []   # for euBuyUSA list
    all_deals = []      # for topDeals

    for t in active:
        yr = t["year"]
        tr = t["target_region"]
        ar = t["acquirer_region"]
        v = t["value"]

        targets_by_region[tr] += 1
        acquirers_by_region[ar] += 1

        if tr == "Europe":
            eu_targets_total += 1
            eu_by_acq_region[ar] += 1
            if ar in eu_size_values and v:
                eu_size_values[ar].append(v)

        if ar == "USA" and tr == "Europe":
            usa_buy_eu += 1
            if v:
                usa_buy_eu_value += v
            usa_eu_per_year[yr] = usa_eu_per_year.get(yr, 0) + 1
            usa_eu_deals.append(t)
        elif ar == "Europe" and tr == "USA":
            eu_buy_usa += 1
            if v:
                eu_buy_usa_value += v
            eu_usa_deals.append(t)
        elif ar == "Europe" and tr == "Europe":
            eu_buy_eu += 1
        elif ar == "Asia" and tr == "Europe":
            asia_buy_eu += 1

        if v:
            disclosed_value += v

        # Year data (counts by target region)
        if yr not in year_counts:
            year_counts[yr] = {"USA": 0, "Europe": 0, "Asia": 0, "Other": 0}
        year_counts[yr][tr] += 1

        # Year value data (by target region)
        if yr not in year_values:
            year_values[yr] = {"USA": 0, "Europe": 0, "Asia": 0}
        if v and tr in year_values[yr]:
            year_values[yr][tr] += v

        # All deals for top-deals ranking
        all_deals.append(t)

    # ── Build output structures ──

    # euSizeByAcquirer: {region: {avg, median, total, count}}
    eu_size_by_acq = {}
    for region, vals in eu_size_values.items():
        if vals:
            eu_size_by_acq[region] = {
                "avg": round(sum(vals) / len(vals)),
                "median": round(statistics.median(vals)),
                "total": round(sum(vals)),
                "count": len(vals),
            }

    # yearData: sorted list of {year, USA, Europe, Asia, Other}
    year_data = sorted(
        [{"year": yr, **counts} for yr, counts in year_counts.items()],
        key=lambda x: x["year"],
    )

    # yearValueData: sorted list of {year, USA, Europe, Asia}
    year_value_data = sorted(
        [{"year": yr, **vals} for yr, vals in year_values.items()],
        key=lambda x: x["year"],
    )

    # usaBuyEU: top deals sorted by value desc
    usa_eu_deals.sort(key=lambda t: t["value"] or 0, reverse=True)
    usa_buy_eu_list = [
        {"acq": t["acquirer"], "tgt": t["target"], "tgtC": t["target_country"],
         "yr": t["year"], "v": t["value"], "url": t.get("link", "")}
        for t in usa_eu_deals[:25]
    ]

    # euBuyUSA: top deals sorted by value desc
    eu_usa_deals.sort(key=lambda t: t["value"] or 0, reverse=True)
    eu_buy_usa_list = [
        {"acq": t["acquirer"], "acqC": t["acquirer_country"], "tgt": t["target"],
         "yr": t["year"], "v": t["value"], "url": t.get("link", "")}
        for t in eu_usa_deals[:20]
    ]

    # topDeals: top 12 by value
    all_deals.sort(key=lambda t: t["value"] or 0, reverse=True)
    top_deals = [
        {"yr": t["year"], "acq": t["acquirer"], "aR": t["acquirer_region"],
         "tgt": t["target"], "tR": t["target_region"], "v": t["value"],
         "url": t.get("link", "")}
        for t in all_deals[:12]
        if t["value"]
    ]

    # usaBuyEUPerYear
    all_years = sorted(set(yr for yr in year_counts))
    usa_eu_per_year_list = [
        {"yr": yr, "n": usa_eu_per_year.get(yr, 0)}
        for yr in all_years
    ]

    # blockedDeals output (preserve the rich metadata from blocked_deals.json)
    blocked_output = []
    for bd_match in matched_blocked:
        entry = {k: v for k, v in bd_match.items() if k != "_txn"}
        blocked_output.append(entry)
    # Also include any blocked deals that weren't found in scraped data
    matched_keys = {(b["acq"].lower(), b["tgt"].lower()) for b in matched_blocked}
    for bd in blocked_deals:
        if (bd["acq"].lower(), bd["tgt"].lower()) not in matched_keys:
            blocked_output.append(bd)

    # allDeals: every deal for the raw-data tab (sorted by year desc, then value desc)
    all_deals_sorted = sorted(all_deals, key=lambda t: (-(t["year"] or 0), -(t["value"] or 0)))
    all_deals_list = [
        {"yr": t["year"], "mo": t.get("month", ""), "type": t.get("type", ""),
         "acq": t["acquirer"], "acqC": t.get("acquirer_country", ""), "aR": t["acquirer_region"],
         "tgt": t["target"], "tgtC": t.get("target_country", ""), "tR": t["target_region"],
         "v": t["value"], "url": t.get("link", "")}
        for t in all_deals_sorted
    ]

    return {
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "total": total,
        "euTargetsTotal": eu_targets_total,
        "totalUSABuyEU": usa_buy_eu,
        "totalEUBuyUSA": eu_buy_usa,
        "totalEUBuyEU": eu_buy_eu,
        "totalAsiaBuyEU": asia_buy_eu,
        "totalDisclosedValue": round(disclosed_value),
        "totalUSABuyEUValue": round(usa_buy_eu_value),
        "totalEUBuyUSAValue": round(eu_buy_usa_value),
        "targetsByRegion": targets_by_region,
        "acquirersByRegion": acquirers_by_region,
        "euByAcquirer": eu_by_acq_region,
        "euSizeByAcquirer": eu_size_by_acq,
        "yearData": year_data,
        "yearValueData": year_value_data,
        "usaBuyEU": usa_buy_eu_list,
        "euBuyUSA": eu_buy_usa_list,
        "topDeals": top_deals,
        "blockedDeals": blocked_output,
        "usaBuyEUPerYear": usa_eu_per_year_list,
        "allDeals": all_deals_list,
    }


# ── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape EPIC Photonics Index M&A data")
    parser.add_argument("--url", default=EPIC_URL, help="URL to scrape")
    parser.add_argument("--output", default="data.json", help="Output JSON path (relative to CWD)")
    parser.add_argument("--dry-run", action="store_true", help="Parse but don't save")
    args = parser.parse_args()

    try:
        transactions = scrape(args.url)
        if not transactions:
            log.error("No transactions scraped — check page structure.")
            sys.exit(1)

        blocked = load_blocked_deals()
        stats = compute_stats(transactions, blocked)

        log.info("── Summary ──")
        log.info("  Total deals:       %d", stats["total"])
        log.info("  EU targets:        %d", stats["euTargetsTotal"])
        log.info("  USA → EU:          %d  ($%.1fB)", stats["totalUSABuyEU"], stats["totalUSABuyEUValue"] / 1000)
        log.info("  EU → USA:          %d  ($%.1fB)", stats["totalEUBuyUSA"], stats["totalEUBuyUSAValue"] / 1000)
        log.info("  Disclosed value:   $%.1fB", stats["totalDisclosedValue"] / 1000)
        log.info("  Blocked deals:     %d", len(stats["blockedDeals"]))

        if args.dry_run:
            log.info("Dry-run — not saving.")
            print(json.dumps(stats, indent=2))
        else:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w") as f:
                json.dump(stats, f, indent=2)
            log.info("Saved to %s", out)

    except Exception as e:
        log.error("Scraper failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
