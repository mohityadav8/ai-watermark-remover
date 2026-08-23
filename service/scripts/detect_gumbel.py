#!/usr/bin/env python3
"""Model-free keyed-Gumbel (Aaronson EXP) text-watermark detector.

Implements the detection arithmetic of the ARBI keyed-Gumbel technical report
(Sections 2-3): replay the keyed sampler's noise from the text alone and test
whether the observed tokens look like winners of keyed draws.

    seed = Hash(key, last H tokens)          H = 4 by default
    u_t  = PRF(seed, token_t)                -> replayable from text alone
    S    = sum_t -log(1 - u_t)               ~ Gamma(counted, 1) under the null
    p    = P(Gamma(counted, 1) >= S)         exact for integer shape

Repeated context windows are masked (the generator falls back to ordinary
randomness on recurrence; the detector applies the same skip rule), so reused
windows contribute no evidence. Positions with fewer than H preceding tokens
have no full window and are not counted either.

Stdlib-only: the p-value uses the exact Poisson-sum identity for an integer
Gamma shape (upper regularized incomplete gamma), so scipy is never required.

Honesty caveat (same as the MarkLLM harness): detection is a *same-key replay*
— it is valid only against the same key, tokenizer, and PRF layout used at
generation (self-hosted engines such as arbi-serve). A negative result
establishes nothing: unwatermarked text, another provider's key, and human
text all sit at chance. This detector is not a vendor oracle.

The default PRF layout here is HMAC-SHA256 over packed token ids. It is a
clean-room, auditable instantiation of the scheme, not bit-compatible with any
specific engine's kernel: for exact replay against a real engine, pass its
token ids (--tokens) and, if the engine uses a different PRF, reimplement the
two functions in this module accordingly.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import struct
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import emit_json, eprint, read_text_input

DEFAULT_WINDOW = 4
DEFAULT_THRESHOLD = 1e-6

_ID_PACK = struct.Struct(">Q")
_SIMPLE_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _token_id(tok: str) -> int:
    """Deterministic 64-bit id for a simple-tokenizer token string."""
    return int.from_bytes(hashlib.sha256(tok.encode("utf-8")).digest()[:8], "big")


def tokenize_simple(text: str) -> list[int]:
    """Deterministic word/run tokenizer -> stable token ids.

    Convenience path for testing and quick checks. Exact replay against a real
    engine requires the engine's own tokenizer: use --tokens with its ids.
    """
    return [_token_id(t) for t in _SIMPLE_TOKEN_RE.findall(text.lower())]


def load_token_ids(raw: str) -> list[int]:
    """Parse a token-id input: a JSON array, or one integer per line."""
    stripped = raw.strip()
    if stripped.startswith("["):
        data = json.loads(stripped)
        if not isinstance(data, list) or not all(isinstance(x, int) for x in data):
            raise ValueError("token-id JSON must be an array of integers")
        return data
    ids: list[int] = []
    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        ids.append(int(line, 0))
    return ids


def _normalize_key(raw: str) -> bytes:
    """Key -> bytes: 0x<hex> decodes to raw bytes, anything else is UTF-8."""
    s = raw.strip()
    if s.startswith("0x") or s.startswith("0X"):
        hexpart = s[2:]
        if (
            not hexpart
            or len(hexpart) % 2
            or any(c not in "0123456789abcdefABCDEF" for c in hexpart)
        ):
            raise ValueError("invalid hex key (expected 0x followed by even-length hex)")
        return bytes.fromhex(hexpart)
    return s.encode("utf-8")


def _seed(key: bytes, window: tuple[int, ...]) -> bytes:
    """Context-window seed: HMAC-SHA256(key, packed window)."""
    packed = b"".join(_ID_PACK.pack(t) for t in window)
    return hmac.new(key, packed, hashlib.sha256).digest()


def _uniform(seed: bytes, token_id: int) -> float:
    """Per-candidate uniform in (0, 1): HMAC-SHA256(seed, token id)."""
    digest = hmac.new(seed, _ID_PACK.pack(token_id), hashlib.sha256).digest()
    return (int.from_bytes(digest[:8], "big") + 0.5) / (1 << 64)


def _poisson_survival(s: float, n: int) -> float:
    """P(Gamma(n, 1) >= s) = e^{-s} * sum_{k=0}^{n-1} s^k / k! (exact for integer n).

    Computed via logsumexp so large statistics never overflow or NaN.
    """
    if n <= 0 or s <= 0.0:
        return 1.0
    lns = math.log(s)
    log_term = 0.0  # k = 0 term is s^0 / 0! = 1
    maxv = 0.0
    for k in range(1, n):
        log_term += lns - math.log(k)
        if log_term > maxv:
            maxv = log_term
    log_term = 0.0
    acc = math.exp(-maxv)  # k = 0 term
    for k in range(1, n):
        log_term += lns - math.log(k)
        acc += math.exp(log_term - maxv)
    p = math.exp(-s + maxv + math.log(acc))
    if p < 0.0:
        return 0.0
    if p > 1.0:
        return 1.0
    return p


def detect_token_ids(
    token_ids: Sequence[int],
    key: str | bytes,
    *,
    window: int = DEFAULT_WINDOW,
    threshold: float = DEFAULT_THRESHOLD,
    mask_repeated: bool = True,
) -> dict[str, Any]:
    """Run the keyed-Gumbel replay test over a sequence of token ids.

    mask_repeated mirrors the generator's repeated-window masking: a context
    window is counted only on its first occurrence (the generator fell back to
    ordinary randomness on recurrence, so later occurrences carry no signal).
    Positions with fewer than window preceding tokens are never counted.
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be in (0, 1)")
    key_bytes = _normalize_key(key) if isinstance(key, str) else bytes(key)
    ids = list(token_ids)
    for t in ids:
        if not isinstance(t, int) or isinstance(t, bool) or t < 0 or t > (1 << 64) - 1:
            raise ValueError(f"token id out of range: {t!r}")

    total = len(ids)
    statistic = 0.0
    counted = 0
    seen_windows: set[tuple[int, ...]] = set()
    skipped_repeated = 0
    for t in range(window, total):
        win = tuple(ids[t - window : t])
        if mask_repeated:
            if win in seen_windows:
                skipped_repeated += 1
                continue
            seen_windows.add(win)
        u = _uniform(_seed(key_bytes, win), ids[t])
        statistic += -math.log1p(-u)
        counted += 1

    skipped_no_context = min(window, total)
    report: dict[str, Any] = {
        "detector": "gumbel",
        "scheme": "exp",
        "vendor": "self-hosted",
        "available": True,
        "window": window,
        "threshold": threshold,
        "tokens_total": total,
        "skipped_no_context": skipped_no_context,
        "skipped_repeated": skipped_repeated,
        "counted": counted,
    }
    if counted == 0:
        report["is_watermarked"] = False
        report["p_value"] = 1.0
        report["score"] = 0.0
        report["note"] = "no verifiable token positions (text too short for a full context window)"
        return report
    p = _poisson_survival(statistic, counted)
    report["statistic"] = round(statistic, 6)
    report["p_value"] = p
    report["score"] = round(-math.log10(p) if p > 0.0 else 300.0, 6)
    report["is_watermarked"] = p < threshold
    report["note"] = (
        "same-key replay of the keyed-Gumbel (Aaronson EXP) watermark; valid only "
        "against the same key, tokenizer, and PRF layout used at generation"
    )
    return report


def detect_text(
    text: str,
    key: str | bytes,
    *,
    window: int = DEFAULT_WINDOW,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Run the replay test over plain text via the deterministic tokenizer."""
    return detect_token_ids(tokenize_simple(text), key, window=window, threshold=threshold)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", nargs="?", default="-", help="Text file, or - for stdin")
    (
        p.add_argument(
            "--tokens",
            action="store_true",
            help="Treat the input as token ids (JSON array or one integer per line) "
            "instead of text — required for exact replay with an engine's tokenizer",
        ),
    )
    (
        p.add_argument(
            "--key",
            default=os.environ.get("WATERMARKS_GUMBEL_KEY"),
            help="Watermark key (0x<hex> or a string); default: $WATERMARKS_GUMBEL_KEY. "
            "Preferred via env — keys on argv are visible in ps/history.",
        ),
    )
    (
        p.add_argument(
            "--window",
            type=int,
            default=DEFAULT_WINDOW,
            help=f"Context window size H in tokens (default: {DEFAULT_WINDOW})",
        ),
    )
    (
        p.add_argument(
            "--threshold",
            type=float,
            default=DEFAULT_THRESHOLD,
            help=f"p-value threshold for is_watermarked (default: {DEFAULT_THRESHOLD:g})",
        ),
    )
    p.add_argument("--json", action="store_true", help="Emit the report as JSON on stdout")
    (
        p.add_argument(
            "--force-text",
            action="store_true",
            help="Process input even when it looks like a binary container",
        ),
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    if not args.key:
        eprint("error: no watermark key (pass --key or set WATERMARKS_GUMBEL_KEY)")
        return 2
    raw = read_text_input(args.path, allow_binary=args.force_text)
    try:
        if args.tokens:
            report = detect_token_ids(
                load_token_ids(raw),
                args.key,
                window=args.window,
                threshold=args.threshold,
            )
        else:
            report = detect_text(raw, args.key, window=args.window, threshold=args.threshold)
    except (ValueError, json.JSONDecodeError) as e:
        eprint(f"error: {e}")
        return 2
    if args.json:
        emit_json(report)
    else:
        print(
            f"keyed-Gumbel (EXP) detection: watermarked={report['is_watermarked']} "
            f"p={report['p_value']:.3g} (threshold {report['threshold']:g}) "
            f"counted={report['counted']}/{report['tokens_total']} tokens, skipped "
            f"{report['skipped_no_context'] + report['skipped_repeated']} "
            f"(no-context {report['skipped_no_context']}, repeated-window "
            f"{report['skipped_repeated']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
