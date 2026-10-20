from pathlib import Path

import pytest
from hackbot_agents.review_load.config import (
    CONFIGS_DIR,
    ReviewLoadConfig,
    load_config,
)
from hackbot_runtime import AgentError
from pydantic import ValidationError

MINIMAL = """
review_group = "example-reviewers"

[members]
alice = "Alice Example"

[report]
slack_channel = "#example"
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    (tmp_path / f"{name}.toml").write_text(body)
    return tmp_path


def test_minimal_config_gets_the_defaults(tmp_path):
    config = load_config("example", _write(tmp_path, "example", MINIMAL))
    assert config.name == "example"
    assert config.review_group == "example-reviewers"
    assert config.members == {"alice": "Alice Example"}
    assert config.report.new_work_days == 1
    assert config.report.overdue_work_days == 7
    assert config.authors.staff_email_domains == ["@mozilla.com"]
    assert config.report.stale_work_days == 60
    assert config.paths == []


def test_unknown_config_stops_the_run(tmp_path):
    with pytest.raises(AgentError, match="nope"):
        load_config("nope", tmp_path)


@pytest.mark.parametrize("name", ["../secrets", "a/b", "", "Media"])
def test_config_name_cannot_leave_the_directory(tmp_path, name):
    with pytest.raises(AgentError):
        load_config(name, tmp_path)


def test_unknown_key_is_refused(tmp_path):
    with pytest.raises(AgentError, match="typo_key"):
        load_config("bad", _write(tmp_path, "bad", MINIMAL + "typo_key = 1\n"))


def _config(**report) -> ReviewLoadConfig:
    return ReviewLoadConfig.model_validate(
        {
            "name": "x",
            "review_group": "g",
            "report": {"slack_channel": "#x", **report},
        }
    )


@pytest.mark.parametrize(
    "limits",
    [
        {"new_work_days": 7},
        {"new_work_days": 8},
        {"overdue_work_days": 60},
        {"overdue_work_days": 30, "stale_work_days": 20},
    ],
)
def test_limits_must_ascend(limits):
    with pytest.raises(ValidationError):
        _config(**limits)


@pytest.mark.parametrize(
    "field", ["new_work_days", "overdue_work_days", "stale_work_days"]
)
def test_limits_must_be_positive(field):
    with pytest.raises(ValidationError):
        _config(**{field: 0})


@pytest.mark.parametrize("removed", ["age_buckets_work_days", "max_listed"])
def test_removed_keys_are_refused(removed):
    with pytest.raises(ValidationError):
        _config(**{removed: 1})


def test_blank_channel_is_refused():
    with pytest.raises(ValidationError):
        _config(slack_channel="")


def test_email_domains_start_with_at():
    with pytest.raises(ValidationError):
        ReviewLoadConfig.model_validate(
            {
                "name": "x",
                "review_group": "g",
                "report": {"slack_channel": "#x"},
                "authors": {"staff_email_domains": ["mozilla.com"]},
            }
        )


def test_every_shipped_config_loads():
    names = [path.stem for path in CONFIGS_DIR.glob("*.toml")]
    assert "media-playback" in names
    for name in names:
        load_config(name)


def test_media_playback_config():
    config = load_config("media-playback")
    assert config.review_group == "media-playback-reviewers"
    assert config.report.slack_channel == "#media-playback"
    assert "alwu" in config.members


def test_paths_are_normalised(tmp_path):
    body = MINIMAL.replace(
        "[members]",
        'paths = ["/dom/media/"]\nexcludes = ["dom/media/webrtc/"]\n\n[members]',
    )
    config = load_config("p", _write(tmp_path, "p", body))
    assert config.paths == ["dom/media"]
    assert config.excludes == ["dom/media/webrtc"]


def test_media_playback_scope():
    config = load_config("media-playback")
    assert config.paths == ["dom/media"]
    assert "dom/media/webrtc" in config.excludes
    assert config.report.stale_work_days == 60
