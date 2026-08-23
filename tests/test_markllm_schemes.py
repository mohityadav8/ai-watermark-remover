"""Multi-scheme support in the MarkLLM text harness.

CI-covered assertions that the extended scheme surface remains backward
compatible: new scheme keys are additive, the bench's default is still
synthid, and the rewrite loop accepts the new schemes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from detect_text_watermark import SCHEMES


def test_scheme_map_covers_all_schemes() -> None:
    assert SCHEMES["kgw"] == "KGW"
    assert SCHEMES["synthid"] == "SynthID"
    assert SCHEMES["synthid-text"] == "SynthID"
    # Additional schemes: EXP (Gumbel), Unigram, SIR.
    assert SCHEMES["exp"] == "EXP"
    assert SCHEMES["unigram"] == "Unigram"
    assert SCHEMES["sir"] == "SIR"


def test_scheme_map_is_additive_over_legacy_keys() -> None:
    # The legacy surface must remain exactly as shipped (backward compat).
    legacy = {"kgw": "KGW", "synthid": "SynthID", "synthid-text": "SynthID"}
    for key, alg in legacy.items():
        assert SCHEMES[key] == alg


def test_bench_scheme_flag_defaults_to_synthid() -> None:
    from bench_synthid_text import build_parser

    parser = build_parser()
    args = parser.parse_args(["--markllm-dir", ".", "--rewrite-model", "m"])
    assert args.scheme == "synthid"
    assert args.config is None
    scheme_actions = [a for a in parser._actions if a.dest == "scheme" and a.choices]
    assert scheme_actions, "no --scheme action with choices"
    assert set(scheme_actions[0].choices) >= {"kgw", "synthid", "exp", "unigram", "sir"}


def test_rewrite_accepts_new_schemes() -> None:
    """--markllm-scheme must accept exp/unigram/sir for the adaptive loop."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "rewrite_text.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    help_text = proc.stdout + proc.stderr
    assert "--markllm-scheme" in help_text
    for scheme in ("exp", "unigram", "sir"):
        assert scheme in help_text
