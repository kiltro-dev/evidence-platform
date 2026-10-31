"""Live connector tests. Require network; not part of the offline gate."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.connectors.openalex import search_works  # noqa: E402
from atlas.connectors.worldbank import fetch_observations  # noqa: E402

pytestmark = pytest.mark.live


def test_worldbank_gdp_usa_2023():
    rows = fetch_observations("NY.GDP.MKTP.CD", "USA", 2023, 2023)
    assert len(rows) == 1
    assert rows[0]["value"] > 20e12  # sanity bound, not an exact pin
    assert rows[0]["countryiso3code"] == "USA"


def test_worldbank_missing_country_returns_empty():
    rows = fetch_observations("NY.GDP.MKTP.CD", "XXX", 2023, 2023)
    assert rows == []


def test_openalex_search_returns_metadata():
    works = search_works("speed of light measurement", per_page=3)
    assert len(works) >= 1
    w = works[0]
    assert w.get("title")
    assert isinstance(w.get("publication_year"), int)
