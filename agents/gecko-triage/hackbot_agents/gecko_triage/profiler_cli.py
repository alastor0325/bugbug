"""profiler-cli from the Firefox tree, the version the tree's own tools use.

`mach bootstrap` installs it from a toolchain artifact that follows the Firefox Profiler
deploys, so a new profile format is readable once the tree updates it. A run installs
only that step, `ensure_npm_tools`, which also brings the tree's Node, into a state
directory of its own, so its launcher directory holds only the npm tools.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from .isolation import hidden_env

INSTALL_TIMEOUT = 600
_MARK = "BIN "
_INSTALL = f"""
import sys
from pathlib import Path
from mach.util import get_state_dir
from mozboot.base import BaseBootstrapper
b = BaseBootstrapper(no_interactive=True)
b.state_dir = Path(get_state_dir())
b.ensure_npm_tools()
if not b.npm_tools_installed():
    sys.exit("the npm tools were not installed")
print({_MARK!r} + str(b.mozbuild_bin_dir()))
"""


def install_command(repo: Path) -> list[str]:
    return [str(repo / "mach"), "python", "-c", _INSTALL]


def bin_dir(output: str) -> Path | None:
    """The directory with the `profiler-cli` launcher, from the install's output."""
    for line in output.splitlines():
        if line.startswith(_MARK):
            return Path(line[len(_MARK) :].strip())
    return None


def with_bin_on_path(path: str, bin_dir: Path) -> str:
    """``path`` with ``bin_dir`` first, so the tree's `profiler-cli` wins."""
    rest = [p for p in path.split(os.pathsep) if p and p != str(bin_dir)]
    return os.pathsep.join([str(bin_dir), *rest])


def install(
    repo: Path, run: Callable = subprocess.run, state_dir: Path | None = None
) -> Path | None:
    """Install the tree's `profiler-cli` and return its launcher directory, or None.

    A failure does not fail the run: on a host, a `profiler-cli` already on PATH stays
    in use. The image has no other `profiler-cli`, so there the agent reports the
    profiles as unread. The install gets no secret of the agent's environment.
    """
    state_dir = state_dir or Path(tempfile.mkdtemp(prefix="gecko-triage-mozbuild-"))
    env = {
        **os.environ,
        **hidden_env(os.environ),
        "MOZBUILD_STATE_PATH": str(state_dir),
    }
    try:
        done = run(
            install_command(repo),
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[gecko_triage] profiler-cli from the tree: {e}", file=sys.stderr)
        return None
    found = bin_dir(done.stdout) if done.returncode == 0 else None
    if found is None:
        tail = f"{done.stdout}\n{done.stderr}".strip()[-500:]
        print(
            f"[gecko_triage] profiler-cli from the tree was not installed "
            f"(exit {done.returncode}): {tail}",
            file=sys.stderr,
        )
    return found
