"""Bug fix triage tool -- a Bugzilla triage agent.

Orchestrates a Claude agent that triages bugs according to rulesets
in the rules/ directory. The agent has access to a source repository
and reaches Bugzilla via an out-of-process MCP broker (HTTP transport)
that holds the Bugzilla token — the agent process itself never sees it.
"""

import sys
from pathlib import Path
from shlex import quote

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox import FirefoxContext
from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    McpServerConfig,
    ResultMessage,
)
from hackbot_runtime import ActionsRecorder, AgentError, HackbotAgentResult
from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions.claude_sdk import actions_server_for, actions_to_tool_names
from hackbot_runtime.changes import base_commit
from hackbot_runtime.claude import Reporter

from . import gates
from .config import (
    BUG_FIX_ACTIONS,
    BUGZILLA_READ_TOOLS,
    FIREFOX_TOOLS,
    PHABRICATOR_READ_TOOLS,
    SOURCE_WRITE_TOOLS,
)
from .teamconfig import RULES_PATH, load_team

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"
RULES_DIR = HERE / "rules"
# The SDK's 1 MB default is too small for large messages.
MAX_BUFFER_SIZE = 10 * 1024 * 1024


class BugFixResult(HackbotAgentResult):
    bug_id: int
    result: str | None = None


def render_prompt(name: str, **fields: object) -> str:
    """Render a prompt template from ``prompts/`` via ``str.format``.

    Prompt text lives in ``prompts/*.md`` rather than inline in Python, so it
    stays readable and editable. Substituted values are inserted verbatim
    (``str.format`` does not re-scan them), so an untrusted ``comment`` cannot
    break out of its ``{comment}`` placeholder.
    """
    return (PROMPTS / name).read_text().format(**fields)


_PLATFORM_NAMES = {"linux": "Linux", "darwin": "macOS", "win32": "Windows"}


def runner_platform(value: str) -> str:
    """The name of the platform this run is on, from ``sys.platform``."""
    return _PLATFORM_NAMES.get(value, value)


def build_system_prompt(rules_dir: Path, team_dir: Path | None) -> str:
    """The system prompt, with the team's section appended when a team is set."""
    prompt = render_prompt("system.md", rules_dir=str(rules_dir.resolve()))
    if team_dir is None:
        return prompt
    section = render_prompt(
        "team.md",
        label=load_team(team_dir).label,
        platform=runner_platform(sys.platform),
        max_rounds=gates.MAX_REVIEW_ROUNDS,
        rules=(team_dir / RULES_PATH).read_text().strip(),
    )
    return f"{prompt}\n\n{section}"


def select_workflow(
    *,
    bug: int,
    revision_id: int | None,
    comment: str | None,
    bugzilla_needinfo_flag_id: int | None,
    rules_dir: Path,
) -> str:
    """Select the prompt for exactly one of the three bug-fix modes."""
    if bugzilla_needinfo_flag_id is not None:
        return render_prompt("bugzilla-needinfo.md", bug_id=bug, comment=comment)
    if revision_id:
        return render_prompt(
            "follow-up.md", revision_id=revision_id, bug_id=bug, comment=comment
        )
    return render_prompt(
        "triage-and-fix.md", bug_id=bug, rules_path=str(rules_dir.resolve())
    )


def _record_needinfo_clear(
    recorder: ActionsRecorder, *, bug_id: int, flag_id: int | None
) -> None:
    """Record the clear after responding to a Bugzilla needinfo webhook."""
    if flag_id is None or not recorder.actions:
        return

    recorder.record(
        "bugzilla.update_bug",
        {
            "bug_id": bug_id,
            "changes": {"flags": [{"id": flag_id, "status": "X"}]},
        },
        reasoning="Clear the needinfo flag that triggered this response.",
    )


def make_investigator() -> AgentDefinition:
    """Create a single generic investigator subagent definition."""
    return AgentDefinition(
        description=(
            "Focused investigator for answering a specific question about "
            "a bug or the source tree. The main agent writes your complete "
            "instructions at spawn time — follow them precisely and return "
            "only what was asked for."
        ),
        prompt=(
            "You are a focused investigator subagent. You will be given a "
            "self-contained task by the triage agent. Complete it and return "
            "a concise answer. Do not make Bugzilla modifications — you have "
            "read-only access. Do not speculate beyond what you can verify."
        ),
        tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            *BUGZILLA_READ_TOOLS,
            *FIREFOX_TOOLS,
        ],
        model="inherit",
    )


def _write_mozconfig(fx_ctx: FirefoxContext) -> None:
    """Write a debug browser config, preserving any existing config.

    ``--enable-debug`` turns on assertions that catch a whole class of mistakes
    during development, and ``--enable-clang-plugin`` adds Mozilla's own static
    analysis on top.
    """
    if fx_ctx.mozconfig.exists():
        return
    fx_ctx.mozconfig.write_text(
        "ac_add_options --enable-application=browser\n"
        "ac_add_options --enable-debug\n"
        "ac_add_options --enable-optimize\n"
        "ac_add_options --enable-clang-plugin\n"
        # For the evaluate_js_shell tool: desktop builds skip the JS shell.
        "ac_add_options --enable-js-shell\n"
        f"mk_add_options MOZ_OBJDIR={quote(str(fx_ctx.objdir))}\n"
    )


def make_reviewer(team_dir: Path, base: str) -> AgentDefinition:
    """The read-only subagent that reviews a team run's patch with a fresh context."""
    team = load_team(team_dir)
    return AgentDefinition(
        description=(
            "Reviews the current patch against the team's rules and review "
            "checklists, with a fresh context. It ends with `BLOCKING: <n>`."
        ),
        prompt=render_prompt(
            "reviewer.md",
            label=team.label,
            base=base,
            review_docs=", ".join(f"`{d}`" for d in team.review_docs) or "none",
            rules=(team_dir / RULES_PATH).read_text().strip(),
        ),
        tools=["Read", "Grep", "Glob", "Bash"],
        model="inherit",
    )


def team_session(team_dir: Path, repo: Path) -> dict:
    """The options a team run adds: the reviewer, the gates and their hooks."""
    state = gates.Gates(repo)
    return {
        "mcp_servers": {gates.SERVER_NAME: gates.gates_server(state)},
        "agents": {gates.REVIEWER: make_reviewer(team_dir, base_commit(repo))},
        "allowed_tools": [gates.RECORD_GATE_TOOL],
        "hooks": {
            "PreToolUse": [
                HookMatcher(
                    matcher=f"{gates.SUBMIT_TOOL}|{gates.UPDATE_TOOL}",
                    hooks=[gates.submit_guard(state)],
                )
            ],
            "PostToolUse": [
                HookMatcher(
                    matcher=gates.SPAWN_TOOLS,
                    hooks=[gates.review_hook(state)],
                )
            ],
        },
        # A one-shot run must wait for the reviewer's result.
        "env": {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
    }


async def run_bug_fix(
    *,
    bugzilla_mcp_server: McpServerConfig,
    phabricator_mcp_server: McpServerConfig,
    source_repo: Path,
    fx_ctx: FirefoxContext,
    bug: int,
    comment: str | None = None,
    revision_id: int | None = None,
    bugzilla_needinfo_flag_id: int | None = None,
    rules_dir: Path = RULES_DIR,
    team_dir: Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    verbose: bool = False,
    log: Path | None = None,
    actions_recorder: ActionsRecorder | None = None,
) -> BugFixResult:
    """Triage and fix a single Bugzilla bug with a claude-agent-sdk agent.

    Returns a :class:`BugFixResult` on success; raises :class:`AgentError` if the
    agent ends in an error.
    """
    print(f"[bug_fix] triaging bug {bug}", file=sys.stderr)

    # Firefox build/eval MCP server (in-process; no tokens). The runtime
    # derives fx_ctx from the prepared source checkout and the agent's
    # hackbot.toml. Provision the config before the session can call build tools.
    _write_mozconfig(fx_ctx)
    firefox_server = build_sdk_server("firefox", fx_ctx, firefox.TOOLS)

    user_prompt = select_workflow(
        bug=bug,
        revision_id=revision_id,
        comment=comment,
        bugzilla_needinfo_flag_id=bugzilla_needinfo_flag_id,
        rules_dir=rules_dir,
    )

    # Action-recording MCP server (in-process). Standalone/script runs pass
    # actions_recorder=None and get a local recorder that copies attachments
    # under ./artifacts (no uploader).
    actions_recorder, actions_server = actions_server_for(
        actions_recorder, types=BUG_FIX_ACTIONS
    )
    enabled_action_tools = actions_to_tool_names(BUG_FIX_ACTIONS)

    system_prompt = build_system_prompt(rules_dir, team_dir)
    team = team_session(team_dir, source_repo) if team_dir else {}

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={
            "bugzilla": bugzilla_mcp_server,
            "phabricator": phabricator_mcp_server,
            "firefox": firefox_server,
            ACTIONS_SERVER_NAME: actions_server,
            **team.get("mcp_servers", {}),
        },
        agents={"investigator": make_investigator(), **team.get("agents", {})},
        cwd=str(source_repo.resolve()),
        add_dirs=[str(rules_dir.resolve())],
        permission_mode="bypassPermissions",
        allowed_tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            "Task",
            *SOURCE_WRITE_TOOLS,
            *BUGZILLA_READ_TOOLS,
            *PHABRICATOR_READ_TOOLS,
            *enabled_action_tools,
            *FIREFOX_TOOLS,
            *team.get("allowed_tools", []),
        ],
        model=model,
        max_turns=max_turns,
        **({"effort": effort} if effort else {}),
        setting_sources=[],
        max_buffer_size=MAX_BUFFER_SIZE,
        hooks=team.get("hooks"),
        env=team.get("env", {}),
    )

    result_msg: ResultMessage | None = None
    with Reporter(verbose=verbose, log_path=log) as reporter:
        reporter.header(f"bug {bug}")
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_prompt)
            async for msg in client.receive_response():
                reporter.message(msg)
                if isinstance(msg, ResultMessage):
                    result_msg = msg

    if result_msg is None:
        raise AgentError(f"bug {bug}: agent produced no result message")
    if result_msg.is_error:
        raise AgentError(
            f"bug {bug} triage failed: {result_msg.result or result_msg.subtype}"
        )

    _record_needinfo_clear(
        actions_recorder,
        bug_id=bug,
        flag_id=bugzilla_needinfo_flag_id,
    )

    return BugFixResult(
        bug_id=bug,
        result=result_msg.result,
        num_turns=result_msg.num_turns,
        total_cost_usd=result_msg.total_cost_usd,
    )
