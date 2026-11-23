# Governance pilot findings (recorded in org-context)

**Date:** 2026-11-23  
**org-context revision:** `839f584` (fresh clone after PR #3 pilot-evaluation-coverage)  
**Scope:** Skill Governance consolidation work; metadata-only MCP; approved local roots only.

## What was demonstrated earlier

On approved roots (`Security-review-skills`, `agent-eval-gate`, `skills-review`, `org-context`):

- Shared declared deps: `pydantic`, `pytest`, `pyyaml`
- `openai` declared only in `agent-eval-gate`
- Default MCP tools listed without `read_evidence`; no evidence-body leak
- Inventory commit for Eval matched the transitional Governance pin SHA

## What this does *not* prove

- Agent productivity or fewer development iterations
- Runtime usage, deployment state, or approval authority
- Justification for a Governance-specific adapter or production dependency

## Fresh repo updates acknowledged

PR #3 added `docs/PILOT_EVAL.md`, `docs/CURSOR_MCP.md`, workflow ref coverage helpers,
and explicit MCP coverage limits. Competency cases R1–R3 / C1–C2 / U1–U3 are the
correct next experiment protocol. They are **not yet independently reviewed or
scored** in this note.

## Architecture note (external)

Governance product direction changed to a **physical monorepo** (Security + Eval
in one repo). That decision is independent of org-context output and must not be
driven by this inventory.

## Recommendation (unchanged)

Keep org-context experimental and separate. Run the reviewed PILOT_EVAL protocol
before any adapter discussion. Never use org-context as Security Gate, Eval Gate,
or approval authority.
