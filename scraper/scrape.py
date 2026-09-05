#!/usr/bin/env python3
"""
Scrape the DGHS Dengue Dynamic Dashboard and emit a stable JSON feed.

Source: https://dashboard.dghs.gov.bd/pages/heoc_dengue_v1.php

The source page is server-rendered PHP: there is no API. All numbers live in
(a) KPI divs, (b) HTML tables, and (c) inline Highcharts configs. This script
parses all three and writes a versioned JSON document that apps can poll.

Stdlib only - no pip install needed in CI.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

SOURCE_URL = "https://dashboard.dghs.gov.bd/pages/heoc_dengue_v1.php"
SCHEMA_VERSION = 1

# The site 403s on a bare urllib/curl user agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------

def fetch(url=SOURCE_URL, retries=3, timeout=60):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return raw.decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last = exc
            print(f"fetch attempt {attempt + 1}/{retries} failed: {exc}", file=sys.stderr)
    raise SystemExit(f"could not fetch {url}: {last}")


# --------------------------------------------------------------------------
# small HTML helpers
# --------------------------------------------------------------------------

def strip_tags(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", text).strip()


def to_number(text):
    """'7,446' -> 7446 ; '' -> None ; keeps floats."""
    if text is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", str(text).replace(",", ""))
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return int(cleaned)
    except ValueError:
        try:
            return float(cleaned)
        except ValueError:
            return None


def parse_site_date(text):
    """'05-Sep-2026' -> '2026-09-05' (ISO), else None."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", text)
    if not m:
        return None
    day, mon, year = m.group(1), m.group(2).lower(), m.group(3)
    if mon not in MONTHS:
        return None
    return f"{year}-{MONTHS[mon]:02d}-{int(day):02d}"


def match_braces(html, start):
    """Return the balanced {...} block beginning at/after index `start`."""
    i = html.find("{", start)
    if i == -1:
        return None
    depth = 0
    in_str = None
    j = i
    while j < len(html):
        ch = html[j]
        if in_str:
            if ch == "\\":
                j += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "'\"":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[i:j + 1]
        j += 1
    return None


# --------------------------------------------------------------------------
# Highcharts extraction
# --------------------------------------------------------------------------

def js_array_at(text, key):
    """Extract the bracketed array that follows `key:` in a JS object literal."""
    m = re.search(r"\b%s\s*:\s*\[" % re.escape(key), text)
    if not m:
        return None
    i = text.index("[", m.end() - 1)
    depth = 0
    in_str = None
    j = i
    while j < len(text):
        ch = text[j]
        if in_str:
            if ch == "\\":
                j += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "'\"":
            in_str = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
        j += 1
    return None


def parse_categories(block):
    raw = js_array_at(block, "categories")
    if not raw:
        return []
    return [strip_tags(double or single)
            for double, single in re.findall(r'"([^"]*)"|\'([^\']*)\'', raw)]


def parse_data_points(raw):
    """Handle both `data:[1,2,3]` and `data:[{name:'Male', y:25367}, ...]`."""
    if raw is None:
        return []
    if "{" in raw:
        points = []
        for chunk in re.finditer(r"\{[^{}]*\}", raw):
            piece = chunk.group(0)
            name = re.search(r"name\s*:\s*['\"]([^'\"]*)['\"]", piece)
            y = re.search(r"\by\s*:\s*(-?[\d.]+)", piece)
            points.append({
                "name": name.group(1) if name else None,
                "y": to_number(y.group(1)) if y else None,
            })
        return points
    return [None if tok == "null" else to_number(tok)
            for tok in re.findall(r"-?\d+(?:\.\d+)?|null", raw)]


def parse_series(block):
    raw = js_array_at(block, "series")
    if not raw:
        return []
    inner = raw[1:-1]
    series = []
    pos = 0
    while True:
        start = inner.find("{", pos)
        if start == -1:
            break
        entry = match_braces(inner, start)
        if entry is None:
            break
        pos = start + len(entry)
        name = re.search(r"\bname\s*:\s*['\"]([^'\"]*)['\"]", entry)
        stype = re.search(r"\btype\s*:\s*['\"]([^'\"]*)['\"]", entry)
        data_raw = js_array_at(entry, "data")
        series.append({
            "name": name.group(1) if name else None,
            "type": stype.group(1) if stype else None,
            "data": parse_data_points(data_raw),
        })
    return series


def nearest_title(html, index):
    """Closest <h3 class="box-title"> heading preceding `index`."""
    best = None
    for m in re.finditer(r'<h3[^>]*class="[^"]*box-title[^"]*"[^>]*>(.*?)</h3>',
                         html, re.S | re.I):
        if m.start() < index:
            best = strip_tags(m.group(1))
        else:
            break
    return best


def extract_charts(html):
    charts = {}
    for m in re.finditer(r"Highcharts\.chart\(\s*['\"]([^'\"]+)['\"]\s*,", html):
        chart_id = m.group(1)
        block = match_braces(html, m.end())
        if not block:
            continue
        div = html.find('id="%s"' % chart_id)
        charts[chart_id] = {
            "title": nearest_title(html, div if div != -1 else m.start()),
            "categories": parse_categories(block),
            "series": parse_series(block),
        }
    return charts


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------

def extract_tables(html):
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
    tables = []
    for m in re.finditer(r"<table.*?</table>", body, re.S | re.I):
        rows = []
        for tr in re.findall(r"<tr.*?</tr>", m.group(0), re.S | re.I):
            cells = [strip_tags(c) for c in
                     re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
            if any(cells):
                rows.append(cells)
        if len(rows) < 2:
            continue
        header, data_rows = rows[0], rows[1:]
        records = []
        for row in data_rows:
            record = {}
            for idx, col in enumerate(header):
                value = row[idx] if idx < len(row) else None
                record[col or f"col{idx}"] = value if idx == 0 else to_number(value)
            records.append(record)
        tables.append({
            "title": nearest_title(body, m.start()),
            "headers": header,
            "rows": records,
        })
    return tables


# --------------------------------------------------------------------------
# KPI header
# --------------------------------------------------------------------------

def extract_summary(html, charts):
    summary = {}

    # The four header tiles are <div class="item_one"> blocks, identified by icon:
    # w_a = this week's cases, w_d = this week's deaths,
    # c_a = cumulative cases, c_d = cumulative deaths.
    icon_map = {"w_a": "week_cases", "w_d": "week_deaths",
                "c_a": "ytd_cases", "c_d": "ytd_deaths"}
    for m in re.finditer(r'<div class="item_one"[^>]*>(.*?)</div>', html, re.S | re.I):
        chunk = m.group(1)
        icon = re.search(r"dengue/(\w+)\.png", chunk)
        if not icon or icon.group(1) not in icon_map:
            continue
        text = strip_tags(re.sub(r"<img[^>]*>", "", chunk))
        week = re.search(r"(W\d+)\s*:", text)
        if week:
            summary["epi_week"] = week.group(1)
            text = text.split(":", 1)[1]
        summary[icon_map[icon.group(1)]] = to_number(text)

    def first_value(chart_id):
        chart = charts.get(chart_id) or {}
        for s in chart.get("series", []):
            if s.get("data"):
                point = s["data"][0]
                return point.get("y") if isinstance(point, dict) else point
        return None

    summary["last24_cases"] = first_value("affected_case_last_24_hour")
    summary["last24_deaths"] = first_value("death_case_last_24_hour")
    summary["discharged_last24"] = first_value("dengue_discharged_total_and_24_hours")

    discharged = charts.get("dengue_discharged_total_and_24_hours", {})
    for s in discharged.get("series", []):
        if s.get("name", "").lower().startswith("discharged from") and s.get("data"):
            summary["discharged_ytd"] = s["data"][0]

    # Cumulative tiles are the authoritative YTD figures; fall back to the charts.
    summary.setdefault("ytd_cases", first_value("affected_case_in_year"))
    summary.setdefault("ytd_deaths", first_value("death_case_in_year"))
    return summary


def extract_meta(html):
    updated = re.search(r"Last Updated:\s*</span>\s*([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})", html)
    if not updated:
        updated = re.search(r"Last Updated:.{0,80}?([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})", html, re.S)
    label = updated.group(1) if updated else None
    year = re.search(r'<option value="(\d{4})"\s+selected', html)
    return {
        "source_url": SOURCE_URL,
        "source_name": "DGHS Health Emergency Operation Center & Control Room",
        "last_updated_label": label,
        "last_updated": parse_site_date(label),
        "year": int(year.group(1)) if year else None,
    }


# --------------------------------------------------------------------------
# build + write
# --------------------------------------------------------------------------

def build(html):
    charts = extract_charts(html)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "meta": extract_meta(html),
        "summary": extract_summary(html, charts),
        "charts": charts,
        "tables": extract_tables(html),
    }
    return doc


def sanity_check(doc):
    """Refuse to publish an obviously broken scrape (site redesign, error page)."""
    problems = []
    s = doc["summary"]
    if not s.get("ytd_cases"):
        problems.append("missing cumulative case count")
    if len(doc["charts"]) < 10:
        problems.append(f"only {len(doc['charts'])} charts parsed (expected ~27)")
    if not doc["meta"].get("last_updated"):
        problems.append("missing 'Last Updated' date")
    return problems


def unchanged_since_last_run(doc, latest_path):
    """True if only `fetched_at` differs from what is already published.

    Every run stamps a new `fetched_at`, so a naive file comparison always
    reports a difference and CI would commit on every schedule tick. Compare
    the payload itself instead, so history records DGHS updates and nothing else.
    """
    if not os.path.exists(latest_path):
        return False
    try:
        with open(latest_path, encoding="utf-8") as fh:
            previous = json.load(fh)
    except (ValueError, OSError):
        return False
    return {k: v for k, v in doc.items() if k != "fetched_at"} == \
           {k: v for k, v in previous.items() if k != "fetched_at"}


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(root, "docs", "data")

    html_override = os.environ.get("DENGUE_HTML_FILE")
    if html_override:
        with open(html_override, encoding="utf-8", errors="replace") as fh:
            html = fh.read()
    else:
        html = fetch()

    doc = build(html)
    problems = sanity_check(doc)
    if problems:
        print("SANITY CHECK FAILED:", "; ".join(problems), file=sys.stderr)
        if not os.environ.get("DENGUE_FORCE_WRITE"):
            raise SystemExit(1)

    latest_path = os.path.join(data_dir, "latest.json")
    if unchanged_since_last_run(doc, latest_path):
        print(f"no change: DGHS figures for {doc['meta']['last_updated']} already published")
        return

    write_json(latest_path, doc)

    # Compact feed for lightweight app polling.
    summary_doc = {
        "schema_version": SCHEMA_VERSION,
        "fetched_at": doc["fetched_at"],
        "last_updated": doc["meta"]["last_updated"],
        "year": doc["meta"]["year"],
        "summary": doc["summary"],
    }
    write_json(os.path.join(data_dir, "summary.json"), summary_doc)

    # Daily archive keyed by the report date the site itself reports.
    report_date = doc["meta"]["last_updated"]
    if report_date:
        write_json(os.path.join(data_dir, "history", f"{report_date}.json"), doc)

    index_path = os.path.join(data_dir, "history", "index.json")
    existing = []
    if os.path.exists(index_path):
        try:
            with open(index_path, encoding="utf-8") as fh:
                existing = json.load(fh)
        except (ValueError, OSError):
            existing = []
    if report_date and report_date not in existing:
        existing.append(report_date)
    write_json(index_path, sorted(existing))

    print(f"ok: {report_date} | ytd_cases={doc['summary'].get('ytd_cases')} "
          f"ytd_deaths={doc['summary'].get('ytd_deaths')} "
          f"charts={len(doc['charts'])} tables={len(doc['tables'])}")


if __name__ == "__main__":
    main()
