"""Guard against broken relative links in repository documentation."""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)#\s]+)(?:#[^)]*)?\)")


def _markdown_files() -> list[Path]:
    files = [REPO_ROOT / "README.md", REPO_ROOT / "constitute.md"]
    files.extend(sorted((REPO_ROOT / "docs").rglob("*.md")))
    return files


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: p.name)
def test_relative_links_resolve(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for target in LINK_PATTERN.findall(text):
        if "://" in target or target.startswith("mailto:"):
            continue
        assert (path.parent / target).exists(), f"{path.name}: broken link {target}"


def test_constitution_exists_and_lists_core_rules() -> None:
    text = (REPO_ROOT / "constitute.md").read_text(encoding="utf-8")
    assert "Conventional Commits" in text
    assert "English only" in text
    assert "No agent write credentials" in text
