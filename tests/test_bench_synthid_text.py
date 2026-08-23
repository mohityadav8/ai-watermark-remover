"""Tests for the SynthID-text removal benchmark (bench_synthid_text.py).

Mock-based: the heavy steps (MarkLLM watermark/detect, Layer B rewrite,
are faked, so the suite needs no torch, no network, and no
rewrite backend. It exercises orchestration, sanity gating, aggregation,
controls, and the JSON/CSV/Markdown outputs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bench_synthid_text as bench
from bench_synthid_text import (
    _parse_stats_json,
    aggregate,
    estimate_tokens,
    load_corpus,
    parse_variants,
)

DETECT_POS = {"available": True, "is_watermarked": True, "score": 2.0}
DETECT_NEG = {"available": True, "is_watermarked": False, "score": -1.0}


def _args(**overrides):
    values = dict(
        markllm_dir="fake-markllm",
        corpus=SCRIPTS.parents[1] / "benchmarks" / "corpus",
        docs=3,
        seeds=1,
        seed_base=1,
        max_new_tokens=300,
        variants="paraphrase:1,paraphrase:3",
        restamp_control=False,
        out_dir=Path("out"),
        tag="t",
        markllm_model="facebook/opt-1.3b",
        markllm_timeout=600.0,
        rewrite_backend="ollama",
        rewrite_model="llama3.2",
        rewrite_base_url="http://127.0.0.1:11434",
        rewrite_api_key=None,
        rewrite_allow_remote=False,
        rewrite_temperature=0.9,
        rewrite_loops=1,
        chars_per_token=4.0,
        cost_per_mtok_in=0.0,
        cost_per_mtok_out=0.0,
        no_worker=True,
        scheme="synthid",
        config=None,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def _make_bench(tmp_path, monkeypatch, patch_steps=True, **args_overrides):
    """A Benchmark with all heavy steps faked and controllable."""
    args = _args(out_dir=tmp_path, **args_overrides)
    b = bench.Benchmark(args, Path(args.markllm_dir))
    if patch_steps:
        monkeypatch.setattr(b, "watermark_sample", _fake_watermark)
        monkeypatch.setattr(
            b, "detect", lambda text: DETECT_POS if text.startswith("watermarked") else DETECT_NEG
        )
        monkeypatch.setattr(
            b, "rewrite", lambda text, strength, candidates: (text + " rewritten", _rewrite_stats())
        )
    return b, args


def _fake_watermark(prompt_path, seed, out_dir):
    return {
        "watermarked": (
            "watermarked sample text for seed "
            + str(seed)
            + " with the numbers 42 and 7 and enough words to pass the gate"
        ),
        "unwatermarked": "plain sample text " + str(seed),
        "watermarked_chars": 44,
        "unwatermarked_chars": 20,
        "payload": {},
    }


def _rewrite_stats(cleared=True, evaluator="markllm", attempts_made=1, passed=True):
    return {
        "mode": "rewritten",
        "evaluator": evaluator,
        "attempts_made": attempts_made,
        "passed": passed,
        "markllm": {
            "before": dict(DETECT_POS),
            "after": {"available": True, "is_watermarked": not cleared, "score": -0.5},
            "cleared": cleared,
            "note": "same-config only",
        },
        "output_chars": 100,
        "candidate_scores": [],
    }


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_default_variants_is_paraphrase_three():
    args = bench.build_parser().parse_args(["--markllm-dir", "x", "--rewrite-model", "m"])
    assert args.variants == "paraphrase:3"


def test_parse_variants():
    assert parse_variants("paraphrase:1,backtranslate:3") == [
        ("paraphrase", 1),
        ("backtranslate", 3),
    ]
    assert parse_variants(" structural:5 ") == [("structural", 5)]
    with pytest.raises(SystemExit):
        parse_variants("paraphrase")
    with pytest.raises(SystemExit):
        parse_variants("paraphrase:0")
    with pytest.raises(SystemExit):
        parse_variants("")


def test_load_corpus(tmp_path):
    (tmp_path / "a.txt").write_text("seed one", encoding="utf-8")
    (tmp_path / "b.txt").write_text("seed two", encoding="utf-8")
    (tmp_path / "skip.me").write_text("not a txt", encoding="utf-8")
    docs = load_corpus(tmp_path, limit=10)
    assert [(d, t) for d, t in docs] == [("a", "seed one"), ("b", "seed two")]
    assert load_corpus(tmp_path, limit=1) == [("a", "seed one")]
    single = tmp_path / "b.txt"
    assert load_corpus(single, limit=10) == [("b", "seed two")]


def test_parse_stats_json_skips_warning_lines():
    stderr = "note: evaluator=markllm attempts=1/3 passed=true\n" + json.dumps(
        {"mode": "rewritten", "markllm": {"cleared": True}}
    )
    stats = _parse_stats_json(stderr)
    assert stats is not None
    assert stats["markllm"]["cleared"] is True
    assert _parse_stats_json("no json here") is None


def test_estimate_tokens():
    assert estimate_tokens("x" * 100, 4.0) == 25
    assert estimate_tokens("", 4.0) == 1


def test_aggregate_clear_rate_and_efficiency():
    rows = [
        {
            "variant": "rewrite-paraphrase:1",
            "kind": "rewrite",
            "before_pos": True,
            "after_pos": False,
            "cleared": True,
            "score_before": 2.0,
            "score_after": -1.0,
            "quality": {
                "lexical_divergence": 0.8,
                "length_ratio": 1.0,
                "numbers_preserved": 1.0,
                "tokens_in": 200,
                "tokens_out": 200,
            },
            "seconds": 1.0,
            "usd": 0.0,
            "attempts": 1,
            "notes": [],
        },
        {
            "variant": "rewrite-paraphrase:1",
            "kind": "rewrite",
            "before_pos": True,
            "after_pos": True,
            "cleared": False,
            "score_before": 2.0,
            "score_after": 1.5,
            "quality": {
                "lexical_divergence": 0.6,
                "length_ratio": 1.0,
                "numbers_preserved": 1.0,
                "tokens_in": 300,
                "tokens_out": 300,
            },
            "seconds": 2.0,
            "usd": 0.0,
            "attempts": 3,
            "notes": [],
        },
    ]
    agg = aggregate(rows, [("paraphrase", 1)])
    a = agg["rewrite-paraphrase:1"]
    assert a["n"] == 2
    assert a["cleared"] == 1
    assert a["clear_rate"] == 0.5
    assert a["mean_score_delta"] == 1.75  # ((2-(-1)) + (2-1.5)) / 2
    assert a["mean_tokens_out"] == 250
    assert a["mean_attempts"] == 2.0  # (1 + 3) / 2
    assert a["clears_per_mtok_out"] == pytest.approx(2000.0)  # 0.5 / (250/1e6)


def test_aggregate_controls_included():
    rows = [
        {
            "variant": "control",
            "kind": "control",
            "before_pos": True,
            "after_pos": True,
            "cleared": False,
            "quality": {
                "lexical_divergence": 0.0,
                "length_ratio": 1.0,
                "numbers_preserved": 1.0,
                "tokens_in": 100,
                "tokens_out": 100,
            },
            "seconds": 0.1,
            "usd": 0.0,
            "notes": ["no removal applied (baseline)"],
        },
        {
            "variant": "layer-a",
            "kind": "layer-a",
            "before_pos": True,
            "after_pos": True,
            "cleared": False,
            "quality": {
                "lexical_divergence": 0.0,
                "length_ratio": 1.0,
                "numbers_preserved": 1.0,
                "tokens_in": 100,
                "tokens_out": 100,
            },
            "seconds": 0.1,
            "usd": 0.0,
            "notes": [],
        },
    ]
    agg = aggregate(rows, [("paraphrase", 1)])
    assert list(agg) == ["control", "layer-a"]
    assert agg["control"]["clear_rate"] == 0.0
    assert agg["layer-a"]["clear_rate"] == 0.0


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def test_generate_samples_sanity_gate(tmp_path, monkeypatch):
    b, _ = _make_bench(tmp_path, monkeypatch, docs=2)
    samples = b.generate_samples(tmp_path / "work")
    assert len(samples) == 2
    for s in samples:
        assert s["excluded"] is False
        assert s["before"]["is_watermarked"] is True


def test_sanity_gate_excludes_undetected(tmp_path, monkeypatch):
    b, _ = _make_bench(tmp_path, monkeypatch, docs=1)
    monkeypatch.setattr(b, "detect", lambda text: DETECT_NEG)
    samples = b.generate_samples(tmp_path / "work")
    assert len(samples) == 1
    assert samples[0]["excluded"] is True
    assert "sanity gate" in samples[0]["excluded_reason"]


def test_run_variants_rows_and_clear_rate(tmp_path, monkeypatch):
    b, _ = _make_bench(tmp_path, monkeypatch, docs=1, variants="paraphrase:1")
    samples = b.generate_samples(tmp_path / "work")
    rows = b.run_variants(samples, tmp_path / "work")
    kinds = [r["kind"] for r in rows]
    assert "control" in kinds and "layer-a" in kinds and "rewrite" in kinds
    rewrite_rows = [r for r in rows if r["kind"] == "rewrite"]
    assert len(rewrite_rows) == 1
    assert rewrite_rows[0]["cleared"] is True
    assert rewrite_rows[0]["attempts"] == 1
    assert rewrite_rows[0]["evaluator"] == "markllm"
    assert rewrite_rows[0]["passed"] is True
    control = next(r for r in rows if r["kind"] == "control")
    assert control["cleared"] is False
    layer_a = next(r for r in rows if r["kind"] == "layer-a")
    assert layer_a["cleared"] is False
    assert any("no removal applied" in n for n in control["notes"])


def test_rewrite_failure_is_recorded_not_fatal(tmp_path, monkeypatch):
    b, _ = _make_bench(tmp_path, monkeypatch, docs=1, variants="paraphrase:1")

    def _boom(text, strength, candidates):
        raise RuntimeError("backend down")

    monkeypatch.setattr(b, "rewrite", _boom)
    samples = b.generate_samples(tmp_path / "work")
    rows = b.run_variants(samples, tmp_path / "work")
    failed = [r for r in rows if r["kind"] == "rewrite"]
    assert len(failed) == 1
    assert failed[0]["cleared"] is None
    assert any("rewrite failed" in n for n in failed[0]["notes"])


def test_restamp_control_rows(tmp_path, monkeypatch):
    b, _ = _make_bench(tmp_path, monkeypatch, docs=1, variants="paraphrase:1", restamp_control=True)
    samples = b.generate_samples(tmp_path / "work")
    rows = b.run_variants(samples, tmp_path / "work")
    restamps = [r for r in rows if r["kind"] == "restamp"]
    assert len(restamps) == 1
    # fake rewrite turns plain text into "… rewritten"; detect is text-based
    assert restamps[0]["after_pos"] in (True, False)


# ---------------------------------------------------------------------------
# Outputs via main()
# ---------------------------------------------------------------------------


class _FakeBench:
    def __init__(self, args, upstream):
        self.args = args
        self.python = "python3"
        self.variants = parse_variants(args.variants)
        self.corpus = load_corpus(args.corpus, args.docs)
        self.chars_per_token = args.chars_per_token

    def close_worker(self):
        pass

    def generate_samples(self, workdir):
        return [
            {
                "doc": "d1",
                "seed": 1,
                "excluded": False,
                "notes": [],
                "watermarked": "wm text",
                "unwatermarked": "plain text",
                "before": dict(DETECT_POS),
                "plain_detect": dict(DETECT_NEG),
            }
        ]

    def run_variants(self, samples, workdir):
        return [
            {
                "doc": "d1",
                "seed": 1,
                "variant": "rewrite-paraphrase:1",
                "kind": "rewrite",
                "before_pos": True,
                "after_pos": False,
                "cleared": True,
                "score_before": 2.0,
                "score_after": -1.0,
                "quality": {
                    "lexical_divergence": 0.8,
                    "length_ratio": 1.0,
                    "numbers_preserved": 1.0,
                    "urls_preserved": 1.0,
                    "tokens_in": 100,
                    "tokens_out": 100,
                },
                "seconds": 1.5,
                "usd": 0.0,
                "notes": [],
            },
            {
                "doc": "d1",
                "seed": 1,
                "variant": "control",
                "kind": "control",
                "before_pos": True,
                "after_pos": True,
                "cleared": False,
                "quality": {
                    "lexical_divergence": 0.0,
                    "length_ratio": 1.0,
                    "numbers_preserved": 1.0,
                    "tokens_in": 100,
                    "tokens_out": 100,
                },
                "seconds": 0.1,
                "usd": 0.0,
                "notes": ["no removal applied (baseline)"],
            },
            {
                "doc": "d1",
                "seed": 1,
                "variant": "layer-a",
                "kind": "layer-a",
                "before_pos": True,
                "after_pos": True,
                "cleared": False,
                "quality": {
                    "lexical_divergence": 0.0,
                    "length_ratio": 1.0,
                    "numbers_preserved": 1.0,
                    "tokens_in": 100,
                    "tokens_out": 100,
                },
                "seconds": 0.1,
                "usd": 0.0,
                "notes": [],
            },
        ]


def test_main_writes_outputs(tmp_path, monkeypatch, capsys):
    (tmp_path / "markllm" / "watermark").mkdir(parents=True)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "d1.txt").write_text("prompt", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(bench, "Benchmark", _FakeBench)
    monkeypatch.setattr(bench, "_repo_commit", lambda: "abc123")
    monkeypatch.setattr(bench, "_markllm_commit", lambda upstream: "def456")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bench_synthid_text.py",
            "--markllm-dir",
            str(tmp_path / "markllm"),
            "--corpus",
            str(corpus),
            "--docs",
            "1",
            "--rewrite-model",
            "llama3.2",
            "--variants",
            "paraphrase:1",
            "--out-dir",
            str(out),
            "--tag",
            "ci-test",
        ],
    )
    rc = bench.main()
    assert rc == 0
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "# SynthID-text removal benchmark — ci-test" in report
    assert "rewrite-paraphrase:1" in report
    assert "caveat" in report.lower() or "not google" in report.lower()
    data = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert data["meta"]["tag"] == "ci-test"
    assert data["meta"]["repo_commit"] == "abc123"
    agg = data["aggregates"]["rewrite-paraphrase:1"]
    assert agg["clear_rate"] == 1.0
    csv = (out / "results.csv").read_text(encoding="utf-8")
    assert "doc,seed,variant" in csv
    assert "rewrite-paraphrase:1" in csv


def test_main_requires_markllm_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["bench_synthid_text.py", "--rewrite-model", "m"])
    assert bench.main() == 2


def test_main_rejects_remote_rewrite_without_flag(tmp_path, monkeypatch, capsys):
    (
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "bench_synthid_text.py",
                "--markllm-dir",
                str(tmp_path),
                "--rewrite-model",
                "m",
                "--rewrite-base-url",
                "http://api.example.com",
            ],
        ),
    )
    assert bench.main() == 2


def test_main_allow_remote_from_env(tmp_path, monkeypatch):
    """WATERMARKS_REWRITE_ALLOW_REMOTE=1 satisfies the remote-URL check."""
    (tmp_path / "markllm" / "watermark").mkdir(parents=True)
    monkeypatch.setenv("WATERMARKS_REWRITE_ALLOW_REMOTE", "1")
    monkeypatch.setattr(bench, "Benchmark", _FakeBench)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bench_synthid_text.py",
            "--markllm-dir",
            str(tmp_path / "markllm"),
            "--rewrite-model",
            "m",
            "--rewrite-base-url",
            "http://api.example.com",
            "--out-dir",
            str(tmp_path / "out"),
        ],
    )
    assert bench.main() == 0


def test_worker_publishes_port_env(tmp_path, monkeypatch):
    """A live worker publishes WATERMARKS_MARKLLM_PORT for child processes."""

    class _PortWorker(_FakeWorker):
        def __init__(self, python, script, upstream, model, timeout, **kwargs):
            super().__init__(python, script, upstream, model, timeout, **kwargs)
            self.port = 12345
            os.environ["WATERMARKS_MARKLLM_PORT"] = str(self.port)

        def close(self):
            os.environ.pop("WATERMARKS_MARKLLM_PORT", None)

    monkeypatch.setattr(bench, "MarkLLMWorker", _PortWorker)
    monkeypatch.delenv("WATERMARKS_MARKLLM_PORT", raising=False)
    b, _ = _make_bench(tmp_path, monkeypatch, no_worker=False, patch_steps=False)
    assert os.environ.get("WATERMARKS_MARKLLM_PORT") == "12345"
    b.close_worker()
    assert "WATERMARKS_MARKLLM_PORT" not in os.environ


# ---------------------------------------------------------------------------
def test_aggregate_tolerates_list_in_notes():
    """A row whose notes contain a non-string must not crash aggregation."""
    rows = [
        {
            "variant": "rewrite-paraphrase:1",
            "kind": "rewrite",
            "before_pos": True,
            "after_pos": False,
            "cleared": True,
            "score_before": 2.0,
            "score_after": -1.0,
            "quality": {
                "lexical_divergence": 0.8,
                "length_ratio": 1.0,
                "numbers_preserved": 1.0,
                "tokens_in": 100,
                "tokens_out": 100,
            },
            "seconds": 1.0,
            "usd": 0.0,
            "notes": ["clean note", ["nested", "list"], {"d": 1}],
        }
    ]
    agg = aggregate(rows, [("paraphrase", 1)])
    assert agg["rewrite-paraphrase:1"]["notes"] == ["clean note"]


# Persistent MarkLLM worker
# ---------------------------------------------------------------------------


class _FakeWorker:
    info: dict = {"device": "cuda"}  # noqa: RUF012 - test double

    def __init__(self, python, script, upstream, model, timeout, *, scheme="synthid", config=None):
        pass

    def watermark(self, prompt, seed, max_new_tokens):
        return {
            "watermarked": prompt + " WM",
            "unwatermarked": prompt + " PL",
            "watermarked_chars": len(prompt) + 3,
            "unwatermarked_chars": len(prompt) + 3,
            "payload": {},
        }

    def detect(self, text):
        return dict(DETECT_POS if "WM" in text else DETECT_NEG)

    def close(self):
        pass


def test_worker_used_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "MarkLLMWorker", _FakeWorker)
    b, _ = _make_bench(tmp_path, monkeypatch, no_worker=False, patch_steps=False)
    assert b.worker is not None
    assert b.detect("x WM y")["is_watermarked"] is True
    assert b.detect("plain text")["is_watermarked"] is False


def test_worker_watermark_routing(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "MarkLLMWorker", _FakeWorker)
    b, _ = _make_bench(tmp_path, monkeypatch, no_worker=False, patch_steps=False)
    p = tmp_path / "prompt.txt"
    p.write_text("hello", encoding="utf-8")
    out = b.watermark_sample(p, 1, tmp_path)
    assert out["watermarked"] == "hello WM"
    assert out["unwatermarked"] == "hello PL"


def test_worker_fallback_on_start_failure(tmp_path, monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("serve unavailable")

    monkeypatch.setattr(bench, "MarkLLMWorker", _Boom)
    calls = []

    def fake_detect(py, script, upstream, text, model, timeout, *, scheme="synthid", config=None):
        calls.append(text)
        return dict(DETECT_NEG)

    monkeypatch.setattr(bench, "run_detect", fake_detect)
    b, _ = _make_bench(tmp_path, monkeypatch, no_worker=False, patch_steps=False)
    assert b.worker is None
    r = b.detect("plain text")
    assert calls == ["plain text"]
    assert r["is_watermarked"] is False


def test_worker_disabled_with_flag(tmp_path, monkeypatch):
    b, _ = _make_bench(tmp_path, monkeypatch, no_worker=True)
    assert b.worker is None


def test_handle_serve_request_protocol():
    import types

    import detect_text_watermark as dt

    class FakeWM:
        def __init__(self):
            self.config = types.SimpleNamespace(gen_kwargs={})

        def generate_watermarked_text(self, prompt):
            return prompt + " WM"

        def generate_unwatermarked_text(self, prompt):
            return prompt + " PL"

        def detect_watermark(self, text, return_dict=False):
            wm = text.endswith("WM")
            return {"is_watermarked": wm, "score": 2.0 if wm else -1.0}

    wm = FakeWM()
    r = dt._handle_serve_request(
        wm, {"op": "watermark", "id": 1, "prompt": "hello", "seed": None, "max_new_tokens": 10}, 0.5
    )
    assert r["ok"] and r["watermarked"] == "hello WM" and r["id"] == 1
    r = dt._handle_serve_request(wm, {"op": "detect", "id": 2, "text": "hello WM"}, 0.5)
    assert r["ok"] and r["is_watermarked"] is True and r["score"] == 2.0
    r = dt._handle_serve_request(wm, {"op": "detect", "id": 3, "text": "hello PL"}, 0.5)
    assert r["is_watermarked"] is False
    r = dt._handle_serve_request(wm, {"op": "nope", "id": 4}, 0.5)
    assert r["ok"] is False and "unknown op" in r["error"]
    r = dt._handle_serve_request(wm, {"op": "watermark", "id": 5, "prompt": ""}, 0.5)
    assert r["ok"] is False
    r = dt._handle_serve_request(wm, {"op": "exit", "id": 6}, 0.5)
    assert r["ok"] is True


def test_run_cmd_no_rlimit_preexec(monkeypatch):
    """_run_cmd must not apply the common 4 GiB RLIMIT_AS (kills torch/CUDA)."""
    import subprocess as _sp

    calls = {}

    class _FakePopen:
        def __init__(self, *a, **k):
            calls["kwargs"] = k
            self.returncode = 0
            self.stdout = "{}"
            self.stderr = ""

    monkeypatch.setattr(_sp, "run", _FakePopen)
    import bench_synthid_text as b

    b._run_cmd(["echo", "hi"], timeout=5)
    assert "preexec_fn" not in calls["kwargs"]
