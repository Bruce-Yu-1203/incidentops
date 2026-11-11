"""The offline lens asset stays reproducible from the original radial formula."""

import subprocess
import sys
from pathlib import Path


def test_displacement_texture_matches_the_generator() -> None:
    generator = Path(__file__).resolve().parents[2] / "scripts/generate_chat_displacement.py"
    subprocess.run([sys.executable, str(generator), "--check"], check=True)
