# Feature Ideas

Planned enhancements for CT Amend Watch. All features have been implemented.

---

## Feature 1: Direct Amendment Links -- IMPLEMENTED

**Goal:** Link directly to the specific amendment PDF on the CGA site by its LCO number, rather than guessing or falling back to the bill status page.

### What Was Done

Investigation of the CGA bill status page revealed that amendment links are in static HTML (no JS rendering needed) across two sections:

- **Called Amendments:** link text like `House Schedule D LCO# 2413 (R)`, href like `/2026/amd/S/pdf/2026SB-00298-R00HD-AMD.pdf`
- **Uncalled Amendments:** link text like `House LCO Amendment #2413 (R)`, href like `/2026/lcoamd/pdf/2026LCO02413-R00-AMD.pdf`

`find_amendment_pdf_on_status()` was rewritten to:

- Only consider links with `/amd/` or `/lcoamd/` in the href, filtering out fiscal notes, votes, and other unrelated links
- Extract the LCO number from each link's text using the regex `LCO[#\s]*(\d+)` and compare as integers to handle leading-zero mismatches
- Return the first exact match with no greedy fallback

The notification message now always includes the bill status URL as a secondary reference, and shows the direct amendment link when found.

---

## Feature 2: LLM Summarization -- IMPLEMENTED

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

#### Layer A: Bill Number Watchlist -- IMPLEMENTED

Bill filtering is configured via `config.json`:

```json
{
  "filter_mode": "watchlist",
  "watched_bills": ["SB00298", "HB05032", "HB06450"],
  "ignored_bills": []
}
```

Three filter modes are supported:

- `"all"` — notify on every amendment (default)
- `"watchlist"` — only notify on amendments to bills in `watched_bills`
- `"blocklist"` — notify on everything except bills in `ignored_bills`

Key design decisions:

- `load_config()` reads `config.json` with safe fallback to defaults if the file is missing or malformed
- `should_notify_bill()` handles case-insensitive comparison
- The filter runs inside the notification loop *after* state has advanced, so filtered amendments are still marked as seen and won't re-trigger on the next run
- Config path is overridable via `CT_AMEND_CONFIG_PATH` env var

#### Layer B: Committee / Subject Filter -- IMPLEMENTED

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

#### Layer C: LLM Relevance Scoring -- IMPLEMENTED

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

## Feature 4: Public Launch Package -- IMPLEMENTED

**Goal:** Once all features are coded and tested, share the tool publicly so fellow CT lobbyists can set it up themselves. The audience is semi-technical — comfortable following step-by-step instructions but unlikely to know git CLI or Python.

### Checklist

#### Beginner-Friendly README Rewrite

The current README is developer-oriented. Rewrite it for the target audience:

- **Plain-language intro** at the top: what this does, why it's useful, what you'll get (Telegram notifications with direct amendment links and AI summaries when new amendments drop)
- **No jargon** in the setup guide — no mentions of Playwright, headless browsers, Chromium, cron, pip, etc. Just "follow these steps"
- **GitHub Actions as the only path** — don't mention VPS/cron in the main guide (keep VPS_CRON.md as a separate doc for advanced users)
- **Step-by-step with exact clicks:**
  1. Create a Telegram bot (message @BotFather, walk through every prompt)
  2. Get your chat ID (message @userinfobot or similar)
  3. Fork the repo (point-and-click on GitHub, no terminal)
  4. Add secrets in GitHub Settings > Secrets > Actions (name each one explicitly)
  5. Edit `config.json` in the GitHub web editor to pick your bills
  6. Enable the workflow (it may be disabled on fork by default — show where to click)
  7. Verify it's working (check Actions tab, wait for first Telegram message)
- **Troubleshooting section:** common issues like "I forked but nothing is happening" (workflow disabled on fork), "I'm not getting messages" (wrong chat ID), etc.
- **Move technical reference** (env vars table, state tracking, project structure, dependencies) into a collapsible `<details>` section or a separate TECHNICAL.md

#### Anthropic API Key Instructions (if LLM features are enabled)

- How to sign up at console.anthropic.com
- How to create an API key
- How to add it as a GitHub secret (`ANTHROPIC_API_KEY`)
- Note on cost expectations (fractions of a cent per summary)

#### Social Media Launch

**REMINDER:** Before posting, ask Claude to help draft optimized posts for each platform.

Platforms to consider:

- **LinkedIn** — primary audience (CT lobbyists, government affairs professionals). Post should be professional, explain the problem it solves (manually refreshing CGA pages during session), and link to the repo. Include a screenshot of a Telegram notification.
- **X/Twitter** — shorter version, good for reaching CT politics / civic tech community
- **CT-specific Slack/Discord communities** — if any exist for lobbyists or civic tech

Post should cover:

- The problem: during session, amendments drop fast and you need to catch them in real time
- The solution: automated monitoring with Telegram notifications, direct PDF links, bill filtering, AI summaries
- The ask: it's free, open source, takes 10 minutes to set up, no coding required
- A screenshot or short video of the Telegram notification in action

---

## Implementation Order

```
Feature 1  (Direct Links)    ──> DONE
Feature 3A (Bill Filter)     ──> DONE
Feature 2  (Summarization)   ──> DONE
Feature 3B (Subject Filter)  ──> DONE
Feature 3C (LLM Relevance)   ──> DONE
Feature 4  (Public Launch)   ──> DONE
```

Recommended next: **2 -> 3B -> 3C -> 4**

Summarization (2) adds the most user-facing value of what remains. Subject filtering (3B) is a straightforward scrape of existing page data. LLM relevance (3C) compounds in value once summarization is live. Public launch (4) is the final step once everything is working and tested.
