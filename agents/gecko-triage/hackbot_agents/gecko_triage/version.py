"""The version of the code that made a run, recorded as `findings.agent_version`.

A local run reads the short commit of its git checkout. The Docker image has no `.git`,
so it reads `GECKO_TRIAGE_COMMIT`, which the image takes from the `GIT_COMMIT` build
arg. Without either, the version is the package version with `+unknown`.
"""

import os
import subprocess
import sys
from collections.abc import Mapping
from functools import cache
from importlib import metadata
from pathlib import Path

PACKAGE = "hackbot-agent-gecko-triage"
COMMIT_ENV = "GECKO_TRIAGE_COMMIT"
HERE = Path(__file__).resolve().parent


def git_commit(where: Path = HERE) -> str | None:
    """The short commit of the git checkout that holds ``where``, or None.

    A tree with changes gets `-dirty`. Tags are left out, so the value is always the
    commit. Any failure gives None, so the next fallback runs.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(where), "describe", "--always", "--dirty", "--exclude=*"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception:  # see docstring
        return None
    return done.stdout.strip() or None


def package_version() -> str:
    try:
        return metadata.version(PACKAGE)
    except metadata.PackageNotFoundError:
        return "0.0.0"


def agent_version(where: Path = HERE, env: Mapping[str, str] = os.environ) -> str:
    """The git commit, else the image's commit, else the package version."""
    return (
        git_commit(where)
        or (env.get(COMMIT_ENV) or "").strip()
        or f"{package_version()}+unknown"
    )


@cache
def current_agent_version() -> str:
    """This process's `agent_version`. It never raises: an unknown version is no error."""
    try:
        return agent_version()
    except Exception as e:  # see docstring
        print(f"[gecko_triage] agent version unknown ({e})", file=sys.stderr)
        return "unknown"
