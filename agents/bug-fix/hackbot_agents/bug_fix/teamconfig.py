"""Read a team's setup for the optional ``team`` run input.

A team directory (``teams/<name>/``) holds ``team.toml`` (the label and the Bugzilla
components the team owns) and ``rules/implementation.md`` (how the team wants a patch
written). Both are validated strictly when they are read.
"""

import re
import tomllib
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints

HERE = Path(__file__).resolve().parent
TEAMS_DIR = HERE / "teams"
RULES_FILE = "implementation.md"

_TEAM_NAME = re.compile(r"[a-z][a-z0-9-]*")

Text = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Component(_Strict):
    product: Text
    component: Text

    @property
    def key(self) -> str:
        return f"{self.product} :: {self.component}"


class Team(_Strict):
    # How the prompt names the team, e.g. "Firefox Audio/Video".
    label: Text
    components: tuple[Component, ...] = Field(min_length=1)


def team_dir(teams_dir: Path, name: str) -> Path:
    """The directory of team ``name``.

    The name is a run input, so it must be a plain lower-case name of an existing team,
    never a path.
    """
    if not _TEAM_NAME.fullmatch(name):
        raise ValueError(f"team {name!r} is not a lower-case name")
    root = teams_dir / name
    if not (root / "team.toml").is_file():
        raise ValueError(f"no team {name!r} in {teams_dir}")
    if not (root / "rules" / RULES_FILE).is_file():
        raise ValueError(f"team {name!r} lacks rules/{RULES_FILE}")
    return root


def load_team(path: Path) -> Team:
    return Team.model_validate(tomllib.loads(path.read_text()))
