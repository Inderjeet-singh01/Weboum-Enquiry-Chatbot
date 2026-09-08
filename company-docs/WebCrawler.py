from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup


# ============================================================
# WEBOUM KNOWLEDGE CRAWLER V3
# ============================================================
#
# Purpose:
#   Build a clean RAG-ready knowledge JSON from:
#     1) NEW: https://weboum.com/
#     2) OLD: http://192.168.1.2:3001/
#
# Rules:
#   - NEW is authoritative when the same topic/fact overlaps.
#   - Unique OLD information is preserved.
#   - Exact and near-duplicate content is reduced.
#   - Common headers/footers/CTAs are removed.
#   - Technology / service / solution / industry categories
#     are determined mainly from URL, not page text.
#   - JS-rendered pages can be handled with Playwright.
#   - Sitemap + internal-link + section-seed discovery are used.
#
# Output:
#   company-docs/weboum_knowledge.json
#
# ============================================================


# ============================================================
# CONFIG
# ============================================================

NEW_SOURCE = "https://weboum.com/"
OLD_SOURCE = "http://192.168.1.2:3001/"

SOURCES = [
    ("new", NEW_SOURCE),
    ("old", OLD_SOURCE),
]

OUTPUT_DIR = "company-docs"
OUTPUT_FILE = f"{OUTPUT_DIR}/weboum_knowledge.json"

REQUEST_TIMEOUT = 20
REQUEST_DELAY = 0.12

MAX_PAGES_PER_SOURCE = 500
MAX_DEPTH = 8

# Pages shorter than this may be client-rendered.
MIN_HTTP_TEXT_FOR_BROWSER_FALLBACK = 280

# Browser fallback is strongly recommended for the old Node.js site.
USE_PLAYWRIGHT = True

# Set True only if you want every page rendered in Chromium.
# Usually False is much faster: browser is used only when needed.
FORCE_PLAYWRIGHT = False

# Crawl common section roots even when they are not linked directly.
SECTION_SEEDS = [
    "/",
    "/about",
    "/about-us",
    "/contact",
    "/services/",
    "/solutions/",
    "/technology/",
    "/technologies/",
    "/industries/",
    "/portfolio",
    "/projects",
    "/resources",
    "/faq",
    "/hire-developer",
    "/hire-developers",
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36 "
    "WeboumKnowledgeCrawler/3.0"
)


# ============================================================
# EXTRACTION RULES
# ============================================================

# These are usually navigation / UI / legal / technical files,
# not company knowledge.
EXCLUDED_PATH_PARTS = {
    "/login",
    "/logout",
    "/register",
    "/signup",
    "/cart",
    "/checkout",
    "/account",
    "/wp-admin",
    "/wp-login",
    "/xmlrpc",
    "/privacy",
    "/privacy-policy",
    "/terms",
    "/terms-and-conditions",
    "/cookie",
    "/cookie-policy",
    "/refund-policy",
    "/careers",
    "/career",
    "/job_post",
    "/jobs",
    "/author/",
    "/tag/",
    "/feed",
}


EXCLUDED_EXACT_PATHS = {
    "/all-logos",
}


EXCLUDED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".bmp",
    ".pdf",
    ".zip",
    ".rar",
    ".7z",
    ".mp4",
    ".mp3",
    ".wav",
    ".avi",
    ".mov",
    ".css",
    ".js",
    ".xml",
    ".json",
}


REMOVE_SELECTORS = [
    "script",
    "style",
    "noscript",
    "svg",
    "canvas",
    "iframe",
    "template",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "[role='navigation']",
    "[aria-label*='navigation']",
    "[aria-label*='menu']",
    ".cookie",
    ".cookies",
    ".cookie-banner",
    ".cookie-consent",
    ".popup",
    ".modal",
    ".newsletter",
    ".subscribe",
    ".social-share",
    ".breadcrumb",
    ".breadcrumbs",
    ".share-buttons",
]


# Obvious repeated footer / CTA phrases seen on the site.
# These patterns are intentionally narrow so legitimate service
# information is not deleted.
BOILERPLATE_PATTERNS = [
    r"Custom Software Development\s*&?\s*Enterprises Solutions",
    r"30-Days FREE Support",
    r"100% Satisfaction Guaranteed",
    r"Since 2012,\s*we deliver 100% project success.*",
    r"24/7 Support:\s*Email,\s*Call,\s*or Skype",
    r"Dedicated Team",
    r"Exceptional Consultation",
    r"Trusted by Global Companies",
    r"Have an Idea for a Software Product\?.*",
    r"Lets build your SaaS platform, AI system or enterprise software\.?",
]


# ============================================================
# OPTIONAL FTfy
# ============================================================

try:
    from ftfy import fix_text as _ftfy_fix_text
except Exception:
    _ftfy_fix_text = None


def repair_mojibake(text: str) -> str:
    """
    Repair UTF-8 text that has been incorrectly decoded as Latin-1 /
    Windows-1252, e.g.:
        Letâs -> Let's
        todayâs -> today's
        ð¤ -> 🤖
    """

    if not text:
        return ""

    if _ftfy_fix_text:
        try:
            text = _ftfy_fix_text(text)
        except Exception:
            pass

    # Extra safe replacements for common leftovers.
    replacements = {
        "â": "’",
        "â": "‘",
        "â": "“",
        "â": "”",
        "â": "—",
        "â": "–",
        "â¦": "…",
        "â¢": "•",
        "Â": "",
        "ï¸": "️",
    }

    for bad, good in replacements.items():
        text = text.replace(bad, good)

    # Second pass when obvious mojibake is still present.
    suspicious = (
        text.count("â") +
        text.count("Â") +
        text.count("ð") +
        text.count("ï¸")
    )

    if suspicious >= 2:
        try:
            repaired = (
                text.encode("latin1", errors="ignore")
                .decode("utf-8", errors="ignore")
            )
            if repaired:
                text = repaired
        except Exception:
            pass

    return text


def clean_text(text: str) -> str:
    text = repair_mojibake(text)

    text = unicodedata.normalize("NFKC", text)

    text = text.replace("\xa0", " ")

    # Normalize common bullet variants.
    text = text.replace("•", "-")

    # Remove invisible control characters but keep newlines/tabs.
    text = "".join(
        char
        for char in text
        if char in ("\n", "\t", " ")
        or not unicodedata.category(char).startswith("C")
    )

    # Whitespace cleanup.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+\n", "\n\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def normalize_for_matching(text: str) -> str:
    """
    Aggressive normalization for duplicate detection only.
    It is NOT used as the final content.
    """
    text = repair_mojibake(text).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ============================================================
# URL HELPERS
# ============================================================

def normalize_url(url: str) -> str:
    url, _ = urldefrag(url)

    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()

    path = parsed.path or "/"

    if path != "/":
        path = "/" + path.strip("/") + "/"

    # Root should remain "/" exactly.
    if path == "///":
        path = "/"

    # Ignore query parameters for company-content crawling.
    return f"{scheme}://{host}{path}"


def host(url: str) -> str:
    return urlparse(url).netloc.lower()


def is_http_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def is_internal(url: str, base_url: str) -> bool:
    return host(url) == host(base_url)


def path_of(url: str) -> str:
    return (urlparse(url).path or "/").lower()


def should_skip_url(url: str) -> bool:
    path = path_of(url)

    if path in EXCLUDED_EXACT_PATHS:
        return True

    for excluded in EXCLUDED_PATH_PARTS:
        if path == excluded.rstrip("/") or path.startswith(
            excluded
        ):
            return True

    for extension in EXCLUDED_EXTENSIONS:
        if path.endswith(extension):
            return True

    return False


# ============================================================
# CATEGORY
# ============================================================

def classify_category(url: str) -> str:
    """
    Category is primarily determined from URL.

    This prevents footer words like "contact" from turning
    a service page into category=contact.
    """

    path = path_of(url).strip("/")

    if path == "":
        return "company"

    # Contact pages.
    if (
        path == "contact"
        or path.startswith("contact/")
        or path.startswith("about-us/contact")
        or path.startswith("request-a-quote")
    ):
        return "contact"

    # Company pages.
    if path == "about" or path == "about-us":
        return "company"

    # Special existing path that is actually a hosting service.
    if path.startswith("about-us/hosting-management"):
        return "service"

    # Excluded career pages.
    if (
        path.startswith("about-us/careers")
        or path.startswith("about-us/job_post")
    ):
        return "excluded"

    # Services.
    if path.startswith("services/"):
        return "service"

    # Solutions.
    if path.startswith("solutions/"):
        return "solution"

    # Technology.
    if path.startswith("technology/") or path.startswith(
        "technologies/"
    ):
        return "technology"

    # Industries.
    if path.startswith("industry/") or path.startswith(
        "industries/"
    ):
        return "industry"

    # Projects / portfolio.
    if path == "projects" or path.startswith("projects/"):
        return "project"

    if path == "portfolio" or path.startswith("portfolio/"):
        return "portfolio"

    # Resources.
    if path == "resources" or path.startswith("resources/"):
        return "resource"

    # FAQ.
    if path == "faq" or path.startswith("faq/"):
        return "faq"

    # Hiring.
    if (
        path == "hire-developer"
        or path == "hire-developers"
        or path.startswith("hire-developer/")
        or path.startswith("hire-developers/")
    ):
        return "service"

    # CRM/product pages.
    if path in {"crm", "ai-automation"}:
        return "solution"

    return "general"


# ============================================================
# CANONICAL TOPIC
# ============================================================

def slug_parts(url: str) -> list[str]:
    path = path_of(url).strip("/")

    parts = re.split(r"[/_-]+", path)

    return [
        part
        for part in parts
        if part
        not in {
            "services",
            "service",
            "solutions",
            "solution",
            "technology",
            "technologies",
            "industry",
            "industries",
            "about",
            "about-us",
        }
    ]


def canonical_topic(url: str) -> str:
    """
    Map equivalent URL names from NEW and OLD into the same
    topic without merging unrelated pages.

    Examples:
        web-developer -> web-development
        web-development -> web-development
        ai-develperment -> ai-development
        cloud-devops -> cloud-devops
        devops -> cloud-devops
    """

    path = path_of(url).strip("/")

    # --------------------------------------------------------
    # Company overview
    # --------------------------------------------------------

    if path in {"", "about", "about-us"}:
        return "company:overview"

    # --------------------------------------------------------
    # Contact
    # --------------------------------------------------------

    if (
        path == "contact"
        or path.startswith("about-us/contact")
        or path.startswith("request-a-quote")
    ):
        return "company:contact"

    # --------------------------------------------------------
    # Services / solutions with known synonyms
    # --------------------------------------------------------

    aliases = {
        # AI
        "services/ai-develperment": "ai-development",
        "services/ai-development": "ai-development",
        "services/ai-developer": "ai-development",

        "services/ai-automation": "ai-automation",
        "solutions/ai-automation": "ai-automation",
        "ai-automation": "ai-automation",

        # Web
        "services/web-developer": "web-development",
        "services/web-development": "web-development",

        # Mobile/application
        "services/mobile-app": "mobile-app-development",
        "services/application-developer": "application-development",

        # Data
        "services/data-analytics": "data-analytics",

        # Cybersecurity
        "services/cybersecurity": "cybersecurity",
        "solutions/cybersecurity": "cybersecurity",

        # Blockchain
        "services/blockchain-solutions": "blockchain",

        # Cloud/DevOps
        "services/cloud-devops": "cloud-devops",
        "solutions/devops": "cloud-devops",

        # Marketing
        "services/digital-marketing-3": "digital-marketing",
        "services/Digital-Marketing-Packages".lower(): "digital-marketing",

        # Design
        "services/web-designing": "web-design",
        "services/graphic-design": "graphic-design",

        # Content
        "services/content-writing": "content-writing",

        # Consulting
        "services/it-consulting": "it-consulting",

        # Hosting
        "about-us/hosting-management": "hosting-management",

        # Disaster recovery
        "solutions/backup-disaster-recovery": (
            "backup-disaster-recovery"
        ),

        # Product lifecycle
        "solutions/product-lifecycle-management": (
            "product-lifecycle-management"
        ),

        # Shopify
        "solutions/shopify-developer": "shopify-development",

        # SLA
        "solutions/sla-support-services": "sla-support",

        # CRM
        "crm": "crm",
    }

    if path in aliases:
        return aliases[path]

    # --------------------------------------------------------
    # Technology pages should remain separate topics.
    # --------------------------------------------------------

    if path.startswith("technology/"):
        slug = path.split("/", 1)[1]
        return f"technology:{slug}"

    if path.startswith("technologies/"):
        slug = path.split("/", 1)[1]
        return f"technology:{slug}"

    # --------------------------------------------------------
    # Industries remain separate.
    # --------------------------------------------------------

    if path.startswith("industries/"):
        slug = path.split("/", 1)[1]
        return f"industry:{slug}"

    if path.startswith("industry/"):
        slug = path.split("/", 1)[1]
        return f"industry:{slug}"

    # --------------------------------------------------------
    # Projects/resources/portfolio remain separate knowledge.
    # --------------------------------------------------------

    if path == "projects" or path.startswith("projects/"):
        return "projects"

    if path == "portfolio" or path.startswith("portfolio/"):
        return "portfolio"

    if path == "resources" or path.startswith("resources/"):
        return "resources"

    # --------------------------------------------------------
    # Hire developer
    # --------------------------------------------------------

    if path in {
        "hire-developer",
        "hire-developers",
    }:
        return "hire-developers"

    # --------------------------------------------------------
    # FAQ
    # --------------------------------------------------------

    if path == "faq" or path.startswith("faq/"):
        return "faq"

    # --------------------------------------------------------
    # Generic fallback:
    # Use the meaningful path itself.
    # --------------------------------------------------------

    parts = slug_parts(url)

    return (
        "general:" + "-".join(parts)
        if parts
        else "general:root"
    )


# ============================================================
# BLOCK MODEL
# ============================================================

@dataclass
class Page:
    url: str
    source_version: str
    title: str
    category: str
    topic: str
    content: str
    word_count: int
    discovered_depth: int
    http_status: int


# ============================================================
# TEXT / BLOCK FUNCTIONS
# ============================================================

def remove_boilerplate(text: str) -> str:

    for pattern in BOILERPLATE_PATTERNS:

        text = re.sub(
            pattern,
            "",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

    return clean_text(text)


def dedupe_lines_within_page(text: str) -> str:
    lines = text.splitlines()

    seen = set()
    result = []

    for line in lines:

        line = line.strip()

        if not line:
            if result and result[-1] != "":
                result.append("")
            continue

        normalized = normalize_for_matching(line)

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)
        result.append(line)

    return "\n".join(result).strip()


def split_blocks(content: str) -> list[str]:

    blocks = []

    current = []

    for line in content.splitlines():

        line = line.strip()

        if not line:

            if current:

                blocks.append(
                    "\n".join(current).strip()
                )

                current = []

            continue

        current.append(line)

    if current:
        blocks.append(
            "\n".join(current).strip()
        )

    return [
        block
        for block in blocks
        if block
    ]


def block_heading(block: str) -> Optional[str]:

    first_line = block.splitlines()[0].strip()

    if first_line.startswith("#"):

        heading = first_line.lstrip("#").strip()

        return normalize_for_matching(
            heading
        )

    return None


def similarity(a: str, b: str) -> float:

    a = normalize_for_matching(a)
    b = normalize_for_matching(b)

    if not a or not b:
        return 0.0

    return SequenceMatcher(
        None,
        a,
        b,
    ).ratio()


def token_overlap(a: str, b: str) -> float:

    a_tokens = set(
        normalize_for_matching(a).split()
    )

    b_tokens = set(
        normalize_for_matching(b).split()
    )

    if not a_tokens or not b_tokens:
        return 0.0

    intersection = len(
        a_tokens & b_tokens
    )

    union = len(
        a_tokens | b_tokens
    )

    return (
        intersection / union
        if union
        else 0.0
    )


# ============================================================
# HTML EXTRACTION
# ============================================================

def extract_title(soup: BeautifulSoup) -> str:

    h1 = soup.find("h1")

    if h1:
        value = clean_text(
            h1.get_text(" ", strip=True)
        )

        if value:
            return value

    if soup.title:

        value = clean_text(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

        if value:
            return value

    return "Untitled"


def remove_unwanted_elements(
    soup: BeautifulSoup
) -> None:

    for selector in REMOVE_SELECTORS:

        try:

            for element in soup.select(
                selector
            ):

                element.decompose()

        except Exception:
            # One malformed selector should not crash
            # the complete crawl.
            continue


def choose_content_container(
    soup: BeautifulSoup
):

    candidates = [
        soup.find("main"),
        soup.find("article"),
        soup.find(
            attrs={
                "role": "main"
            }
        ),
        soup.find(
            id=re.compile(
                r"^(main|content|page|primary)",
                re.I
            )
        ),
    ]

    for candidate in candidates:

        if candidate:
            text = clean_text(
                candidate.get_text(
                    "\n",
                    strip=True
                )
            )

            if len(text.split()) >= 40:
                return candidate

    return soup.body or soup


def extract_blocks(
    container
) -> list[str]:

    blocks = []

    elements = container.find_all(
        [
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "p",
            "li",
            "blockquote",
            "dt",
            "dd",
            "tr",
        ]
    )

    for element in elements:

        tag = element.name

        # Table rows.
        if tag == "tr":

            cells = [
                clean_text(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in element.find_all(
                    ["th", "td"]
                )
            ]

            cells = [
                cell
                for cell in cells
                if cell
            ]

            if not cells:
                continue

            text = " | ".join(cells)

            blocks.append(
                text
            )

            continue

        text = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if not text:
            continue

        # Avoid repeated paragraph text inside <li>.
        if tag == "p":
            parent = element.parent

            if parent and parent.name == "li":
                continue

        if tag.startswith("h"):

            level = int(tag[1])

            text = (
                f"{'#' * level} "
                f"{text}"
            )

        elif tag == "li":

            text = f"- {text}"

        elif tag == "blockquote":

            text = f"> {text}"

        blocks.append(text)

    return blocks


def extract_content_from_html(
    html: str
) -> tuple[str, str]:

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    title = extract_title(soup)

    remove_unwanted_elements(
        soup
    )

    container = choose_content_container(
        soup
    )

    blocks = extract_blocks(
        container
    )

    content = "\n\n".join(
        blocks
    )

    content = remove_boilerplate(
        content
    )

    content = dedupe_lines_within_page(
        content
    )

    content = clean_text(
        content
    )

    # If the body was unusually empty, use meta description.
    if len(content.split()) < 30:

        meta = (
            soup.find(
                "meta",
                attrs={
                    "name": "description"
                }
            )
            or soup.find(
                "meta",
                attrs={
                    "property":
                    "og:description"
                }
            )
        )

        if meta and meta.get("content"):

            description = clean_text(
                meta.get("content")
            )

            if description:

                content = (
                    f"# {title}\n\n"
                    f"{description}"
                )

    return title, content


# ============================================================
# SITEMAP DISCOVERY
# ============================================================

def parse_sitemap_xml(
    xml_text: str
) -> tuple[list[str], list[str]]:

    soup = BeautifulSoup(
        xml_text,
        "xml"
    )

    urls = []

    nested_sitemaps = []

    for loc in soup.find_all("loc"):

        value = clean_text(
            loc.get_text(
                strip=True
            )
        )

        if not value:
            continue

        # sitemapindex contains sitemap URLs;
        # urlset contains page URLs.
        parent = loc.parent

        if parent and parent.name == "sitemap":
            nested_sitemaps.append(
                value
            )

        else:
            urls.append(
                value
            )

    return urls, nested_sitemaps


def get_sitemaps(
    base_url: str
) -> list[str]:

    candidates = [
        "/robots.txt",
        "/sitemap.xml",
        "/sitemap_index.xml",
        "/wp-sitemap.xml",
        "/sitemap-index.xml",
        "/page-sitemap.xml",
        "/post-sitemap.xml",
    ]

    found = []

    # --------------------------------------------------------
    # robots.txt
    # --------------------------------------------------------

    try:

        response = session.get(
            urljoin(
                base_url,
                "/robots.txt"
            ),
            timeout=REQUEST_TIMEOUT
        )

        if response.ok:

            for line in response.text.splitlines():

                if line.lower().startswith(
                    "sitemap:"
                ):

                    sitemap_url = line.split(
                        ":",
                        1
                    )[1].strip()

                    if sitemap_url:
                        found.append(
                            sitemap_url
                        )

    except requests.RequestException:
        pass

    # --------------------------------------------------------
    # Direct sitemap candidates
    # --------------------------------------------------------

    for path in candidates[1:]:

        url = urljoin(
            base_url,
            path
        )

        if url not in found:

            try:

                response = session.get(
                    url,
                    timeout=REQUEST_TIMEOUT
                )

                if response.ok:

                    content_type = (
                        response.headers
                        .get(
                            "content-type",
                            ""
                        )
                        .lower()
                    )

                    text_start = (
                        response.text[:500]
                        .lower()
                    )

                    if (
                        "xml" in content_type
                        or "<urlset" in text_start
                        or "<sitemapindex" in text_start
                    ):

                        found.append(
                            url
                        )

            except requests.RequestException:
                continue

    return list(
        dict.fromkeys(found)
    )


def discover_sitemap_urls(
    base_url: str,
    max_urls: int = 5000
) -> list[str]:

    sitemap_urls = get_sitemaps(
        base_url
    )

    queue = deque(
        sitemap_urls
    )

    visited_sitemaps = set()

    discovered_pages = []

    while queue and len(
        discovered_pages
    ) < max_urls:

        sitemap_url = queue.popleft()

        if sitemap_url in visited_sitemaps:
            continue

        visited_sitemaps.add(
            sitemap_url
        )

        try:

            response = session.get(
                sitemap_url,
                timeout=REQUEST_TIMEOUT
            )

            if not response.ok:
                continue

            page_urls, nested = (
                parse_sitemap_xml(
                    response.text
                )
            )

            for nested_url in nested:

                normalized = normalize_url(
                    nested_url
                )

                if normalized not in visited_sitemaps:
                    queue.append(
                        normalized
                    )

            for page_url in page_urls:

                normalized = normalize_url(
                    page_url
                )

                if is_internal(
                    normalized,
                    base_url
                ):

                    discovered_pages.append(
                        normalized
                    )

                    if len(
                        discovered_pages
                    ) >= max_urls:
                        break

        except requests.RequestException:
            continue

    return list(
        dict.fromkeys(
            discovered_pages
        )
    )


# ============================================================
# PLAYWRIGHT
# ============================================================

class BrowserRenderer:

    def __init__(self):
        self.playwright = None
        self.browser = None

        if not USE_PLAYWRIGHT:
            return

        try:

            from playwright.sync_api import (
                sync_playwright
            )

            self.playwright = (
                sync_playwright().start()
            )

            self.browser = (
                self.playwright.chromium.launch(
                    headless=True
                )
            )

        except Exception as exc:

            print(
                "[PLAYWRIGHT] Disabled:",
                exc
            )

    def fetch(
        self,
        url: str
    ) -> Optional[str]:

        if not self.browser:
            return None

        page = None

        try:

            page = self.browser.new_page(
                user_agent=USER_AGENT
            )

            page.goto(
                url,
                wait_until="networkidle",
                timeout=30000
            )

            page.wait_for_timeout(
                800
            )

            return page.content()

        except Exception as exc:

            print(
                f"[PLAYWRIGHT ERROR] "
                f"{url} -> {exc}"
            )

            return None

        finally:

            if page:
                page.close()

    def close(self):

        if self.browser:
            self.browser.close()

        if self.playwright:
            self.playwright.stop()


# ============================================================
# HTTP FETCH
# ============================================================

def http_fetch(
    url: str
) -> tuple[
    Optional[str],
    int,
    Optional[str]
]:

    try:

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        return (
            response.text,
            response.status_code,
            response.headers.get(
                "content-type",
                ""
            ).lower()
        )

    except requests.RequestException as exc:

        return (
            None,
            0,
            str(exc)
        )


# ============================================================
# CRAWLER
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
})


class SiteCrawler:

    def __init__(
        self,
        source_version: str,
        base_url: str,
        max_pages: int
    ):

        self.source_version = (
            source_version
        )

        self.base_url = normalize_url(
            base_url
        )

        self.max_pages = max_pages

        self.renderer = (
            BrowserRenderer()
        )

        self.visited = set()

        self.discovered = set()

        self.pages: list[Page] = []

        self.not_found = []

        self.errors = []

        self.skipped = []

    def browser_needed(
        self,
        html: Optional[str],
        content: str,
        link_count: int
    ) -> bool:

        if not USE_PLAYWRIGHT:
            return False

        if FORCE_PLAYWRIGHT:
            return True

        if not html:
            return True

        if len(
            content.split()
        ) < MIN_HTTP_TEXT_FOR_BROWSER_FALLBACK:

            return True

        # A page with almost no links despite being a section
        # page may have client-rendered navigation.
        if (
            link_count == 0
            and path_of(
                self.base_url
            ) == "/"
        ):
            return True

        return False

    def extract_links(
        self,
        html: str,
        current_url: str
    ) -> list[str]:

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        links = []

        for anchor in soup.find_all(
            "a",
            href=True
        ):

            href = anchor.get(
                "href",
                ""
            ).strip()

            if not href:
                continue

            if href.startswith(
                (
                    "#",
                    "mailto:",
                    "tel:",
                    "javascript:",
                    "data:",
                )
            ):
                continue

            absolute = urljoin(
                current_url,
                href
            )

            if not is_http_url(
                absolute
            ):
                continue

            normalized = normalize_url(
                absolute
            )

            if not is_internal(
                normalized,
                self.base_url
            ):
                continue

            if should_skip_url(
                normalized
            ):
                continue

            links.append(
                normalized
            )

        return list(
            dict.fromkeys(
                links
            )
        )

    def seed_urls(self) -> list[str]:

        seeds = []

        # Root-relative section seeds.
        for seed in SECTION_SEEDS:

            url = normalize_url(
                urljoin(
                    self.base_url,
                    seed
                )
            )

            if is_internal(
                url,
                self.base_url
            ):

                if not should_skip_url(
                    url
                ):

                    seeds.append(
                        url
                    )

        # Sitemap URLs.
        sitemap_pages = (
            discover_sitemap_urls(
                self.base_url
            )
        )

        for url in sitemap_pages:

            if (
                is_internal(
                    url,
                    self.base_url
                )
                and not should_skip_url(url)
            ):

                seeds.append(
                    url
                )

        return list(
            dict.fromkeys(
                seeds
            )
        )

    def crawl(
        self
    ) -> tuple[
        list[Page],
        dict
    ]:

        print()
        print(
            "=" * 70
        )

        print(
            f"CRAWLING "
            f"{self.source_version.upper()}"
        )

        print(
            "=" * 70
        )

        queue = deque()

        for url in self.seed_urls():

            if url not in self.discovered:

                self.discovered.add(
                    url
                )

                queue.append(
                    (
                        url,
                        0
                    )
                )

        while (
            queue
            and len(self.visited)
            < self.max_pages
        ):

            current_url, depth = (
                queue.popleft()
            )

            if current_url in self.visited:
                continue

            if depth > MAX_DEPTH:
                continue

            if should_skip_url(
                current_url
            ):
                continue

            self.visited.add(
                current_url
            )

            print(
                f"[{len(self.visited):03d}] "
                f"depth={depth} "
                f"{current_url}"
            )

            html, status, content_type = (
                http_fetch(
                    current_url
                )
            )

            if status == 404:

                self.not_found.append(
                    current_url
                )

                continue

            if status == 0:

                # Retry through Playwright.
                rendered = (
                    self.renderer.fetch(
                        current_url
                    )
                )

                if rendered:

                    html = rendered
                    status = 200
                    content_type = "text/html"

                else:

                    self.errors.append({
                        "url": current_url,
                        "error": content_type,
                    })

                    continue

            if status >= 400:

                self.errors.append({
                    "url": current_url,
                    "error": f"HTTP {status}",
                })

                continue

            if (
                content_type
                and "text/html"
                not in content_type
                and "xhtml"
                not in content_type
            ):

                continue

            if not html:
                continue

            title, content = (
                extract_content_from_html(
                    html
                )
            )

            raw_links = self.extract_links(
                html,
                current_url
            )

            # Browser fallback for JS-heavy pages.
            if self.browser_needed(
                html,
                content,
                len(raw_links)
            ):

                rendered = (
                    self.renderer.fetch(
                        current_url
                    )
                )

                if rendered:

                    rendered_title, rendered_content = (
                        extract_content_from_html(
                            rendered
                        )
                    )

                    rendered_links = (
                        self.extract_links(
                            rendered,
                            current_url
                        )
                    )

                    if (
                        len(rendered_content.split())
                        > len(content.split())
                    ):
                        title = rendered_title
                        content = rendered_content

                    raw_links = list(
                        dict.fromkeys(
                            raw_links
                            + rendered_links
                        )
                    )

            # Discover more internal pages.
            for link in raw_links:

                if (
                    link not in self.discovered
                    and not should_skip_url(link)
                ):

                    self.discovered.add(
                        link
                    )

                    queue.append(
                        (
                            link,
                            depth + 1
                        )
                    )

            category = classify_category(
                current_url
            )

            # Excluded page.
            if category == "excluded":
                self.skipped.append(
                    current_url
                )
                continue

            words = content.split()

            # Skip obviously empty / loading pages.
            if len(words) < 35:

                self.skipped.append(
                    {
                        "url": current_url,
                        "reason": (
                            f"only {len(words)} "
                            "words"
                        ),
                    }
                )

                continue

            page = Page(
                url=current_url,
                source_version=self.source_version,
                title=title,
                category=category,
                topic=canonical_topic(
                    current_url
                ),
                content=content,
                word_count=len(words),
                discovered_depth=depth,
                http_status=status,
            )

            self.pages.append(
                page
            )

            time.sleep(
                REQUEST_DELAY
            )

        self.renderer.close()

        report = {
            "source_version": (
                self.source_version
            ),
            "base_url": self.base_url,
            "visited": len(
                self.visited
            ),
            "discovered": len(
                self.discovered
            ),
            "stored": len(
                self.pages
            ),
            "skipped": len(
                self.skipped
            ),
            "not_found": (
                self.not_found
            ),
            "errors": (
                self.errors
            ),
        }

        print()
        print(
            f"Finished "
            f"{self.source_version}: "
            f"visited={report['visited']}, "
            f"discovered={report['discovered']}, "
            f"stored={report['stored']}"
        )

        return self.pages, report


# ============================================================
# PAGE-LEVEL CLEANUP
# ============================================================

def exact_dedupe_pages(
    pages: list[Page]
) -> list[Page]:

    seen = {}

    result = []

    for page in pages:

        key = (
            normalize_for_matching(
                page.content
            )
        )

        if not key:
            continue

        digest = hashlib.sha256(
            key.encode("utf-8")
        ).hexdigest()

        if digest in seen:

            # Keep the better page if duplicate URLs
            # have been reached.
            existing = seen[digest]

            if (
                page.source_version == "new"
                and existing.source_version
                != "new"
            ):

                result.remove(
                    existing
                )

                result.append(
                    page
                )

                seen[digest] = page

            continue

        seen[digest] = page

        result.append(page)

    return result


def remove_boilerplate_across_pages(
    pages: list[Page]
) -> list[Page]:
    """
    Detect exact repeated content blocks appearing across
    many pages and remove them.

    This is stronger than only hard-coding the footer text.
    """

    if len(pages) < 3:
        return pages

    block_page_count = Counter()

    page_blocks = {}

    for page in pages:

        blocks = split_blocks(
            page.content
        )

        page_blocks[
            page.url
        ] = blocks

        seen_this_page = set()

        for block in blocks:

            normalized = normalize_for_matching(
                block
            )

            # Ignore tiny blocks.
            if len(
                normalized.split()
            ) < 5:

                continue

            if normalized in seen_this_page:
                continue

            seen_this_page.add(
                normalized
            )

            block_page_count[
                normalized
            ] += 1

    # Repeated in at least 35% of pages:
    # likely common site boilerplate.
    threshold = max(
        3,
        int(
            len(pages) * 0.35
        )
    )

    common_blocks = {
        normalized
        for normalized, count
        in block_page_count.items()
        if count >= threshold
    }

    cleaned = []

    for page in pages:

        kept_blocks = []

        for block in page_blocks[
            page.url
        ]:

            normalized = (
                normalize_for_matching(
                    block
                )
            )

            # Do not remove the entire home/about/contact page
            # just because a short generic line repeats.
            if (
                normalized in common_blocks
                and len(
                    normalized.split()
                ) < 80
            ):

                continue

            kept_blocks.append(
                block
            )

        content = "\n\n".join(
            kept_blocks
        )

        content = remove_boilerplate(
            content
        )

        content = clean_text(
            content
        )

        cleaned_page = Page(
            url=page.url,
            source_version=page.source_version,
            title=page.title,
            category=page.category,
            topic=page.topic,
            content=content,
            word_count=len(
                content.split()
            ),
            discovered_depth=(
                page.discovered_depth
            ),
            http_status=page.http_status,
        )

        cleaned.append(
            cleaned_page
        )

    return cleaned


# ============================================================
# TOPIC MERGING
# ============================================================

def merge_blocks(
    base_blocks: list[str],
    extra_blocks: list[str],
    prefer_base: bool
) -> list[str]:

    result = list(
        base_blocks
    )

    base_normalized = [
        normalize_for_matching(
            block
        )
        for block in base_blocks
    ]

    base_headings = {
        heading
        for heading in (
            block_heading(block)
            for block in base_blocks
        )
        if heading
    }

    for extra in extra_blocks:

        extra_norm = normalize_for_matching(
            extra
        )

        if not extra_norm:
            continue

        # Exact duplicate.
        if extra_norm in base_normalized:
            continue

        # Same heading means the NEW version should
        # be authoritative for that section.
        heading = block_heading(
            extra
        )

        if heading and heading in base_headings:
            continue

        duplicate = False

        for existing in result:

            score = similarity(
                extra,
                existing
            )

            overlap = token_overlap(
                extra,
                existing
            )

            if score >= 0.84 or overlap >= 0.82:

                duplicate = True
                break

        if duplicate:
            continue

        result.append(
            extra
        )

    return result


def merge_pages_with_same_topic(
    pages: list[Page],
    source_priority: list[str]
) -> dict[str, list[Page]]:

    groups = {}

    for page in pages:

        groups.setdefault(
            page.topic,
            []
        ).append(
            page
        )

    return groups


def merge_topic_group(
    topic: str,
    pages: list[Page]
) -> dict:

    # --------------------------------------------------------
    # NEW pages first.
    # If no NEW page exists, OLD becomes the base.
    # --------------------------------------------------------

    new_pages = [
        page
        for page in pages
        if page.source_version == "new"
    ]

    old_pages = [
        page
        for page in pages
        if page.source_version == "old"
    ]

    if new_pages:

        # Longest NEW page gives us a strong base,
        # then other NEW pages are merged in.
        base = max(
            new_pages,
            key=lambda page: page.word_count
        )

        merged_blocks = split_blocks(
            base.content
        )

        # Merge other NEW pages.
        for page in new_pages:

            if page.url == base.url:
                continue

            merged_blocks = merge_blocks(
                merged_blocks,
                split_blocks(
                    page.content
                ),
                prefer_base=True,
            )

        primary_source = "new"

        # Now append unique OLD information.
        for old_page in old_pages:

            merged_blocks = merge_blocks(
                merged_blocks,
                split_blocks(
                    old_page.content
                ),
                prefer_base=True,
            )

        source_versions = (
            ["new"] +
            (
                ["old"]
                if old_pages
                else []
            )
        )

        canonical_url = base.url

        title = base.title

        category = base.category

    else:

        # OLD-only topic.
        base = max(
            old_pages,
            key=lambda page: page.word_count
        )

        merged_blocks = split_blocks(
            base.content
        )

        for page in old_pages:

            if page.url == base.url:
                continue

            merged_blocks = merge_blocks(
                merged_blocks,
                split_blocks(
                    page.content
                ),
                prefer_base=True,
            )

        primary_source = "old"
        source_versions = ["old"]
        canonical_url = base.url
        title = base.title
        category = base.category

    merged_content = "\n\n".join(
        merged_blocks
    )

    merged_content = remove_boilerplate(
        merged_content
    )

    merged_content = dedupe_lines_within_page(
        merged_content
    )

    merged_content = clean_text(
        merged_content
    )

    source_urls = [
        page.url
        for page in pages
    ]

    doc_id = hashlib.sha256(
        topic.encode("utf-8")
    ).hexdigest()[:16]

    return {
        "id": doc_id,
        "topic": topic,
        "title": title,
        "category": category,
        "primary_source": primary_source,
        "source_versions": source_versions,
        "source_urls": source_urls,
        "word_count": len(
            merged_content.split()
        ),
        "content": merged_content,
    }


# ============================================================
# QUALITY VALIDATION
# ============================================================

def validate_final_documents(
    documents: list[dict]
) -> dict:

    category_counts = Counter()

    source_counts = Counter()

    topic_counts = Counter()

    warnings = []

    mojibake_markers = (
        "â",
        "ð",
        "ï¸",
        "Â",
    )

    mojibake_docs = []

    short_docs = []

    for doc in documents:

        category_counts[
            doc["category"]
        ] += 1

        for source in doc[
            "source_versions"
        ]:
            source_counts[
                source
            ] += 1

        topic_counts[
            doc["topic"]
        ] += 1

        if doc["word_count"] < 50:

            short_docs.append({
                "title": doc["title"],
                "topic": doc["topic"],
                "words": doc["word_count"],
            })

        if any(
            marker in doc["content"]
            for marker in mojibake_markers
        ):

            mojibake_docs.append(
                doc["title"]
            )

    # Required knowledge categories.
    required_categories = {
        "company",
        "service",
        "solution",
        "technology",
        "industry",
        "contact",
    }

    for required in required_categories:

        if category_counts[
            required
        ] == 0:

            warnings.append(
                f"No final documents in "
                f"category='{required}'"
            )

    if mojibake_docs:

        warnings.append(
            "Mojibake remains in some documents."
        )

    if short_docs:

        warnings.append(
            "Some final documents are unusually short."
        )

    return {
        "documents": len(
            documents
        ),
        "categories": dict(
            category_counts
        ),
        "source_versions": dict(
            source_counts
        ),
        "duplicate_topics": {
            topic: count
            for topic, count
            in topic_counts.items()
            if count > 1
        },
        "short_documents": short_docs,
        "mojibake_documents": (
            mojibake_docs
        ),
        "warnings": warnings,
    }


# ============================================================
# SAVE
# ============================================================

def save_knowledge(
    documents: list[dict],
    crawl_reports: dict,
    validation: dict
):

    import os

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    payload = {
        "project": (
            "Weboum Company Chatbot "
            "Knowledge Base"
        ),

        "version": "3.0",

        "description": (
            "RAG knowledge extracted from "
            "the NEW public Weboum site and "
            "the OLD local Weboum site. "
            "NEW is authoritative for overlapping "
            "topics while unique OLD knowledge "
            "is preserved."
        ),

        "sources": {
            "new": NEW_SOURCE,
            "old": OLD_SOURCE,
        },

        "documents": documents,

        # Audit information stays in the SAME JSON file.
        # Your RAG loader can simply read payload["documents"].
        "extraction_report": {
            "crawl": crawl_reports,
            "validation": validation,
        },
    }

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2
        )

    print()
    print(
        "=" * 70
    )
    print(
        "KNOWLEDGE BASE SAVED"
    )
    print(
        "=" * 70
    )

    print(
        f"File: {OUTPUT_FILE}"
    )

    print(
        f"Final documents: "
        f"{len(documents)}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "=" * 70
    )

    print(
        "WEBOUM KNOWLEDGE EXTRACTION V3"
    )

    print(
        "=" * 70
    )

    print(
        f"NEW: {NEW_SOURCE}"
    )

    print(
        f"OLD: {OLD_SOURCE}"
    )

    # --------------------------------------------------------
    # Crawl each website.
    # --------------------------------------------------------

    all_pages = []

    crawl_reports = {}

    for source_version, base_url in SOURCES:

        crawler = SiteCrawler(
            source_version=source_version,
            base_url=base_url,
            max_pages=(
                MAX_PAGES_PER_SOURCE
            ),
        )

        pages, report = (
            crawler.crawl()
        )

        all_pages.extend(
            pages
        )

        crawl_reports[
            source_version
        ] = report

    # --------------------------------------------------------
    # Exact dedupe.
    # --------------------------------------------------------

    all_pages = exact_dedupe_pages(
        all_pages
    )

    # --------------------------------------------------------
    # Cross-page boilerplate cleanup.
    # --------------------------------------------------------

    all_pages = (
        remove_boilerplate_across_pages(
            all_pages
        )
    )

    # --------------------------------------------------------
    # Recalculate topic after cleanup.
    # --------------------------------------------------------

    for page in all_pages:

        page.topic = canonical_topic(
            page.url
        )

    # --------------------------------------------------------
    # Topic groups.
    # --------------------------------------------------------

    groups = (
        merge_pages_with_same_topic(
            all_pages,
            ["new", "old"]
        )
    )

    final_documents = []

    for topic, pages in groups.items():

        document = (
            merge_topic_group(
                topic,
                pages
            )
        )

        # Skip merged docs that became too small.
        if document[
            "word_count"
        ] < 35:

            continue

        final_documents.append(
            document
        )

    # --------------------------------------------------------
    # Stable ordering.
    # --------------------------------------------------------

    category_order = {
        "company": 0,
        "contact": 1,
        "service": 2,
        "solution": 3,
        "technology": 4,
        "industry": 5,
        "project": 6,
        "portfolio": 7,
        "resource": 8,
        "faq": 9,
        "general": 10,
    }

    final_documents.sort(
        key=lambda doc: (
            category_order.get(
                doc["category"],
                99
            ),
            doc["title"].lower()
        )
    )

    # --------------------------------------------------------
    # Validation.
    # --------------------------------------------------------

    validation = (
        validate_final_documents(
            final_documents
        )
    )

    # --------------------------------------------------------
    # Save.
    # --------------------------------------------------------

    save_knowledge(
        documents=final_documents,
        crawl_reports=crawl_reports,
        validation=validation,
    )

    # --------------------------------------------------------
    # Console report.
    # --------------------------------------------------------

    print()
    print(
        "=" * 70
    )

    print(
        "FINAL VALIDATION"
    )

    print(
        "=" * 70
    )

    print(
        "Documents by category:"
    )

    for category, count in sorted(
        validation[
            "categories"
        ].items()
    ):

        print(
            f"  {category}: {count}"
        )

    print()

    print(
        "Documents by source:"
    )

    for source, count in (
        validation[
            "source_versions"
        ].items()
    ):

        print(
            f"  {source}: {count}"
        )

    print()

    if validation[
        "duplicate_topics"
    ]:

        print(
            "WARNING: duplicate final topics:"
        )

        for topic, count in (
            validation[
                "duplicate_topics"
            ].items()
        ):

            print(
                f"  {topic}: {count}"
            )

    else:

        print(
            "Topic duplication: PASS"
        )

    if validation[
        "mojibake_documents"
    ]:

        print(
            "WARNING: encoding issues remain:"
        )

        for title in validation[
            "mojibake_documents"
        ]:

            print(
                f"  - {title}"
            )

    else:

        print(
            "Encoding: PASS"
        )

    if validation[
        "warnings"
    ]:

        print()
        print(
            "Validation warnings:"
        )

        for warning in validation[
            "warnings"
        ]:

            print(
                f"  - {warning}"
            )

    else:

        print()
        print(
            "Validation warnings: NONE"
        )

    print()
    print(
        "IMPORTANT:"
    )

    print(
        "Open company-docs/weboum_knowledge.json "
        "and review the documents before embeddings."
    )

    print(
        "RAG data is available under:"
    )

    print(
        'payload["documents"]'
    )


if __name__ == "__main__":
    main()
