from pathlib import Path

from hackbot_runtime import HackbotContext, checkout_revision, run_async
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import BugFixResult, run_bug_fix
from .preflight import fetch_bug
from .teamconfig import TEAMS_DIR, team_dir, team_for_bug


class AgentInputs(BaseSettings):
    bug_id: int
    broker_url: str
    revision_id: int | None = None
    comment: str | None = None
    bugzilla_needinfo_flag_id: int | None = None
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None
    # The team whose rules the run follows, read from `TEAM` as a name under `teams/`
    # and kept as that directory. When unset, the team owning the bug's component.
    team: Path | None = None

    model_config = SettingsConfigDict(extra="ignore")

    def broker_endpoint(self, path: str) -> str:
        return f"{self.broker_url.rstrip('/')}/{path.lstrip('/')}"

    @field_validator("team", mode="before")
    @classmethod
    def _team_dir(cls, team: str | None) -> Path | None:
        return team_dir(TEAMS_DIR, team) if team is not None else None

    @property
    def bugzilla_mcp_url(self) -> str:
        return self.broker_endpoint("/bugzilla/mcp")

    @property
    def phabricator_mcp_url(self) -> str:
        """The broker's Phabricator MCP endpoint.

        Derived from the broker URL rather than taken as its own input: both
        endpoints are served by the same sidecar, so there is nothing for a
        caller to configure independently.
        """
        return self.broker_endpoint("/phabricator/mcp")

    @model_validator(mode="after")
    def _validate_mode(self) -> AgentInputs:
        """Require exactly one coherent normal, Phabricator, or Bugzilla mode."""
        if self.bugzilla_needinfo_flag_id is not None:
            if self.revision_id is not None:
                raise ValueError(
                    "bugzilla_needinfo_flag_id cannot be combined with revision_id"
                )
            if not self.comment:
                raise ValueError(
                    "comment (COMMENT) is required when "
                    "bugzilla_needinfo_flag_id is set"
                )
        elif self.revision_id is not None and not self.comment:
            raise ValueError(
                "comment (COMMENT) is required when revision_id is set, to "
                "respond on the revision"
            )
        return self


async def main(ctx: HackbotContext) -> BugFixResult:
    inputs = AgentInputs()

    if inputs.revision_id:
        await checkout_revision(ctx, inputs.revision_id, inputs.broker_url)
    else:
        await ctx.prepare_repo()

    team = inputs.team or team_for_bug(
        TEAMS_DIR, await fetch_bug(inputs.bugzilla_mcp_url, inputs.bug_id)
    )

    return await run_bug_fix(
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        phabricator_mcp_server={
            "type": "http",
            "url": inputs.phabricator_mcp_url,
        },
        source_repo=ctx.repo_path,
        fx_ctx=ctx.firefox,
        bug=inputs.bug_id,
        revision_id=inputs.revision_id,
        comment=inputs.comment,
        bugzilla_needinfo_flag_id=inputs.bugzilla_needinfo_flag_id,
        team_dir=team,
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        actions_recorder=ctx.actions,
    )


if __name__ == "__main__":
    run_async(main)
