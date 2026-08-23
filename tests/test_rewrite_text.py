"""Tests for Layer B rewrite_text hook (offline / print-prompt + client hardening)."""

from __future__ import annotations

import http.server
import json
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rewrite_text
from rewrite_text import (
    _check_remote,
    _flag_env,
    _lexical_divergence,
    _select_candidate,
    build_prompt,
    rewrite,
)


def _rewrite_kwargs(**overrides):
    kwargs = dict(
        backend="print-prompt",
        model=None,
        base_url=None,
        api_key=None,
        strength="paraphrase",
        lang="French",
        original_lang="English",
        timeout=5.0,
        layer_a_after=True,
        temperature=0.9,
        candidates=1,
    )
    kwargs.update(overrides)
    return kwargs


def test_build_prompt_paraphrase_is_word_choice_plus_syntax():
    p = build_prompt("paraphrase", "Hello world facts 42.", lang="French", original_lang="English")
    assert "Hello world facts 42." in p
    assert "clause order" in p
    assert "function words" in p


def test_build_prompt_humanize_and_code_contain_text():
    for strength, keyword in (("humanize", "human wrote it"), ("code", "comments")):
        p = build_prompt(strength, "ABC 123", lang="French", original_lang="English")
        assert "ABC 123" in p
        assert keyword in p


def test_build_prompt_unknown_strength_raises():
    with pytest.raises(ValueError):
        build_prompt("nope", "ABC", lang="French", original_lang="English")


def test_print_prompt_backend():
    out, info = rewrite("Sample prose about water marks.", **_rewrite_kwargs())
    assert info["mode"] == "print-prompt"
    assert "Sample prose" in out
    assert info["backend"] == "print-prompt"
    assert info["temperature"] == 0.9


def test_print_prompt_ignores_candidates():
    out, info = rewrite("Sample prose about water marks.", **_rewrite_kwargs(candidates=2))
    assert info["mode"] == "print-prompt"
    assert isinstance(out, str)
    assert "Sample prose" in out


def test_structural_and_backtranslate_prompts():
    for strength in ("structural", "backtranslate"):
        p = build_prompt(strength, "ABC 123", lang="German", original_lang="English")
        assert "ABC 123" in p


def test_lexical_divergence_identical_is_zero():
    assert _lexical_divergence("the cat sat", "the cat sat") == 0.0


def test_lexical_divergence_fully_different_higher_than_similar():
    similar = _lexical_divergence("the cat sat on the mat", "the dog sat on the mat")
    different = _lexical_divergence("the cat sat on the mat", "alpha beta gamma delta")
    assert different > similar


def test_lexical_divergence_empty_inputs():
    assert _lexical_divergence("", "") == 0.0
    assert _lexical_divergence("", "text") == 1.0
    assert _lexical_divergence("text", "") == 1.0


def test_select_candidate_prefers_more_divergent():
    original = "the cat sat on the mat"
    best, scores = _select_candidate(
        original,
        ["the cat sat on the mat", "the dog sat on the mat", "alpha beta gamma delta"],
    )
    assert best == "alpha beta gamma delta"
    assert len(scores) == 3


# ---------------------------------------------------------------------------
# Iterative rewrite loop with detection-guided evaluation
# ---------------------------------------------------------------------------


class _FakeMarkLLM:
    """Stand-in for text_detectors.MarkLLMTextDetector (no subprocess).

    Verdict by exact text match: "the cat sat on the mat" is watermarked,
    everything else is not.
    """

    name = "markllm"

    def __init__(self, **kwargs):
        self._kwargs = kwargs

    def available(self) -> bool:
        return True

    def detect(self, text: str) -> dict:
        return {
            "detector": "markllm",
            "scheme": "kgw",
            "vendor": "open-llm",
            "available": True,
            "is_watermarked": text == "the cat sat on the mat",
            "score": 3.0 if text == "the cat sat on the mat" else 0.5,
            "threshold": 3.0,
        }


def _rewrite_candidates_kwargs(**overrides):
    kwargs = dict(
        backend="ollama",
        model="m",
        base_url="http://127.0.0.1:11434",
        api_key=None,
        strength="paraphrase",
        lang="French",
        original_lang="English",
        timeout=10,
        layer_a_after=False,
        temperature=0.9,
        candidates=2,
    )
    kwargs.update(overrides)
    return kwargs


def _two_candidates(monkeypatch):
    """call_ollama yields an identical then a fully divergent candidate."""
    texts = iter(["the cat sat on the mat", "alpha beta gamma delta"])
    monkeypatch.setattr(rewrite_text, "call_ollama", lambda *a, **k: next(texts))


def test_default_candidates_and_max_loops_are_one(monkeypatch):
    monkeypatch.delenv("WATERMARKS_REWRITE_CANDIDATES", raising=False)
    monkeypatch.delenv("WATERMARKS_REWRITE_LOOPS", raising=False)
    assert rewrite_text.DEFAULT_CANDIDATES == 1
    assert rewrite_text.DEFAULT_MAX_LOOPS == 1
    args = rewrite_text.build_parser().parse_args(["x.txt"])
    assert args.candidates == 1
    assert args.max_loops == 1
    monkeypatch.setenv("WATERMARKS_REWRITE_CANDIDATES", "5")
    monkeypatch.setenv("WATERMARKS_REWRITE_LOOPS", "7")
    args = rewrite_text.build_parser().parse_args(["x.txt"])
    assert args.candidates == 5
    assert args.max_loops == 7


def test_evaluator_is_lexical_without_markllm_scheme(monkeypatch):
    monkeypatch.setattr(
        rewrite_text,
        "MarkLLMTextDetector",
        lambda *a, **k: pytest.fail("markllm must not be built without --markllm-scheme"),
    )
    _two_candidates(monkeypatch)
    out, info = rewrite("the cat sat on the mat", **_rewrite_candidates_kwargs())
    # no detector configured: lexical divergence runs all attempts, no verdict
    assert info["evaluator"] == "lexical-divergence"
    assert info["passed"] is None
    assert info["attempts_made"] == 2
    assert out == "alpha beta gamma delta"
    assert all(
        c["evaluation"]["evaluator"] == "lexical-divergence" for c in info["candidate_scores"]
    )
    assert "markllm" not in info


def test_duplicate_candidates_select_first(monkeypatch):
    texts = iter(["alpha beta gamma delta", "alpha beta gamma delta"])
    monkeypatch.setattr(rewrite_text, "call_ollama", lambda *a, **k: next(texts))
    out, info = rewrite("the cat sat on the mat", **_rewrite_candidates_kwargs())
    assert out == "alpha beta gamma delta"
    assert [c["selected"] for c in info["candidate_scores"]] == [True, False]


def test_markllm_evaluator_loop_stops_on_pass(monkeypatch):
    monkeypatch.setattr(rewrite_text, "MarkLLMTextDetector", _FakeMarkLLM)
    _two_candidates(monkeypatch)
    out, info = rewrite(
        "the cat sat on the mat",
        **_rewrite_candidates_kwargs(markllm_scheme="kgw", markllm_dir="/x"),
    )
    assert info["evaluator"] == "markllm"
    assert out == "alpha beta gamma delta"
    assert info["attempts_made"] == 2
    assert info["passed"] is True
    cs = info["candidate_scores"]
    assert [c["passed"] for c in cs] == [False, True]
    assert [c["selected"] for c in cs] == [False, True]
    assert cs[0]["lexical_divergence"] == 0.0
    assert cs[1]["lexical_divergence"] == 1.0
    assert cs[0]["evaluation"]["is_watermarked"] is True
    assert cs[1]["evaluation"]["is_watermarked"] is False
    # before/after detection on the original and the final output
    mk = info["markllm"]
    assert mk["before"]["is_watermarked"] is True
    assert mk["after"]["is_watermarked"] is False
    assert mk["cleared"] is True


def test_markllm_detector_parameterized_from_cli(monkeypatch):
    captured = {}

    class _Capture(_FakeMarkLLM):
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs
            super().__init__(**kwargs)

    monkeypatch.setattr(rewrite_text, "MarkLLMTextDetector", _Capture)
    monkeypatch.setattr(rewrite_text, "call_ollama", lambda *a, **k: "alpha beta gamma delta")
    rewrite(
        "the cat sat on the mat",
        **_rewrite_candidates_kwargs(markllm_scheme="kgw", markllm_dir="/x"),
    )
    assert captured["kwargs"] == {
        "scheme": "kgw",
        "upstream_dir": "/x",
        "model": "facebook/opt-1.3b",
        "timeout": 180.0,
    }


def test_loop_stops_on_first_passing_attempt(monkeypatch):
    monkeypatch.setattr(rewrite_text, "MarkLLMTextDetector", _FakeMarkLLM)
    monkeypatch.setattr(rewrite_text, "call_ollama", lambda *a, **k: "alpha beta gamma delta")
    out, info = rewrite(
        "the cat sat on the mat",
        **_rewrite_candidates_kwargs(
            candidates=3, max_loops=3, markllm_scheme="kgw", markllm_dir="/x"
        ),
    )
    assert out == "alpha beta gamma delta"
    assert info["candidates"] == 3
    assert info["max_loops"] == 3
    assert info["attempts_made"] == 1  # passed on the first attempt
    assert info["passed"] is True
    assert info["candidate_scores"][0]["selected"] is True


def test_loop_exhausts_max_attempts_without_pass(monkeypatch):
    class _NeverClears:
        name = "markllm"

        def __init__(self, **kwargs):
            pass

        def available(self):
            return True

        def detect(self, text):
            score = {"aaa": 3.0, "bbb": 2.0, "ccc": 1.0}.get(text, 2.5)
            return {
                "detector": "markllm",
                "available": True,
                "is_watermarked": True,
                "score": score,
            }

    monkeypatch.setattr(rewrite_text, "MarkLLMTextDetector", _NeverClears)
    texts = iter(["aaa", "bbb", "ccc"])
    monkeypatch.setattr(rewrite_text, "call_ollama", lambda *a, **k: next(texts))
    out, info = rewrite(
        "the cat sat on the mat",
        **_rewrite_candidates_kwargs(candidates=3, markllm_scheme="kgw", markllm_dir="/x"),
    )
    assert out == "ccc"  # best-effort: lowest watermark score wins
    assert info["attempts_made"] == 3
    assert info["max_loops"] == 1
    assert info["passed"] is False
    assert all(c["passed"] is False for c in info["candidate_scores"])
    selected = [c for c in info["candidate_scores"] if c["selected"]]
    assert len(selected) == 1
    assert selected[0]["evaluation"]["score"] == 1.0
    assert info["markllm"]["cleared"] is False
    assert "Exhausted" in info["note"]


def test_max_loops_retry_new_variants_until_pass(monkeypatch):
    class _PassOnThird:
        name = "markllm"

        def __init__(self, **kwargs):
            pass

        def available(self):
            return True

        def detect(self, text):
            wm = text in ("aaa", "bbb")
            score = {"aaa": 3.0, "bbb": 2.0}.get(text, 0.5)
            return {
                "detector": "markllm",
                "available": True,
                "is_watermarked": wm,
                "score": score,
            }

    monkeypatch.setattr(rewrite_text, "MarkLLMTextDetector", _PassOnThird)
    texts = iter(["aaa", "bbb", "ccc"])
    monkeypatch.setattr(rewrite_text, "call_ollama", lambda *a, **k: next(texts))
    out, info = rewrite(
        "the cat sat on the mat",
        **_rewrite_candidates_kwargs(
            candidates=1, max_loops=3, markllm_scheme="kgw", markllm_dir="/x"
        ),
    )
    assert out == "ccc"  # loops retry new variants until an evaluation passes
    assert info["max_loops"] == 3
    assert info["attempts_made"] == 3
    assert info["passed"] is True
    assert [c["loop"] for c in info["candidate_scores"]] == [0, 1, 2]
    assert [c["passed"] for c in info["candidate_scores"]] == [False, False, True]
    assert info["candidate_scores"][2]["selected"] is True


def test_max_loops_exhausted_across_loops(monkeypatch):
    class _NeverClears:
        name = "markllm"

        def __init__(self, **kwargs):
            pass

        def available(self):
            return True

        def detect(self, text):
            score = {"a": 4.0, "b": 3.0, "c": 2.0, "d": 1.0}.get(text, 2.5)
            return {
                "detector": "markllm",
                "available": True,
                "is_watermarked": True,
                "score": score,
            }

    monkeypatch.setattr(rewrite_text, "MarkLLMTextDetector", _NeverClears)
    texts = iter(["a", "b", "c", "d"])
    monkeypatch.setattr(rewrite_text, "call_ollama", lambda *a, **k: next(texts))
    out, info = rewrite(
        "the cat sat on the mat",
        **_rewrite_candidates_kwargs(
            candidates=2, max_loops=2, markllm_scheme="kgw", markllm_dir="/x"
        ),
    )
    assert info["max_loops"] == 2
    assert info["attempts_made"] == 4  # 2 loops x 2 candidates
    assert info["passed"] is False
    assert out == "d"  # best-effort: lowest watermark score across all loops
    assert [c["loop"] for c in info["candidate_scores"]] == [0, 0, 1, 1]
    selected = [c for c in info["candidate_scores"] if c["selected"]]
    assert len(selected) == 1 and selected[0]["evaluation"]["score"] == 1.0


def test_evaluator_fail_soft_verdict_unavailable(monkeypatch):
    class _Boom:
        name = "markllm"

        def __init__(self, **kwargs):
            pass

        def available(self):
            return True

        def detect(self, text):
            raise RuntimeError("detector exploded")

    monkeypatch.setattr(rewrite_text, "MarkLLMTextDetector", _Boom)
    _two_candidates(monkeypatch)
    out, info = rewrite(
        "the cat sat on the mat",
        **_rewrite_candidates_kwargs(markllm_scheme="kgw", markllm_dir="/x"),
    )
    # no verdicts available: the loop runs all attempts, falls back to
    # divergence, and never fails the rewrite
    assert out == "alpha beta gamma delta"
    assert info["passed"] is False
    assert info["attempts_made"] == 2
    entry = info["candidate_scores"][0]["evaluation"]
    assert entry["available"] is False
    assert "exploded" in entry["error"]
    assert info["markllm"]["before"]["available"] is False
    assert info["markllm"]["cleared"] is None


def test_single_candidate_attempt(monkeypatch):
    monkeypatch.setattr(rewrite_text, "MarkLLMTextDetector", _FakeMarkLLM)
    monkeypatch.setattr(rewrite_text, "call_ollama", lambda *a, **k: "REWRITTEN OUTPUT")
    out, info = rewrite(
        "the cat sat on the mat",
        **_rewrite_candidates_kwargs(candidates=1, markllm_scheme="kgw", markllm_dir="/x"),
    )
    assert out == "REWRITTEN OUTPUT"
    assert info["candidates"] == 1
    assert info["max_loops"] == 1
    assert info["attempts_made"] == 1
    assert info["passed"] is True
    assert len(info["candidate_scores"]) == 1
    assert info["candidate_scores"][0]["selected"] is True
    assert info["markllm"]["cleared"] is True


# ---------------------------------------------------------------------------
# HTTP client hardening: default-deny allowlist, scheme guard, no redirects
# ---------------------------------------------------------------------------


def _rewrite_http_kwargs(base_url: str, **overrides):
    kwargs = dict(
        backend="openai-compatible",
        model="m",
        base_url=base_url,
        api_key="sk-test-key-123",
        strength="paraphrase",
        lang="French",
        original_lang="English",
        timeout=5.0,
        layer_a_after=False,
        temperature=0.9,
        candidates=1,
    )
    kwargs.update(overrides)
    return kwargs


def test_check_remote_loopback_allowed_without_opt_in():
    # Must not raise.
    _check_remote("http://127.0.0.1:11434", allow_remote=False)
    _check_remote("http://localhost:11434", allow_remote=False)
    _check_remote("http://[::1]:11434", allow_remote=False)


def test_check_remote_denies_non_loopback_without_opt_in():
    with pytest.raises(SystemExit):
        _check_remote("http://example.com:11434", allow_remote=False)


def test_check_remote_allows_non_loopback_with_opt_in(capsys):
    _check_remote("http://example.com:11434", allow_remote=True)
    err = capsys.readouterr().err
    assert "content will leave this machine" in err


def test_check_remote_denies_non_http_scheme():
    with pytest.raises(SystemExit):
        _check_remote("file:///etc/passwd", allow_remote=True)


def test_flag_env(monkeypatch):
    assert not _flag_env("WATERMARKS_REWRITE_ALLOW_REMOTE")
    monkeypatch.setenv("WATERMARKS_REWRITE_ALLOW_REMOTE", "1")
    assert _flag_env("WATERMARKS_REWRITE_ALLOW_REMOTE")
    monkeypatch.setenv("WATERMARKS_REWRITE_ALLOW_REMOTE", "true")
    assert _flag_env("WATERMARKS_REWRITE_ALLOW_REMOTE")
    monkeypatch.setenv("WATERMARKS_REWRITE_ALLOW_REMOTE", "0")
    assert not _flag_env("WATERMARKS_REWRITE_ALLOW_REMOTE")


def test_openai_compatible_sends_reasoning_effort_when_set():
    captured = {}

    class Collector(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            captured["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices": [{"message": {"content": "rewritten"}}]}')

        def log_message(self, format, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result, _ = rewrite(
            "hello",
            **_rewrite_http_kwargs(
                f"http://127.0.0.1:{server.server_address[1]}",
                reasoning_effort="none",
            ),
        )
        assert result == "rewritten"
        assert captured["body"]["reasoning_effort"] == "none"

        captured.clear()
        rewrite(
            "hello",
            **_rewrite_http_kwargs(
                f"http://127.0.0.1:{server.server_address[1]}",
                reasoning_effort=None,
            ),
        )
        assert "reasoning_effort" not in captured["body"]
    finally:
        server.shutdown()


def test_rewrite_denies_remote_host_without_opt_in():
    with pytest.raises(SystemExit):
        rewrite("secret text", **_rewrite_http_kwargs("http://example.com:11434"))


def test_rewrite_blocks_redirect_and_never_sends_key():
    """A 302 from the (loopback) endpoint must not re-send the API key to the
    redirect target — the request must fail instead."""
    state: dict = {"collector_port": None}
    captured: dict = {}

    class Redirector(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{state['collector_port']}/collect",
            )
            self.end_headers()

        def log_message(self, format, *args):
            pass

    class Collector(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            captured["auth"] = self.headers.get("Authorization")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices": [{"message": {"content": "rewritten"}}]}')

        def log_message(self, format, *args):
            pass

    collector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    redirector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
    state["collector_port"] = collector.server_address[1]
    threading.Thread(target=collector.serve_forever, daemon=True).start()
    threading.Thread(target=redirector.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError):
            rewrite(
                "secret text",
                **_rewrite_http_kwargs(f"http://127.0.0.1:{redirector.server_address[1]}"),
            )
        time.sleep(0.2)
        assert captured == {}, "redirect target received a request (key leak?)"
    finally:
        collector.shutdown()
        redirector.shutdown()
