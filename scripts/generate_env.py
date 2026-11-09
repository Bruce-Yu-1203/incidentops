"""Create a local ``.env`` from ``.env.example`` with fresh random tokens.

Every distinct ``change-me-*`` placeholder becomes its own random token, so no
two identities share a credential. Repeating the same placeholder links two
variables to one value (for example a client and the server that verifies it).
Existing ``.env`` files are never overwritten.
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"change-me-[a-z-]+")


def render(template: str) -> str:
    """Replace each placeholder with a unique random hex token."""
    values: dict[str, str] = {}

    def token(match: re.Match[str]) -> str:
        return values.setdefault(match.group(0), secrets.token_hex(16))

    rendered = PLACEHOLDER.sub(token, template)
    # The local control database password is fixed by compose.yaml defaults.
    return rendered.replace(
        "incidentops:" + rendered.split("incidentops:", 1)[1].split("@", 1)[0] + "@",
        "incidentops:incidentops@",
        1,
    )


def main(root: Path) -> int:
    target = root / ".env"
    if target.exists():
        print(f"{target} already exists; leaving it unchanged")
        return 0
    target.write_text(render((root / ".env.example").read_text(encoding="utf-8")), "utf-8")
    target.chmod(0o600)
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(__file__).resolve().parents[1]))
