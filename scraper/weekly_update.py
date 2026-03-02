#!/usr/bin/env python3
"""
Weekly Photonics M&A Dashboard Update Pipeline
───────────────────────────────────────────────
1. Scrape latest deals from EPIC Photonics Index
2. Diff against previous dataset to find NEW deals
3. Classify new deals (keyword matching)
4. Mark unsure deals for human review
5. Merge classifications into master file
6. Regenerate data.json with filtered relevant deals
7. Return summary for email

Run from repo root:
  python scraper/weekly_update.py
"""

import json
import sys
import html
import re
from pathlib import Path
from datetime import datetime
from statistics import median as stat_median
from collections import defaultdict, Counter

# Paths — relative to repo root
REPO = Path(__file__).resolve().parent.parent
SCRAPER_DIR = REPO / "scraper"
DATA_JSON = REPO / "data.json"
CLASSIFIED_FILE = REPO / "scraper" / "classified_deals.json"
PREVIOUS_DEALS_FILE = REPO / "scraper" / "previous_alldeals.json"
DEAL_MAP_FILE = REPO / "scraper" / "last_email_deal_map.json"

sys.path.insert(0, str(SCRAPER_DIR))
from scrape import scrape, load_blocked_deals, compute_stats


# ── Classification (same logic as classify_deals_v2.py) ──────────────

def decode(s):
    if s is None: return ''
    return html.unescape(str(s)).strip()

def word_match(text, keyword):
    if len(keyword) <= 4:
        pattern = r'(?:^|[\s,\-(/])' + re.escape(keyword) + r'(?:$|[\s,\-)/.])'
        return bool(re.search(pattern, text, re.IGNORECASE))
    return keyword.lower() in text.lower()

RELEVANT_TARGET_KW = [
    'photon', 'optic', 'optical', 'laser', 'lidar', 'fiber', 'fibre',
    'lens', 'infrared', 'spectro', 'oled', 'waveguide', 'holograph',
    'machine vision', 'computer vision', 'image sensor', 'cmos sensor',
    'photovoltaic', 'solar cell', 'quantum', 'night vision', 'thermal imaging',
    'hyperspectral', 'endoscop', 'microscop', 'spectrometer',
    'illuminat', 'photonic', 'optoelectronic', 'electro-optic', 'electro optic',
    'semiconduc', 'wafer', 'silicon', 'gallium', 'arsenide', 'nitride', 'carbide',
    'transistor', 'mosfet', 'igbt', 'diode', 'thyristor',
    'asic', 'fpga', 'microcontroller', 'microprocessor',
    'dram', 'sram', 'nand', 'flash memory', 'memory chip',
    'analog device', 'mixed signal', 'power management',
    'rf module', 'mmic', 'rf semiconduc',
    'lithograph', 'etch system', 'deposition', 'epitax',
    'mems', 'accelerometer', 'gyroscope',
    'photoresist', 'mask', 'reticle', 'pellicle',
    'thin film', 'sputt', 'crystal grow', 'cleanroom',
    'transceiver', 'amplifier', 'modulator',
    'radar', 'signal process', 'defense electron',
    'avionics', 'satellite commun', 'space electron',
    'autonomous driv', 'adas', 'self-driving',
    'optical network', 'dwdm', 'coherent optic',
    'millimeter wave', 'mmwave', 'power electron',
    'printed circuit', 'pcb', 'substrate',
    'chip', 'chipmaker', 'foundry', 'fab ',
    'sensor', 'detector', 'photodetect',
    'led ', 'display', 'lighting',
    'connector', 'cable', 'antenna',
    'embedded system', 'imaging', 'camera system',
    'processor', 'gpu', 'cpu', 'soc',
    'test and measurement', 'probe card',
    'vacuum', 'coating', 'deposition system',
]

KNOWN_RELEVANT_TARGETS = [
    'intel', 'amd', 'nvidia', 'qualcomm', 'broadcom', 'texas instrument',
    'analog device', 'microchip tech', 'on semiconductor', 'onsemi', 'nxp',
    'infineon', 'stmicroelectronic', 'renesas', 'marvell', 'micron',
    'asml', 'lam research', 'applied material', 'kla corp', 'tokyo electron',
    'coherent', 'ii-vi', 'lumentum', 'trumpf', 'ipg photonics',
    'hamamatsu', 'jenoptik', 'rofin', 'thorlabs',
    'corning', 'furukawa', 'sumitomo electric', 'fujikura',
    'osram', 'lumileds', 'cree', 'wolfspeed', 'nichia',
    'keyence', 'cognex', 'basler', 'flir', 'teledyne',
    'keysight', 'rohde schwarz', 'anritsu', 'viavi',
    'skyworks', 'qorvo', 'murata', 'tdk',
    'amkor', 'ase group', 'siltronic', 'sumco', 'shin-etsu',
    'entegris', 'mks instrument', 'brooks automation',
    'onto innovation', 'axcelis', 'veeco', 'aixtron',
    'arm hold', 'synopsys', 'cadence', 'mentor graphic',
    'xilinx', 'lattice semicond', 'maxim integrated',
    'cypress semicond', 'dialog semicond', 'silicon lab',
    'power integrations', 'monolithic power', 'vishay',
    'littelfuse', 'sensata', 'te connectivity',
    'amphenol', 'molex',
    'velodyne', 'luminar', 'innoviz', 'ouster', 'aeva',
    'macom', 'semtech', 'maxlinear', 'silicon motion',
    'zygo', 'bruker', 'perkinelmer', 'excelitas', 'ushio',
    'nlight', 'finisar', 'acacia commun', 'inphi',
    'mellanox', 'luxtera', 'source photonics',
    'emcore', 'oclaro', 'photop',
    'raytheon', 'northrop grumman', 'lockheed', 'bae systems', 'l3harris',
    'thales', 'leonardo', 'elbit', 'rafael',
    'bosch', 'continental', 'denso', 'valeo', 'magna',
    'sony semicond', 'rohm', 'alps alpine',
    'skywater', 'tower semicond', 'united microelectronics', 'x-fab',
    'soitec', 'iqe', 'win semicond', 'vanguard international',
    'cirrus logic', 'microchip', 'microsemi',
    'ii-vi', 'newport corp', 'mks instrument',
    'cohu', 'formfactor', 'teradyne', 'advantest',
    'kulicke', 'nordson', 'mycronic',
    'disco corp', 'screen holdings', 'kokusai', 'hitachi high-tech',
    'photon control', 'photon dynamics', 'freedom photon',
]

KNOWN_NOT_RELEVANT_TARGETS = [
    'vmware', 'activision', 'blizzard', 'twitter', 'slack', 'github',
    'linkedin', 'whatsapp', 'instagram', 'youtube',
    'salesforce', 'tableau', 'splunk', 'datadog', 'snowflake',
    'figma', 'canva', 'unity software',
    'citrix', 'red hat', 'mongodb', 'elastic',
    'nuance commun', 'cerner', 'veeva',
    'doordash', 'airbnb',
    'whole foods', 'mgm resort', 'paramount',
    'kraft', 'heinz', 'mondelez',
    'pfizer', 'abbvie', 'allergan', 'celgene', 'gilead',
    'astrazeneca', 'novartis', 'roche', 'sanofi',
]

NOT_RELEVANT_TARGET_KW = [
    'software platform', 'saas ', 'cloud platform', 'cybersecurity',
    'fintech', 'banking platform', 'payment solution', 'insurance',
    'social media', 'video game', 'mobile app', 'gaming studio',
    'pharmaceutical', 'biotech', 'therapeut', 'clinical trial',
    'vaccine', 'genomic', 'protein',
    'food service', 'beverage', 'restaurant chain', 'fashion',
    'real estate', 'property manage', 'mortgage',
    'oil and gas', 'petroleum', 'mining',
    'airline', 'hotel chain', 'tourism',
    'publishing', 'entertainment',
    'consulting firm', 'staffing', 'recruitment',
    'logistics platform', 'freight',
    'construction', 'cement',
    'agriculture', 'farming', 'fertilizer',
    'textile', 'apparel', 'clothing',
    'furniture', 'e-learning', 'edtech',
    'health insurance', 'hospital chain',
    'marketing platform', 'ad tech',
    'streaming service', 'music',
    'cryptocurrency', 'blockchain', 'nft',
    'ride-sharing', 'food delivery',
]

KNOWN_RELEVANT_ACQUIRERS = [
    'intel', 'amd', 'nvidia', 'qualcomm', 'broadcom', 'texas instrument',
    'analog device', 'microchip', 'on semiconductor', 'onsemi', 'nxp',
    'infineon', 'stmicroelectronic', 'renesas', 'marvell', 'micron',
    'asml', 'lam research', 'applied material', 'kla',
    'coherent', 'ii-vi', 'lumentum', 'trumpf', 'ipg photonics',
    'keyence', 'cognex', 'teledyne', 'keysight', 'viavi',
    'skyworks', 'qorvo',
    'amkor', 'entegris', 'mks instrument',
    'onto innovation', 'axcelis', 'veeco', 'aixtron',
    'synopsys', 'cadence',
    'macom', 'semtech', 'maxlinear',
    'raytheon', 'northrop', 'lockheed', 'bae systems', 'l3harris',
    'thales', 'leonardo', 'elbit',
    'bosch', 'continental', 'denso', 'valeo',
    'tower semicond', 'globalfoundries',
    'cohu', 'formfactor', 'teradyne', 'advantest',
    'danaher', 'fortive', 'roper', 'ametek',
    'honeywell', 'emerson', 'rockwell',
    'samsung', 'sony', 'panasonic', 'toshiba', 'hitachi',
    'siemens', 'schneider', 'abb',
    'apple', 'google', 'amazon', 'microsoft', 'meta',
    'ouster', 'luminar', 'innoviz', 'mobileye',
    'electro optic', 'newport', 'nlight',
    'wolfspeed', 'cree',
    'vishay', 'littelfuse', 'sensata',
    'amphenol', 'te connectivity',
]


def classify_deal(tgt_raw, acq_raw):
    """Classify a single deal. Returns (classification, reason)."""
    tgt = decode(tgt_raw).lower()
    acq = decode(acq_raw).lower()

    for kw in KNOWN_NOT_RELEVANT_TARGETS:
        if kw in tgt:
            return 'other', 'Known non-semi/photonics target'
    for kw in KNOWN_RELEVANT_TARGETS:
        if kw in tgt:
            return 'relevant', 'Known semi/photonics company'
    for kw in RELEVANT_TARGET_KW:
        if word_match(tgt, kw):
            return 'relevant', f'Target keyword: {kw}'
    for kw in NOT_RELEVANT_TARGET_KW:
        if kw in tgt:
            return 'other', 'Non-relevant target keyword'
    for kw in KNOWN_RELEVANT_ACQUIRERS:
        if kw in acq:
            return 'unsure', 'Relevant acquirer, target unclear'
    for kw in RELEVANT_TARGET_KW:
        if word_match(acq, kw):
            return 'unsure', f'Acquirer keyword: {kw}'
    return 'unsure', 'Cannot determine from names'


# ── Region mapping (same as regenerate_data.py) ──────────────────────

EUROPE_COUNTRIES = {
    'United Kingdom', 'Germany', 'France', 'Netherlands', 'Sweden', 'Finland',
    'Switzerland', 'Italy', 'Austria', 'Belgium', 'Denmark', 'Norway', 'Ireland',
    'Spain', 'Portugal', 'Poland', 'Czech Republic', 'Hungary', 'Romania',
    'Luxembourg', 'Estonia', 'Latvia', 'Lithuania', 'Greece', 'Croatia',
    'Slovenia', 'Slovakia', 'Bulgaria', 'Cyprus', 'Malta', 'Iceland', 'Liechtenstein',
    'Scotland', 'Wales', 'Northern Ireland', 'England'
}
USA_VARIANTS = {'United States', 'USA', 'US'}
ASIA_COUNTRIES = {
    'China', 'Japan', 'South Korea', 'Taiwan', 'Singapore', 'India', 'Malaysia',
    'Thailand', 'Vietnam', 'Philippines', 'Indonesia', 'Hong Kong', 'Australia',
    'New Zealand', 'Israel'
}

def get_region(country):
    if not country: return 'Other'
    c = country.strip()
    if c in USA_VARIANTS: return 'USA'
    if c in EUROPE_COUNTRIES: return 'Europe'
    if c in ASIA_COUNTRIES: return 'Asia'
    return 'Other'


# ── Diff logic ────────────────────────────────────────────────────────

def deal_fingerprint(deal):
    """Create a unique fingerprint for a deal to detect duplicates."""
    # Use acquirer + target + year as the key
    acq = decode(deal.get('acquirer', deal.get('acq', ''))).lower().strip()
    tgt = decode(deal.get('target', deal.get('tgt', ''))).lower().strip()
    yr = deal.get('year', deal.get('yr', 0))
    return f"{yr}|{acq}|{tgt}"


def find_new_deals(current_scraped, previous_alldeals):
    """Find deals in current scrape that weren't in previous dataset."""
    prev_fps = set()
    for d in previous_alldeals:
        fp = deal_fingerprint(d)
        prev_fps.add(fp)

    new_deals = []
    for d in current_scraped:
        fp = deal_fingerprint(d)
        if fp not in prev_fps:
            new_deals.append(d)

    return new_deals


# ── Regenerate data.json (same logic as regenerate_data.py) ──────────

def regenerate_data_json(raw_data_json_content, classified_deals):
    """Filter to relevant deals and recompute all stats."""
    classification_map = {}
    for deal in classified_deals:
        classification_map[deal['idx']] = deal['classification']

    all_deals_original = raw_data_json_content.get('allDeals', [])
    relevant_deals = []
    excluded_count = 0
    for i, deal in enumerate(all_deals_original):
        cls = classification_map.get(i, 'relevant')
        if cls == 'relevant':
            relevant_deals.append(deal)
        else:
            excluded_count += 1

    total = len(relevant_deals)

    # Targets/acquirers by region
    targets_by_region = defaultdict(int)
    acquirers_by_region = defaultdict(int)
    for d in relevant_deals:
        targets_by_region[d.get('tR', 'Other')] += 1
        acquirers_by_region[d.get('aR', 'Other')] += 1

    eu_targets_total = targets_by_region.get('Europe', 0)

    # Cross-border flows
    usa_buy_eu = [d for d in relevant_deals if d.get('aR') == 'USA' and d.get('tR') == 'Europe']
    eu_buy_usa = [d for d in relevant_deals if d.get('aR') == 'Europe' and d.get('tR') == 'USA']
    eu_buy_eu = [d for d in relevant_deals if d.get('aR') == 'Europe' and d.get('tR') == 'Europe']
    asia_buy_eu = [d for d in relevant_deals if d.get('aR') == 'Asia' and d.get('tR') == 'Europe']

    total_disclosed_value = sum(d['v'] for d in relevant_deals if d.get('v'))
    total_usa_buy_eu_value = sum(d['v'] for d in usa_buy_eu if d.get('v'))
    total_eu_buy_usa_value = sum(d['v'] for d in eu_buy_usa if d.get('v'))

    # EU by acquirer
    eu_by_acquirer = defaultdict(int)
    for d in relevant_deals:
        if d.get('tR') == 'Europe':
            eu_by_acquirer[d.get('aR', 'Other')] += 1

    def compute_size_stats(deals):
        vals = [d['v'] for d in deals if d.get('v')]
        if not vals: return {"avg": 0, "median": 0, "total": 0, "count": 0}
        return {"avg": round(sum(vals)/len(vals)), "median": round(stat_median(vals)),
                "total": round(sum(vals)), "count": len(vals)}

    eu_size_by_acquirer = {}
    for region in ['USA', 'Europe', 'Asia']:
        deals = [d for d in relevant_deals if d.get('aR') == region and d.get('tR') == 'Europe']
        eu_size_by_acquirer[region] = compute_size_stats(deals)

    years = sorted(set(d['yr'] for d in relevant_deals if d.get('yr')))
    year_data = []
    for yr in years:
        yr_deals = [d for d in relevant_deals if d.get('yr') == yr]
        row = {"year": yr, "USA": 0, "Europe": 0, "Asia": 0, "Other": 0}
        for d in yr_deals:
            r = d.get('tR', 'Other')
            row[r] = row.get(r, 0) + 1
        year_data.append(row)

    year_value_data = []
    for yr in years:
        yr_deals = [d for d in relevant_deals if d.get('yr') == yr and d.get('v')]
        row = {"year": yr, "USA": 0, "Europe": 0, "Asia": 0}
        for d in yr_deals:
            r = d.get('tR', 'Other')
            if r in row: row[r] += d['v']
        year_value_data.append(row)

    usa_buy_eu_per_year = []
    for yr in years:
        n = len([d for d in usa_buy_eu if d.get('yr') == yr])
        if n > 0 or yr >= 2013:
            usa_buy_eu_per_year.append({"yr": yr, "n": n})

    top_deals = sorted([d for d in relevant_deals if d.get('v')], key=lambda d: d['v'], reverse=True)[:12]
    top_deals_fmt = [{"yr": d['yr'], "acq": d['acq'], "aR": d.get('aR','Other'),
                      "tgt": d['tgt'], "tR": d.get('tR','Other'), "v": d['v'],
                      "url": d.get('url','')} for d in top_deals]

    usa_buy_eu_sorted = sorted([d for d in usa_buy_eu if d.get('v')], key=lambda d: d['v'], reverse=True)
    usa_buy_eu_fmt = [{"acq": d['acq'], "tgt": d['tgt'], "tgtC": d.get('tgtC',''),
                       "yr": d['yr'], "v": d['v'], "url": d.get('url','')} for d in usa_buy_eu_sorted[:20]]

    eu_buy_usa_sorted = sorted([d for d in eu_buy_usa if d.get('v')], key=lambda d: d['v'], reverse=True)
    eu_buy_usa_fmt = [{"acq": d['acq'], "acqC": d.get('acqC',''), "tgt": d['tgt'],
                       "yr": d['yr'], "v": d['v'], "url": d.get('url','')} for d in eu_buy_usa_sorted[:15]]

    blocked_deals = [bd for bd in raw_data_json_content.get('blockedDeals', [])
                     if bd.get('status', 'BLOCKED') == 'BLOCKED']

    return {
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "total": total,
        "totalUnfiltered": len(all_deals_original),
        "excludedDeals": excluded_count,
        "euTargetsTotal": eu_targets_total,
        "totalUSABuyEU": len(usa_buy_eu),
        "totalEUBuyUSA": len(eu_buy_usa),
        "totalEUBuyEU": len(eu_buy_eu),
        "totalAsiaBuyEU": len(asia_buy_eu),
        "totalDisclosedValue": total_disclosed_value,
        "totalUSABuyEUValue": total_usa_buy_eu_value,
        "totalEUBuyUSAValue": total_eu_buy_usa_value,
        "targetsByRegion": dict(targets_by_region),
        "acquirersByRegion": dict(acquirers_by_region),
        "euByAcquirer": dict(eu_by_acquirer),
        "euSizeByAcquirer": eu_size_by_acquirer,
        "yearData": year_data,
        "yearValueData": year_value_data,
        "usaBuyEU": usa_buy_eu_fmt,
        "euBuyUSA": eu_buy_usa_fmt,
        "topDeals": top_deals_fmt,
        "blockedDeals": blocked_deals,
        "usaBuyEUPerYear": usa_buy_eu_per_year,
        "allDeals": relevant_deals
    }


# ── Main pipeline ─────────────────────────────────────────────────────

def run_weekly_update():
    """
    Full weekly pipeline. Returns a dict with:
      - new_deals: list of new deals found
      - classified_new: list of {deal, classification, reason}
      - needs_review: list of unsure deals needing human review
      - total_before / total_after: deal counts
      - summary_text: formatted text for email
    """
    print(f"[{datetime.now()}] Starting weekly update...")

    # 1. Load previous UNFILTERED dataset for diffing
    #    (previous_alldeals.json has ALL deals, not just relevant ones)
    if PREVIOUS_DEALS_FILE.exists():
        with open(PREVIOUS_DEALS_FILE) as f:
            prev_alldeals = json.load(f)
    elif DATA_JSON.exists():
        # Fallback: use data.json (may be filtered, but better than nothing)
        with open(DATA_JSON) as f:
            prev_data = json.load(f)
        prev_alldeals = prev_data.get('allDeals', [])
    else:
        prev_alldeals = []

    print(f"  Previous dataset: {len(prev_alldeals)} deals (unfiltered baseline)")

    # 2. Scrape fresh data
    print("  Scraping EPIC Photonics Index...")
    transactions = scrape()
    if not transactions:
        print("  ERROR: No transactions scraped!")
        return None
    print(f"  Scraped {len(transactions)} total transactions")

    # 3. Build full raw data.json (unfiltered) using scraper's compute_stats
    blocked = load_blocked_deals()
    raw_stats = compute_stats(transactions, blocked)

    # 4. Find new deals by diffing
    new_deals = find_new_deals(transactions, prev_alldeals)
    print(f"  Found {len(new_deals)} new deals")

    # 5. Classify new deals
    classified_new = []
    for d in new_deals:
        cls, reason = classify_deal(d.get('target', ''), d.get('acquirer', ''))
        classified_new.append({
            'deal': d,
            'classification': cls,
            'reason': reason
        })

    counts = Counter(c['classification'] for c in classified_new)
    print(f"  Classifications: {dict(counts)}")

    # 6. Load/update master classification file
    if CLASSIFIED_FILE.exists():
        with open(CLASSIFIED_FILE) as f:
            master_classified = json.load(f)
    else:
        master_classified = []

    # Build lookup of existing classifications by fingerprint
    existing_fps = {}
    for cd in master_classified:
        fp = f"{cd.get('yr','')}|{decode(cd.get('acq','')).lower()}|{decode(cd.get('tgt','')).lower()}"
        existing_fps[fp] = cd['classification']

    # For the full scraped dataset, build classification for every deal
    all_classifications = []
    for i, deal_data in enumerate(raw_stats['allDeals']):
        fp = f"{deal_data.get('yr','')}|{decode(deal_data.get('acq','')).lower()}|{decode(deal_data.get('tgt','')).lower()}"
        if fp in existing_fps:
            cls = existing_fps[fp]
        else:
            # New deal — use auto-classification; unsure defaults to relevant (shows on dashboard)
            cls_result, _ = classify_deal(deal_data.get('tgt', ''), deal_data.get('acq', ''))
            cls = cls_result if cls_result != 'unsure' else 'relevant'

        all_classifications.append({
            'idx': i,
            'yr': deal_data.get('yr'),
            'acq': decode(deal_data.get('acq', '')),
            'tgt': decode(deal_data.get('tgt', '')),
            'classification': cls
        })

    # Save updated classifications
    with open(CLASSIFIED_FILE, 'w') as f:
        json.dump(all_classifications, f)
    print(f"  Saved {len(all_classifications)} classifications to {CLASSIFIED_FILE}")

    # 7. Regenerate filtered data.json
    filtered_data = regenerate_data_json(raw_stats, all_classifications)
    with open(DATA_JSON, 'w') as f:
        json.dump(filtered_data, f)
    print(f"  Regenerated data.json: {filtered_data['total']} relevant, {filtered_data['excludedDeals']} excluded")

    # 8. Save current allDeals as "previous" for next week's diff
    with open(PREVIOUS_DEALS_FILE, 'w') as f:
        json.dump(raw_stats['allDeals'], f)

    # 8b. Generate review.json for the Review tab in the dashboard
    review_json_path = REPO / "review.json"
    cls_lookup = {cd['idx']: cd['classification'] for cd in all_classifications}
    review_deals = []
    for i, d in enumerate(raw_stats['allDeals']):
        review_deals.append({
            'idx': i, 'yr': d.get('yr'), 'mo': d.get('mo', ''),
            'acq': d.get('acq', ''), 'acqC': d.get('acqC', ''),
            'aR': d.get('aR', ''), 'tgt': d.get('tgt', ''),
            'tgtC': d.get('tgtC', ''), 'tR': d.get('tR', ''),
            'v': d.get('v'), 'url': d.get('url', ''),
            'cls': cls_lookup.get(i, 'relevant')
        })
    review_data = {
        'generated': datetime.now().strftime('%Y-%m-%d'),
        'totalDeals': len(review_deals),
        'deals': review_deals
    }
    with open(review_json_path, 'w') as f:
        json.dump(review_data, f)
    print(f"  Generated review.json: {len(review_deals)} deals")

    # 9. Build summary
    needs_review = [c for c in classified_new if c['classification'] == 'unsure']
    auto_relevant = [c for c in classified_new if c['classification'] == 'relevant']
    auto_other = [c for c in classified_new if c['classification'] == 'other']

    summary_lines = [
        f"Photonics M&A Weekly Update — {datetime.now().strftime('%B %d, %Y')}",
        f"",
        f"Scraped {len(transactions)} total deals from EPIC Photonics Index.",
        f"Found {len(new_deals)} new deals since last update.",
        f"",
        f"Auto-classified: {len(auto_relevant)} relevant, {len(auto_other)} non-relevant",
        f"Needs your review: {len(needs_review)} deals",
        f"",
        f"Dashboard now shows {filtered_data['total']} relevant deals ({filtered_data['excludedDeals']} ignored).",
        f"",
    ]

    if new_deals:
        summary_lines.append("NEW DEALS THIS WEEK:")
        summary_lines.append("=" * 60)
        for i, c in enumerate(classified_new, 1):
            d = c['deal']
            v_str = f"${d.get('value', 0)}M" if d.get('value') else "undisclosed"
            flag = ""
            if c['classification'] == 'unsure':
                flag = " [NEEDS REVIEW]"
            elif c['classification'] == 'other':
                flag = " [auto: non-relevant]"
            summary_lines.append(
                f"  {i}. {d.get('acquirer','?')} ({d.get('acquirer_country','?')}) "
                f"→ {d.get('target','?')} ({d.get('target_country','?')}) "
                f"| {d.get('year','')} | {v_str}{flag}"
            )
        summary_lines.append("")

    if needs_review:
        summary_lines.append("DEALS NEEDING YOUR CLASSIFICATION:")
        summary_lines.append("-" * 40)
        summary_lines.append("Reply with deal numbers to mark as 'other', e.g.: '3, 7, 12 = other'")
        summary_lines.append("All unlisted deals will remain as 'relevant'.")
        summary_lines.append("")
    else:
        summary_lines.append("No deals need manual review this week.")

    summary_text = "\n".join(summary_lines)
    print(f"\n{summary_text}")

    # Save deal number → fingerprint mapping for reply parsing
    deal_map = {}
    for i, c in enumerate(classified_new, 1):
        d = c['deal']
        fp = deal_fingerprint(d)
        deal_map[str(i)] = fp
    with open(DEAL_MAP_FILE, 'w') as f:
        json.dump(deal_map, f)
    print(f"  Saved deal map ({len(deal_map)} entries) to {DEAL_MAP_FILE}")

    return {
        'new_deals': new_deals,
        'classified_new': classified_new,
        'needs_review': needs_review,
        'total_before': len(prev_alldeals),
        'total_after': len(transactions),
        'dashboard_relevant': filtered_data['total'],
        'dashboard_excluded': filtered_data['excludedDeals'],
        'summary_text': summary_text,
    }


if __name__ == "__main__":
    result = run_weekly_update()
    if result:
        print(f"\nDone. {len(result['new_deals'])} new deals, {len(result['needs_review'])} need review.")
    else:
        print("\nUpdate failed.")
        sys.exit(1)
