import json
import re

from playwright.sync_api import expect

from conftest import enroll_device, save_configurations


def test_enroll_device_via_form_lands_on_configure_page(page, live_server):
    page.goto(live_server)

    page.get_by_label("Device identifier").fill("board-e2e")
    page.get_by_role("button", name="Enroll new device").click()

    page.wait_for_url("**/devices/board-e2e/configure")
    expect(page.locator("h1")).to_contain_text("board-e2e")
    # Enrollment pre-populates one blank default configuration row.
    expect(page.locator(".config-row")).to_have_count(1)

    # The device now shows up in the list with configure/dashboard links.
    page.goto(live_server)
    row = page.locator("table.devices tbody tr")
    expect(row).to_have_count(1)
    expect(row.locator("td").first).to_have_text("board-e2e")
    expect(row.get_by_role("link", name="Configure")).to_be_visible()
    expect(row.get_by_role("link", name="View dashboard")).to_be_visible()


def test_delete_device_removes_it_from_list(page, live_server):
    enroll_device(live_server, "board-e2e")

    page.goto(live_server)
    row = page.locator("table.devices tbody tr")
    expect(row).to_have_count(1)

    page.once("dialog", lambda dialog: dialog.accept())
    row.get_by_role("button", name="Delete").click()

    expect(page.locator("table.devices tbody tr")).to_have_count(0)
    expect(page.locator("#no-devices")).to_be_visible()


def test_delete_device_dismissed_keeps_it(page, live_server):
    enroll_device(live_server, "board-e2e")

    page.goto(live_server)
    row = page.locator("table.devices tbody tr")

    page.once("dialog", lambda dialog: dialog.dismiss())
    row.get_by_role("button", name="Delete").click()

    expect(page.locator("table.devices tbody tr")).to_have_count(1)


def test_enroll_duplicate_device_shows_error(page, live_server):
    enroll_device(live_server, "board-e2e")

    page.goto(live_server)
    page.get_by_label("Device identifier").fill("board-e2e")
    page.get_by_role("button", name="Enroll new device").click()

    expect(page.locator("#enroll-status")).to_contain_text("already enrolled")


def test_configure_device_via_station_picker(page, live_server):
    enroll_device(live_server, "board-e2e")
    page.goto(f"{live_server}/devices/board-e2e/configure")

    origin = page.locator(".config-row .origin-input")
    origin.fill("Far")
    options = page.locator(".station-picker-list li")
    expect(options).to_have_count(1)
    expect(options.first).to_have_text("Far Away Ave (A)")
    options.first.click()
    expect(origin).to_have_value("Far Away Ave")

    # The service dropdown is limited to routes serving the origin station.
    service = page.locator(".config-row .service-select")
    expect(service.locator("option")).to_have_text(["A"])

    page.locator(".config-row .direction-select").select_option("downtown")

    page.get_by_role("button", name="Save configurations").click()
    expect(page.locator("#save-status")).to_have_text("Saved.")


def test_duplicate_service_is_rejected_in_page(page, live_server):
    enroll_device(live_server, "board-e2e")
    page.goto(f"{live_server}/devices/board-e2e/configure")

    page.get_by_role("button", name="+ Add configuration").click()
    for row in page.locator(".config-row").all():
        row.locator(".origin-input").fill("Test")
        page.get_by_text("Test Station (A, C)", exact=True).click()
        row.locator(".service-select").select_option("A")
        row.locator(".direction-select").select_option("uptown")

    page.get_by_role("button", name="Save configurations").click()
    expect(page.locator("#save-status")).to_contain_text("one configuration per service")


def test_dashboard_shows_upcoming_departure(page, live_server):
    enroll_device(live_server, "board-e2e")
    save_configurations(
        live_server,
        "board-e2e",
        [{"origin_station_id": "101", "service": "A", "direction": "uptown"}],
    )

    page.goto(f"{live_server}/devices/board-e2e/dashboard")
    board = page.locator(".departures-board")
    expect(board.locator(".row-index").first).to_have_text("1")
    expect(board.locator(".service-bullet").first).to_have_text("A")
    expect(board.locator(".terminus").first).to_have_text("Far Away Ave")
    # The stub train arrives ~5 minutes after server start.
    expect(board.locator(".minutes").first).to_contain_text(re.compile(r"^[45]"))


def test_dashboard_debug_pretty_prints_api_json(page, live_server):
    enroll_device(live_server, "board-e2e")
    save_configurations(
        live_server,
        "board-e2e",
        [{"origin_station_id": "101", "service": "A", "direction": "uptown"}],
    )

    page.goto(f"{live_server}/devices/board-e2e/dashboard?debug=1")
    # The board still renders...
    expect(page.locator(".departures-board .terminus").first).to_have_text("Far Away Ave")
    # ...with the exact API payload pretty-printed below it.
    debug = page.locator(".debug-json")
    expect(debug).to_contain_text('"service": "A"')
    expect(debug).to_contain_text('"terminus": "Far Away Ave"')
    expect(debug).to_contain_text('"arrives_at":')


def test_dashboard_updates_last_request_timestamp(page, live_server):
    enroll_device(live_server, "board-e2e")
    save_configurations(
        live_server,
        "board-e2e",
        [{"origin_station_id": "101", "service": "A", "direction": "uptown"}],
    )

    page.goto(f"{live_server}/devices/board-e2e/dashboard")
    expect(page.locator(".departures-board .terminus").first).to_have_text("Far Away Ave")

    page.goto(live_server)
    # Polling the API as the device stamped the row, so it no longer says "never".
    expect(page.locator("table.devices tbody td").nth(1)).not_to_have_text("never")


# The real cache scrapes Wiktionary and friends; stub the endpoint in the
# browser so these exercise the rendering, not the network.
WOTD_FIXTURE = {
    "en": {
        "language": "en",
        "date": "2026-09-26",
        "word": "susurrus",
        "part_of_speech": "noun",
        "definitions": ["A whispering or rustling sound."],
        "source_url": "https://en.wiktionary.org/wiki/susurrus",
    },
    "es": {
        "language": "es",
        "date": "2026-09-26",
        "word": "la madrugada",
        "part_of_speech": "sustantivo",
        "definitions": [
            "Las primeras horas despu\u00e9s de la medianoche.",
            "Vigilia desde despu\u00e9s de la medianoche hasta el amanecer.",
        ],
        "source_url": "https://dle.rae.es/madrugada",
    },
    "de": {
        "language": "de",
        "date": "2026-09-26",
        "word": "der Ohrwurm",
        "part_of_speech": "Substantiv",
        "definitions": ["Eine Melodie, die einem nicht mehr aus dem Kopf geht."],
        "source_url": "https://www.duden.de/rechtschreibung/Ohrwurm",
    },
}


def _stub_word_of_the_day(page, payload):
    page.route(
        "**/public/word-of-the-day",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )


def test_devices_page_shows_word_of_the_day(page, live_server):
    _stub_word_of_the_day(page, WOTD_FIXTURE)
    page.goto(live_server)

    entries = page.locator("#wotd .wotd-entry")
    expect(entries).to_have_count(3)

    english = entries.nth(0)
    expect(english).to_contain_text("English")
    expect(english.locator(".wotd-word")).to_contain_text("susurrus")
    expect(english.locator(".wotd-pos")).to_have_text("noun")
    # A single-sense word renders as a plain paragraph, not a one-item list.
    expect(english.locator(".wotd-definition")).to_have_text(
        "A whispering or rustling sound."
    )
    expect(english.locator(".wotd-definitions")).to_have_count(0)
    expect(english.get_by_role("link", name="source")).to_have_attribute(
        "href", "https://en.wiktionary.org/wiki/susurrus"
    )

    # Spanish and German nouns arrive with their article already attached.
    # .wotd-word nests the part of speech, so match the word within it.
    expect(entries.nth(1).locator(".wotd-word")).to_contain_text("la madrugada")
    expect(entries.nth(2).locator(".wotd-word")).to_contain_text("der Ohrwurm")


def test_word_of_the_day_missing_language_still_shows_the_others(page, live_server):
    # The API returns null for a language whose source failed; the page has to
    # surface that without losing the two that worked.
    _stub_word_of_the_day(page, {**WOTD_FIXTURE, "de": None})
    page.goto(live_server)

    entries = page.locator("#wotd .wotd-entry")
    expect(entries).to_have_count(3)
    expect(entries.nth(0).locator(".wotd-word")).to_contain_text("susurrus")
    expect(entries.nth(2)).to_contain_text("Source fetch failed.")
    expect(entries.nth(2).locator(".wotd-word")).to_have_count(0)


def test_word_of_the_day_endpoint_failure_is_reported(page, live_server):
    page.route(
        "**/public/word-of-the-day",
        lambda route: route.fulfill(status=503, body="upstream down"),
    )
    page.goto(live_server)

    expect(page.locator("#wotd")).to_contain_text("Could not load the word of the day")
    # The rest of the page still works.
    expect(page.locator("#enroll-form")).to_be_visible()


def test_word_of_the_day_shows_every_sense_of_a_multi_sense_word(page, live_server):
    # The page scrolls, so it shows all senses rather than picking one the way
    # a fixed-size device display has to.
    _stub_word_of_the_day(page, WOTD_FIXTURE)
    page.goto(live_server)

    spanish = page.locator("#wotd .wotd-entry").nth(1)
    senses = spanish.locator(".wotd-definitions li")
    expect(senses).to_have_count(2)
    expect(senses.nth(0)).to_have_text("Las primeras horas después de la medianoche.")
    expect(senses.nth(1)).to_have_text(
        "Vigilia desde después de la medianoche hasta el amanecer."
    )
