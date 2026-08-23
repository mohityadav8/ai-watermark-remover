"""score_synthid.py --json must emit pure JSON on stdout.

The reverse-SynthID upstream prints progress ("CodebookV4 loaded: ...")
straight to stdout. image_meta.py json.loads the scorer's stdout, so any
such leak corrupts the score payload. This test drives the real script as
a subprocess against a stub upstream whose classes are deliberately noisy,
and fails if a single non-JSON byte reaches stdout.

The stub extraction dir also ships a fake ``cv2`` module: the script does
``sys.path.insert(0, extraction)`` before ``import cv2``, so the test runs
without OpenCV installed.
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "service" / "scripts" / "score_synthid.py"


def _write_stub_upstream(root: Path) -> Path:
    ext = root / "src" / "extraction"
    ext.mkdir(parents=True)
    (root / "artifacts").mkdir()
    (root / "artifacts" / "spectral_codebook_v4.npz").touch()

    (ext / "cv2.py").write_text(
        "COLOR_BGR2RGB = 4\n"
        "def imread(path):\n"
        "    return object()\n"
        "def cvtColor(img, code):\n"
        "    return img\n"
    )
    (ext / "synthid_bypass_v4.py").write_text(
        "class SpectralCodebookV4:\n"
        "    def load(self, path):\n"
        "        print(f'CodebookV4 loaded: {path}')\n"  # the noisy print
    )
    (ext / "robust_extractor.py").write_text(
        "from types import SimpleNamespace\n"
        "class RobustSynthIDExtractor:\n"
        "    def detect_from_v4_codebook(self, rgb, codebook, model=None):\n"
        "        print('extractor progress chatter')\n"  # more stdout noise
        "        return SimpleNamespace(\n"
        "            details={'profile_key': 'stub', 'exact_match': False,\n"
        "                     'per_channel_scores': [0.1], 'per_channel_n': [1]},\n"
        "            is_watermarked=False, confidence=0.42,\n"
        "            phase_match=0.0, multi_scale_consistency=0.0,\n"
        "        )\n"
    )
    return root


def test_json_stdout_survives_noisy_upstream(tmp_path):
    upstream = _write_stub_upstream(tmp_path / "upstream")
    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nstub")

    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(img), "--upstream-dir", str(upstream), "--json"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)  # corrupt stdout fails here
    assert payload["available"] is True
    assert payload["confidence"] == 0.42
    # the noise must land on stderr, not vanish
    assert "CodebookV4 loaded" in r.stderr
    assert "extractor progress chatter" in r.stderr
