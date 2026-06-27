"""Static check: every relative link in the image's Markdown resolves to a file.

Runs without daemons (part of `make lint`). Catches a renamed/missing sample or
doc before it ships a dead link.
"""
import re
from pathlib import Path

import pytest

IMAGE_DIR = Path(__file__).resolve().parent.parent
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

MARKDOWN_FILES = sorted(IMAGE_DIR.rglob("*.md"))


def _relative_links(md_path: Path):
    text = md_path.read_text(encoding="utf-8")
    for raw in LINK_RE.findall(text):
        target = raw.split("#", 1)[0].split(" ", 1)[0].strip()
        if not target:
            continue
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if target.startswith("/"):  # absolute repo path, out of scope here
            continue
        yield target


def test_markdown_files_exist():
    assert MARKDOWN_FILES, "expected at least README.md under the image dir"


@pytest.mark.parametrize("md_path", MARKDOWN_FILES, ids=lambda p: p.name)
def test_relative_links_resolve(md_path):
    broken = []
    for target in _relative_links(md_path):
        resolved = (md_path.parent / target).resolve()
        if not resolved.exists():
            broken.append(target)
    assert not broken, f"{md_path.name}: dead relative link(s): {broken}"
