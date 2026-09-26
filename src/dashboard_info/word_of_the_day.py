import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LANGUAGES = ("en", "es", "de")

# get_text(separator=" ") inserts a space at every tag boundary even where
# the source had none, e.g. "<a>person</a>." -> "person .". Undo that around
# punctuation so the definition reads naturally.
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:)])")
_SPACE_AFTER_OPEN_PAREN_RE = re.compile(r"([(])\s+")


def _detagged_text(tag) -> str:
    text = tag.get_text(" ", strip=True)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    return _SPACE_AFTER_OPEN_PAREN_RE.sub(r"\1", text)

# Wikimedia asks bots to send a descriptive User-Agent; Duden and rae-api.com
# get the same one for simplicity, and it makes us honest either way.
USER_AGENT = "dashboard-info-api/0.1 (word-of-the-day feature)"

EN_WIKTIONARY_API = "https://en.wiktionary.org/w/api.php"
RAE_API_BASE = "https://rae-api.com/api"
DUDEN_WOTD_URL = "https://www.duden.de/wort-des-tages"


@dataclass
class WordOfTheDayEntry:
    language: str  # "en" | "es" | "de"
    date: str  # ISO date, server-local
    word: str
    part_of_speech: str | None
    definition: str
    source_url: str


async def fetch_english(client: httpx.AsyncClient) -> WordOfTheDayEntry:
    """en.wiktionary.org genuinely rotates daily. Today's entry is rendered
    into a small box with stable RSS-feed ids (WOTD-rss-title/-description)
    that survive redesigns better than any CSS class would."""
    today = date.today()
    page = f"Wiktionary:Word_of_the_day/{today.year}/{today:%B}_{today.day}"
    resp = await client.get(
        EN_WIKTIONARY_API,
        params={"action": "parse", "page": page, "prop": "text", "format": "json"},
    )
    resp.raise_for_status()
    html = resp.json()["parse"]["text"]["*"]
    soup = BeautifulSoup(html, "html.parser")

    title = soup.find(id="WOTD-rss-title")
    if title is None:
        raise ValueError(f"no WOTD-rss-title on {page!r}")
    word = title.get_text(strip=True)

    bold = title.find_parent("b")
    pos_tag = bold.find_next_sibling("i") if bold else None
    part_of_speech = pos_tag.get_text(strip=True) if pos_tag else None

    description = soup.find(id="WOTD-rss-description")
    first_sense = description.find("li") if description else None
    if first_sense is None:
        raise ValueError(f"no WOTD-rss-description on {page!r}")
    definition = _detagged_text(first_sense)

    return WordOfTheDayEntry(
        language="en",
        date=today.isoformat(),
        word=word,
        part_of_speech=part_of_speech,
        definition=definition,
        source_url=f"https://en.wiktionary.org/wiki/{page}",
    )


async def fetch_spanish(client: httpx.AsyncClient) -> WordOfTheDayEntry:
    """es.wiktionary.org only has a word of the *week*; RAE's own site does
    rotate daily. rae-api.com is an unofficial hosted wrapper around it that
    gives clean JSON for both the daily word and the full dictionary entry,
    so no HTML scraping is needed for this language at all."""
    daily = await client.get(f"{RAE_API_BASE}/daily")
    daily.raise_for_status()
    word = daily.json()["data"]["word"]

    entry = await client.get(f"{RAE_API_BASE}/words/{word}")
    entry.raise_for_status()
    sense = entry.json()["data"]["meanings"][0]["senses"][0]

    part_of_speech = sense.get("category")
    if sense.get("gender"):
        part_of_speech = f"{part_of_speech}, {sense['gender']}" if part_of_speech else sense["gender"]

    return WordOfTheDayEntry(
        language="es",
        date=date.today().isoformat(),
        word=word,
        part_of_speech=part_of_speech,
        definition=sense["description"],
        source_url=f"https://dle.rae.es/{word}",
    )


def _duden_section_text(soup: BeautifulSoup, heading_text: str) -> str | None:
    heading = soup.find(
        lambda tag: tag.name in ("h2", "h3") and tag.get_text(strip=True) == heading_text
    )
    if heading is None:
        return None
    content = heading.find_next(["p", "ul", "ol"])
    return _detagged_text(content) if content else None


async def fetch_german(client: httpx.AsyncClient) -> WordOfTheDayEntry:
    """de.wiktionary.org only has a "Wort der Woche"; Duden's site runs a
    genuine daily word but has no API. The landing page gives the word and
    its link to the full entry; the entry page's heading is "Bedeutung" for
    single-sense words or "Bedeutungsübersicht" for multi-sense ones."""
    landing = await client.get(DUDEN_WOTD_URL)
    landing.raise_for_status()
    soup = BeautifulSoup(landing.text, "html.parser")

    # The word-of-the-day widget on the homepage/landing page is this one
    # element id; everything else on the page (page title, nav, archive
    # links) also mentions "Wort des Tages" so isn't a safe anchor.
    container = soup.find(id="block-numero-wordoftheday")
    if container is None:
        raise ValueError("no #block-numero-wordoftheday on Duden landing page")
    word_link = container.find("a", class_="scene__title-link") or container.find("a")
    if word_link is None or not word_link.get("href"):
        raise ValueError("no word-of-the-day link on Duden landing page")
    word = word_link.get_text(strip=True).replace("\xad", "")
    entry_url = urljoin(DUDEN_WOTD_URL, word_link["href"])

    pos_link = container.find("a", class_="scene__main")
    part_of_speech = pos_link.get_text(strip=True) if pos_link else None

    entry_resp = await client.get(entry_url)
    entry_resp.raise_for_status()
    entry_soup = BeautifulSoup(entry_resp.text, "html.parser")

    definition = _duden_section_text(entry_soup, "Bedeutung") or _duden_section_text(
        entry_soup, "Bedeutungsübersicht"
    )
    if definition is None:
        raise ValueError(f"no Duden definition section for {word!r}")

    return WordOfTheDayEntry(
        language="de",
        date=date.today().isoformat(),
        word=word,
        part_of_speech=part_of_speech,
        definition=definition,
        source_url=entry_url,
    )


class WordOfTheDayFetcher:
    """Fetches today's word for one language; any failure propagates so the
    cache can decide whether to fall back to a previous entry."""

    _FETCH_BY_LANGUAGE = {"en": fetch_english, "es": fetch_spanish, "de": fetch_german}

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            timeout=15, headers={"User-Agent": USER_AGENT}
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self, language: str) -> WordOfTheDayEntry:
        return await self._FETCH_BY_LANGUAGE[language](self._client)


class WordOfTheDayCache:
    """One cached entry per language, refetched once per (server-local)
    calendar day. Mirrors FeedCache's staleness-check-then-lock shape (see
    feeds.py) but keyed on date instead of a TTL, and a failed refetch keeps
    serving whatever was cached before rather than going empty."""

    def __init__(self, fetcher: WordOfTheDayFetcher | None = None):
        self._fetcher = fetcher or WordOfTheDayFetcher()
        self._entries: dict[str, WordOfTheDayEntry | None] = dict.fromkeys(LANGUAGES)
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._fetcher.close()

    def _stale(self) -> list[str]:
        today = date.today().isoformat()
        return [
            lang
            for lang in LANGUAGES
            if self._entries[lang] is None or self._entries[lang].date != today
        ]

    async def entries_for_today(self) -> dict[str, WordOfTheDayEntry | None]:
        stale = self._stale()
        if stale:
            async with self._lock:
                # Another request may have refreshed while we waited.
                stale = self._stale()
                if stale:
                    results = await asyncio.gather(
                        *(self._fetcher.fetch(lang) for lang in stale),
                        return_exceptions=True,
                    )
                    for lang, result in zip(stale, results):
                        if isinstance(result, Exception):
                            logger.exception(
                                "failed to fetch word of the day for %r", lang
                            )
                        else:
                            self._entries[lang] = result
        return dict(self._entries)
