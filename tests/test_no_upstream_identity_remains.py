"""The repository must not carry the previous project's identity.

This started as a rename: the code inherited a name, an env prefix, an MCP tool
prefix, a site theme, sponsor slots and contact addresses from the project it was
forked from. A half-done rename is worse than none, because what survives reads
like the parts that mattered were skipped.

So the rule is a scan, not a promise: every tracked text file is checked, and the
allowlist is narrow and *asserted to be used* -- an exemption that matches nothing
is stale and gets reported.

The forbidden strings are assembled at runtime. This file is itself a tracked text
file, so a literal here would make its own target legal and the guard would go
blind while still passing -- the trap the symbol net documents for itself.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Assembled, never written down whole. The *variable names* are also chosen so
# they do not contain a target -- a name like OLD_`hori`+`zon` would make this
# file report itself, which is how a self-check turns into noise.
OLD_PROJECT = "hori" + "zon"       # the previous project name, any case
OLD_PREFIX = "hz" + "_"            # its MCP tool prefix
OLD_PEOPLE = "Thy" + "srael"        # its author, as a contact or a link
OLD_HOST = "1123" + ".top"          # its hosted demo / API domains
OLD_NAMES = (OLD_PROJECT, OLD_PREFIX, OLD_PEOPLE, OLD_HOST)

# Where an old name is legitimate, with the reason. Keep this short: every entry
# is a place the identity is allowed to survive.
ALLOWLIST = {
    # MIT's obligation is to keep the copyright notice, and that notice names them.
    "LICENSE": (OLD_PEOPLE,),
    # A changelog and the phase plans record what things were *called* then.
    "CHANGELOG.md": (OLD_PROJECT, OLD_PREFIX, OLD_HOST),
    "docs/superpowers/": (OLD_PROJECT, OLD_PREFIX, OLD_PEOPLE),
}

_BINARY = re.compile(r"\.(png|jpe?g|gif|ico|woff2?|graffle|pyc|db|zip|lock)$", re.I)


def _allowed_for(relative: str) -> set[str]:
    allowed: set[str] = set()
    for prefix, tokens in ALLOWLIST.items():
        if relative.startswith(prefix):
            allowed.update(token.lower() for token in tokens)
    return allowed


def offenders_in(relative: str, text: str) -> list[str]:
    """Which old names this file carries and is not exempted for."""
    lowered = text.lower()
    allowed = _allowed_for(relative)
    return [
        token for token in OLD_NAMES
        if token.lower() in lowered and token.lower() not in allowed
    ]


def _tracked_text_files() -> list[tuple[str, str]]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(REPO_ROOT), capture_output=True, check=True
    ).stdout.decode("utf-8")
    out: list[tuple[str, str]] = []
    for name in listing.split("\0"):
        if not name or _BINARY.search(name):
            continue
        path = REPO_ROOT / name
        if path.is_file():
            out.append((name, path.read_text(encoding="utf-8", errors="replace")))
    return out


def test_no_old_identity_outside_the_allowlist() -> None:
    offenders = {
        name: found
        for name, text in _tracked_text_files()
        if (found := offenders_in(name, text))
    }
    assert not offenders, f"the old identity survives in: {offenders}"


def test_this_guard_carries_no_whole_old_name() -> None:
    """The reflexivity check, run on the guard itself rather than assumed.

    Every forbidden token here is built by concatenation, so the file's own text
    must scan clean. If someone "simplifies" it into literals, the exemption
    would come from this file and the scan would stop seeing the name anywhere.
    """
    here = Path(__file__).name
    text = Path(__file__).read_text(encoding="utf-8")
    assert offenders_in(f"tests/{here}", text) == [], "this file wrote a target down whole"


def test_every_allowlist_entry_is_actually_used() -> None:
    """An exemption nobody needs should be deleted, not carried forward.

    Without this the allowlist is where the guard goes to quietly stop working:
    add one line and every future leak in that file passes forever.
    """
    dead = []
    for prefix, tokens in ALLOWLIST.items():
        target = REPO_ROOT / prefix
        files = [target] if target.is_file() else [
            p for p in target.rglob("*") if p.is_file() and not _BINARY.search(p.name)
        ]
        text = "".join(p.read_text(encoding="utf-8", errors="replace").lower() for p in files)
        for token in tokens:
            if token.lower() not in text:
                dead.append(f"{prefix}: {token}")
    assert not dead, f"allowlisted but absent, so the exemption is stale: {dead}"


def test_the_scan_would_report_a_planted_name() -> None:
    """Bite proof in both directions, on synthetic inputs.

    A planted old name has to surface in an ordinary file and has to stay quiet
    inside LICENSE, whose copyright line is the one place it must remain.
    """
    planted = f"# {OLD_PROJECT} setup\nset {OLD_PREFIX}run_pipeline and {OLD_HOST}\n"
    assert offenders_in("docs/guide.md", planted) == [OLD_PROJECT, OLD_PREFIX, OLD_HOST]
    assert offenders_in("LICENSE", f"Copyright (c) 2026 {OLD_PEOPLE}\n{OLD_PROJECT}\n") == [
        OLD_PROJECT
    ]
    assert offenders_in("README.md", "# Periscope\n") == []
