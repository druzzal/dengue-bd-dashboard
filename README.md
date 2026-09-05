# Bangladesh Dengue Data Feed

An auto-updating mirror of the DGHS **[Dengue Dynamic Dashboard for Bangladesh](https://dashboard.dghs.gov.bd/pages/heoc_dengue_v1.php)**,
published as a GitHub Pages site with stable JSON endpoints your app can poll.

The source page is server-rendered PHP with no API — every figure is baked into HTML tables and
inline Highcharts configs. A scheduled GitHub Action scrapes it, converts it to JSON, and commits
the result, so your app never has to parse HTML.

---

## What gets published

| Endpoint | Contents |
|---|---|
| `docs/data/summary.json` | Headline numbers only (~350 bytes). Poll this. |
| `docs/data/latest.json` | Everything: KPIs, all 27 chart series, all 8 tables (~40 KB). |
| `docs/data/history/index.json` | Array of archived report dates. |
| `docs/data/history/YYYY-MM-DD.json` | Full snapshot for one past report date. |

Once Pages is on, these are served at:

```
https://<your-username>.github.io/<repo-name>/data/summary.json
https://<your-username>.github.io/<repo-name>/data/latest.json
```

GitHub Pages sends `Access-Control-Allow-Origin: *`, so a browser or mobile app can fetch them directly.

---

## Setup (one time)

1. **Create the repo and push**

   ```bash
   cd dengue-bd-dashboard
   gh repo create dengue-bd-dashboard --public --source=. --push
   ```

   Or create an empty repo on github.com, then:

   ```bash
   git remote add origin https://github.com/<you>/dengue-bd-dashboard.git
   git branch -M main && git push -u origin main
   ```

2. **Enable GitHub Pages** — repo *Settings → Pages* → Source: **Deploy from a branch**,
   Branch: **main**, Folder: **/docs** → Save.

3. **Allow the Action to commit** — repo *Settings → Actions → General →
   Workflow permissions* → **Read and write permissions** → Save.

4. **Run it once** — *Actions* tab → *Update dengue data* → **Run workflow**.

Your dashboard is then live at `https://<you>.github.io/dengue-bd-dashboard/`.

---

## How updating works

`.github/workflows/update-data.yml` runs `scraper/scrape.py` every 3 hours and on demand.
DGHS publishes its daily press release around **14:30 Bangladesh time (08:30 UTC)**; the 3-hourly
cadence catches late or re-issued updates the same day. If nothing changed, the job commits nothing.

Before writing, the scraper runs a sanity check (cumulative count present, ~27 charts parsed,
report date found) and **fails loudly instead of publishing garbage** if DGHS redesigns the page —
so your app keeps serving the last good data rather than empty values.

> **Note:** GitHub disables scheduled workflows in repos with no activity for 60 days. The daily data
> commits keep this repo active, so it stays on. If the feed ever goes quiet, re-enable it from the Actions tab.

---

## Consuming it from your app

Poll `summary.json`, and only pull the big `latest.json` when `last_updated` changes:

```js
const BASE = 'https://<you>.github.io/dengue-bd-dashboard/data';

async function refresh(cachedDate) {
  const s = await (await fetch(`${BASE}/summary.json`, {cache: 'no-store'})).json();
  if (s.last_updated === cachedDate) return null;      // nothing new
  return await (await fetch(`${BASE}/latest.json`, {cache: 'no-store'})).json();
}
```

### `summary.json`

```json
{
  "schema_version": 1,
  "fetched_at": "2026-09-06T04:10:22Z",
  "last_updated": "2026-09-05",
  "year": 2026,
  "summary": {
    "epi_week": "W35",
    "week_cases": 7446,
    "week_deaths": 23,
    "ytd_cases": 41032,
    "ytd_deaths": 113,
    "last24_cases": 988,
    "last24_deaths": 2,
    "discharged_last24": 986,
    "discharged_ytd": 38179
  }
}
```

`fetched_at` is when the scraper ran; **`last_updated` is the report date DGHS itself stamps** —
use that one to decide whether the data is new.

### `latest.json`

```jsonc
{
  "schema_version": 1,
  "fetched_at": "...",
  "meta":    { "source_url": "...", "last_updated": "2026-09-05", "year": 2026 },
  "summary": { /* same as above */ },
  "charts": {
    "confirmed_case": {
      "title": "Dengue affected (Admitted) by date",
      "categories": ["01-Jan-26", "02-Jan-26", "..."],
      "series": [{"name": "Affected (Admitted) by date", "type": "column", "data": [76, 63, 53]}]
    }
    // ...26 more
  },
  "tables": [
    {
      "title": "Age group distribution of affected cases of last 24 hours",
      "headers": ["Age Group", "Male", "Female", "Total"],
      "rows": [{"Age Group": "0-5", "Male": 35, "Female": 22, "Total": 57}]
    }
  ]
}
```

Chart series come in two shapes: a flat number array, or `{"name": "Male", "y": 25367}` objects for
pie charts. Numbers are parsed to real numbers (`"7,446"` → `7446`); missing values are `null`.

### Useful chart keys

| Key | Chart |
|---|---|
| `confirmed_case` | Daily admissions, current year |
| `death_case` | Daily deaths, current year |
| `by_week_case` | Admissions by EPI week, current year vs 2023–2025 |
| `division_case` / `division_death` | Division distribution |
| `by_month_case` | Monthly admissions |
| `year_case` | Yearly totals since 2018 |
| `dengue_affected_by_age_group` / `dengue_death_by_age_group` | Age-group breakdown |
| `dengue_affected_by_gender` / `dengue_death_by_gender` | Sex breakdown (pie shape) |
| `affected_in_division_by_week` | Division × EPI week |
| `div_city_cor_case_last_24_hour` | Division & city corporation, last 24h |

Run `python3 -c "import json;print(list(json.load(open('docs/data/latest.json'))['charts']))"` for the full list.

---

## Running the scraper locally

```bash
python3 scraper/scrape.py                     # fetch live and write docs/data/
DENGUE_HTML_FILE=saved.html python3 scraper/scrape.py   # parse a saved page instead
DENGUE_FORCE_WRITE=1 python3 scraper/scrape.py          # write even if sanity checks fail
```

Standard library only — no dependencies to install.

Preview the site locally:

```bash
python3 -m http.server -d docs 8000   # then open http://localhost:8000
```

---

## Caveats

- **Unofficial.** DGHS is the authority for all figures; this repo only reformats what it publishes.
- Historical years (2024, 2025) are behind a POST form on the source page and are **not** scraped;
  only the current year plus whatever comparison series DGHS embeds in the EPI-week chart.
- Age-group tables occasionally contain source-side glitches (for example a stray `42309` row where a
  date was entered as an age group). Rows are passed through as published rather than silently dropped.
- Be a good citizen: the schedule is 8 requests/day. Don't lower the interval much.
