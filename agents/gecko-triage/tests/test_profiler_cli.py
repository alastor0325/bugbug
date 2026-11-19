import subprocess
from pathlib import Path

from hackbot_agents.gecko_triage import profiler_cli

REPO = Path("/workspace/firefox")


def _done(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_the_install_runs_the_trees_npm_tools_step_through_mach():
    cmd = profiler_cli.install_command(REPO)
    assert cmd[:3] == [str(REPO / "mach"), "python", "-c"]
    assert "ensure_npm_tools()" in cmd[3]


def test_the_bin_dir_comes_from_the_marked_line():
    out = "Installing npm tools: profiler-cli...\nBIN /root/.mozbuild/bin\n"
    assert profiler_cli.bin_dir(out) == Path("/root/.mozbuild/bin")
    assert profiler_cli.bin_dir("Installing npm tools: profiler-cli...\n") is None
    assert profiler_cli.bin_dir("") is None


def test_the_bin_dir_goes_first_on_path_once():
    bin_dir = Path("/root/.mozbuild/bin")
    assert profiler_cli.with_bin_on_path("/usr/bin:/bin", bin_dir) == (
        "/root/.mozbuild/bin:/usr/bin:/bin"
    )
    assert profiler_cli.with_bin_on_path("/root/.mozbuild/bin:/bin", bin_dir) == (
        "/root/.mozbuild/bin:/bin"
    )
    assert profiler_cli.with_bin_on_path("", bin_dir) == "/root/.mozbuild/bin"


def test_a_successful_install_returns_the_bin_dir():
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _done(stdout="BIN /root/.mozbuild/bin\n")

    assert profiler_cli.install(REPO, run=run) == Path("/root/.mozbuild/bin")
    ((cmd, kwargs),) = calls
    assert cmd == profiler_cli.install_command(REPO)
    assert kwargs["cwd"] == REPO
    assert kwargs["timeout"] == profiler_cli.INSTALL_TIMEOUT


def test_a_failed_install_returns_none_and_the_run_goes_on(capsys):
    def failing(cmd, **kwargs):
        return _done(returncode=1, stderr="JjVersionError")

    def no_marker(cmd, **kwargs):
        return _done(stdout="Could not install profiler-cli.\n")

    def timed_out(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    for run in (failing, no_marker, timed_out, missing):
        assert profiler_cli.install(REPO, run=run) is None
    assert capsys.readouterr().err.count("profiler-cli from the tree") == 4
