import httpx

import pytest

from dashboard_info.word_of_the_day import (
    _german_article,
    _spanish_article,
    _with_article,
    fetch_english,
    fetch_german,
    fetch_spanish,
)

# Trimmed from a real en.wiktionary.org action=parse&prop=text response --
# the WOTD-rss-* ids are the stable bit RSS readers rely on, so they're the
# safest thing to anchor a parser to.
EN_WOTD_HTML = """
<div class="mw-parser-output">
<div class="mf-wotd" title="Word of the day">
<table class="wotd-container"><tbody>
<tr><td><div class="wotd-header">Word of the day</div></td></tr>
<tr><td><b><a href="/wiki/enfranchisement#English" title="enfranchisement">
<span id="WOTD-rss-title">enfranchisement</span></a></b> <i>n</i></td></tr>
<tr><td><div id="WOTD-rss-description">
<ol><li>The action of enfranchising; also, the fact or state of being
enfranchised.
<ol><li>Release from imprisonment, political oppression, or slavery.</li></ol>
</li></ol>
</div></td></tr>
</tbody></table></div></div>
"""

RAE_WORDS_CASA = {
    "ok": True,
    "data": {
        "word": "casa",
        "meanings": [
            {
                "senses": [
                    {
                        "category": "noun",
                        "gender": "feminine",
                        "description": "Edificio para habitar.",
                    }
                ]
            }
        ],
    },
}

# Trimmed from the real Duden pages: the landing page's word-of-the-day
# widget links the word and its part of speech; the entry page's heading is
# "Bedeutung" for a single-sense word like this one.
DUDEN_LANDING_HTML = """
<div id="block-numero-wordoftheday">
<section class="scene scene--style_corp">
  <header class="scene__top">
    <div class="scene__intro">Wort des Tages</div>
    <h2 class="scene__title">
      <a class="scene__title-link" href="/rechtschreibung/Irrealis">Ir&shy;re&shy;a&shy;lis</a>
    </h2>
    <a href="/rechtschreibung/Irrealis" class="scene__main">Substantiv, maskulin</a>
  </header>
</section>
</div>
"""

DUDEN_ENTRY_HTML = """
<h2>Bedeutung</h2>
<p>Modus des irrealen Wunsches, einer als unwirklich hingestellten Aussage.</p>
"""


async def test_fetch_english_parses_word_and_definition():
    async def handler(request):
        assert request.url.params["action"] == "parse"
        return httpx.Response(200, json={"parse": {"text": {"*": EN_WOTD_HTML}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    entry = await fetch_english(client)

    assert entry.language == "en"
    assert entry.word == "enfranchisement"
    assert entry.part_of_speech == "n"
    # One outer sense; the nested sub-sense is folded into its text rather
    # than listed again.
    assert len(entry.definitions) == 1
    assert "enfranchising" in entry.definitions[0]
    assert "Release from imprisonment" in entry.definitions[0]


# en.wiktionary renders one <ol> per part of speech, so a word with both a
# proper-noun and a common-noun section has senses spread over sibling lists.
EN_WOTD_MULTI_HTML = """
<div class="mw-parser-output">
<div id="WOTD-rss-description">
<ol><li>Mount Megiddo, a hill in modern Israel.</li>
<li>(by extension) The battle itself.</li></ol>
<ol><li>A catastrophic or great conflict.</li>
<li>(chess) A type of chess game.</li></ol>
</div></div>
"""


async def test_fetch_english_keeps_every_sense_across_sibling_lists():
    async def handler(request):
        html = EN_WOTD_MULTI_HTML.replace(
            '<div class="mw-parser-output">',
            '<div class="mw-parser-output"><b><span id="WOTD-rss-title">Armageddon</span></b>',
        )
        return httpx.Response(200, json={"parse": {"text": {"*": html}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    entry = await fetch_english(client)

    assert entry.definitions == [
        "Mount Megiddo, a hill in modern Israel.",
        "(by extension) The battle itself.",
        "A catastrophic or great conflict.",
        "(chess) A type of chess game.",
    ]


async def test_fetch_spanish_combines_daily_and_word_lookup():
    async def handler(request):
        if request.url.path == "/api/daily":
            return httpx.Response(200, json={"ok": True, "data": {"word": "casa"}})
        assert request.url.path == "/api/words/casa"
        return httpx.Response(200, json=RAE_WORDS_CASA)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    entry = await fetch_spanish(client)

    assert entry.language == "es"
    # A noun is served with its article, so it can be learnt as one unit.
    assert entry.word == "la casa"
    assert entry.part_of_speech == "noun, feminine"
    assert entry.definitions == ["Edificio para habitar."]


async def test_fetch_german_scrapes_landing_and_entry_pages():
    async def handler(request):
        if request.url.path == "/wort-des-tages":
            return httpx.Response(200, text=DUDEN_LANDING_HTML)
        assert request.url.path == "/rechtschreibung/Irrealis"
        return httpx.Response(200, text=DUDEN_ENTRY_HTML)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    entry = await fetch_german(client)

    assert entry.language == "de"
    assert entry.word == "der Irrealis"
    assert entry.part_of_speech == "Substantiv, maskulin"
    assert len(entry.definitions) == 1
    assert "irrealen Wunsches" in entry.definitions[0]


# Multi-sense RAE entries split senses across "meanings" (separate
# etymologies), each with its own numbered list.
RAE_WORDS_BANCO = {
    "ok": True,
    "data": {
        "word": "banco",
        "meanings": [
            {
                "senses": [
                    {"category": "noun", "gender": "masculine",
                     "description": "Asiento en que pueden sentarse varias personas."},
                    {"category": "noun", "description": "Conjunto de peces que van juntos."},
                ]
            },
            {"senses": [{"category": "noun", "description": "Establecimiento de credito."}]},
        ],
    },
}


async def test_fetch_spanish_keeps_every_sense_across_meanings():
    async def handler(request):
        if request.url.path == "/api/daily":
            return httpx.Response(200, json={"ok": True, "data": {"word": "banco"}})
        return httpx.Response(200, json=RAE_WORDS_BANCO)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    entry = await fetch_spanish(client)

    assert entry.definitions == [
        "Asiento en que pueden sentarse varias personas.",
        "Conjunto de peces que van juntos.",
        "Establecimiento de credito.",
    ]
    # Part of speech stays a one-line hint taken from the first sense.
    assert entry.part_of_speech == "noun, masculine"


# Trimmed from duden.de/rechtschreibung/Schloss. Multi-sense entries use
# div#bedeutungen with one <li id="Bedeutung-N"> per sense (sub-senses get
# "1a"/"1b"); the examples and idiom blocks beside each sense are not part of
# the definition, and a cross-reference sense carries a dl.tuple instead of
# an enumeration__text div.
DUDEN_ENTRY_MULTI_HTML = """
<h2>Bedeutungen (4)</h2>
<div id="bedeutungen" class="division">
  <ol class="enumeration">
    <li id="Bedeutung-1a" class="enumeration__sub-item">
      <div class="enumeration__text">Vorrichtung zum Verschlie&szlig;en</div>
      <dl class="note"><dt>Beispiele</dt><dd>ein Schloss aufbrechen</dd></dl>
    </li>
    <li id="Bedeutung-1b" class="enumeration__sub-item">
      <figure class="depiction"><figcaption>&copy; MEV Verlag, Augsburg</figcaption></figure>
      <dl class="tuple"><dt>Kurzform f&uuml;r</dt><dd>Vorh&auml;ngeschloss</dd></dl>
    </li>
    <li id="Bedeutung-2" class="enumeration__item">
      <div class="enumeration__text">Schnappverschluss</div>
    </li>
  </ol>
</div>
"""


async def test_fetch_german_keeps_every_sense_and_drops_examples():
    async def handler(request):
        if request.url.path == "/wort-des-tages":
            return httpx.Response(200, text=DUDEN_LANDING_HTML)
        return httpx.Response(200, text=DUDEN_ENTRY_MULTI_HTML)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    entry = await fetch_german(client)

    assert entry.definitions == [
        "Vorrichtung zum Verschließen",
        "Kurzform für Vorhängeschloss",
        "Schnappverschluss",
    ]
    # The example list and the image credit are not definitions.
    joined = " ".join(entry.definitions)
    assert "Beispiele" not in joined
    assert "MEV Verlag" not in joined


@pytest.mark.parametrize(
    "word,gender,expected",
    [
        ("aguachile", "masculine", "el aguachile"),
        ("casa", "feminine", "la casa"),
        # A common-gender noun has no single article, so it shows both.
        ("periodista", "masculine_and_feminine", "el/la periodista"),
        # A feminine noun starting with a stressed /a/ takes "el": stress is
        # on the first syllable either by the penultimate-syllable rule
        # ("a-gua", "ham-bre") or by a written accent ("a-gui-la").
        ("agua", "feminine", "el agua"),
        ("hambre", "feminine", "el hambre"),
        ("\u00e1guila", "feminine", "el \u00e1guila"),
        ("hacha", "feminine", "el hacha"),
        # ...but not when the stress falls later, written accent or not.
        ("aguja", "feminine", "la aguja"),
        ("aldea", "feminine", "la aldea"),
        ("acci\u00f3n", "feminine", "la acci\u00f3n"),
        # Verbs and adjectives have no gender and so get no article.
        ("correr", None, "correr"),
    ],
)
def test_spanish_article(word, gender, expected):
    assert _with_article(word, _spanish_article(word, gender)) == expected


@pytest.mark.parametrize(
    "part_of_speech,expected",
    [
        ("Substantiv, maskulin", "der Wort"),
        ("Substantiv, feminin", "die Wort"),
        ("Substantiv, Neutrum", "das Wort"),
        # Duden files some nouns under more than one gender, and a Pluralwort
        # ("Ferien") under none of them.
        (
            "Substantiv, maskulin, oder Substantiv, feminin, oder Substantiv, Neutrum",
            "der/die/das Wort",
        ),
        ("Pluralwort", "die Wort"),
        # Anything that isn't a noun has no article.
        ("starkes Verb", "Wort"),
        ("Adjektiv", "Wort"),
        (None, "Wort"),
    ],
)
def test_german_article(part_of_speech, expected):
    assert _with_article("Wort", _german_article(part_of_speech)) == expected
