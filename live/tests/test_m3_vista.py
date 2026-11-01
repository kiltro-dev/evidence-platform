"""M3 vista tests: frames + read + xcheck. Doubles only — 0 live calls.

Frames run over a synthetic clip built in tmp (2 matplotlib PNGs +
imageio_ffmpeg, never the live keynote); read uses FakeVision doubles;
xcheck is pure code incl. the planted F6 case (said 40%, shown 0.97%).
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.vista.frames import (  # noqa: E402
    closest_frame,
    extract_frames,
    ffmpeg_exe,
    load_srt,
    slide_at,
)
from live.vista.read import (  # noqa: E402
    READ_SCHEMA,
    build_data_url,
    read_frame,
    sniff_mime,
    validate_read,
)
from live.vista.xcheck import (  # noqa: E402
    chart_numbers,
    cross_check,
    extract_numbers,
    rel_diff,
)


def _slide_png(path, color, label):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    fig.patch.set_facecolor(color)
    ax.axis("off")
    ax.text(0.5, 0.5, label, ha="center", va="center", fontsize=40)
    fig.savefig(path, dpi=50)
    plt.close(fig)


def _make_clip(tmp_path):
    """6s synthetic clip: 3s slate A + hard cut + 3s slate B (no audio).

    Saturated slates (scene score needs full-strength color steps) joined
    with the concat filter + re-encode (same proven pattern as
    test_ingest.py: stream-copy joins score near-zero scene).
    """
    a = str(tmp_path / "a.png")
    b = str(tmp_path / "b.png")
    _slide_png(a, "#ff0000", "SLATE A")
    _slide_png(b, "#0000ff", "SLATE B")
    clip = str(tmp_path / "clip.mp4")
    r = subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error",
         "-loop", "1", "-framerate", "10", "-t", "3", "-i", a,
         "-loop", "1", "-framerate", "10", "-t", "3", "-i", b,
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "mpeg4", clip],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode == 0, r.stderr.decode()[-300:]
    return clip


def test_frames_synthetic_clip_scene_and_periodic(tmp_path):
    clip = _make_clip(tmp_path)
    out = str(tmp_path / "frames")
    frames = extract_frames(clip, out, periodic_s=5.0)
    assert len(frames) >= 2  # t=0 heartbeat + hard cut at t=3
    for f in frames:
        assert set(f) == {"path", "t_sec", "slide"}
        assert os.path.exists(f["path"])
    times = [f["t_sec"] for f in frames]
    assert times == sorted(times)
    assert min(times) < 0.5
    assert any(2.5 < t < 3.5 for t in times)  # scene cut caught
    assert all(f["slide"] is None for f in frames)  # no SRT alongside


def test_frames_slide_mapping_from_srt(tmp_path):
    clip = _make_clip(tmp_path)
    srt = tmp_path / "clip.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\n slate A\n\n"
        "2\n00:00:03,000 --> 00:00:06,000\n slate B\n\n")
    cues = load_srt(str(srt))
    assert len(cues) == 2
    assert slide_at(0.1, cues) == 1
    assert slide_at(3.0, cues) == 2
    assert slide_at(99.0, cues) == 2
    out = str(tmp_path / "frames")
    frames = extract_frames(clip, out, periodic_s=5.0, srt_path=str(srt))
    slides = {f["slide"] for f in frames}
    assert slides <= {1, 2}
    assert slides == {1, 2}
    mid = closest_frame(frames, 4.5)
    assert mid is not None and mid["slide"] == 2
    assert closest_frame([], 1.0) is None


def test_frames_missing_video_raises(tmp_path):
    try:
        extract_frames(str(tmp_path / "nope.mp4"), str(tmp_path / "o"))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")


class FakeVision:
    """Test double for the vision client (complete_vision interface)."""

    name = "fake-vision"
    available = True

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def complete_vision(self, system, user_text, image_data_urls,
                        max_tokens=2000, json_schema=None):
        self.calls.append({
            "system": system[:40],
            "user": user_text,
            "n_images": len(image_data_urls),
            "max_tokens": max_tokens,
            "schema": (json_schema or {}).get("name"),
        })
        return self._script.pop(0)


RECORDED_TRUNCATED = {
    "on_screen_text": "The Growth Illusion US GDP 2023: 27.8 trillion",
    "tables": [],
    "charts": [{
        "kind": "bar",
        "title": "US GDP doubled in one year (!)",
        "values": [{"label": "2023", "value": 27.8},
                   {"label": "2024", "value": 29.3}],
        "unit": "trillion dollars",
        "y_axis_min": 27.0,
        "y_axis_max": 30.0,
        "truncated_axis": True,
        "notes": "axis starts at 27, +5% looks like doubling",
    }],
}

RECORDED_MESSY = {
    "on_screen_text": "Percent Games",
    "tables": [{"caption": "t", "headers": ["a"], "rows": [["1"]]}],
    "charts": [
        "not-a-chart",
        {"kind": "weird-kind", "title": "World population growth, last year",
         "values": [{"label": "Last year", "value": 0.97},
                    {"label": "bad", "value": "N/A"},
                    {"label": "bool", "value": True}],
         "unit": "percent", "y_axis_min": 0, "y_axis_max": 5,
         "truncated_axis": False, "notes": ""},
    ],
}


def _frame_at(path, t_sec=97.1, slide=6):
    return {"path": path, "t_sec": t_sec, "slide": slide}


def test_read_double_strict_schema(tmp_path):
    png = str(tmp_path / "f.png")
    _slide_png(png, "#101820", "X")
    frame = _frame_at(png)
    out = read_frame(frame, vision=FakeVision([RECORDED_TRUNCATED]))
    assert out["frame"] == png and out["t_sec"] == 97.1 and out["slide"] == 6
    assert out["model"] == "fake-vision"
    assert out["uncertainties"] == []
    assert len(out["charts"]) == 1
    chart = out["charts"][0]
    assert chart["truncated_axis"] is True
    assert chart["values"] == [{"label": "2023", "value": 27.8},
                               {"label": "2024", "value": 29.3}]
    assert chart["frame"] == png and chart["t_sec"] == 97.1  # visual citation


def test_read_double_malformed_dropped(tmp_path):
    png = str(tmp_path / "f.png")
    _slide_png(png, "#101820", "X")
    out = read_frame(_frame_at(png, 113.0, 7),
                     vision=FakeVision([RECORDED_MESSY]))
    assert len(out["charts"]) == 1  # non-object dropped, noted
    chart = out["charts"][0]
    assert chart["kind"] == "other"  # unknown kind coerced
    assert chart["values"] == [{"label": "Last year", "value": 0.97}]
    assert any("dropped 1" in u for u in out["uncertainties"])
    assert len(out["tables"]) == 1
    assert out["tables"][0]["t_sec"] == 113.0


def test_read_no_vision_degrades(tmp_path):
    png = str(tmp_path / "f.png")
    _slide_png(png, "#101820", "X")
    out = read_frame(_frame_at(png), vision=None)
    assert out["charts"] == [] and out["tables"] == []
    assert any("no vision" in u for u in out["uncertainties"])
    # Unreadable file degrades too (never raises, never guesses).
    out = read_frame(_frame_at(str(tmp_path / "missing.png")),
                     vision=FakeVision([RECORDED_TRUNCATED]))
    assert out["charts"] == []
    assert any("unreadable" in u for u in out["uncertainties"])


def test_read_schema_shape_and_data_url(tmp_path):
    assert READ_SCHEMA["name"] == "vista_frame_read"
    assert set(READ_SCHEMA["schema"]["required"]) == {
        "on_screen_text", "tables", "charts"}
    png = tmp_path / "f.png"
    _slide_png(str(png), "#101820", "X")
    raw = png.read_bytes()
    assert sniff_mime(raw) == "image/png"
    assert build_data_url(raw).startswith("data:image/png;base64,")
    try:
        sniff_mime(b"junk-bytes")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for junk bytes")


def test_validate_read_non_object():
    out = validate_read([1, 2], {"path": "p", "t_sec": 3.0})
    assert out["charts"] == [] and out["uncertainties"]


def _visual(t_sec, charts):
    return {"frame": f"/tmp/vista-{t_sec:06.0f}.png", "t_sec": t_sec,
            "slide": None, "charts": charts, "tables": []}


def _chart(title, values, unit="percent", truncated=False):
    return {"kind": "bar", "title": title,
            "values": [{"label": lb, "value": v} for lb, v in values],
            "unit": unit, "y_axis_min": None, "y_axis_max": None,
            "truncated_axis": truncated, "notes": ""}


def test_xcheck_match_within_tolerance():
    speech = [{"text": "In 2023, American GDP was 27.8 trillion dollars.",
               "t_sec": 95.0}]
    visuals = [_visual(97.1, [_chart(
        "US GDP", [("2023", 27.8), ("2024", 29.3)],
        unit="trillion dollars", truncated=True)])]
    res = cross_check(speech, visuals)
    assert res["stats"]["n_match"] == 1
    assert res["stats"]["n_mismatch"] == 0 and res["events"] == []
    assert res["matches"][0]["rel_diff"] == 0.0


def test_xcheck_f6_planted_mismatch_40_vs_097():
    """Planted F6: speaker says ~40%, chart shows 0.97% -> visual.mismatch."""
    speech = [{"text": "Last year alone, world population grew by nearly 40 percent.",
               "t_sec": 115.0,
               "numbers": [{"value": 40.0, "unit": "percent"}]}]
    visuals = [_visual(113.7, [_chart(
        "World population growth, last year", [("Last year", 0.97)])])]
    res = cross_check(speech, visuals)
    assert res["stats"]["n_mismatch"] == 1
    assert res["stats"]["n_match"] == 0
    assert len(res["events"]) == 1
    ev = res["events"][0]
    assert ev["type"] == "visual.mismatch"
    assert ev["said"]["value"] == 40.0
    assert ev["shown"]["value"] == 0.97
    assert ev["rel_diff"] > 0.9


def test_xcheck_truncated_axis_trend_flag():
    speech = [{"text": "One year later it had doubled, as this chart clearly shows.",
               "t_sec": 99.0}]
    visuals = [_visual(97.1, [_chart(
        "US GDP", [("2023", 27.8)], unit="trillion dollars", truncated=True)])]
    res = cross_check(speech, visuals)
    assert res["stats"]["n_flags"] == 1
    flag = res["flags"][0]
    assert flag["type"] == "visual.misleading_axis"
    assert flag["cue"] == "doubled"
    assert any(e["type"] == "visual.misleading_axis" for e in res["events"])
    # Same words, honest axis -> no flag.
    honest = [_visual(97.1, [_chart("US GDP", [("2023", 27.8)],
                                    unit="trillion dollars")])]
    res2 = cross_check(speech, honest)
    assert res2["stats"]["n_flags"] == 0


def test_xcheck_tolerance_boundary():
    speech = [{"text": "Growth was 1.0 percent.", "t_sec": 10.0,
               "numbers": [{"value": 1.0, "unit": "percent"}]}]
    close = [_visual(10.0, [_chart("g", [("a", 1.01)])])]  # 1% off -> match
    far = [_visual(10.0, [_chart("g", [("a", 1.05)])])]  # 5% off -> mismatch
    assert cross_check(speech, close)["stats"]["n_match"] == 1
    assert cross_check(speech, far)["stats"]["n_mismatch"] == 1
    assert rel_diff(0.0, 0.0) == 0.0


def test_xcheck_unit_guard_and_window():
    # 8 billion (count) vs a percent chart -> skipped, never a mismatch.
    speech = [{"text": "Humanity passed 8 billion people.", "t_sec": 105.0}]
    visuals = [_visual(113.7, [_chart("growth", [("Last year", 0.97)])])]
    res = cross_check(speech, visuals)
    assert res["stats"]["n_skipped_no_chart"] == 1
    assert res["stats"]["n_mismatch"] == 0 and res["events"] == []
    # Same units but outside the ±15s window -> skipped too.
    speech2 = [{"text": "Growth was 40 percent.", "t_sec": 10.0,
                "numbers": [{"value": 40.0, "unit": "percent"}]}]
    res2 = cross_check(speech2, visuals)
    assert res2["stats"]["n_skipped_no_chart"] == 1
    assert res2["events"] == []


def test_extract_numbers_units_and_years():
    nums = extract_numbers("Last year alone, world population grew by nearly 40 percent.")
    assert [(n["base"], n["uclass"]) for n in nums] == [(40.0, "percent")]
    nums = extract_numbers("The United States produced 29.3 trillion dollars of GDP in 2024.")
    assert [(n["base"], n["uclass"]) for n in nums] == [(29.3e12, "money")]
    nums = extract_numbers("China is home to 1.41 billion people.")
    assert [(n["base"], n["uclass"]) for n in nums] == [(1.41e9, "count")]
    assert chart_numbers(_chart("g", [("a", 27.8)], unit="trillion dollars")) == [
        {"label": "a", "value": 27.8e12, "uclass": "money"}]
