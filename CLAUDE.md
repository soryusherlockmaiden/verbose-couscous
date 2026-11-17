# CLAUDE.md

`AGENTS.md` is the canonical instruction file for AI contributors in this repository. It is imported below so Claude Code loads it automatically:

@AGENTS.md

## Before starting any work

1. Read `AGENTS.md` (above) completely.
2. Read `README.md`, then the sections of `PRD.md`, `SPEC.md` and `ROADMAP.md` relevant to the assigned milestone. For a first session, read all four documents in full. Do not rely on earlier conversations.
3. Confirm which milestone is assigned. If none is, ask; do not pick one yourself.

## Non-negotiables (details in AGENTS.md)

- Stay within the assigned milestone's scope (AGENTS.md §D–§E).
- No real orders, test-orders or transfers in M0–M4, and never arm live trading or handle production credentials (AGENTS.md §G).
- Completion requires executed lint, type, test, secret-scan and Docker checks with recorded results. Never report `PASS` without evidence (AGENTS.md §F).
- Do not change `PRD.md` without explicit operator instruction. Do not decide open `SC-*` contracts silently.
- End every session with the handoff report (AGENTS.md §I).

## Claude Code specifics

- Use plan mode, or present a short plan, before multi-file changes. Track multi-step milestone work with the task list.
- In sandboxed or cloud sessions, network access may be restricted. Do not disable the sandbox or work around network policy. Report a blocked host as `BLOCKED`.
- If these files and the project documents ever conflict, the project documents win (AGENTS.md §B).
