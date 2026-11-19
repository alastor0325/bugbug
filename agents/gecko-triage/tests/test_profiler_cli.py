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
    # A failed download only prints a line, so the check makes it a failure.
    assert "if not b.npm_tools_installed():" in cmd[3]


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
    assert profiler_cli.with_bin_on_path("", bin_dir) == "/root/.mozbuild/bin"


def test_a_successful_install_returns_the_bin_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(profiler_cli.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("SOME_API_KEY", "secret")
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _done(stdout="BIN /root/.mozbuild/bin\n")

    found = profiler_cli.install(REPO, run=run)
    assert found == Path("/root/.mozbuild/bin")
    ((cmd, kwargs),) = calls
    assert cmd == profiler_cli.install_command(REPO)
    assert kwargs["cwd"] == REPO
    assert kwargs["timeout"] == profiler_cli.INSTALL_TIMEOUT
    assert Path(kwargs["env"]["MOZBUILD_STATE_PATH"]).parent == tmp_path
    assert kwargs["env"]["SOME_API_KEY"] == ""


def test_the_install_gets_a_state_dir_of_its_own(tmp_path, monkeypatch):
    monkeypatch.setattr(profiler_cli.tempfile, "tempdir", str(tmp_path))
    seen = []

    def run(cmd, **kwargs):
        seen.append(kwargs["env"]["MOZBUILD_STATE_PATH"])
        return _done(stdout="BIN /x\n")

    profiler_cli.install(REPO, run=run)
    profiler_cli.install(REPO, run=run)
    assert len(set(seen)) == 2
    assert all(Path(s).parent == tmp_path for s in seen)


def test_a_failed_install_returns_none_and_the_run_goes_on(
    capsys, monkeypatch, tmp_path
):
    monkeypatch.setattr(profiler_cli.tempfile, "tempdir", str(tmp_path))

    def failing(cmd, **kwargs):
        return _done(returncode=1, stdout="Could not install node.", stderr="")

    def no_marker(cmd, **kwargs):
        return _done(stdout="Could not install profiler-cli.\n")

    def timed_out(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    for run in (failing, no_marker, timed_out, missing):
        assert profiler_cli.install(REPO, run=run) is None
    err = capsys.readouterr().err
    assert err.count("profiler-cli from the tree") == 4
    assert "Could not install node." in err
