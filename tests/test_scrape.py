"""
Tests for Chirp scraping logic.

These tests are designed to run without a live browser or Selenium installation.
All Selenium names used inside core.py are patched at the module level before
any calls into _parse_items or scrape_chirp are made.
"""

from unittest.mock import MagicMock, patch

from selenium.common.exceptions import StaleElementReferenceException

from chirp_to_libib.core import _parse_items, scrape_chirp

# A lightweight stand-in for selenium.webdriver.common.by.By
_MockBy = type("By", (), {"CSS_SELECTOR": "css selector", "XPATH": "xpath"})


def _mock_text_element(text):
    """A MagicMock WebElement stand-in for a text-bearing element (title,
    byline). core.py's _element_text() helper reads get_attribute
    ("textContent") first, falling back to .text — a plain
    MagicMock(text=...) would return a truthy (bogus) MagicMock from
    get_attribute() and never fall back, so this explicitly returns None
    from get_attribute to exercise the .text fallback the same way these
    tests were written before _element_text existed."""
    el = MagicMock()
    el.get_attribute.return_value = None
    el.text = text
    return el


# Patch targets: all Selenium names used inside chirp_to_libib.core at call time
_SELENIUM_PATCHES = {
    "chirp_to_libib.core.By": _MockBy,
    "chirp_to_libib.core.WebDriverWait": MagicMock(),
    "chirp_to_libib.core.EC": MagicMock(),
}


# ==========================
# PARSE ITEMS TESTS
# ==========================


@patch.dict("chirp_to_libib.core.__dict__", _SELENIUM_PATCHES)
def test_parse_items_basic():
    item = MagicMock()
    img_mock = MagicMock()
    # srcset=None → _extract_cover_url falls through to src
    img_mock.get_attribute.side_effect = lambda attr: (
        None if attr == "srcset" else "http://example.com/cover.jpg"
    )
    item.find_element.side_effect = [
        _mock_text_element("Test Title"),  # title element
        _mock_text_element("By Author Name"),  # byline element
        img_mock,  # cover image element
    ]
    result = _parse_items([item])
    assert len(result) == 1
    assert result[0][0] == "Test Title"
    assert result[0][1] == "Author Name"
    assert result[0][2] == "http://example.com/cover.jpg"


@patch.dict("chirp_to_libib.core.__dict__", _SELENIUM_PATCHES)
def test_parse_items_missing_author():
    item = MagicMock()
    img_mock = MagicMock()
    img_mock.get_attribute.side_effect = lambda attr: (
        None if attr == "srcset" else "http://example.com/cover.jpg"
    )
    item.find_element.side_effect = [
        _mock_text_element("Test Title"),
        Exception("no byline element"),
        img_mock,
    ]
    result = _parse_items([item])
    assert result[0][1] == ""


@patch.dict("chirp_to_libib.core.__dict__", _SELENIUM_PATCHES)
def test_parse_items_missing_cover():
    item = MagicMock()
    item.find_element.side_effect = [
        _mock_text_element("Test Title"),
        _mock_text_element("By Author"),
        Exception("no primary cover img"),
        Exception("no fallback cover img"),
    ]
    result = _parse_items([item])
    assert result[0][2] == ""


# ==========================
# SCRAPE TESTS (MOCKED SELENIUM)
# ==========================


@patch("chirp_to_libib.core._login")
@patch("chirp_to_libib.core._build_driver")
@patch.dict("chirp_to_libib.core.__dict__", _SELENIUM_PATCHES)
def test_scrape_chirp_basic(mock_build_driver, mock_login):
    driver = MagicMock()
    mock_build_driver.return_value = driver

    with patch(
        "chirp_to_libib.core._parse_items",
        return_value=[("Title A", "Author A", "coverA")],
    ):
        result = scrape_chirp("email", "password", max_pages=1)

    assert len(result) == 1
    assert result[0][0] == "Title A"
    assert result[0][1] == "Author A"


@patch("chirp_to_libib.core._login")
@patch("chirp_to_libib.core._build_driver")
@patch.dict("chirp_to_libib.core.__dict__", _SELENIUM_PATCHES)
def test_scrape_chirp_no_items(mock_build_driver, mock_login):
    driver = MagicMock()
    mock_build_driver.return_value = driver
    driver.find_elements.return_value = []

    with patch("chirp_to_libib.core._parse_items", return_value=[]):
        result = scrape_chirp("email", "password", max_pages=1)

    assert result == []


@patch("chirp_to_libib.core._login")
@patch("chirp_to_libib.core._build_driver")
@patch("chirp_to_libib.core.WebDriverWait")
@patch.dict("chirp_to_libib.core.__dict__", {"By": _MockBy})
def test_scrape_chirp_waits_for_page_content_to_change_before_advancing(
    mock_wait_cls, mock_build_driver, mock_login
):
    """Regression test: clicking Next on Chirp's React-rendered grid doesn't
    guarantee the old page's items are gone (or even that the DOM node at
    list position 1 gets replaced rather than mutated in place) by the time
    the loop comes back around — without confirming the content actually
    changed, the next iteration's presence-of-element wait can resolve
    instantly against old, still-present items, silently re-scraping the
    same page. A prior version of this fix used EC.staleness_of() on the
    first old item, which turned out not to work live: it timed out even
    though the page had genuinely changed, meaning the site reuses/mutates
    that DOM node rather than unmounting it. This tests the replacement —
    comparing rendered text at that position — by pulling the actual
    condition function passed to WebDriverWait.until() and calling it
    directly against fake before/after driver states.

    Uses direct patch() on WebDriverWait rather than the module's usual
    _SELENIUM_PATCHES/patch.dict("module.__dict__", ...) convention — that
    convention does not actually override chirp_to_libib.core.WebDriverWait
    (verified directly: the real class remains bound inside the patch.dict
    context), which every other test in this file gets away with only
    because the *real* WebDriverWait.until() ends up polling a condition
    that's a MagicMock call chain — always truthy on the first check,
    regardless of the driver.
    """
    driver = MagicMock()
    mock_build_driver.return_value = driver
    mock_wait_cls.return_value.until.return_value = True

    page1_item = _mock_text_element("Title A by Author A")
    page2_item = _mock_text_element("Title B by Author B")
    next_button = MagicMock(name="next_button")

    driver.find_elements.side_effect = [
        [page1_item],  # page 1's book items (XPath)
        [next_button],  # page 1's next-page button is present
        [page2_item],  # page 2's book items (XPath)
        [],  # no next-page button on page 2 — stop
    ]

    with patch(
        "chirp_to_libib.core._parse_items",
        side_effect=[
            [("Title A", "Author A", "coverA")],
            [("Title B", "Author B", "coverB")],
        ],
    ):
        result = scrape_chirp("email", "password", max_pages=None)

    assert len(result) == 2
    next_button.click.assert_called_once()

    # WebDriverWait.until() is also called for the ordinary presence-of-
    # element wait at the top of each loop iteration (real EC here, since
    # only WebDriverWait is mocked) — three calls total: page 1's presence
    # wait, this fix's page-advanced wait, page 2's presence wait. Identify
    # ours by name rather than position, since it's the one condition that
    # isn't EC's own generated predicate.
    calls = mock_wait_cls.return_value.until.call_args_list
    assert len(calls) == 3
    condition = calls[1].args[0]
    assert condition.__name__ == "_page_advanced"

    still_old_driver = MagicMock()
    still_old_driver.find_elements.return_value = [page1_item]
    assert condition(still_old_driver) is False

    advanced_driver = MagicMock()
    advanced_driver.find_elements.return_value = [page2_item]
    assert condition(advanced_driver) is True


@patch("chirp_to_libib.core._login")
@patch("chirp_to_libib.core._build_driver")
@patch("chirp_to_libib.core.WebDriverWait")
@patch.dict("chirp_to_libib.core.__dict__", {"By": _MockBy})
def test_scrape_chirp_page_advanced_condition_survives_stale_element(
    mock_wait_cls, mock_build_driver, mock_login
):
    """Regression test for a real crash: WebDriverWait.until() only ignores
    NoSuchElementException by default, not StaleElementReferenceException —
    so a condition function that lets a stale-element read propagate crashes
    the whole job immediately (confirmed live: this happened on page 7 of a
    real 27-page Chirp scrape) instead of just being treated as "not yet
    advanced, keep polling." Verifies the actual condition function handles
    this rather than merely asserting a wait happened."""
    driver = MagicMock()
    mock_build_driver.return_value = driver
    mock_wait_cls.return_value.until.return_value = True

    page1_item = _mock_text_element("Title A by Author A")
    next_button = MagicMock(name="next_button")

    driver.find_elements.side_effect = [
        [page1_item],  # page 1's book items
        [next_button],  # page 1's next-page button is present
        [],  # page 2's book items (empty — don't care for this test)
        [],  # no next-page button on page 2 — stop
    ]

    with patch(
        "chirp_to_libib.core._parse_items",
        side_effect=[[("Title A", "Author A", "coverA")], []],
    ):
        result = scrape_chirp("email", "password", max_pages=None)

    assert len(result) == 1

    calls = mock_wait_cls.return_value.until.call_args_list
    condition = next(c.args[0] for c in calls if c.args[0].__name__ == "_page_advanced")

    # Any interaction with a stale WebElement raises in real Selenium — not
    # just .text — so make get_attribute (the first thing _element_text
    # calls) raise, exercising the exception regardless of internal order.
    stale_item = MagicMock(name="stale_item")
    stale_item.get_attribute.side_effect = StaleElementReferenceException(
        "stale element reference"
    )
    stale_driver = MagicMock()
    stale_driver.find_elements.return_value = [stale_item]

    # Must not raise — a stale read mid-poll means "not advanced yet."
    assert condition(stale_driver) is False


@patch("chirp_to_libib.core._login")
@patch("chirp_to_libib.core._build_driver")
@patch("chirp_to_libib.core.WebDriverWait")
@patch.dict("chirp_to_libib.core.__dict__", {"By": _MockBy})
def test_scrape_chirp_skips_wait_when_capturing_marker_text_is_stale(
    mock_wait_cls, mock_build_driver, mock_login
):
    """If reading the first item's text right before the click already hits
    a StaleElementReferenceException, there's no valid marker to wait for —
    should fall back to not waiting at all rather than crashing."""
    driver = MagicMock()
    mock_build_driver.return_value = driver
    mock_wait_cls.return_value.until.return_value = True

    stale_item = MagicMock(name="stale_item")
    stale_item.get_attribute.side_effect = StaleElementReferenceException(
        "stale element reference"
    )
    next_button = MagicMock(name="next_button")

    driver.find_elements.side_effect = [
        [stale_item],  # page 1's book items — but it's stale
        [next_button],  # page 1's next-page button is present
        [],  # page 2's book items (empty — don't care for this test)
        [],  # no next-page button on page 2 — stop
    ]

    with patch(
        "chirp_to_libib.core._parse_items",
        side_effect=[[("Title A", "Author A", "coverA")], []],
    ):
        result = scrape_chirp("email", "password", max_pages=None)

    assert len(result) == 1
    next_button.click.assert_called_once()

    # Only the two ordinary presence-of-element waits happened — no
    # _page_advanced wait, since there was no valid marker text to compare.
    calls = mock_wait_cls.return_value.until.call_args_list
    assert not any(c.args[0].__name__ == "_page_advanced" for c in calls)
