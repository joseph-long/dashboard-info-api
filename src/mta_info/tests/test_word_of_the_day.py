import httpx

from mta_info.word_of_the_day import fetch_english, fetch_german, fetch_spanish

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
    assert "enfranchising" in entry.definition


async def test_fetch_spanish_combines_daily_and_word_lookup():
    async def handler(request):
        if request.url.path == "/api/daily":
            return httpx.Response(200, json={"ok": True, "data": {"word": "casa"}})
        assert request.url.path == "/api/words/casa"
        return httpx.Response(200, json=RAE_WORDS_CASA)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    entry = await fetch_spanish(client)

    assert entry.language == "es"
    assert entry.word == "casa"
    assert entry.part_of_speech == "noun, feminine"
    assert entry.definition == "Edificio para habitar."


async def test_fetch_german_scrapes_landing_and_entry_pages():
    async def handler(request):
        if request.url.path == "/wort-des-tages":
            return httpx.Response(200, text=DUDEN_LANDING_HTML)
        assert request.url.path == "/rechtschreibung/Irrealis"
        return httpx.Response(200, text=DUDEN_ENTRY_HTML)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    entry = await fetch_german(client)

    assert entry.language == "de"
    assert entry.word == "Irrealis"
    assert entry.part_of_speech == "Substantiv, maskulin"
    assert "irrealen Wunsches" in entry.definition
