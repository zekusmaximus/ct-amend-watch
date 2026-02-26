#!/usr/bin/env python3
import json
import os
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

HOUSE_URL = "https://www.cga.ct.gov/asp/CGAAmendProc/CGAHouseAmendReport.asp"
SENATE_URL = "https://www.cga.ct.gov/asp/CGAAmendProc/CGASenateAmendReport.asp"

STATE_PATH = os.environ.get(
    "CT_AMEND_STATE_PATH",
    os.path.join(os.path.dirname(__file__), "state.json"),
)

USER_AGENT = "ct-amend-watcher/1.3 (+playwright)"

DEBUG = os.environ.get("CT_AMEND_DEBUG", "0") == "1"
REQUIRE_TELEGRAM = os.environ.get("CT_REQUIRE_TELEGRAM", "0") == "1"

# Your screenshot confirms columns like:
# "Cal. # | LCO # | Bill # | Date Rec. | Sched. Ltr."
HEADER_NORMALIZE_RE = re.compile(r"\s+", re.UNICODE)


def norm(s: str) -> str:
    return HEADER_NORMALIZE_RE.sub(" ", (s or "").strip())


def load_state():
    if not os.path.exists(STATE_PATH):
        return {"house_last_lco": None, "senate_last_lco": None}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def get_telegram_creds():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID.")
    return token, chat_id


def telegram_send(text: str):
    token, chat_id = get_telegram_creds()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=25,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()


def goto_with_retry(page, url: str, tries: int = 3, timeout_ms: int = 30000):
    last = None
    for _ in range(tries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return
        except PWTimeoutError as e:
            last = e
            try:
                page.wait_for_timeout(800)
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                return
            except Exception:
                pass
    raise last


def safe_click_any(page, locators, timeout_ms: int = 1500) -> bool:
    for locator in locators:
        try:
            locator.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def click_sort_date_desc(page):
    """
    Attempts to click UI controls for Date + Descending.
    Not required for correctness because we also sort in Python,
    but harmless if present.
    """
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(200)

    _ = safe_click_any(
        page,
        [
            page.get_by_role("link", name=re.compile(r"^\s*date\s*$", re.I)),
            page.get_by_role("button", name=re.compile(r"^\s*date\s*$", re.I)),
            page.get_by_text(re.compile(r"^\s*date\s*$", re.I)).first,
            page.locator("text=Date").first,
        ],
    )

    page.wait_for_timeout(200)

    _ = safe_click_any(
        page,
        [
            page.get_by_role("link", name=re.compile(r"descending", re.I)),
            page.get_by_role("button", name=re.compile(r"descending", re.I)),
            page.get_by_text(re.compile(r"descending", re.I)).first,
            page.locator("text=Descending").first,
        ],
    )

    page.wait_for_timeout(300)


def parse_mmddyyyy(s: str):
    """
    Returns (yyyy, mm, dd) tuple for sorting, or (0,0,0) if invalid.
    Accepts m/d/yyyy or mm/dd/yy.
    """
    s = norm(s)
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if not m:
        return (0, 0, 0)
    mm = int(m.group(1))
    dd = int(m.group(2))
    yy = int(m.group(3))
    if yy < 100:
        yy += 2000
    return (yy, mm, dd)


def find_report_table(page):
    """
    Find the amendment report table by detecting headers.
    We specifically look for headers including "LCO" and "Bill" and "Date".
    """
    tables = page.locator("table")
    tcount = tables.count()
    if tcount == 0:
        return None, None

    best = None
    best_headers = None
    best_score = -1

    for i in range(tcount):
        t = tables.nth(i)
        try:
            header_row = t.locator("tr").first
            headers = header_row.locator("th, td")
            hc = headers.count()
            if hc < 3:
                continue

            header_texts = [norm(headers.nth(j).inner_text()) for j in range(hc)]
            header_join = " | ".join(h.lower() for h in header_texts)

            # Score presence of expected header tokens
            score = 0
            if "lco" in header_join:
                score += 10
            if "bill" in header_join:
                score += 10
            if "date" in header_join:
                score += 5
            if "cal" in header_join:
                score += 2
            if "sched" in header_join:
                score += 1

            # plus row count
            score += min(t.locator("tr").count(), 200)

            if score > best_score:
                best_score = score
                best = t
                best_headers = header_texts
        except Exception:
            continue

    return best, best_headers


def header_index(headers, needle_patterns):
    """
    headers: list[str] (as displayed)
    needle_patterns: list[regex] searched in normalized lower header
    """
    if not headers:
        return None
    lower = [norm(h).lower() for h in headers]
    for idx, h in enumerate(lower):
        for pat in needle_patterns:
            if re.search(pat, h):
                return idx
    return None


def extract_rows_from_report(page, base_url: str):
    """
    DOM-driven extraction from the report table.
    Returns rows:
      - lco (string digits)
      - bill_status_url (absolute) from hyperlink on Bill #
      - bill_label (e.g., SB00298 as displayed)
      - date_text (e.g., 2/25/2026)
      - row_text (fallback)
    """
    table, headers = find_report_table(page)
    if table is None:
        return []

    lco_idx = header_index(headers, [r"\blco\b"])
    bill_idx = header_index(headers, [r"\bbill\b"])
    date_idx = header_index(headers, [r"\bdate\b"])

    # Your example guarantees these exist; if not, bail so we don't mis-parse.
    if lco_idx is None or bill_idx is None or date_idx is None:
        if DEBUG:
            print("[debug] Could not locate required columns. Headers:", headers)
        return []

    out = []
    rows = table.locator("tr")
    rc = rows.count()

    # Assume first row is header
    for ri in range(1, rc):
        r = rows.nth(ri)
        cells = r.locator("td")
        cc = cells.count()
        if cc == 0:
            continue

        # Some tables may have fewer cells on spacer rows; skip
        if max(lco_idx, bill_idx, date_idx) >= cc:
            continue

        lco_raw = norm(cells.nth(lco_idx).inner_text())
        lco_m = re.search(r"(\d+)", lco_raw)
        if not lco_m:
            continue
        lco = lco_m.group(1)

        bill_cell = cells.nth(bill_idx)
        bill_label = norm(bill_cell.inner_text())

        bill_a = bill_cell.locator("a").first
        bill_href = None
        try:
            if bill_a.count() > 0:
                bill_href = bill_a.get_attribute("href")
        except Exception:
            bill_href = None

        bill_status_url = urljoin(base_url, bill_href) if bill_href else None

        date_text = norm(cells.nth(date_idx).inner_text())

        row_text = norm(r.inner_text())

        out.append(
            {
                "lco": lco,
                "bill_status_url": bill_status_url,
                "bill_label": bill_label,
                "date_text": date_text,
                "row_text": row_text,
            }
        )

    return out


def find_amendment_pdf_on_status(status_url: str, lco: str):
    """
    Fetches the bill status page and finds the best matching amendment link for this LCO.
    Returns absolute URL if found, else None.
    """
    if not status_url:
        return None

    r = requests.get(status_url, timeout=25, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")
    links = soup.find_all("a", href=True)

    # Prefer direct PDF links mentioning the LCO
    candidates = []
    for a in links:
        href = a["href"]
        text = a.get_text(" ", strip=True)
        blob = f"{text} {href}"
        if lco in blob:
            abs_url = urljoin(status_url, href)
            score = 0
            if abs_url.lower().endswith(".pdf"):
                score += 10
            if "amend" in blob.lower() or "amd" in blob.lower():
                score += 3
            candidates.append((score, abs_url))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    # Fallback: any amendment-looking PDF
    for a in links:
        abs_url = urljoin(status_url, a["href"])
        text = a.get_text(" ", strip=True).lower()
        if abs_url.lower().endswith(".pdf") and ("amend" in text or "amend" in abs_url.lower() or "amd" in abs_url.lower()):
            return abs_url

    return None


def process_chamber(chamber_name: str, report_url: str, state_key: str, playwright, telegram_ready: bool):
    state = load_state()
    last_seen = state.get(state_key)

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()

    try:
        goto_with_retry(page, report_url)

        # Optional; correctness does not depend on these clicks
        try:
            click_sort_date_desc(page)
        except Exception:
            pass

        rows = extract_rows_from_report(page, base_url=report_url)

        # Sort newest-first by Date Rec. (and then by LCO as tie-breaker)
        rows.sort(key=lambda r: (parse_mmddyyyy(r.get("date_text", "")), int(r["lco"])), reverse=True)

        if DEBUG:
            print(f"[{chamber_name}] extracted rows: {len(rows)}")
            for r in rows[:10]:
                print(
                    f"  date={r.get('date_text')}  LCO={r['lco']}  bill={r['bill_label']}  status={r['bill_status_url']}"
                )

        if not rows:
            return

        # Determine "new" rows until we hit last_seen (newest-first)
        new_rows = []
        for row in rows:
            if last_seen and row["lco"] == last_seen:
                break
            new_rows.append(row)

        if not new_rows:
            return

        # Update last seen to the newest LCO (top after sort)
        newest_lco = rows[0]["lco"]
        state[state_key] = newest_lco
        save_state(state)

        # Notify oldest-first for readability
        for row in reversed(new_rows):
            status_url = row["bill_status_url"]
            pdf = None
            if status_url:
                try:
                    pdf = find_amendment_pdf_on_status(status_url, row["lco"])
                except Exception:
                    pdf = None

            msg_lines = [
                f"CT {chamber_name} amendment update",
                f"Date Rec.: {row.get('date_text') or 'Unknown'}",
                f"LCO {row['lco']}",
                f"Bill: {row['bill_label']}",
            ]

            if pdf:
                msg_lines.append(f"Amendment PDF: {pdf}")
            elif status_url:
                msg_lines.append(f"Status page: {status_url}")
                msg_lines.append("(Could not locate PDF link for this LCO—status page should contain it.)")
            else:
                msg_lines.append("Could not locate bill/status link on report row.")
                msg_lines.append(f"Row: {row['row_text']}")

            msg = "\n".join(msg_lines)

            if telegram_ready:
                telegram_send(msg)
            else:
                if DEBUG:
                    print(f"[debug] would notify:\n{msg}\n")

    finally:
        try:
            context.close()
        finally:
            browser.close()


def main():
    telegram_ready = True
    try:
        _ = get_telegram_creds()
    except Exception:
        telegram_ready = False

    if REQUIRE_TELEGRAM and not telegram_ready:
        raise SystemExit("Telegram is required: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")

    if DEBUG and not telegram_ready:
        print("[debug] Telegram creds not set; scrape-only mode (no notifications).")

    with sync_playwright() as p:
        process_chamber("House", HOUSE_URL, "house_last_lco", p, telegram_ready=telegram_ready)
        process_chamber("Senate", SENATE_URL, "senate_last_lco", p, telegram_ready=telegram_ready)


if __name__ == "__main__":
    main()