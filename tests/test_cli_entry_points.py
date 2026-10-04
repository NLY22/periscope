"""Guards for the commands a user pastes into a shell.

`uv run <cmd>` appears only in error and help paths, so no test reaches it
during normal work and a stale name sits there unnoticed. This fork renamed
every console script from `periscope-*` to `periscope-*`, and three CLIs kept
printing `uv run periscope-wizard` afterwards - a command that does not exist.
"""

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UV_RUN = re.compile(r"uv run ([a-z][a-z0-9_-]*)")
# Only this project's own scripts are checked: `uv run playwright install
# chromium` names a dependency's entrypoint, which pyproject's scripts table
# cannot vouch for either way.
OWN_COMMANDS = ("periscope", "periscope")


def installed_scripts() -> set:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return set(tomllib.load(handle)["project"]["scripts"])


def suggested_commands() -> list:
    """(file, line, command) for every copy-pasteable hint in user-facing text."""
    surfaces = [REPO_ROOT / "README.md"]
    surfaces += sorted((REPO_ROOT / "src").rglob("*.py"))
    surfaces += sorted((REPO_ROOT / "docs").glob("*.md"))
    found = []
    for path in surfaces:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in UV_RUN.finditer(line):
                found.append(
                    (path.relative_to(REPO_ROOT).as_posix(), lineno, match.group(1))
                )
    return found


def test_every_hinted_command_of_ours_is_installed():
    installed = installed_scripts()
    ours = [hit for hit in suggested_commands() if hit[2].startswith(OWN_COMMANDS)]

    assert [hit for hit in ours if hit[2] not in installed] == []
    # Non-vacuity: the scan has to actually see the hints it is guarding, or a
    # broken regex would pass just as quietly as the stale names did.
    assert len(ours) >= 3


def test_scan_would_catch_a_renamed_command():
    # The real defect, replayed: a stale name in prose has to be caught by this
    # same scan, or a broken regex would pass as quietly as the stale names did.
    # The ghost command is built at runtime so writing it down here cannot make
    # it a real command.
    ghost = "periscope-" + "wizardx"
    assert UV_RUN.findall(f"Run `uv run {ghost}` to set up.") == [ghost]
    assert ghost not in installed_scripts()
    assert "periscope-wizard" in installed_scripts()
