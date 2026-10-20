"""The per-group configs in ``configs/<name>.toml``."""

import re
import tomllib
from pathlib import Path
from typing import Annotated

from hackbot_runtime import AgentError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

CONFIGS_DIR = Path(__file__).parent / "configs"

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")

PositiveInt = Annotated[int, Field(gt=0)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReportSettings(_Strict):
    slack_channel: Annotated[str, Field(min_length=1)]
    # Ages in work days from the review request. Below `new_work_days` a review is
    # new; from `overdue_work_days` it is overdue; from `stale_work_days` it is only
    # counted as stale.
    new_work_days: PositiveInt = 1
    overdue_work_days: PositiveInt = 7
    stale_work_days: PositiveInt = 60

    @model_validator(mode="after")
    def _ascending(self) -> "ReportSettings":
        if not self.new_work_days < self.overdue_work_days < self.stale_work_days:
            raise ValueError("need new_work_days < overdue_work_days < stale_work_days")
        return self


class AuthorSettings(_Strict):
    staff_email_domains: list[str] = ["@mozilla.com"]
    # Phabricator handles that the domain check gets wrong.
    staff_accounts: list[str] = []
    external_accounts: list[str] = []

    @field_validator("staff_email_domains")
    @classmethod
    def _domains(cls, domains: list[str]) -> list[str]:
        bad = [d for d in domains if not d.startswith("@") or len(d) < 2]
        if bad:
            raise ValueError(f"domains must start with '@': {', '.join(bad)}")
        return [d.lower() for d in domains]


class ReviewLoadConfig(_Strict):
    name: str
    review_group: Annotated[str, Field(min_length=1)]
    # Phabricator handle -> display name.
    members: dict[str, str] = {}
    # A request to a member, not the group, counts only if the revision changes a
    # file under one of `paths` and not under one of `excludes`. Empty: all count.
    paths: list[str] = []
    excludes: list[str] = []
    report: ReportSettings
    authors: AuthorSettings = AuthorSettings()

    @field_validator("paths", "excludes")
    @classmethod
    def _strip_slashes(cls, paths: list[str]) -> list[str]:
        return [p.strip("/") for p in paths]

    @property
    def group_label(self) -> str:
        return f"#{self.review_group}"


def load_config(name: str, configs_dir: Path = CONFIGS_DIR) -> ReviewLoadConfig:
    """Load ``configs_dir/<name>.toml``, failing the run on an unknown or invalid config."""
    if not _NAME.match(name):
        raise AgentError(f"Invalid config name {name!r}")
    path = configs_dir / f"{name}.toml"
    if not path.is_file():
        known = sorted(p.stem for p in configs_dir.glob("*.toml"))
        raise AgentError(f"Unknown config {name!r}; known: {', '.join(known)}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    try:
        return ReviewLoadConfig.model_validate({**data, "name": name})
    except ValidationError as exc:
        raise AgentError(f"Invalid config {name!r}: {exc}") from exc
