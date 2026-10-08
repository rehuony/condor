---
# Shared behavioural rules, injected into EVERY agent session (FEAT-095):
# loop ticks, chats and background workers alike. Edit this file and
# the change lands on the next TICK with no restart — the tick prompt is built
# fresh each time. The chat / agent / worker surface builds its instructions
# once at MCP import, so those seats pick an edit up on the next MCP server
# start (same asymmetry the skills index already has).
#
# One agent can override the lot with its own agents/<slug>/core_rules.md.
---
- **Read before you act on a playbook.** When a SKILL matches the flow, CALL
  `manage_skill(action="read", name="...")` — a real tool call, every time, not
  a recall from earlier context. Identifying a skill is NOT reading it. Only
  then follow its steps.
- **Routines run, they are not rewritten.** When a skill links a routine
  ("→ routine: X"), execute it with `manage_routines(action="run", name="X")`
  instead of reimplementing what it already does by hand.
- **Short tool chains.** 1–5 calls per response or tick. One skill-driven flow
  beats a long chain of raw calls that reconstructs what the playbook says.
- **Research has one readable entry point.** New research belongs under
  `.condor/research/<topic>/<YYYY-MM-DD>/<HHmmss>-<short-id>/report.md`
  (UTC date/time, lowercase kebab-case topic, unique short id). Keep datasets
  in `data/`, figures in `assets/`, and scripts/logs/checkpoints in `work/`;
  create only what is needed. The report has YAML `title`, `summary`, and
  timezone-aware `created_at`, then a matching H1 and concise findings,
  evidence/method, risks, next steps, and linked sources/attachments.
  Follow `agents/_defaults/research-artifacts.md` and its report template;
  the `research_artifacts` skill carries the same procedure when available.
  Pass the destination and this standard to delegated researchers. Return
  descriptive `/research/<relative-path>/report.md` links, never local absolute
  paths. Preserve existing artifacts and references when updating an old study.
- **Never end a turn with a background task outstanding.** If you launch a Bash
  command with `run_in_background`, collect its output before you answer. Prefer
  a foreground command with a generous `timeout` — a task that finishes after
  your turn ends will interrupt the user's *next* question with stale work.
- **Confirm before you move money — when there is someone to confirm with.**
  In a chat, or any seat with a human in it, orders, swaps, LP mutations and
  anything destructive get confirmed with the user first.
  In an unattended loop the approval already happened: the user approved the
  launch, with its capital and its risk limits, and the runtime checks every
  call against that envelope before it runs. There, act inside your limits
  without asking — a trade held for a confirmation nobody is there to give is
  not caution, it is a loop that does not work. Either way the guard is the
  runtime, never the wording of the prompt you happen to be in.
