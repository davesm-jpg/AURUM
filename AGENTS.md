# AURUM agent instructions

AURUM researches COMEX Micro Gold futures and the Liquidity Sweep → Market Structure Shift → Inverse Fair Value Gap Reaction hypothesis. It supports research and paper simulation only. Keep live execution, brokerage-account connections and credentials, actual position management and funded-account orders outside the codebase. Use authorized market data. Keep secrets and licensed data out of Git and label every synthetic fixture and result.

## Start each session

1. Locate the Git root. Check the branch, status, existing changes and relevant commits.
2. Read [the handoff](docs/HANDOFF.md), relevant [roadmap](docs/ROADMAP.md) and [architecture](docs/ARCHITECTURE.md). Consult [the canonical specification](docs/PROJECT_SPEC.md) for requirements.
3. Inspect task-related source and tests. Verify the checkpoint and continue the highest-priority unblocked task. Preserve unrelated edits.

Current user instructions take precedence, followed by the specification, source and tests, architecture and decisions, roadmap, handoff and prior conversation. Investigate conflicts.

## Implement and verify

- Use focused, independently verifiable slices in the Python modular monolith. Keep critical algorithms deterministic and testable offline. Reuse the same detection path for replay and future monitoring.
- Use aware UTC timestamps, explicit contract identity and data provenance. Release information only when actually available. Preserve source data, flag gaps and prevent affected research alerts. Keep adjusted research data separate from executable contract prices.
- Record rule, input and cost versions. Preserve the final chronological holdout. Keep historical, synthetic, Monte Carlo and forward paper results distinct. Report actual observation counts and disclose material assumptions.
- Write tests alongside changes. Review files and the Git diff, run relevant pytest and configured Ruff/mypy checks, and report exact outcomes. Make logical commits without rewriting unrelated history.

## Save the checkpoint

Update relevant architecture, decisions, roadmap, validation, known issues and changelog. Update [the handoff](docs/HANDOFF.md) after meaningful work or blockers, before context compaction and before ending. Include actual evidence, a verified commit, exact next steps and limits. Use the format in the specification. Resume requests mean continuing from this checkpoint.
