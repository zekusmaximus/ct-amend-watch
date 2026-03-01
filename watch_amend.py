#!/usr/bin/env python3
import json
import os
import re
import tempfile

import requests
from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright


def load_dotenv_file(filename: str = ".env"):
    """
    Minimal .env loader:
    - Reads KEY=VALUE pairs from a file next to this script.
    - Ignores blank lines and comments.
    - Does not overwrite already-set environment variables.
    """
    env_path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue

            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]

            os.environ.setdefault(key, value)


load_dotenv_file()

# === Config ===
HOUSE_URL = "https://www.cga.ct.gov/asp/CGAAmendProc/CGAHouseAmendRptDisp.asp?optSortby=D&optSortOrder=Desc"
SENATE_URL = "https://www.cga.ct.gov/asp/CGAAmendProc/CGASenateAmendRptDisp.asp?optSortby=D&optSortOrder=Desc"

# CGA Bill Status (we'll construct this directly from Bill # like SB00298 / HB05032)
BILL_STATUS_BASE = "https://www.cga.ct.gov/asp/CGABillStatus/cgabillstatus.asp"
SESSION_YEAR = int(os.environ.get("CT_SESSION_YEAR", "2026"))

STATE_PATH = os.environ.get(
    "CT_AMEND_STATE_PATH",
    os.path.join(os.path.dirname(__file__), "state.json"),
)
CONFIG_PATH = os.environ.get(
    "CT_AMEND_CONFIG_PATH",
    os.path.join(os.path.dirname(__file__), "config.json"),
)

USER_AGENT = "ct-amend-watcher/1.4 (+playwright)"

DEBUG = os.environ.get("CT_AMEND_DEBUG", "0") == "1"
REQUIRE_TELEGRAM = os.environ.get("CT_REQUIRE_TELEGRAM", "0") == "1"
ENABLE_SUMMARY = (
    os.environ.get("CT_ENABLE_SUMMARY", "0") == "1"
    and bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
)


# === Helpers ===
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def default_state():
    return {
        "house_last_lco": norm(os.environ.get("CT_HOUSE_LAST_LCO", "")) or None,
        "senate_last_lco": norm(os.environ.get("CT_SENATE_LAST_LCO", "")) or None,
    }


def load_state():
    defaults = default_state()
    if not os.path.exists(STATE_PATH):
        return defaults

    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return defaults

    if not isinstance(data, dict):
        return defaults

    merged = {
        "house_last_lco": data.get("house_last_lco", defaults["house_last_lco"]),
        "senate_last_lco": data.get("senate_last_lco", defaults["senate_last_lco"]),
    }
    for key in ("house_last_lco", "senate_last_lco"):
        val = merged.get(key)
        merged[key] = norm(str(val)) if val is not None else None
        if not merged[key]:
            merged[key] = None
    return merged


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def load_config():
    defaults = {
        "filter_mode": "all",
        "watched_bills": [],
        "ignored_bills": [],
        "watched_subjects": [],
        "watched_committees": [],
        "interests": [],
        "relevance_threshold": 4,
    }
    if not os.path.exists(CONFIG_PATH):
        return defaults
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return defaults
        return {**defaults, **data}
    except Exception:
        return defaults


def should_notify_bill(bill_label: str, config: dict) -> bool:
    mode = config.get("filter_mode", "all")
    bill_upper = bill_label.upper().strip()
    if mode == "watchlist":
        return bill_upper in {b.upper().strip() for b in config.get("watched_bills", [])}
    elif mode == "blocklist":
        return bill_upper not in {b.upper().strip() for b in config.get("ignored_bills", [])}
    return True



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


def click_sort_date_desc(page):
    """
    Optional: tries to click Date + Descending on the page.
    We DO NOT depend on this; we also sort in Python.
    """
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(200)

    def safe_click_any(locators, timeout_ms: int = 1500) -> bool:
        for locator in locators:
            try:
                locator.click(timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    _ = safe_click_any(
        [
            page.get_by_role("link", name=re.compile(r"^\s*date\s*$", re.I)),
            page.get_by_role("button", name=re.compile(r"^\s*date\s*$", re.I)),
            page.get_by_text(re.compile(r"^\s*date\s*$", re.I)).first,
            page.locator("text=Date").first,
        ]
    )

    page.wait_for_timeout(200)

    _ = safe_click_any(
        [
            page.get_by_role("link", name=re.compile(r"descending", re.I)),
            page.get_by_role("button", name=re.compile(r"descending", re.I)),
            page.get_by_text(re.compile(r"descending", re.I)).first,
            page.locator("text=Descending").first,
        ]
    )

    page.wait_for_timeout(250)


def parse_mmddyyyy(s: str):
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


def bill_status_url_from_bill(bill: str) -> str:
    """
    bill is like SB00298 or HB05032 (letters + 5 digits).
    CGA accepts bill_num=<bill> and which_year=<session>.
    """
    bill = norm(bill)
    # Defensive: allow SB9 style too, but your report shows SB00298 format.
    return f"{BILL_STATUS_BASE}?selBillType=Bill&bill_num={bill}&which_year={SESSION_YEAR}"


# Your “descending date order” view looks like tabular text:
# Cal. #  LCO #  Bill #   Date Rec.  Sched. Ltr.
# 0       2284   SB00299  2/25/2026  A
#
# We'll parse the BODY INNER TEXT, which avoids relying on HTML table structure.
ROW_RE = re.compile(
    r"^\s*(\d+)\s+(\d{1,6})\s+([A-Z]{2}\d{5})\s+(\d{1,2}/\d{1,2}/\d{4})\s*([A-Z])?\s*$"
)


def extract_rows_from_report_text(page, base_url: str):
    """
    Extract amendment rows by parsing visible text.
    Returns dicts: lco, bill_label, bill_status_url, date_text, sched_letter, row_text
    """
    body_text = page.locator("body").inner_text()
    lines = [ln.rstrip() for ln in body_text.splitlines() if ln.strip()]

    rows = []
    for ln in lines:
        m = ROW_RE.match(ln)
        if not m:
            continue

        cal_no = m.group(1)  # unused but available
        lco = m.group(2)
        bill = m.group(3)
        date_text = m.group(4)
        sched = (m.group(5) or "").strip()

        rows.append(
            {
                "lco": lco,
                "bill_label": bill,
                "bill_status_url": bill_status_url_from_bill(bill),
                "date_text": date_text,
                "sched_letter": sched,
                "row_text": norm(ln),
                "cal_no": cal_no,
            }
        )

    return rows



def build_direct_pdf_url(lco: str) -> str:
    """
    Construct a direct PDF URL from the LCO number alone.

    Uncalled-amendment PDFs live at a predictable path:
        /2026/lcoamd/pdf/2026LCO{lco}-R00-AMD.pdf

    This avoids scraping the bill-status page (disallowed by robots.txt).
    """
    lco_padded = lco.zfill(5)
    return f"https://www.cga.ct.gov/{SESSION_YEAR}/lcoamd/pdf/{SESSION_YEAR}LCO{lco_padded}-R00-AMD.pdf"


def find_amendment_pdf(lco: str):
    """
    Try to locate the amendment PDF by constructing the URL directly.

    Returns the PDF URL if reachable, else None.
    """
    url = build_direct_pdf_url(lco)
    try:
        r = requests.head(url, timeout=15, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
        if r.status_code == 200:
            return url
    except Exception:
        pass

    if DEBUG:
        print(f"[debug] direct PDF URL not reachable for LCO {lco}: {url}")
    return None


def extract_text_from_pdf_url(pdf_url: str, max_pages: int = 20) -> str | None:
    """Download an amendment PDF and extract its text content."""
    import pdfplumber

    resp = requests.get(pdf_url, timeout=30, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name

    try:
        text_parts = []
        with pdfplumber.open(tmp_path) as pdf:
            for i, pg in enumerate(pdf.pages):
                if i >= max_pages:
                    break
                page_text = pg.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n".join(text_parts) if text_parts else None
    finally:
        os.unlink(tmp_path)


def summarize_amendment(amendment_text: str, bill_label: str) -> str | None:
    """Use Claude Haiku to generate a plain-language summary of the amendment."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    client = anthropic.Anthropic(api_key=api_key)

    prompt = (
        f"You are summarizing a Connecticut legislative amendment for bill {bill_label}. "
        f"Provide a 2-3 sentence plain-language summary. Focus on: what the amendment "
        f"changes, who it affects, and any fiscal impact. Be concise and factual.\n\n"
        f"Amendment text:\n{amendment_text[:12000]}"
    )

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()


def score_relevance(summary: str, interests: list[str]) -> int | None:
    """Ask Claude Haiku to score 1-10 how relevant an amendment is to user interests."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not interests:
        return None

    client = anthropic.Anthropic(api_key=api_key)

    interest_list = "\n".join(f"- {i}" for i in interests)
    prompt = (
        f"Given these topics of interest:\n{interest_list}\n\n"
        f"And this amendment summary:\n{summary}\n\n"
        f"Rate 1-10 how relevant this amendment is to the listed interests. "
        f"Respond with ONLY a single integer."
    )

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        return int(message.content[0].text.strip())
    except ValueError:
        return None


def process_chamber(chamber_name: str, report_url: str, state_key: str, playwright, telegram_ready: bool):
    state = load_state()
    last_seen = state.get(state_key)

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()

    try:
        goto_with_retry(page, report_url)

        # Optional UI sort click (not required)
        try:
            click_sort_date_desc(page)
        except Exception:
            pass

        rows = extract_rows_from_report_text(page, base_url=report_url)

        # Sort newest-first by Date Rec. then by LCO as tie-breaker
        rows.sort(key=lambda r: (parse_mmddyyyy(r.get("date_text", "")), int(r["lco"])), reverse=True)

        if DEBUG:
            print(f"[{chamber_name}] extracted rows: {len(rows)}")
            for r in rows[:10]:
                print(
                    f"  date={r.get('date_text')}  LCO={r['lco']}  bill={r['bill_label']}  status={r['bill_status_url']}  sched={r.get('sched_letter')}"
                )

        if not rows:
            return

        # Determine new rows until we hit last_seen (newest-first)
        new_rows = []
        for row in rows:
            if last_seen and row["lco"] == last_seen:
                break
            new_rows.append(row)

        if not new_rows:
            return

        # Update last seen to newest LCO (top after sort)
        newest_lco = rows[0]["lco"]
        state[state_key] = newest_lco
        save_state(state)

        # Notify oldest-first for readability
        config = load_config()
        for row in reversed(new_rows):
            if not should_notify_bill(row["bill_label"], config):
                if DEBUG:
                    print(f"[debug] skipping {row['bill_label']} LCO {row['lco']} (filtered by {config['filter_mode']})")
                continue

            status_url = row["bill_status_url"]

            try:
                pdf = find_amendment_pdf(row["lco"])
            except Exception:
                pdf = None

            msg_lines = [
                f"CT {chamber_name} amendment update",
                f"Date Rec.: {row.get('date_text') or 'Unknown'}",
                f"LCO {row['lco']}",
                f"Bill: {row['bill_label']}",
            ]
            if row.get("sched_letter"):
                msg_lines.append(f"Sched. Ltr.: {row['sched_letter']}")

            if pdf:
                msg_lines.append(f"Amendment: {pdf}")

            summary = None
            if pdf and ENABLE_SUMMARY:
                try:
                    amendment_text = extract_text_from_pdf_url(pdf)
                    if amendment_text:
                        summary = summarize_amendment(amendment_text, row["bill_label"])
                        if DEBUG:
                            print(f"[debug] summary for LCO {row['lco']}: {summary[:80]}...")
                except Exception as e:
                    if DEBUG:
                        print(f"[debug] summarization failed for LCO {row['lco']}: {e}")

            # --- Feature 3C: LLM Relevance Scoring ---
            relevance_score = None
            if summary and config.get("interests"):
                try:
                    threshold = config.get("relevance_threshold", 4)
                    relevance_score = score_relevance(summary, config["interests"])
                    if relevance_score is not None and relevance_score < threshold:
                        if DEBUG:
                            print(f"[debug] skipping LCO {row['lco']} — relevance score {relevance_score} < {threshold}")
                        continue
                except Exception as e:
                    if DEBUG:
                        print(f"[debug] relevance scoring failed for LCO {row['lco']}: {e}")

            if summary:
                msg_lines.append(f"\nSummary: {summary}")
            if relevance_score is not None:
                msg_lines.append(f"Relevance: {relevance_score}/10")

            msg_lines.append(f"Bill status: {status_url}")

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
