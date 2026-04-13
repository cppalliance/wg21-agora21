"""
Fetcher for WG21 papers (open-std.org monthly mailing tables).

Source: provenance copy of ``agora21/fetcher.py`` at repo root (same script
intended for eventual ``boost-data-collector``). Monthly mailing detection uses
``#mailingYYYY-MM`` anchors and per-mailing ``<table>`` rows on the year index
page — not an Apache directory listing.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Optional

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

logger = logging.getLogger(__name__)

BASE_URL = "https://www.open-std.org/jtc1/sc22/wg21/docs/papers"

DEFAULT_USER_AGENT = "agora21-pipeline/0.1 (+https://wg21.org)"

_MAILING_ANCHOR_RE = re.compile(r"^mailing\d{4}-\d{2}$")
# Paper link in first column: e.g. p1234r0.pdf, n4920.html, sd-9.md
_PAPER_LINK_PATTERN = re.compile(r"((?:p\d+r\d+|n\d+|sd-\d+))\.([a-z]+)", re.IGNORECASE)


def extract_paper_metadata_from_table_row(
    cells: list[Tag],
    page_url: str,
) -> Optional[dict]:
    """
    Extract paper metadata from a WG21 mailing table row (td/th cells).

    Current year pages (e.g. 2026) use eight columns::

        WG21 Number | Title | Author | Document Date | Mailing Date |
        Previous Version | Subgroup | Disposition

    So **subgroup is index 6**, not 4. Index 4 is *mailing date* (string as shown on the site).

    Older pages used a shorter row (five data columns); then subgroup was at index 4.
    If ``len(cells) >= 8`` we use the 8-column layout; otherwise we keep the legacy mapping.
    """
    if not cells:
        return None

    first_cell = cells[0]
    base = urllib.parse.urlparse(BASE_URL)

    title = ""
    if len(cells) > 1:
        title = cells[1].text.strip()

    authors: list[str] = []
    if len(cells) > 2:
        authors_raw = cells[2].text.strip()
        if authors_raw:
            authors = [
                a.strip() for a in re.split(r",| and ", authors_raw) if a.strip()
            ]

    document_date = None
    if len(cells) > 3:
        date_str = cells[3].text.strip()
        if date_str:
            document_date = date_str

    # 8+ columns: mailing date [4], previous version [5], subgroup [6], disposition [7]
    subgroup = ""
    if len(cells) >= 8:
        subgroup = cells[6].text.strip()
    elif len(cells) > 4:
        subgroup = cells[4].text.strip()

    for link in first_cell.find_all("a", href=True):
        href = link.get("href", "")
        match = _PAPER_LINK_PATTERN.search(href)
        if not match:
            continue

        paper_url = urllib.parse.urljoin(page_url, href)
        parsed = urllib.parse.urlparse(paper_url)
        if parsed.scheme not in ("https", "http") or parsed.netloc != base.netloc:
            logger.warning("Skipping off-origin paper URL %s", paper_url)
            continue

        paper_id = match.group(1).lower()
        file_ext = match.group(2).lower()
        filename = match.group(0).lower()

        return {
            "url": paper_url,
            "filename": filename,
            "type": file_ext,
            "paper_id": paper_id,
            "title": title,
            "authors": authors,
            "document_date": document_date,
            "subgroup": subgroup,
        }

    return None


def _find_table_in_section(anchor) -> Optional[Tag]:
    """
    Find the first <table> that belongs to the current mailing section.
    Stops at the next mailing anchor (id/name matching mailingYYYY-MM) so we
    do not attribute another mailing's table to this section.
    """
    if not anchor:
        return None
    anchor_id = anchor.get("id") or anchor.get("name") or ""
    if not _MAILING_ANCHOR_RE.match(anchor_id):
        return None
    for elem in anchor.next_elements:
        if not hasattr(elem, "name"):  # NavigableString, etc.
            continue
        if elem is anchor:
            continue
        if elem.name == "table":
            return elem
        if not hasattr(elem, "get"):  # e.g. NavigableString
            continue
        next_id = elem.get("id") or elem.get("name") or ""
        if next_id and _MAILING_ANCHOR_RE.match(next_id) and next_id != anchor_id:
            return None  # next section start; no table in this section
    return None


def _dedupe_by_filename(papers: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for p in papers:
        if p["filename"] not in seen:
            seen.add(p["filename"])
            unique.append(p)
    return unique


def parse_papers_for_mailing_from_html(
    html: str,
    mailing_date: str,
    page_url: str,
) -> list[dict]:
    """
    Parse papers for ``mailing_date`` (e.g. ``2026-02``) from a year index HTML
    document. ``page_url`` is the fetch URL (used for resolving relative links).
    """
    soup = BeautifulSoup(html, "html.parser")
    anchor_id = f"mailing{mailing_date}"
    anchor = soup.find(id=anchor_id) or soup.find(attrs={"name": anchor_id})
    if not anchor:
        logger.warning("Anchor %s not found on %s", anchor_id, page_url)
        return []

    table = _find_table_in_section(anchor)
    if not table:
        logger.warning("No table found after anchor %s", anchor_id)
        return []

    paper_rows: list[dict] = []
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells or any(cell.get("colspan") for cell in cells):
            continue

        paper = extract_paper_metadata_from_table_row(cells, page_url)
        if paper:
            paper_rows.append(paper)

    return _dedupe_by_filename(paper_rows)


def fetch_all_mailings(*, timeout: float = 60.0) -> list[dict]:
    """
    Fetch the main index and extract all mailings.
    Returns a list of dicts:
      - mailing_date (e.g. '2025-02')
      - title (e.g. '2025-02 pre-Hagenberg mailing')
      - year (e.g. '2025')
    List is in the order found on the page (usually newest first).
    """
    logger.info("Fetching WG21 main index: %s/", BASE_URL)
    try:
        response = requests.get(
            f"{BASE_URL}/",
            timeout=timeout,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Failed to fetch WG21 index.")
        return []

    pattern = re.compile(r"^(\d{4})/#mailing(\d{4}-\d{2})$")
    soup = BeautifulSoup(response.text, "html.parser")
    mailings: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        match = pattern.search(href)
        if match:
            year, mailing_date = match.groups()
            title = a.text.strip()
            mailings.append(
                {"mailing_date": mailing_date, "title": title, "year": year}
            )

    return mailings


def fetch_papers_for_mailing(
    year: str,
    mailing_date: str,
    *,
    timeout: float = 60.0,
) -> list[dict]:
    """
    Fetch the papers for a specific mailing from the year page.
    ``mailing_date`` is e.g. ``2026-02`` (same as pipeline ``mailing_id``).
    """
    url = f"{BASE_URL}/{year}/"
    logger.info("Fetching mailing %s from %s", mailing_date, url)
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Failed to fetch year page %s.", year)
        return []

    return parse_papers_for_mailing_from_html(response.text, mailing_date, url)
