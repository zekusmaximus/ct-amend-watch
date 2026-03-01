# Feature Ideas

Three planned enhancements for CT Amend Watch, ordered by implementation priority.

---

## Feature 1: Direct Amendment Links

**Goal:** Instead of linking to the bill status page (or a best-guess PDF), link directly to the specific amendment on the CGA site by its LCO number.

### Current Behavior

`find_amendment_pdf_on_status()` fetches the bill status page with `requests` + BeautifulSoup and tries to match a link containing the LCO number. This works sometimes but fails when:
- The LCO number appears differently in the link text vs. what we parsed (formatting, leading zeros, etc.)
- The amendment links are loaded via JavaScript after initial page render
- Multiple amendments exist and the fallback grabs the wrong one

When it fails, the user gets the bill status page URL with a note to find the amendment manually.

### Implementation Plan

#### Step 1: Investigate the bill status page structure

Before writing code, manually check a few bill status pages to understand the link format:

```
https://www.cga.ct.gov/asp/CGABillStatus/cgabillstatus.asp?selBillType=Bill&bill_num=SB00298&which_year=2026
```

- View page source (not DevTools rendered DOM) to see if amendment links are in the raw HTML or JS-rendered
- Note the exact format of LCO references in link text and href attributes
- Check if amendment links go to a PDF directly or to another intermediate page

#### Step 2a: If links are in static HTML (likely)

The current `requests` + BeautifulSoup approach is correct. Just improve the matching:

**File:** `watch_amend.py`, function `find_amendment_pdf_on_status()`

```python
def find_amendment_pdf_on_status(status_url: str, lco: str):
    # Normalize LCO for comparison: strip leading zeros, compare as int
    lco_int = int(lco)

    r = requests.get(status_url, timeout=25, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True)
        blob = f"{text} {href}"

        # Try to find any number in the blob that matches our LCO
        for num_match in re.finditer(r'\d+', blob):
            if int(num_match.group()) == lco_int:
                abs_url = urljoin(status_url, href)
                # Return the link — whether it's a PDF or an amendment detail page
                return abs_url

    return None
```

Key changes:
- Compare LCO as integer to handle leading-zero mismatches (e.g., "2413" vs "02413")
- Drop the scoring system — a direct LCO match is sufficient
- Remove the greedy fallback that could grab the wrong amendment
- Return whatever the link points to (PDF or detail page), since either is more useful than the generic bill status URL

#### Step 2b: If links are JS-rendered (unlikely but possible)

Use the existing Playwright browser instead of `requests`:

```python
def find_amendment_link_with_playwright(page, status_url: str, lco: str):
    page.goto(status_url, wait_until="networkidle", timeout=30000)
    lco_int = int(lco)

    links = page.query_selector_all("a[href]")
    for link in links:
        text = link.inner_text()
        href = link.get_attribute("href")
        blob = f"{text} {href}"

        for num_match in re.finditer(r'\d+', blob):
            if int(num_match.group()) == lco_int:
                return urljoin(status_url, href)

    return None
```

This would require refactoring `process_chamber()` to pass the `page` object (or create a new page in the same context) to avoid launching another browser.

#### Step 3: Update the notification message

Once we reliably have the direct link, simplify the message:

```python
msg_lines = [
    f"CT {chamber_name} amendment update",
    f"Date Rec.: {row.get('date_text') or 'Unknown'}",
    f"LCO {row['lco']}",
    f"Bill: {row['bill_label']}",
]
if row.get("sched_letter"):
    msg_lines.append(f"Sched. Ltr.: {row['sched_letter']}")

if amendment_link:
    msg_lines.append(f"Amendment: {amendment_link}")

# Always include bill status as secondary reference
msg_lines.append(f"Bill status: {status_url}")
```

#### Estimated Changes

- Modify `find_amendment_pdf_on_status()` (~20 lines)
- Update message formatting in `process_chamber()` (~5 lines)
- No new dependencies

---

## Feature 2: LLM Summarization

**Goal:** When a new amendment is detected, fetch the amendment text and generate a 2-3 sentence plain-language summary to include in the Telegram notification.

### Implementation Plan

#### Step 1: Add PDF text extraction

New dependency: `pdfplumber` (lightweight, pure Python, good with tabular/legal PDFs).

```
# requirements.txt addition
pdfplumber>=0.11,<1
```

New utility function:

```python
import pdfplumber
import tempfile

def extract_text_from_pdf_url(pdf_url: str, max_pages: int = 20) -> str | None:
    """Download a PDF and extract its text content."""
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
```

#### Step 2: Add LLM summarization

New dependency: `anthropic` SDK (or use `requests` directly against the API for fewer deps).

**Option A: Anthropic SDK (recommended)**

```
# requirements.txt addition
anthropic>=0.42,<1
```

```python
import anthropic

def summarize_amendment(amendment_text: str, bill_label: str) -> str | None:
    """Use Claude to generate a plain-language summary of the amendment."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    client = anthropic.Anthropic(api_key=api_key)

    prompt = (
        f"You are summarizing a Connecticut legislative amendment for bill {bill_label}. "
        f"Provide a 2-3 sentence plain-language summary. Focus on: what the amendment "
        f"changes, who it affects, and any fiscal impact. Be concise and factual.\n\n"
        f"Amendment text:\n{amendment_text[:12000]}"  # Trim to stay within token budget
    )

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",  # Fast and cheap for summarization
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()
```

Using Haiku keeps cost per summary to fractions of a cent and responds in ~1 second.

**Option B: Requests-only (no SDK dependency)**

```python
def summarize_amendment(amendment_text: str, bill_label: str) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": (
                f"You are summarizing a Connecticut legislative amendment for bill {bill_label}. "
                f"Provide a 2-3 sentence plain-language summary. Focus on: what the amendment "
                f"changes, who it affects, and any fiscal impact. Be concise and factual.\n\n"
                f"Amendment text:\n{amendment_text[:12000]}"
            )}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()
```

#### Step 3: Integrate into the notification flow

In `process_chamber()`, after finding the amendment link:

```python
summary = None
if pdf_url and os.environ.get("CT_ENABLE_SUMMARY", "0") == "1":
    try:
        amendment_text = extract_text_from_pdf_url(pdf_url)
        if amendment_text:
            summary = summarize_amendment(amendment_text, row["bill_label"])
    except Exception as e:
        if DEBUG:
            print(f"[debug] summarization failed: {e}")

# Add to message
if summary:
    msg_lines.append("")
    msg_lines.append(f"Summary: {summary}")
```

#### Step 4: Configuration

New environment variables:

```dotenv
# .env additions
CT_ENABLE_SUMMARY=1          # Toggle summarization on/off
ANTHROPIC_API_KEY=sk-ant-... # Claude API key
```

Add to GitHub Actions secrets: `ANTHROPIC_API_KEY`

#### Step 5: Handle non-PDF amendments

Some amendments may link to HTML pages instead of PDFs. Add a fallback:

```python
def extract_amendment_text(url: str) -> str | None:
    """Extract text from an amendment URL, handling both PDF and HTML."""
    resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "").lower()

    if "pdf" in content_type or url.lower().endswith(".pdf"):
        return extract_text_from_pdf_url(url)
    else:
        soup = BeautifulSoup(resp.text, "lxml")
        # Remove script/style elements
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        return soup.get_text("\n", strip=True)
```

#### Estimated Changes

- New function `extract_text_from_pdf_url()` (~20 lines)
- New function `summarize_amendment()` (~25 lines)
- Modify notification block in `process_chamber()` (~10 lines)
- Update `.env.example` with new variables
- Add `pdfplumber` to requirements.txt (and optionally `anthropic`)

#### Cost Estimate

- Haiku: ~$0.001 per amendment summary (typical amendment is 1-3 pages)
- Most 5-minute runs find zero new amendments, so daily cost is near-zero
- Even during peak filing (dozens of amendments/day): well under $0.10/day

---

## Feature 3: Bill / Subject Matter Filtering

**Goal:** Only send notifications for amendments that match bills or topics you care about, reducing noise during busy filing periods.

### Implementation Plan

This feature has three layers, each building on the previous one. They can be implemented incrementally.

#### Layer A: Bill Number Watchlist (simplest, implement first)

**Concept:** Maintain a list of bill numbers you care about. Only notify if the amendment is for one of those bills. Optionally invert to "notify on everything except these."

**Configuration:**

```json
// config.json
{
  "filter_mode": "all",
  "watched_bills": ["SB00298", "HB05032", "HB06450"],
  "ignored_bills": []
}
```

Where `filter_mode` is one of:
- `"all"` — notify on every amendment (current behavior, default)
- `"watchlist"` — only notify on amendments to bills in `watched_bills`
- `"blocklist"` — notify on everything except bills in `ignored_bills`

**Implementation:**

```python
CONFIG_PATH = os.environ.get(
    "CT_AMEND_CONFIG_PATH",
    os.path.join(os.path.dirname(__file__), "config.json"),
)

def load_config():
    defaults = {"filter_mode": "all", "watched_bills": [], "ignored_bills": []}
    if not os.path.exists(CONFIG_PATH):
        return defaults
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {**defaults, **data}
    except Exception:
        return defaults

def should_notify(bill_label: str, config: dict) -> bool:
    mode = config.get("filter_mode", "all")
    bill_upper = bill_label.upper().strip()

    if mode == "watchlist":
        return bill_upper in {b.upper().strip() for b in config.get("watched_bills", [])}
    elif mode == "blocklist":
        return bill_upper not in {b.upper().strip() for b in config.get("ignored_bills", [])}
    else:
        return True  # "all" mode
```

Then in `process_chamber()`, wrap the notification block:

```python
config = load_config()

for row in reversed(new_rows):
    if not should_notify(row["bill_label"], config):
        if DEBUG:
            print(f"[debug] skipping {row['bill_label']} LCO {row['lco']} (filtered)")
        continue
    # ... existing notification logic ...
```

**Important:** State tracking (`last_seen` LCO) must still advance past filtered amendments. The filter only suppresses notifications — it should not affect which LCO is stored as `last_seen`. This is already the case because `newest_lco` is set from `rows[0]` before the notification loop.

#### Layer B: Committee / Subject Filter (medium complexity)

**Concept:** The bill status page contains committee assignments and subject categorization. Scrape those fields and match against a list of topics.

**Investigation needed:** Check the bill status page HTML to find where committee and subject info appears. It likely looks something like:

```html
<b>Referred To:</b> Joint Committee on Education
<b>Subjects:</b> Education, Children, Taxation
```

**Configuration addition:**

```json
// config.json additions
{
  "watched_subjects": ["education", "housing", "taxation"],
  "watched_committees": ["education", "appropriations"]
}
```

**Implementation:**

```python
def fetch_bill_metadata(status_url: str) -> dict:
    """Scrape committee and subject info from the bill status page."""
    r = requests.get(status_url, timeout=25, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    text = soup.get_text(" ", strip=True).lower()

    # These selectors will need to be refined based on actual page structure
    metadata = {"committees": [], "subjects": []}

    # Look for patterns like "Referred To: Joint Committee on Education"
    ref_match = re.search(r"referred to[:\s]+(.+?)(?:\n|$)", text)
    if ref_match:
        metadata["committees"] = [c.strip() for c in ref_match.group(1).split(",")]

    subj_match = re.search(r"subjects?[:\s]+(.+?)(?:\n|$)", text)
    if subj_match:
        metadata["subjects"] = [s.strip() for s in subj_match.group(1).split(",")]

    return metadata

def matches_subject_filter(metadata: dict, config: dict) -> bool:
    watched_subjects = {s.lower() for s in config.get("watched_subjects", [])}
    watched_committees = {c.lower() for c in config.get("watched_committees", [])}

    if not watched_subjects and not watched_committees:
        return True  # No subject filter configured

    for subj in metadata.get("subjects", []):
        if any(ws in subj.lower() for ws in watched_subjects):
            return True

    for comm in metadata.get("committees", []):
        if any(wc in comm.lower() for wc in watched_committees):
            return True

    return False
```

Update `should_notify()` to also check subject/committee:

```python
def should_notify(bill_label: str, status_url: str, config: dict) -> bool:
    # First check bill-level filter
    if not passes_bill_filter(bill_label, config):
        return False

    # Then check subject/committee filter (requires a network request)
    watched_subjects = config.get("watched_subjects", [])
    watched_committees = config.get("watched_committees", [])

    if not watched_subjects and not watched_committees:
        return True

    try:
        metadata = fetch_bill_metadata(status_url)
        return matches_subject_filter(metadata, config)
    except Exception:
        return True  # On error, don't suppress — better to over-notify
```

**Caching consideration:** Multiple amendments can be for the same bill. Cache `fetch_bill_metadata()` results by bill number to avoid redundant requests:

```python
_bill_metadata_cache = {}

def fetch_bill_metadata_cached(status_url: str, bill_label: str) -> dict:
    if bill_label in _bill_metadata_cache:
        return _bill_metadata_cache[bill_label]
    metadata = fetch_bill_metadata(status_url)
    _bill_metadata_cache[bill_label] = metadata
    return metadata
```

#### Layer C: LLM Relevance Scoring (requires Feature 2)

**Concept:** If summarization is enabled (Feature 2), add a second LLM pass that scores relevance against your configured interests.

**Configuration addition:**

```json
// config.json additions
{
  "interests": [
    "K-12 education funding",
    "affordable housing policy",
    "state employee pensions",
    "environmental regulation"
  ],
  "relevance_threshold": 4
}
```

**Implementation:**

```python
def score_relevance(summary: str, interests: list[str]) -> int | None:
    """Ask the LLM to score 1-10 how relevant this amendment is to user interests."""
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
```

In the notification flow:

```python
# After summarization
if summary and config.get("interests"):
    threshold = config.get("relevance_threshold", 4)
    score = score_relevance(summary, config["interests"])
    if score is not None and score < threshold:
        if DEBUG:
            print(f"[debug] skipping LCO {row['lco']} — relevance score {score} < {threshold}")
        continue
    if score is not None:
        msg_lines.append(f"Relevance: {score}/10")
```

#### Full config.json Example

```json
{
  "filter_mode": "all",
  "watched_bills": ["SB00298", "HB05032"],
  "ignored_bills": [],
  "watched_subjects": ["education", "housing"],
  "watched_committees": ["education", "appropriations"],
  "interests": [
    "K-12 education funding and teacher pay",
    "affordable housing and zoning reform",
    "state budget and appropriations"
  ],
  "relevance_threshold": 4
}
```

#### Estimated Changes

- **Layer A:** New `config.json`, `load_config()`, `should_notify()` (~40 lines), modify notification loop (~5 lines)
- **Layer B:** New `fetch_bill_metadata()`, `matches_subject_filter()` (~50 lines), caching (~10 lines)
- **Layer C:** New `score_relevance()` (~25 lines), modify notification loop (~10 lines), requires Feature 2

No new dependencies for Layers A and B. Layer C reuses the `anthropic` SDK from Feature 2.

---

## Implementation Order

```
Feature 1 (Direct Links)  ──> can be done standalone
Feature 3A (Bill Filter)  ──> can be done standalone
Feature 3B (Subject Filter) ──> can be done standalone
Feature 2 (Summarization) ──> requires ANTHROPIC_API_KEY
Feature 3C (LLM Relevance) ──> requires Feature 2
```

Recommended sequence: **3A -> 1 -> 2 -> 3B -> 3C**

Bill filtering (3A) gives immediate value with the least code. Direct links (1) requires some investigation of the CGA page structure first. Summarization (2) and LLM relevance (3C) are nice-to-haves that compound in value once both are live.
