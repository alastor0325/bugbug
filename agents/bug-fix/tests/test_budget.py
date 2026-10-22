"""Tests for the time budget of a team run."""

from hackbot_agents.bug_fix.budget import budget_hook


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def test_hook_says_nothing_before_the_budget_is_used():
    clock = _Clock()
    hook = budget_hook(10, clock=clock)
    clock.now += 9 * 60
    assert await hook({}, None, None) == {}


async def test_hook_tells_the_agent_to_finish_once():
    clock = _Clock()
    hook = budget_hook(10, clock=clock)
    clock.now += 10 * 60

    first = await hook({}, None, None)
    output = first["hookSpecificOutput"]
    assert output["hookEventName"] == "PostToolUse"
    assert "Start no new work" in output["additionalContext"]

    clock.now += 60
    assert await hook({}, None, None) == {}
