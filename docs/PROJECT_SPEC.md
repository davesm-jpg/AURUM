# AURUM — Complete project specification

Version 1.0. AURUM is a continuous futures-market intelligence, quantitative strategy research, historical backtesting, paper simulation and alerting platform. Its first instrument is COMEX Micro Gold futures (MGC). Its first strategy is Liquidity Sweep → Market Structure Shift → Inverse Fair Value Gap Reaction. Preferred implementation is Python with React/Next.js. Codex maintains the project across sessions.

## I. Role and operating directive

1. **Role.** Act as the lead architect, Python engineer, quantitative researcher, data and market microstructure engineer, DevOps and test engineer, project manager and persistent coordinator. Design and implement the repository, ingestion, quantitative research, strategy detection, backtesting, continuous monitoring, notifications and dashboard. Maintain comprehensive automated tests, milestones, defect resolution, performance, reliability, architecture, decision records and context continuity. Deliver working, tested software.
2. **Priorities.** In order, favor correctness, data integrity, statistical validity, reproducibility, reliability, maintainability, context efficiency, performance, user experience and additional features. Claim implementation and passing tests only with actual code and execution evidence. Identify synthetic data. Report hypothetical results as such and require validation on unseen data before any profitability claim.
3. **Autonomy.** Execute reversible engineering work without repeated approval. Ask when essentials such as purchases, authorized credentials, irreversible external changes or fundamental ambiguity genuinely require the user. Record reasonable reversible defaults. Complete practical verified work during the session. Promise background work only with an actual supported automation mechanism.

## II. Vision and safety

4. **Product.** Continuously analyze available exchange-session data and update with relevant incoming events. Detect, record, explain and evaluate market structure, liquidity proxies and sweeps, BOS, MSS, FVG/IFVG, higher-timeframe direction, premium/discount, session liquidity, regimes, volatility expansion/contraction, scheduled macro events, structural invalidation, hypothetical risk/reward, historical results and stability. Maintain developing and confirmed setups, event history and explainable alerts.
5. **Hypothesis.** Sweeps followed by a structural reversal and a reaction to an imbalance may outperform randomly selected entries. Test net expectancy, drawdown, consistency, transaction-cost sensitivity, unseen performance, regime differences and relationships between setup features and outcomes. Treat win rate as one input to expectancy. A repeatable positive net expectancy is the research objective.
6. **Boundary.** Research, analysis, notifications and paper simulation only. No real-money orders, live account execution connections, actual brokerage position management, funded-account orders, live brokerage credential collection or circumvention of broker or prop-firm restrictions. Hypothetical entries, stops, targets, balances, sizing and account restrictions may be modeled. Label paper scenarios explicitly and make no guarantees or claims that simulated returns will recur.

## III. Instrument, calendar and contracts

7. **MGC.** COMEX/CME Micro Gold futures, 10 troy ounces, USD 0.10 per ounce minimum price increment and USD 1 per tick per contract. Verify with authoritative exchange documents. Track dated futures separately from spot XAUUSD. Changing active contracts, expiry schedules, closures, fees and vendor rules must come from versioned inputs. Support regular sessions, daily maintenance, weekends, holidays, early closes, exceptional halts and DST. Use timezone-aware UTC internally and display UTC, America/New_York and Europe/Prague where relevant. Apply each region's own DST transitions.
8. **Contracts.** Discover listings and active contracts, detect approaching expiry and roll conditions, retain historical metadata and compare volume and open interest when available. Support reproducible continuous datasets with explicit adjusted and unadjusted bases. Retain the generating contract and map signals to actual contract prices. Keep incompatible histories separate, avoid assigning one contract's signal to another, and reserve executable prices for actual contract observations. Document rollover decisions and their inputs.

## IV. Market data

9. **Adapters.** Use interchangeable provider adapters for historical requests, real-time subscriptions, metadata, timestamps, status, errors and reconnection. Support authorized WebSocket and REST feeds, CSV and Parquet, OHLCV and available trades, ticks, quotes, volume and open interest. Report real-time, delayed, historical, simulated or unavailable status accurately. Assume no paid subscription or connected vendor. Provide functional historical replay independently of a live feed.
10. **Integrity.** Validate timestamps, missing candles, duplicates, disorder, OHLC, positive prices, stale data, volume, schedule and contract consistency. Backfill connection gaps and preserve source provenance and raw data. Store each dataset's unique ID, provider, instrument, contract, date range, data type, import time, source timezone, warnings, dataset version and relevant content checksum. Suspend affected alerts on integrity failures. Preserve gaps without fabricating candles.
11. **Aggregation.** Synchronize 1m, 5m, 15m, 1h, 4h and daily bars on consistent exchange-aware boundaries. Use 5m for the setup, 15m/1h/4h for context and optionally daily for macro context. Expose completed bars only at close and produce equivalent results from historical replay and streaming. Favor incremental updates.

## V. Market structure

12. **Pivots.** Use configurable confirmed swings, initially three bars left and three right, with explicit local extrema and equality/tie rules. A pivot becomes usable only after the right bars close. Store pivot and actual confirmation times. Track internal and external structure separately, including HH, HL, LH, LL, consolidation, bullish/bearish sequences and transitions.
13. **BOS.** Detect qualifying upward/downward breaks of previously confirmed highs/lows. Version and evaluate wick, close, volatility-buffered close and displacement-confirmed rules separately. Record direction, level, source swing, break and confirmation times, break magnitude and displacement.
14. **MSS.** Require a qualifying break in the opposite direction of an objectively identified prior structural state. Record the prior state, relevant pivot, break rule, confirmation, displacement and invalidation. Test displacement, body/range ratio, proximity to liquidity events, volatility, session, higher-timeframe direction and elapsed time since sweep as quality features. Reserve MSS labels for qualifying transitions.

## VI. Liquidity

15. **Proxies.** Map previous day/week highs/lows, Asian and London session extremes, confirmed swings, equal and repeatedly tested levels, and range boundaries. Price-derived levels are proxies for liquidity. Store price, type, source period, confirmation, test count, distance, status and invalidation or expiry. Claims about hidden orders require direct evidence.
16. **Sweeps.** A bullish candidate penetrates a previously available downside proxy and meets a configured reclaim rule. Mirror for bearish candidates. Record direction, level, penetration, duration beyond the level, reclaim strength, displacement, follow-through, invalidation and time to MSS. Distinguish wick penetration, confirmed reclaim, sustained or failed breakout, unconfirmed sweep and confirmed candidate. Evaluate subsequent structure separately.

## VII. Imbalances

17. **FVG.** With candles A/B/C, confirm bullish A.high < C.low or bearish A.low > C.high only at C's close. The bounds are the two compared prices in ascending order. Record direction, bounds, formation and confirmation, width in price, ticks and ATR units, displacement, first retest, partial/full mitigation and invalidation. Make minimum width configurable.
18. **IFVG.** Version inversion definitions. An original bearish FVG may become bullish after a qualifying invalidation and confirmation, mirrored for a bullish original. Record original ID, direction and zone, inversion rule, invalidation time, new direction, confirmation requirements, retest and reaction. Preserve point-in-time labels and test utility empirically.

## VIII. Context

19. **Premium/discount.** Record the active, objectively defined structural range, its high, low and midpoint, and premium, discount or equilibrium classification. Test incremental value without assuming reversion.
20. **Sessions.** Version timezone-aware Asian, London, New York, London/New York overlap and other exchange trading periods. Compare frequency, win rate, realized expectancy, volatility, adverse and favorable excursion, holding time and consistency.
21. **Regimes.** Classify trend direction, range, volatility expansion/contraction, thin liquidity, elevated event risk and uncertainty with transparent features. Consider ATR, ADX, realized volatility, swing persistence, directional efficiency, session range, return distributions, volume and spread. Persist measurements and test regime effects. Use only information available at labeling time.
22. **Economic events.** Use reputable scheduled feeds for US CPI, PCE, nonfarm payrolls, Fed decisions, FOMC statements and minutes, other major US releases and monetary-policy events. Record scheduled and confirmed release times where available and classify uncertainty. Disconnected coverage means unknown. Exclude invented events and predictions of unscheduled news.

## IX. Strategy

23. **AURUM LS-MSS-IFVG.** Liquidity Sweep — Market Structure Shift — Inverse Fair Value Gap Reaction is the initial unproven hypothesis.
24. **Bullish sequence.** Establish higher-timeframe context, identify a valid downside proxy, observe a qualifying downside sweep and reclaim, confirm a subsequent bullish MSS, identify the configured FVG/IFVG, confirm zone interaction and remaining signal conditions, select structural invalidation, check hypothetical reward/risk and record the scenario and outcome. Use actual decision times without backdating confirmations.
25. **Bearish sequence.** Mirror the bullish path with upside liquidity and sweep, bearish MSS, imbalance interaction, invalidation, reward/risk and outcome.
26. **Experiments.** Compare MSS alone, sweep+MSS, MSS+FVG, MSS+IFVG, sweep+MSS+FVG, sweep+MSS+IFVG and sweep+MSS+IFVG with higher-timeframe alignment. Compare session, volatility and regime filters on the same data and assumptions. Keep the final holdout independent of selection.
27. **Setup lifecycle.** Persist IDLE → CONTEXT_IDENTIFIED → LIQUIDITY_EVENT → MSS_CONFIRMED → ZONE_IDENTIFIED → RETEST_PENDING → PAPER_SIGNAL → COMPLETED / INVALIDATED / EXPIRED. Use unique IDs, deterministic event times and transitions, restart recovery, expiration, invalidation, deduplication, replay compatibility and rule versions. Distinguish developing from confirmed.
28. **Versioned starting parameters.** MGC, 5m signal, 15m/1h/4h context, 3-left/3-right swings, USD 50 gross risk, 2:1 gross reward/risk, USD 100 gross reward, USD 25,000 simulated starting balance and USD 1,000 initial loss allowance. Lucid Flex research simulation with paper-only execution. Treat these as configurable, unoptimized experimental assumptions.
29. **Sizing.** Convert the structural stop to ticks and use the USD 1 MGC tick value. Use integer quantities and account for costs without rounding above the allowance. Preserve the structural stop. Mark setups incompatible when one contract exceeds the risk constraint. Record structural barriers to a 2:1 target and evaluate structural and fixed targets as separate variants.

## X. Signal quality

30. **Scoring.** Initial experimental 100-point score: higher-timeframe context 15, liquidity 20, MSS 20, FVG/IFVG 15, risk/reward feasibility 10, session/liquidity context 10 and volatility/event conditions 10. Record total, components, contributing features, matched/failed conditions, data quality, rule version, timeframe context and sample size. Scores are experimental rankings, with no calibrated probability interpretation. Validate the score-to-outcome relationship out of sample and redesign or remove it if unsupported.

## XI. Backtesting

31. **Engine.** Run chronological event-driven historical replay through the same strategy code as monitoring. Honor candle and pivot confirmation, realistic intrabar modeling, commission and slippage, rollover, calendar, gaps, reproducible configuration, trade audit logs and versioned results.
32. **Fills.** Model spread where supported, commissions, slippage, latency, eligibility, market availability and stop/target events. For same-bar stop and target with unknown order, use a disclosed conservative policy or mark ambiguous. Compare against higher-resolution data where possible. Use only eligible prices at each event time.
33. **Costs.** Configurable illustrative USD 100 gross win, USD 50 gross loss, USD 1.60 round-trip commission and USD 1 total slippage give USD 97.40 net win, USD 52.60 net loss and roughly 35.1% break-even. Verify current provider-specific fees before treating any schedule as authoritative.
34. **Metrics.** Trade/win/loss counts and rates, gross profit/loss, net profit, commission, estimated slippage, average winner/loser, realized reward/risk, expectancy, profit factor, max drawdown, recovery factor, max consecutive wins/losses, mean and median holding duration, maximum favorable/adverse excursion, monthly/session/weekday/regime/direction performance, return distribution and confidence intervals. Net expectancy equals win rate times average net win minus loss rate times average net loss magnitude.
35. **Samples.** Report at 100, 250, 500 and 1,000 historical observations when available. Always report the covered period and actual qualifying count. Avoid forcing trades or inventing observations.

## XII. Statistical validation

36. **Protocol.** Chronologically separate development, configuration validation, final unseen evaluation and later forward paper. Support walk-forward, rolling out-of-sample, bootstrap, Monte Carlo, parameter and cost sensitivity, regime comparison, clustering and multiple-comparison control when applicable. Prevent leakage from neighboring observations and overlapping labels, account for correlated outcomes and keep the final holdout out of optimization.
37. **Monte Carlo.** Support fixed seeds, configurable path/trade counts, reshuffling, bootstrap, alternative win and reward/risk assumptions, variable costs, losing streaks and breach simulations. Report mean/median ending equity, quantiles, drawdown and losing-streak distributions, breach probability, time to breach and survival through 100/500/1,000 trades. Illustrative win rates are 30%, 35%, 40%, 45%, 50% and 60%. Scenarios are assumptions and cannot establish actual strategy performance.
38. **Reporting.** State observations, period, source, strategy version, costs, uncertainty, sample partition and whether the evaluated data was used for parameter selection. Qualify small or heavily optimized samples and report instability across periods.

## XIII. Lucid Flex simulation

39. **Account model.** Use an independent, configurable hypothetical USD 25K Lucid Flex account. Starting assumptions for official verification are USD 25,000 balance, USD 1,000 loss allowance, end-of-day trailing drawdown, USD 24,000 initial floor, USD 25,100 locking floor and USD 26,100 threshold balance. Use current official provider documents and version rules.
40. **Drawdown.** Track balance, equity, highest eligible end-of-day balance, trailing and locked floors, remaining drawdown and breaches. Verify the breach metric and model intraday violations under the effective rules. Honor trading-session boundaries.
41. **Variants.** Independently configure evaluation/funded phases, daily and maximum loss limits, contract limits, qualifying profit days, payout eligibility, amount, split, effect on the floor, inactivity and transitions. Verify each variant with official sources.
42. **Survival.** Estimate hypothetical breaches, tolerance to adverse movement, losing streak effects, risk/cost sensitivity, equity paths under trailing drawdown and payout effects. Treat survival as a modeled probability and keep simulation separate from execution.

## XIV. Continuous monitoring

43. **Service.** During available sessions subscribe, update bars and structure, track liquidity/sweeps, MSS and FVG/IFVG, evaluate and persist setups, issue research alerts and monitor health. Recognize closures and unavailable data without inventing prices or signals.
44. **Events.** Prefer a replayable event flow with unique IDs and timestamps, safe duplicate/disorder handling and decision history. Event candidates cover data received, candle opened/updated/closed, swing and structure break, liquidity level and sweep, MSS, FVG/IFVG, developing/confirmed/invalidated setup, paper outcome, alert requested/delivered and feed disconnected.

## XV. Notifications

45. **Channels.** Modular Telegram and Discord webhooks first, then email, in-app and mobile push where practical. Require supplied authorization and credentials via environment variables.
46. **Types.** Context/regime change, developing setup, confirmed research signal, invalidation, data-quality warning, daily research summary, weekly performance/stability and monthly longer-term reports.
47. **Contents.** Confirmed research alerts include instrument/contract, direction hypothesis, exchange and Prague timestamps, session, higher-timeframe context, sweep and MSS evidence, zone, hypothetical entry/invalidation/target, reward/risk and cost assumptions, experimental score, comparable sample count, available validation metrics, evidence, cautions, freshness and explicit paper-research status. Attach annotated charts where possible.
48. **Controls.** Deduplicate, honor preferences, severities, rate limits, quiet hours, filters and expiry. Retry failures, record delivery status and cancel where supported. Suppress repeats for unchanged setups.

## XVI. Interface

49. **Design.** Prefer React, TypeScript, Next.js and a maintained time-series chart library. Provide default dark professional terminal styling, clear typography and hierarchy, accessible controls, responsive uncluttered layouts, fast navigation and restrained visual effects.
50. **Overview.** Show instrument and active contract, market state/session, source and freshness, regime and multitimeframe structure, liquidity, MSS and imbalance zones, developing setups, recent confirmed alerts, warnings and relevant historical statistics.
51. **Chart.** Provide timeframe switching and independently toggleable swings, BOS, MSS, sweeps, equal highs/lows, prior day/week and session extremes, FVG/IFVG and hypothetical entries/invalidation/targets. Plot discovery and confirmation at their actual availability.
52. **Analytics.** Show equity and drawdown, win rate and expectancy history, profit factor, trade distributions, performance by session/weekday/regime/side, setup frequency, score calibration, walk-forward, Monte Carlo and cost sensitivity. Clearly distinguish historical, simulated scenario and forward paper results.
53. **Configuration.** Expose instrument/timeframes, variants, pivot/liquidity/imbalance/confirmation/score parameters, provider, notification channels, risk, backtest and account rules. Version changes and preserve prior run reproducibility.

## XVII. Optional machine learning

54. **Baseline first.** After verified deterministic research, consider logistic regression, random forests, gradient boosting or calibration. Use only point-in-time structure, volatility, sweep, displacement, gap, session, volume, efficiency, barrier distance and persistence features. Use time-aware validation, compare against the baseline on out-of-sample expectancy, calibration, drawdown, robustness and stability, and keep the rule-based baseline when gains lack evidence. Exclude autonomous model-directed live orders.

## XVIII. Architecture

55. **Stack.** Prefer Python 3.12+ and a modular monolith. Candidate tools include FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL, TimescaleDB and Redis when justified, NumPy, Pandas or Polars, SciPy, scikit-learn, pytest, Ruff, mypy, Docker and Compose. Keep critical algorithms testable offline.
56. **Domains.** Separate market data, instruments, contracts, calendar, candles, market structure, liquidity, imbalances, regime, economic events, strategy, scoring, backtesting, statistics, Monte Carlo, account simulation, notifications, API, configuration and monitoring. Keep boundaries explicit and avoid dependency cycles.
57. **Schemas.** Version and migrate instruments, contracts, market data/candles, providers, datasets, sessions, swings, structure events, liquidity levels/sweeps, FVG/IFVG, regimes, strategy versions, setup events, alerts, paper trades, backtests, Monte Carlo, account simulations, deliveries, preferences, quality incidents and errors. Index time-series queries and avoid unnecessary duplicate data.
58. **API.** Document REST for market state, instruments/contracts, candles, structures, liquidity, setups, signal history, performance, backtests, Monte Carlo, paper accounts, notification settings and health. Stream dashboard updates via WebSockets or another appropriate mechanism. Validate inputs and protect secrets.

## XIX. Reliability and security

59. **Reliability.** Implement health checks, reconnect with exponential backoff, graceful shutdown, persistence and restart recovery, deduplication, stale/gap recovery, delivery retries, structured logging, exception reporting and monitoring. On critical loss, mark disconnected, suspend affected confirmed alerts, preserve records, recover and backfill if possible, and validate continuity before resuming.
60. **Security.** Environment-managed secrets and sanitized example config, secure tokens, HTTPS, access control where needed, input validation, pinned dependencies, least privilege and backups. Exclude tokens, passwords, credentials and unauthorized licensed data from Git. Require explicit approval for paid infrastructure.

## XX. Verification

61. **Tests.** Write unit tests with labeled synthetic fixtures, integration tests of ingestion/structure/state/storage, replay/stream parity and anti-look-ahead tests for pivots, MSS, FVG, higher-timeframe availability and historical decision stability. Test tick math, sizing, commission, drawdown, locking, breach, payouts and session boundaries. Exercise disconnects, gaps, duplicates, disorder, failed storage, bad config, setup restart and failed notifications.
62. **Milestone gates.** Review modified files and Git diff, run relevant unit/integration tests and configured static checks, verify acceptance, update documentation and save a handoff. Record exact evidence and limitations.

## XXI. Persistent memory

63. **Repository as memory.** Preserve requirements, architecture, decisions, progress, known problems, test evidence, current objective and precise continuation so a fresh session needs no prior conversation.
64. **Required files.** Maintain concise operating instructions in `AGENTS.md`; this full specification; actual system architecture, flows, responsibilities, schema, interfaces, deployment and constraints in `ARCHITECTURE.md`; work tracking in `ROADMAP.md`; current snapshot in `HANDOFF.md`; dated ADRs with alternatives, rationale, consequences and files in `DECISIONS.md`; verification, assumptions and statistical rules in `VALIDATION.md`; issues with ID, severity, affected module, reproduction, status, workaround and resolution in `KNOWN_ISSUES.md`; meaningful completed changes in `CHANGELOG.md`. Roadmap entries require ID, description, priority, dependencies, status, acceptance, files and verification. Statuses are TODO, IN_PROGRESS, BLOCKED and DONE.
65. **Priority.** Current user instructions, canonical requirements, actual code and tests, architecture and decisions, roadmap, handoff, then prior conversation. Investigate discrepancies.
66. **Handoff schema.** Project Status with phase, milestone, branch and last verified commit. Current Objective with ID, objective and acceptance. Completed Work with files and evidence. Work In Progress with files, implemented and remaining portions. Important Decisions with rationale and reference. Known Issues with severity and status. Test Status with commands, passed/failed/not-run and limitations. Environment with dependencies, runtimes, services and missing config. Immediate Next Actions with exact numbered steps. Do Not Repeat with completed work, rejected approaches and unsuccessful attempts. Close with instructions to resume from the verified checkpoint, inspect, complete the next unblocked task, test and update the handoff.
67. **Handoff upkeep.** Target 1,000–2,000 tokens with actionable verified evidence, no secrets or hidden reasoning and historical detail in permanent records. Update after meaningful work, major blockers/decisions, significant tests, before compaction and before ending a session.
68. **Context recovery.** Finish a safe atomic operation if practical, save code and evidence, update roadmap/architecture/decisions/issues/handoff and record continuation steps. Reconstruct after resets from repository files, tests and commits. Avoid inventing progress or reconstructing the entire conversation.

## XXII. Session lifecycle

69. **Startup.** Locate repository root, check Git, read AGENTS and handoff, inspect roadmap, architecture, commits and task-relevant source, verify previous work, identify the next unblocked task and continue. Initialize an empty repository and rebuild missing records only from available evidence. Preserve existing work.
70. **Work loop.** Plan objective, dependencies, files, acceptance and tests. Inspect and reuse existing code. Implement focused production changes. Test and fix failures. Verify actual results and diff. Document and update the handoff.
71. **Resume.** Treat continuation requests as instructions to use the latest repository checkpoint and proceed without asking for existing requirements or restarting completed phases.
72. **Context efficiency.** Use targeted searches, focused excerpts and tests, small patches and reusable fixtures. Avoid duplicated docs, huge logs, repeated failed approaches, redundant implementations and unnecessary expensive runs.

## XXIII. Git

73. **Change management.** Check branch, status and recent commits at startup. Preserve unrelated edits. Use logical commits according to permissions and workflow. Exclude force pushes, unrelated history rewrites, discarded user changes, secrets and unauthorized data. Keep commits and handoffs current for their distinct purposes.

## XXIV. Milestones

74. **Phase 1, repository and data.** Scaffold memory, Python/config, instrument and candle models, import, timestamps, exchange calendar, storage and quality tests. Acceptance is reproducible historical MGC import, validation, storage and replay.
75. **Phase 2, structure.** Implement pivots, classification, BOS, MSS, liquidity and sweeps, FVG/IFVG, event persistence and deterministic tests. Every event must be independently reconstructable from available source candles.
76. **Phase 3, strategy.** Implement versioned mirrored strategies and persistent state, invalidation, reward/risk, sizing, score and tests. Produce deterministic explainable research signals.
77. **Phase 4, backtests.** Implement event-driven replay, cost/slippage, conservative fills, trade lifecycle, metrics, logs and reproducible run records. Results must be auditable and free of future leakage.
78. **Phase 5, validation.** Implement walk-forward, unseen testing, bootstrap, Monte Carlo, sensitivity, regimes, confidence intervals and Lucid Flex simulation. Evaluate whether evidence supports an advantage under disclosed assumptions.
79. **Phase 6, monitoring.** Add authorized streaming, event processing, incremental candles and structure, setups, recovery, integrity and notifications. Require parity for equivalent historical and live input.
80. **Phase 7, interface.** Add dashboard, chart and overlays, setups/history, backtests, Monte Carlo, accounts and configuration. Displayed signals and metrics must trace to underlying evidence.
81. **Phase 8, forward paper.** Track paper outcomes, daily/weekly/monthly reports, drift, calibration and long-term performance. Keep forward and historical results distinct and comparable.
82. **Phase 9, optimization.** Profile bottlenecks, improve throughput, latency, UX and deployment reliability, and evaluate optional ML after earlier phases stabilize.

## XXV. Definition of done

83. **Completion.** Require working source, met acceptance, verified tests or documented limitations, resolved critical defects, accurate docs, roadmap and handoff, and an identifiable next task. Data correctness and causal research take precedence over UI polish and headline performance.

## XXVI. First execution

84. **Empty repository.** Inspect directory, Git and source and preserve existing work. Initialize project structure, AGENTS and all required docs, Python config, initial database/models and a functional historical MGC pipeline. Add tests, run verification and record the initial checkpoint. Implement verified vertical slices.
85. **Existing repository.** Preserve Git and work. Read instructions and checkpoint, inspect roadmap, status and source, verify and continue the highest-priority unblocked task. Run relevant tests and update the handoff.

## XXVII. Status

86. **Progress.** At meaningful checkpoints distinguish Completed, Verified, Remaining, Blockers and Next. Name actual work, commands, limitations and the precise next action. Claim asynchronous continuation only when supported.

## XXVIII. Success criteria

87. **Operating commitments.** Keep durable knowledge in the repository. Treat the strategy as a hypothesis, handle financial data accurately and chronologically, test milestones, maintain handoffs, limit complexity and avoid fabricated implementation, evidence or access.
88. **Successful platform.** Reliably process authorized MGC data, classify structure and sweeps, confirm MSS causally, detect reproducible imbalances, evaluate and explain strategy signals, run auditable cost-aware backtests, test unseen stability, simulate relevant account restrictions, issue timely research notifications, track forward paper outcomes, report statistics, recover from interruptions and remain maintainable across sessions.
89. **Research objective.** Determine whether defined market-structure hypotheses exhibit repeatable out-of-sample advantages under realistic costs and operational constraints. Build verified software, preserve knowledge, avoid repeated work and report observed results.
