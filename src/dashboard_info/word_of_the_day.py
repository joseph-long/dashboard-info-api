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


def _outer_list_items(root) -> list:
    """Every <li> under `root` that isn't nested inside another <li>.

    Sub-senses are already flattened into their parent's text, so taking only
    the outermost items avoids listing a sense twice. `find_all` rather than
    `recursive=False` on one list because a source may render several sibling
    lists (en.wiktionary splits senses across one <ol> per part of speech)."""
    return [li for li in root.find_all("li") if li.find_parent("li") is None]


def _detagged_text(tag) -> str:
    text = tag.get_text(" ", strip=True)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    return _SPACE_AFTER_OPEN_PAREN_RE.sub(r"\1", text)

# A gendered noun is much easier to learn with its article attached, so for
# Spanish and German the `word` we serve is the article plus the word ("el
# aguachile", "die Herbstsonne"). English has no gendered article and nothing
# that isn't a noun gets one.
def _with_article(word: str, article: str | None) -> str:
    return f"{article} {word}" if article else word


_SPANISH_ARTICLE_BY_GENDER = {
    "masculine": "el",
    "feminine": "la",
    # RAE files a common-gender noun ("periodista") under a single entry, so
    # both articles are the honest rendering.
    "masculine_and_feminine": "el/la",
}

_ACCENTED_VOWELS = "\u00e1\u00e9\u00ed\u00f3\u00fa"
_VOWELS = "aeiou\u00fc"
# Two adjacent strong vowels are a hiatus ("al-de-a"); a strong/weak pair is
# one diphthong ("a-gua"). Only unaccented words reach the count, so the
# accented weak vowels that also break a diphthong need not be listed.
_STRONG_VOWELS = "aeo"


def _syllable_count(word: str) -> int:
    """Vowel groups in an unaccented `word`, splitting hiatuses. Enough to
    locate the stressed syllable; not a general-purpose hyphenator."""
    count = 0
    previous = ""
    for char in word:
        if char not in _VOWELS:
            previous = ""
            continue
        if not previous or (previous in _STRONG_VOWELS and char in _STRONG_VOWELS):
            count += 1
        previous = char
    return count


def _stressed_on_first_syllable(word: str) -> bool:
    """Where Spanish orthography puts the stress: a written accent marks the
    stressed vowel outright, and without one the stress falls on the
    penultimate syllable for words ending in a vowel, -n or -s, on the last
    syllable otherwise."""
    if any(char in _ACCENTED_VOWELS for char in word):
        return word.startswith(("\u00e1", "h\u00e1"))
    count = _syllable_count(word)
    if count < 2:
        return True
    stressed = count - 2 if word[-1] in "aeiouns" else count - 1
    return stressed == 0


def _spanish_article(word: str, gender: str | None) -> str | None:
    article = _SPANISH_ARTICLE_BY_GENDER.get(gender or "")
    # "el agua", "el hacha": a singular feminine noun beginning with a
    # stressed /a/ takes the masculine article to avoid the double vowel.
    word = word.lower()
    a_initial = word.startswith(("a", "\u00e1", "ha", "h\u00e1"))
    if article == "la" and a_initial and _stressed_on_first_syllable(word):
        return "el"
    return article


_GERMAN_ARTICLE_BY_GENDER = {"maskulin": "der", "feminin": "die", "neutrum": "das"}
_GERMAN_GENDER_RE = re.compile("|".join(_GERMAN_ARTICLE_BY_GENDER), re.IGNORECASE)


def _german_article(part_of_speech: str | None) -> str | None:
    """der/die/das from Duden's Wortart line ("Substantiv, feminin").

    A word Duden files under several genders ("Substantiv, maskulin, oder
    Substantiv, feminin, oder Substantiv, Neutrum") keeps them all, in its
    order; a Pluralwort ("Ferien") is always "die"; verbs and adjectives,
    having no article, get none."""
    if not part_of_speech:
        return None
    if "Pluralwort" in part_of_speech:
        return "die"
    articles = dict.fromkeys(
        _GERMAN_ARTICLE_BY_GENDER[match.group(0).lower()]
        for match in _GERMAN_GENDER_RE.finditer(part_of_speech)
    )
    return "/".join(articles) or None


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
    # Ready to display: a Spanish or German noun carries its article ("el
    # aguachile", "die Herbstsonne"), which is how you want to learn it.
    word: str
    part_of_speech: str | None
    # Every sense the source lists, in source order, never empty. Which of
    # them to show (and how) is the client's decision: the LED/ePaper firmware
    # has room for one, the web UI scrolls and shows all of them.
    definitions: list[str]
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
    senses = _outer_list_items(description) if description else []
    if not senses:
        raise ValueError(f"no WOTD-rss-description on {page!r}")
    definitions = [_detagged_text(sense) for sense in senses]

    return WordOfTheDayEntry(
        language="en",
        date=today.isoformat(),
        word=word,
        part_of_speech=part_of_speech,
        definitions=definitions,
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
    # A word can carry several "meanings" (separate etymologies), each with
    # its own numbered senses; flattening them keeps RAE's own ordering.
    senses = [
        sense
        for meaning in entry.json()["data"]["meanings"]
        for sense in meaning["senses"]
    ]
    definitions = [s["description"] for s in senses if s.get("description")]
    if not definitions:
        raise ValueError(f"no RAE senses for {word!r}")

    # Part of speech comes from the first sense: later ones can differ (RAE
    # files noun and adjective senses under one word), and the field is a
    # one-line hint, not a per-sense label.
    first = senses[0]
    part_of_speech = first.get("category")
    if first.get("gender"):
        part_of_speech = f"{part_of_speech}, {first['gender']}" if part_of_speech else first["gender"]

    return WordOfTheDayEntry(
        language="es",
        date=date.today().isoformat(),
        word=_with_article(word, _spanish_article(word, first.get("gender"))),
        part_of_speech=part_of_speech,
        definitions=definitions,
        source_url=f"https://dle.rae.es/{word}",
    )


# Duden gives every sense of a multi-sense entry its own <li id="Bedeutung-N">
# (sub-senses get "1a"/"1b"), so the ids are a better anchor than the heading,
# which is "Bedeutungen (4)" -- count included -- rather than a fixed string.
_DUDEN_SENSE_ID_RE = re.compile(r"^Bedeutung-")


def _duden_sense_text(item) -> str | None:
    """One sense's text, minus the example/idiom blocks Duden nests beside it.

    Most senses put their wording in div.enumeration__text. Cross-reference
    senses ("Kurzform für Vorhängeschloss") have no such div and carry a
    dl.tuple instead; anything else is noise (image credits, "Wendungen,
    Redensarten, Sprichwörter" lists) and is skipped."""
    text = item.find(class_="enumeration__text") or item.find("dl", class_="tuple")
    return _detagged_text(text) if text else None


def _duden_definitions(soup: BeautifulSoup) -> list[str]:
    # Multi-sense entries: div#bedeutungen holds the enumeration.
    container = soup.find(id="bedeutungen")
    if container is not None:
        senses = container.find_all("li", id=_DUDEN_SENSE_ID_RE)
        found = [t for t in (_duden_sense_text(li) for li in senses) if t]
        if found:
            return found

    # Single-sense entries: div#bedeutung, whose wording sits in the same
    # enumeration__text div.
    container = soup.find(id="bedeutung")
    if container is not None:
        text = container.find(class_="enumeration__text")
        if text:
            return [_detagged_text(text)]

    # Layout we don't recognise: fall back to the prose right after whichever
    # heading is present. A list here is one sense per item, same as above.
    for heading_text in ("Bedeutung", "Bedeutungsübersicht"):
        heading = soup.find(
            lambda tag: tag.name in ("h2", "h3")
            and tag.get_text(strip=True) == heading_text
        )
        if heading is None:
            continue
        content = heading.find_next(["p", "ul", "ol"])
        if content is None:
            continue
        if content.name in ("ul", "ol"):
            found = [_detagged_text(li) for li in _outer_list_items(content)]
            if found:
                return found
        else:
            return [_detagged_text(content)]

    return []


async def fetch_german(client: httpx.AsyncClient) -> WordOfTheDayEntry:
    """de.wiktionary.org only has a "Wort der Woche"; Duden's site runs a
    genuine daily word but has no API. The landing page gives the word and
    its link to the full entry; see _duden_definitions for the entry page."""
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

    definitions = _duden_definitions(entry_soup)
    if not definitions:
        raise ValueError(f"no Duden definition section for {word!r}")

    return WordOfTheDayEntry(
        language="de",
        date=date.today().isoformat(),
        word=_with_article(word, _german_article(part_of_speech)),
        part_of_speech=part_of_speech,
        definitions=definitions,
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
