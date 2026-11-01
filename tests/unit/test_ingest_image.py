"""Image ingest tests: data-URL, resolution notes, vision drafting,
anchor validation. Offline: generated PNG bytes + doubles only."""

import base64
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.ingest import PROVENANCE_MISSING  # noqa: E402
from atlas.ingest.image import (  # noqa: E402
    MIN_VISION_TOKENS,
    build_data_url,
    describe_image,
    png_dimensions,
    resolution_note,
    sniff_mime,
)


def _build_png(width=64, height=48, color=(200, 30, 30)):
    rows = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    comp = zlib.compress(rows)

    def chunk(typ, data):
        out = struct.pack(">I", len(data)) + typ + data
        return out + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", comp)
            + chunk(b"IEND", b""))


class _ScriptedVision:
    name = "double-vision"
    available = True

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete_vision(self, system, user_text, urls, max_tokens=2000,
                        json_schema=None):
        self.calls.append({"user_text": user_text, "urls": urls,
                           "max_tokens": max_tokens, "schema": json_schema})
        return self.payload


class _FailingVision:
    name = "double-vision-fail"
    available = True

    def complete_vision(self, *a, **k):
        raise RuntimeError("vision boom")


def test_sniff_and_dimensions():
    png = _build_png(64, 48)
    assert sniff_mime(png) == "image/png"
    assert png_dimensions(png) == (64, 48)
    assert resolution_note(png) == "64x48 px"
    with pytest.raises(Exception):
        sniff_mime(b"nope")


def test_data_url_roundtrip():
    png = _build_png()
    url = build_data_url(png)
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == png


def test_describe_image_anchors_region_and_resolution():
    vision = _ScriptedVision({"claims": [
        {"text": "The image shows a red rectangle.", "span": "red rectangle",
         "region": "full"},
    ]})
    result = describe_image(_build_png(), "red.png", vision)
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim.anchor["region"] == "full"
    assert claim.anchor["resolution"] == "64x48 px"
    assert claim.anchor["span"] == "red rectangle"
    assert claim.actor == "model:double-vision"
    assert result.discarded == []
    call = vision.calls[0]
    assert call["max_tokens"] >= MIN_VISION_TOKENS
    assert call["urls"][0].startswith("data:image/png;base64,")
    assert "64x48 px" in call["user_text"]


def test_describe_image_discards_bad_region_and_span():
    vision = _ScriptedVision({"claims": [
        {"text": "A red rectangle fills the frame.", "span": "red rectangle",
         "region": "everywhere-at-once"},  # outside vocabulary
        {"text": "A red rectangle fills the frame.", "span": "blue circle",
         "region": "full"},  # non-verbatim span
    ]})
    result = describe_image(_build_png(), "red.png", vision)
    assert result.claims == []
    assert result.provenance_missing_count == 2
    assert all(d.code == PROVENANCE_MISSING for d in result.discarded)


def test_describe_image_no_vision_zero_claims():
    result = describe_image(_build_png(), "red.png", vision=None)
    assert result.claims == [] and result.discarded == []
    assert any("no vision client" in u for u in result.uncertainties)


def test_describe_image_vision_floor_and_failure():
    vision = _ScriptedVision({"claims": []})
    result = describe_image(_build_png(), "r.png", vision, max_tokens=500)
    assert any("vision floor" in u for u in result.uncertainties)
    assert vision.calls[0]["max_tokens"] == MIN_VISION_TOKENS

    result = describe_image(_build_png(), "r.png", _FailingVision())
    assert result.claims == []
    assert any("vision call failed" in u for u in result.uncertainties)


def test_describe_image_unreadable():
    result = describe_image(b"junk", "junk.bin", _ScriptedVision({}))
    assert result.claims == []
    assert any("unreadable image" in u for u in result.uncertainties)


def test_committed_fixtures_parse():
    root = Path(__file__).resolve().parents[2] / "eval" / "fixtures"
    for name in ("ingest_chart_a.png", "ingest_chart_b.png"):
        data = (root / name).read_bytes()
        assert resolution_note(data) == "480x320 px"
        assert build_data_url(data).startswith("data:image/png;base64,")
