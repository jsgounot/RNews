"""
Metadata extraction service for scientific papers and general URLs.

Priority order for scientific papers:
1. DOI via CrossRef API (most reliable)
2. arXiv API for arxiv.org URLs
3. PubMed API for pubmed/pmc URLs
4. Try to find DOI embedded in page HTML → CrossRef
5. OpenGraph / <meta> tags fallback

The service optionally integrates with a Zotero Translation Server
(https://github.com/zotero/translation-server) if running on localhost:1969.
"""

import re
import httpx
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from typing import Optional


CROSSREF_URL = "https://api.crossref.org/works/{doi}"
ARXIV_API = "https://export.arxiv.org/api/query?id_list={arxiv_id}"
PUBMED_SUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
ZOTERO_SERVER = "http://127.0.0.1:1969/web"

HEADERS = {
    "User-Agent": (
        "RNews/1.0 (mailto:admin@rnews.local) "
        "Python/httpx scientific-aggregator"
    )
}


def extract_doi(text: str) -> Optional[str]:
    """Extract DOI from a string or URL."""
    patterns = [
        r"10\.\d{4,9}/[^\s\"'<>]+",
        r"doi\.org/(10\.\d{4,9}/[^\s\"'<>]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            doi = m.group(1) if m.lastindex and m.lastindex >= 1 and "doi.org" in pat else m.group(0)
            return doi.rstrip(".,;)")
    return None


def extract_arxiv_id(url: str) -> Optional[str]:
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]+(?:v\d+)?)", url)
    return m.group(1) if m else None


def extract_pubmed_id(url: str) -> Optional[str]:
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url)
    if not m:
        m = re.search(r"ncbi\.nlm\.nih\.gov/pubmed/(\d+)", url)
    return m.group(1) if m else None


async def fetch_crossref(doi: str) -> Optional[dict]:
    url = CROSSREF_URL.format(doi=doi)
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            data = r.json().get("message", {})
    except Exception:
        return None

    title_list = data.get("title", [])
    title = title_list[0] if title_list else None

    authors = data.get("author", [])
    first_author = _format_author(authors[0]) if authors else None
    last_author = _format_author(authors[-1]) if len(authors) > 1 else None

    container = data.get("container-title", [])
    journal = container[0] if container else data.get("publisher")

    pub_date = None
    for key in ("published-print", "published-online", "issued"):
        dp = data.get(key, {}).get("date-parts", [[]])
        if dp and dp[0]:
            parts = dp[0]
            pub_date = "-".join(str(p) for p in parts)
            break

    return {
        "title": title,
        "first_author": first_author,
        "last_author": last_author,
        "journal": journal,
        "publication_date": pub_date,
        "doi": doi,
        "item_type": "paper",
    }


def _format_author(author: dict) -> str:
    given = author.get("given", "")
    family = author.get("family", "")
    if given and family:
        return f"{given} {family}"
    return family or given or ""


async def fetch_arxiv(arxiv_id: str) -> Optional[dict]:
    url = ARXIV_API.format(arxiv_id=arxiv_id)
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "xml")
    except Exception:
        return None

    entry = soup.find("entry")
    if not entry:
        return None

    title_tag = entry.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    authors = entry.find_all("author")
    first_author = authors[0].find("name").get_text(strip=True) if authors else None
    last_author = authors[-1].find("name").get_text(strip=True) if len(authors) > 1 else None

    published = entry.find("published")
    pub_date = published.get_text(strip=True)[:10] if published else None

    doi_tag = entry.find("arxiv:doi")
    doi = doi_tag.get_text(strip=True) if doi_tag else f"arXiv:{arxiv_id}"

    return {
        "title": title,
        "first_author": first_author,
        "last_author": last_author,
        "journal": "arXiv",
        "publication_date": pub_date,
        "doi": doi,
        "item_type": "paper",
    }


async def fetch_pubmed(pmid: str) -> Optional[dict]:
    params = {
        "db": "pubmed",
        "id": pmid,
        "retmode": "json",
    }
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
            r = await client.get(PUBMED_SUMMARY, params=params)
            if r.status_code != 200:
                return None
            data = r.json()
    except Exception:
        return None

    result = data.get("result", {}).get(pmid, {})
    if not result:
        return None

    title = result.get("title", "").rstrip(".")
    authors = result.get("authors", [])
    first_author = authors[0].get("name") if authors else None
    last_author = authors[-1].get("name") if len(authors) > 1 else None
    journal = result.get("fulljournalname") or result.get("source")
    pub_date = result.get("pubdate", "")[:10]
    articleids = result.get("articleids", [])
    doi = next((a["value"] for a in articleids if a.get("idtype") == "doi"), None)

    return {
        "title": title,
        "first_author": first_author,
        "last_author": last_author,
        "journal": journal,
        "publication_date": pub_date,
        "doi": doi or pmid,
        "item_type": "paper",
    }


async def fetch_page_metadata(url: str) -> dict:
    """Scrape a page for DOI or OpenGraph metadata."""
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=15, follow_redirects=True
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return {}
            html = r.text
    except Exception:
        return {}

    # Try to find DOI in page
    doi = extract_doi(html)
    if doi:
        result = await fetch_crossref(doi)
        if result:
            return result

    # Fallback: OpenGraph / meta tags
    soup = BeautifulSoup(html, "lxml")
    meta = {}

    title_tag = soup.find("meta", property="og:title") or soup.find("title")
    if title_tag:
        meta["title"] = title_tag.get("content") or title_tag.get_text(strip=True)

    description_tag = soup.find("meta", property="og:description")
    if description_tag:
        meta["description"] = description_tag.get("content", "")

    return meta


async def try_zotero_server(url: str) -> Optional[dict]:
    """Try Zotero translation server if available."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                ZOTERO_SERVER,
                json={"url": url},
                headers={"Content-Type": "application/json"},
            )
            if r.status_code != 200:
                return None
            items = r.json()
            if not items:
                return None
            item = items[0]
    except Exception:
        return None

    creators = item.get("author", []) or item.get("creators", [])

    def fmt(c):
        return f"{c.get('firstName', '')} {c.get('lastName', '')}".strip() or c.get("name", "")

    first_author = fmt(creators[0]) if creators else None
    last_author = fmt(creators[-1]) if len(creators) > 1 else None

    pub_date = item.get("date", "") or item.get("issued", {}).get("date-parts", [[""]])[0]
    if isinstance(pub_date, list):
        pub_date = "-".join(str(p) for p in pub_date)

    return {
        "title": item.get("title"),
        "first_author": first_author,
        "last_author": last_author,
        "journal": item.get("container-title") or item.get("publicationTitle"),
        "publication_date": str(pub_date)[:10] if pub_date else None,
        "doi": item.get("DOI") or item.get("doi"),
        "item_type": "paper",
    }


def is_scientific_url(url: str) -> bool:
    """Heuristic: does this URL likely point to a scientific paper?"""
    patterns = [
        r"doi\.org/",
        r"arxiv\.org/",
        r"pubmed\.ncbi\.nlm\.nih\.gov/",
        r"ncbi\.nlm\.nih\.gov/pubmed",
        r"nature\.com/articles",
        r"science\.org/doi",
        r"cell\.com/",
        r"biorxiv\.org/",
        r"medrxiv\.org/",
        r"pnas\.org/",
        r"journals?\.",
        r"\.pdf$",
    ]
    return any(re.search(p, url, re.IGNORECASE) for p in patterns)


async def extract_metadata(url: str) -> dict:
    """
    Main entry point. Returns a dict with keys:
    title, first_author, last_author, journal, publication_date, doi, item_type
    """
    # 1. Try Zotero translation server (if running)
    zotero_result = await try_zotero_server(url)
    if zotero_result and zotero_result.get("title"):
        return zotero_result

    # 2. DOI in URL
    doi = extract_doi(url)
    if doi:
        result = await fetch_crossref(doi)
        if result:
            return result

    # 3. arXiv
    arxiv_id = extract_arxiv_id(url)
    if arxiv_id:
        result = await fetch_arxiv(arxiv_id)
        if result:
            return result

    # 4. PubMed
    pmid = extract_pubmed_id(url)
    if pmid:
        result = await fetch_pubmed(pmid)
        if result:
            return result

    # 5. Scrape page for DOI / OG tags
    result = await fetch_page_metadata(url)
    if result:
        return result

    return {}
