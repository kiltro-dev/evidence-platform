"""S4 synth-QA tests: metrics, Wilson, manifest, review doubles, E1 scoring.

All offline (0 live calls, 0 ffmpeg): report metrics are exact on
fixtures, Wilson matches tabulated values, the manifest schema is
checked over a tmp repo root, review runs against a fake ffmpeg
runner + a fake vision client, and the E1 code-scoring helpers run on
static truth-shaped rows.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.auditor.wilson import summarize, wilson_interval  # noqa: E402
from live.synthqa import PIECES, media_paths  # noqa: E402
from live.synthqa import bench as B  # noqa: E402
from live.synthqa import report as R  # noqa: E402
from live.synthqa import review as Q  # noqa: E402

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

FF_I = """ffmpeg version 7.0.2
Input #0, mov,mp4, from 'k1_econ.mp4':
  Duration: 00:04:27.04, start: 0.000000, bitrate: 1200 kb/s
  Stream #0:0: Video: h264 1920x1080 [SAR 1:1 DAR 16:9], 30 fps, 30 tbr
  Stream #0:1: Audio: aac 44100 Hz, stereo
"""

FF_VOL = """[Parsed_volumedetect_0] mean_volume: -18.4 dB
[Parsed_volumedetect_0] max_volume: -3.1 dB
"""

SRT_OK = ("1\n00:00:00,000 --> 00:01:00,000\nHello world.\n\n"
          "2\n00:01:00,000 --> 00:02:00,000\nSecond cue here.\n")


# ---------------------------------------------------------------------------
# Wilson (tabulated, z=1.96)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("k,n,lo,hi", [
    (8, 9, 0.565, 0.980),
    (0, 10, 0.000, 0.278),
    (5, 10, 0.237, 0.763),
])
def test_wilson_tabulated(k, n, lo, hi):
    got_lo, got_hi = wilson_interval(k, n)
    assert got_lo == pytest.approx(lo, abs=0.002)
    assert got_hi == pytest.approx(hi, abs=0.002)


def test_wilson_empty_is_maximal_uncertainty():
    assert wilson_interval(0, 0) == (0.0, 1.0)


# ---------------------------------------------------------------------------
# Report metrics exact on fixtures
# ---------------------------------------------------------------------------

def joined_fixture():
    return [
        {"claim_id": "a1", "piece": "k1_econ", "lang": "en", "kind": "k",
         "speakers": 1, "category": "a", "expected": "true",
         "verdict": "supported", "latency_s": 1.0},
        {"claim_id": "a2", "piece": "k1_econ", "lang": "en", "kind": "k",
         "speakers": 1, "category": "a", "expected": "true",
         "verdict": "contradicted", "latency_s": 2.0},
        {"claim_id": "c1", "piece": "p1_panel", "lang": "en", "kind": "p",
         "speakers": 2, "category": "c", "expected": "false",
         "verdict": "contradicted", "latency_s": 3.0},
        {"claim_id": "d1", "piece": "p1_panel", "lang": "en", "kind": "p",
         "speakers": 2, "category": "d", "expected": "false",
         "verdict": "unverifiable", "latency_s": 4.0},
        {"claim_id": "e1", "piece": "clase_economia", "lang": "es",
         "kind": "c", "speakers": 1, "category": "e",
         "expected": "false", "verdict": "supported", "latency_s": 5.0},
    ]


def test_slice_panel_exact():
    panel = R.slice_panel(joined_fixture())
    assert panel["n"] == 5
    assert panel["decided"] == 4  # d1 abstained
    assert panel["correct"] == 2  # a1, c1
    assert panel["accuracy"] == pytest.approx(0.5)
    assert panel["wilson_lo"] == pytest.approx(
        wilson_interval(2, 4)[0], abs=1e-4)
    assert panel["wilson_hi"] == pytest.approx(
        wilson_interval(2, 4)[1], abs=1e-4)
    assert panel["abstention_rate"] == pytest.approx(0.2)
    assert panel["recall"] == pytest.approx(0.4)  # 2/5 incl. abstention
    assert panel["recall_true"] == pytest.approx(0.5)  # 1/2
    assert panel["recall_false"] == pytest.approx(1 / 3, abs=1e-4)  # 1/3
    assert panel["falsos_contradicted"] == ["a2"]
    assert panel["falsos_supported"] == ["e1"]
    assert panel["n_disputed"] == 0


def test_summarize_world_slices_and_excluded(tmp_path):
    bench_results = {
        "pieces": {
            "k1_econ": {
                "excluded": {"coherent_internal": 0, "unverifiable": 5},
                "world_run": {"total_cost_usd": 0.25, "per_claim": [
                    {"claim_id": "a1", "expected": "true",
                     "verdict": "supported", "latency_s": 1.0},
                    {"claim_id": "a2", "expected": "true",
                     "verdict": "contradicted", "latency_s": 2.0}]}},
            "e1_earn": {
                "excluded": {"coherent_internal": 29, "unverifiable": 4,
                             "earnings_false_internal": 7},
                "world_run": None,
                "e1_internal": {"n_scored": 36, "n_pass": 36,
                                "coherence": 1.0}},
        }}
    repo = tmp_path / "repo"
    truth_dir = repo / "live" / "material" / "truth_en"
    truth_dir.mkdir(parents=True)
    (truth_dir / "k1_econ.json").write_text(json.dumps([
        {"id": "a1", "category": "a"}, {"id": "a2", "category": "a"}]),
        encoding="utf-8")
    out = R.summarize_world(bench_results, repo_root=str(repo))
    assert out["n_world"] == 2
    assert out["global"]["correct"] == 1
    assert out["by_category"]["a"]["n"] == 2
    assert out["by_lang"]["en"]["n"] == 2
    assert out["by_speakers"]["1"]["n"] == 2
    assert out["by_piece"]["k1_econ"]["n"] == 2
    assert "e1_earn" not in out["by_piece"]
    assert out["excluded"] == {"coherent_internal": 29, "unverifiable": 9,
                               "earnings_false_internal": 7}
    assert out["e1_internal"]["e1_earn"]["coherence"] == 1.0
    assert out["usd"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Manifest schema (tmp repo root, files mostly missing)
# ---------------------------------------------------------------------------

def test_manifest_schema_missing_files(tmp_path):
    manifest = R.build_manifest(repo_root=str(tmp_path),
                                qa_path=str(tmp_path / "qa-missing.json"))
    assert manifest["n_pieces"] == 12
    assert len(manifest["pieces"]) == 12
    assert "generated_utc" in manifest
    for row in manifest["pieces"]:
        assert set(row) == {"id", "lang", "kind", "speakers", "files",
                            "sha256", "voices", "licenses", "duration_s",
                            "qa_pass", "wer_clean", "recall_clean",
                            "wer_usd"}
        assert set(row["files"]) == {"mp4", "srt", "truth", "wer", "wavs"}
        assert set(row["sha256"]) == {"mp4", "srt", "truth"}
        assert row["sha256"] == {"mp4": None, "srt": None, "truth": None}
        assert set(row["voices"]) == ({"A", "B"} if row["speakers"] == 2
                                      else {"A"})
        assert len(row["licenses"]) == 2
        assert row["duration_s"] is None and row["qa_pass"] is None


def test_sha256_file_exact(tmp_path):
    target = tmp_path / "x.bin"
    target.write_bytes(b"abc")
    assert R.sha256_file(str(target)) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    assert R.sha256_file(str(tmp_path / "missing")) is None


def test_media_paths_registry_complete():
    assert len(PIECES) == 12
    assert sorted(media_paths(PIECES[0])) == ["mp4", "srt", "truth", "wer"]


# ---------------------------------------------------------------------------
# Review with doubles (fake ffmpeg runner + fake vision)
# ---------------------------------------------------------------------------

class FakeRunner:
    """Canned ffmpeg: -i info, volumedetect, and frame extraction."""

    def __init__(self, info=FF_I, vol=FF_VOL):
        self.info = info
        self.vol = vol
        self.cmds = []

    def __call__(self, cmd):
        self.cmds.append(cmd)
        if cmd[-1].endswith(".png"):
            with open(cmd[-1], "wb") as fh:
                fh.write(PNG_BYTES)
            return 0, "", ""
        if "volumedetect" in cmd:
            return 0, "", self.vol
        return 0, "", self.info


class FakeVision:
    available = True
    name = "fake-vision"

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0
        self.last_usage = {"prompt_tokens": 1000, "completion_tokens": 50}

    def complete_vision(self, system, user_text, image_data_urls,
                        max_tokens=2000, json_schema=None):
        self.calls += 1
        assert len(image_data_urls) == 2  # both frames, one call
        assert json_schema["name"] == "synthqa_piece_review"
        return dict(self.reply)


QA_REPLY = {"clarity": "ok", "pacing": "ok", "said_vs_shown": "coherent",
            "badge_visible": True, "secrets_visible": False,
            "notes": "two different slides, figures match the sample"}


def make_mini_repo(tmp_path, srt_text=SRT_OK, truth=None):
    repo = tmp_path / "repo"
    (repo / "data" / "synth" / "en").mkdir(parents=True)
    (repo / "data" / "synth" / "en" / "k1_econ.mp4").write_bytes(b"fake")
    (repo / "data" / "synth" / "en" / "k1_econ.srt").write_text(
        srt_text, encoding="utf-8")
    truth_dir = repo / "live" / "material" / "truth_en"
    truth_dir.mkdir(parents=True)
    rows = truth if truth is not None else [
        {"id": "K1-01", "text": "We are 8.14 billion people."}]
    (truth_dir / "k1_econ.json").write_text(json.dumps(rows),
                                            encoding="utf-8")
    return str(repo)


def test_review_piece_pass_with_doubles(tmp_path):
    repo = make_mini_repo(tmp_path)
    vision = FakeVision(QA_REPLY)
    out = Q.review_piece("k1_econ", vision=vision, repo_root=repo,
                         frames_root=str(tmp_path / "frames"),
                         runner=FakeRunner(), ff="ffmpeg")
    assert out["code_ok"] is True
    assert out["vision_ok"] is True
    assert out["pass"] is True
    assert vision.calls == 1  # exactly one vision call per piece
    assert out["gates"]["duration"]["value"] == pytest.approx(267.04)
    assert out["gates"]["resolution"]["ok"] is True
    assert out["gates"]["fps"]["value"] == pytest.approx(30.0)
    assert out["gates"]["audio_no_clipping"]["ok"] is True
    assert out["gates"]["srt"]["n_cues"] == 2
    assert out["gates"]["badge_flag"]["ok"] is True  # real builder flag
    assert len(out["frames"]) == 2
    assert out["vision"]["cost_usd"] >= 0.0


def test_review_piece_code_only_without_vision(tmp_path):
    repo = make_mini_repo(tmp_path)
    out = Q.review_piece("k1_econ", vision=None, repo_root=repo,
                         frames_root=str(tmp_path / "frames"),
                         runner=FakeRunner(), ff="ffmpeg")
    assert out["vision"]["skipped"] is True
    assert out["code_ok"] is True


def test_review_flags_bad_resolution_clipping_and_srt(tmp_path):
    bad_info = FF_I.replace("1920x1080", "1280x720").replace(
        "Duration: 00:04:27.04", "Duration: 00:01:00.00")
    bad_vol = FF_VOL.replace("max_volume: -3.1 dB", "max_volume: 0.0 dB")
    bad_srt = "1\n00:00:00,000 --> 00:05:00,000\nBeyond duration.\n"
    clipped = b"\xff\x7f" * 100  # 100 full-scale samples: sustained flat
    repo = make_mini_repo(tmp_path, srt_text=bad_srt)
    out = Q.review_piece("k1_econ", vision=None, repo_root=repo,
                         frames_root=str(tmp_path / "frames"),
                         runner=FakeRunner(info=bad_info, vol=bad_vol),
                         bytes_runner=lambda cmd: clipped,
                         ff="ffmpeg")
    assert out["gates"]["duration"]["ok"] is False  # 60 s < 150 s
    assert out["gates"]["resolution"]["ok"] is False
    assert out["gates"]["audio_no_clipping"]["ok"] is False
    assert out["gates"]["srt"]["ok"] is False
    assert out["pass"] is False


def test_flat_top_runs_counts_and_verdict():
    import struct
    clean = struct.pack("<2000h", *([1000, -2000] * 1000))
    assert Q.flat_top_runs(clean)["max_run"] == 0
    clipped = struct.pack("<100h", *([32767] * 100))
    flats = Q.flat_top_runs(clean + clipped + clean)
    assert flats["max_run"] == 100
    assert flats["n_runs_ge16"] == 1
    # Isolated full-scale peaks are not clipping.
    peaks = struct.pack("<6h", *([0, 32767, 0, -32767, 0, 0]))
    assert Q.flat_top_runs(peaks * 100)["n_runs_ge16"] == 0


def test_check_clipping_headroom_and_flat_top_clean():
    runner = FakeRunner()
    out = Q.check_clipping("x.mp4", ff="ffmpeg", runner=runner,
                           bytes_runner=lambda cmd: (_ for _ in ()).throw(
                               AssertionError("no decode needed")))
    assert out["stage"] == "headroom"
    assert out["clipping"] is False
    hot = FakeRunner(vol=FF_VOL.replace("max_volume: -3.1 dB",
                                        "max_volume: -0.0 dB"))
    import struct
    clean = struct.pack("<4000h", *([500, -500] * 2000))
    out = Q.check_clipping("x.mp4", ff="ffmpeg", runner=hot,
                           bytes_runner=lambda cmd: clean)
    assert out["stage"] == "flat-top"
    assert out["clipping"] is False
    assert out["ok"] is True


def test_review_secrets_scan_flags_email_and_path(tmp_path):
    truth = [{"id": "X1", "text": "Contact bob@example.com for more."}]
    srt = ("1\n00:00:00,000 --> 00:01:00,000\n"
           "See /home/alice/keys for details.\n")
    repo = make_mini_repo(tmp_path, srt_text=srt, truth=truth)
    out = Q.review_piece("k1_econ", vision=None, repo_root=repo,
                         frames_root=str(tmp_path / "frames"),
                         runner=FakeRunner(), ff="ffmpeg")
    kinds = {(h["source"], h["kind"]) for h in
             out["gates"]["secrets_scan"]["hits"]}
    assert ("script", "email") in kinds
    assert ("srt", "unix_path") in kinds
    assert out["gates"]["secrets_scan"]["ok"] is False


def test_review_vision_badge_missing_fails(tmp_path):
    repo = make_mini_repo(tmp_path)
    reply = dict(QA_REPLY, badge_visible=False)
    out = Q.review_piece("k1_econ", vision=FakeVision(reply),
                         repo_root=repo,
                         frames_root=str(tmp_path / "frames"),
                         runner=FakeRunner(), ff="ffmpeg")
    assert out["vision_ok"] is False
    assert out["pass"] is False


def test_truth_sample_prefers_frame_times(tmp_path):
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps([
        {"id": "a", "t_aprox": 0.0, "text": "zero"},
        {"id": "b", "t_aprox": 60.0, "text": "sixty"},
        {"id": "c", "t_aprox": 200.0, "text": "two-hundred"}]),
        encoding="utf-8")
    sample = Q.truth_sample(str(truth), n=2, times=[200.0])
    assert "two-hundred" in sample
    head = Q.truth_sample(str(truth), n=1)
    assert head == "[?] zero"  # expected labels ride on every line


def test_adjudicate_mismatch_tiers():
    sample = [
        {"id": "t1", "expected": "true", "text": "India grew 7.4 percent."},
        {"id": "f1", "expected": "false",
         "text": "India shrank 2.5 percent this year."},
    ]
    out = Q.adjudicate_mismatch("script says shrank 2.5% vs 7.4% shown",
                                sample)
    assert out["verdict"] == "trap-induced"
    assert out["hits"][0]["claim_id"] == "f1"
    out = Q.adjudicate_mismatch("frames look fine, different slides",
                                sample)
    assert out["verdict"] == "trap-plausible"
    out = Q.adjudicate_mismatch("contradicts the narration",
                                sample[:1])
    assert out["verdict"] == "genuine"


def test_validate_qa_never_raises():
    out = Q.validate_qa({"clarity": "nope"}, ["a.png"])
    assert out["clarity"] == "unclear"
    assert out["uncertainties"]


def test_badge_flag_both_builders():
    assert Q.badge_flag_ok("en")["ok"] is True
    assert Q.badge_flag_ok("es")["ok"] is True


# ---------------------------------------------------------------------------
# Bench split + E1 code scoring
# ---------------------------------------------------------------------------

def test_split_entries_world_vs_excluded():
    rows = [{"id": "t1", "expected": "true"},
            {"id": "f1", "expected": "false"},
            {"id": "u1", "expected": "unverifiable"},
            {"id": "c1", "expected": "coherent-internal"}]
    split = B.split_entries(rows, "k1_econ")
    assert [r["id"] for r in split["world"]] == ["t1", "f1"]
    assert split["e1_internal"] == []
    assert split["excluded"] == {"coherent_internal": 1, "unverifiable": 1,
                                 "earnings_false_internal": 0, "other": 0}


def test_split_entries_earnings_all_internal():
    rows = [{"id": "e1", "expected": "coherent-internal"},
            {"id": "e2", "expected": "false"},
            {"id": "e3", "expected": "unverifiable"}]
    split = B.split_entries(rows, "e1_earn")
    assert split["world"] == []
    assert len(split["e1_internal"]) == 3
    assert split["excluded"]["earnings_false_internal"] == 1


def test_safe_eval_locales():
    assert B.safe_eval("(29,298,013 - 27,811,517) / 27,811,517", "en") == (
        pytest.approx(0.05345, abs=1e-4))
    assert B.safe_eval("847/753,6 - 1", "es") == pytest.approx(
        0.12393, abs=1e-4)
    assert B.safe_eval("512 + 335", "es") == pytest.approx(847.0)
    with pytest.raises(ValueError):
        B.safe_eval("__import__('os')", "en")
    with pytest.raises(ValueError):
        B.safe_eval("2 ** 10", "en")


def test_check_coherent_arith_pass_and_fail():
    ok_row = {"id": "E1D1",
              "text": "Crecieron un 12,4 por ciento frente a 753,6.",
              "calculation": "847/753,6 - 1 = 0,12393 = 12,393 %",
              "expected": "coherent-internal"}
    out = B.check_coherent(ok_row, "es")
    assert out["pass"] is True
    assert out["check"] == "arith"
    bad_row = dict(ok_row, text="Crecieron un 99,0 por ciento.")
    assert B.check_coherent(bad_row, "es")["pass"] is False


def test_check_coherent_decl_label():
    row = {"id": "E1F1", "text": "Luminara es ficticia.",
           "expected": "coherent-internal",
           "ref": "ficticia por construcción (esta demo)",
           "ground_truth": "declarado ficticio -> COHERENT",
           "calculation": None}
    assert B.check_coherent(row, "es")["pass"] is True
    row2 = dict(row, ref="n/a", ground_truth="n/a")
    assert B.check_coherent(row2, "es")["pass"] is False


def test_check_trap_numeric_date_entity():
    trap = {"id": "E1-12", "expected": "false",
            "text": "Gross margin was 385 percent, our best ever.",
            "ref": "Report says 38.5%, not 385% (x10 trap)"}
    assert B.check_trap(trap, "en")["pass"] is True
    consistent = dict(trap, text="Gross margin was 38.5 percent.")
    assert B.check_trap(consistent, "en")["pass"] is False
    date = {"id": "E1-32", "expected": "false",
            "text": "The report was filed on February 30, 2026.",
            "ref": "filed Oct 28, 2026"}
    assert B.check_trap(date, "en")["pass"] is True
    entity = {"id": "E1-38", "expected": "false",
              "text": "Helioshore is a real listed company.",
              "ref": "Helioshore Industries is fictitious, created "
              "for this demo"}
    assert B.check_trap(entity, "en")["pass"] is True


def test_check_coherent_multistep_operands_and_fall():
    multi = {"id": "E1D17",
             "text": "El dividendo total de 50,4 millones es el 52,3.",
             "calculation": "0,42 x 120 = 50,4; 50,4/96,3 = 0,52336 = 52,336 %",
             "expected": "coherent-internal"}
    assert B.check_coherent(multi, "es")["pass"] is True
    operands = {"id": "E1D4",
                "text": "Son 762 del primero, 801 del segundo y 847.",
                "calculation": "762 + 801 + 847 = 2410",
                "expected": "coherent-internal"}
    out = B.check_coherent(operands, "es")
    assert out["pass"] is True
    assert out["said_via"] == "operands"
    fall = {"id": "E1D11",
            "text": "La deuda cayó un 5,2 por ciento desde 329,1.",
            "calculation": "312/329,1 - 1 = -0,05196 = -5,196 %",
            "expected": "coherent-internal"}
    assert B.check_coherent(fall, "es")["pass"] is True
    anchor = {"id": "E1-35",
              "text": "Asia-Pacific contributed 720 million dollars.",
              "ref": "Invented Q3 2026 report: APAC 0.72B = 720M, exact",
              "expected": "coherent-internal"}
    assert B.check_coherent(anchor, "en")["pass"] is True


def test_check_trap_negation_unit_and_span():
    neg = {"id": "E1-28", "expected": "false",
           "text": "Headcount fell to 2,400 people after mass layoffs.",
           "ref": "Report: 12,400 and growing, not 2,400 after layoffs"}
    assert B.check_trap(neg, "en")["pass"] is True
    unit = {"id": "E1-24", "expected": "false",
            "text": "Research spend was 410 billion dollars this quarter.",
            "ref": "Report says 410 MILLION, not billion (x1000 trap)"}
    out = B.check_trap(unit, "en")
    assert out["pass"] is True
    assert "unit trap" in out["detail"]
    span = {"id": "E1-36", "expected": "false",
            "text": "The three regions sum to 5.82 billion dollars.",
            "ref": "2.41 + 1.69 + 0.72 = 4.82, not 5.82 (wrong sum)"}
    assert B.check_trap(span, "en")["pass"] is True


def test_score_earnings_panel():
    rows = [
        {"id": "d1", "expected": "coherent-internal",
         "text": "Crecieron un 12,4 por ciento.",
         "calculation": "847/753,6 - 1 = 0,12393 = 12,393 %"},
        {"id": "g1", "expected": "unverifiable", "text": "Bienvenidos."},
    ]
    out = B.score_earnings(rows, "es")
    assert out == {"n": 2, "n_scored": 1, "n_pass": 1, "coherence": 1.0,
                   "per_claim": out["per_claim"]}


# ---------------------------------------------------------------------------
# Spot-check with a stubbed transcribe POST (offline)
# ---------------------------------------------------------------------------

def test_spot_check_uses_builder_transcript_convention(tmp_path):
    import wave
    repo = tmp_path / "repo"
    wav_dir = repo / "data" / "synth" / "en" / "wavs"
    wav_dir.mkdir(parents=True)
    wav = wav_dir / "k1_econ_snr10.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    (repo / "data" / "synth" / "en" / "k1_econ.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello world.\n",
        encoding="utf-8")
    truth_dir = repo / "live" / "material" / "truth_en"
    truth_dir.mkdir(parents=True)
    (truth_dir / "k1_econ.json").write_text(
        json.dumps([{"id": "x", "text": "Hello world."}]),
        encoding="utf-8")
    calls = {}

    def fake_post(url, headers, files, timeout_s):
        calls["bias"] = json.loads(files["request"][1])["languageBias"]
        return {"transcript": "Hello world.", "audioDurationMs": 1000,
                "turns": []}

    spot_path = str(tmp_path / "spot.json")
    cell = R.spot_check("k1_econ", 10, "fake-key", repo_root=str(repo),
                        spot_path=spot_path, post=fake_post)
    assert calls["bias"] == ["English"]
    assert cell["wer"] == pytest.approx(0.0)
    assert cell["recall"] == pytest.approx(1.0)
    assert cell["keywords"] is False
    assert json.load(open(spot_path))["cells"][0]["piece"] == "k1_econ"
    with pytest.raises(SystemExit):
        R.spot_check("k1_econ", 5, "fake-key", repo_root=str(repo),
                     spot_path=spot_path, post=fake_post)


# ---------------------------------------------------------------------------
# WER table aggregation (no transcription)
# ---------------------------------------------------------------------------

def test_wer_table_aggregates_builders(tmp_path):
    repo = tmp_path / "repo"
    wer_dir = repo / "data" / "synth" / "en" / "wer_reports"
    wer_dir.mkdir(parents=True)
    (wer_dir / "k1_econ.json").write_text(
        json.dumps({"wer": 0.06, "recall": 0.925, "usd": 0.013}),
        encoding="utf-8")
    table = R.wer_table(repo_root=str(repo),
                        spot_path=str(tmp_path / "spot.json"))
    assert len(table["rows"]) == 12
    k1 = next(r for r in table["rows"] if r["piece"] == "k1_econ")
    assert k1["wer_clean"] == pytest.approx(0.06)
    assert k1["snr_wer"] == {"snr0": "no medido", "snr10": "no medido",
                             "snr20": "no medido"}
    es_row = next(r for r in table["rows"] if r["piece"] == "clase_economia")
    assert es_row["wer_clean"] is None
    assert sorted(es_row["snr_wer"]) == ["snr10", "snr20", "snr5"]
