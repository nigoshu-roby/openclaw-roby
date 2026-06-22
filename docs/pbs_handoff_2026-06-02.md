# PBS Handoff - 2026-06-02

## Overview

Project: Beyond Synapse (PBS) is running on OpenClaw as a local-first worker pipeline.

Current practical focus is not core platform construction but precision and ops:

- Gmail precision and low-noise triage
- Minutes project classification (`wrong_project` reduction)
- Cron stability, secret handling, and operator visibility

Primary task sink remains Neuronic.

## Last committed checkpoint

- Commit: `4b04f682e0`
- Message: `Harden PBS cron flows and modularize Gmail triage`

This commit includes:

- cron installer / uninstaller hardening
- `roby-cron-doctor.sh`
- Gmail triage modularization
- reply-review flow additions
- orchestrator pipeline/profile split
- self-growth advisor-only mode on dirty worktrees

## Current uncommitted work

These files are intentionally still dirty and were not committed yet:

- `MEMORY.md`
- `HEARTBEAT.md`
- `scripts/roby-minutes.py`
- `scripts/roby-tokiwagi-master-registry.py`
- `scripts/roby_context_seed.py`
- `scripts/tests/test_roby_context_seed.py`
- `scripts/tests/test_roby_minutes_quality.py`
- `scripts/tests/test_roby_tokiwagi_master_registry.py`

These changes are the current `wrong_project` reduction work.

## What was changed after the last commit

### 1. Minutes project hint strengthening

`scripts/roby-minutes.py` was updated so that project inference and registry hinting use:

- `client_name`
- `related_entities`

Effect:

- aliases such as `株式会社ボーネルンド`
- related brand/entity names such as `KIDKID`, `キドキド`

now contribute directly to minutes-side project classification.

### 2. Registry builder now carries context-seed project metadata

`scripts/roby-tokiwagi-master-registry.py` was updated so that registry generation merges context-seed metadata into registry rows:

- `client_name`
- `related_entities`

This means the generated registry now exposes richer project hints instead of relying only on historical doc titles and aliases.

An audit append bug in the registry builder was also fixed:

- old code used `append_audit_event(..., counts=..., elapsed_ms=...)`
- current audit helper expects a payload dict
- this is now corrected

### 3. Context-seed parser noise reduction

`scripts/roby_context_seed.py` was updated so descriptive phrases are not incorrectly treated as related entity aliases.

Example:

- kept: `KIDKID`, `キドキド`
- dropped: explanation-style text such as `Moooviではなく予約システムの話が主。`

## Tests already run

All of the following passed during this run:

- `python3 -m unittest scripts.tests.test_roby_self_growth scripts.tests.test_roby_orch_profiles scripts.tests.test_roby_orch_pipelines scripts.tests.test_roby_neuronic scripts.tests.test_roby_gmail_classify scripts.tests.test_roby_gmail_context scripts.tests.test_roby_gmail_tasks scripts.tests.test_roby_mail_reply_review scripts.tests.test_roby_context_seed scripts.tests.test_roby_eval_harness scripts.tests.test_roby_slack_events_server scripts.tests.test_roby_minutes_neuronic`
- `python3 -m unittest skills.roby-mail.scripts.test_gmail_triage_classify skills.roby-mail.scripts.test_gmail_triage_neuronic`
- `python3 -m unittest scripts.tests.test_roby_minutes_quality scripts.tests.test_roby_minutes_neuronic scripts.tests.test_roby_tokiwagi_master_registry`
- `python3 -m unittest scripts.tests.test_roby_context_seed scripts.tests.test_roby_minutes_quality scripts.tests.test_roby_minutes_neuronic scripts.tests.test_roby_tokiwagi_master_registry`
- `python3 -m py_compile scripts/roby-context_seed.py` was not run because the filename is wrong; the actual successful checks were:
  - `python3 -m py_compile scripts/roby_context_seed.py scripts/roby-minutes.py scripts/roby-tokiwagi-master-registry.py`
  - earlier broader `py_compile` checks for orchestrator/Gmail/self-growth also passed
- `./scripts/roby-cron-doctor.sh --deep`
  - result: `fails=0 warnings=0` at the time it was run

## Runtime observations

### Gmail

As of 2026-06-02 JST, `gmail_triage` is healthy and quiet:

- latest cron cycles show `new=0`
- `tasks=0`
- `neuronic_errors=0`
- no active warnings in the latest shown runs

### Self growth

`self_growth` is running hourly in advisor-only mode because the worktree is dirty.

This is expected after the redesign.

Latest observed behavior:

- reports `ADVISOR: patch mode skipped`
- does not touch files
- keeps pointing at the main precision targets

Current priority targets shown by self-growth:

- `案件判定` / `wrong_project`
- `メルマガ判定` / `newsletter_false_positive`
- `確認タスク判定` / `should_be_review_only`

### Memory sync / heartbeat

Latest observed `memory_sync` was not clean.

As of the most recent logs checked:

- `heartbeat_status = HEARTBEAT_ATTENTION`
- unresolved:
  - `Runbook Drill fail 2/13`
  - `stale component: notion_sync`
  - `audit errors: 733`

So PBS is running, but operator health is not fully green.

### Minutes

Important update since the earlier conversation:

recent `minutes_sync` runs are no longer all idle.

Latest observed summaries in `~/.openclaw/roby/minutes_runs.jsonl` included:

- one run with `notion_pages=2`, `tasks=15`, `neuronic_created=14`, `neuronic_updated=1`
- one run with `notion_pages=1`, `tasks=10`, `neuronic_created=10`

So minutes ingestion is currently alive and capable of creating tasks again.

Separate forced dry-runs were also performed for validation:

- target `2026/05/26 社内定例MTG`
  - `notion_pages=1`
  - `candidates_total=1`
  - final `tasks=0`
  - debug showed `ボーネルンド` present in `project_hints`
- target `2026/05/19 社内定例MTG`
  - `notion_pages=1`
  - `tasks=9`
  - dry-run completed successfully

Interpretation:

- project hinting has improved
- the remaining issue is not only classification but also final extraction/gating quality

## Registry verification

The TOKIWAGI registry was regenerated successfully with:

```bash
./scripts/roby-keychain-run.sh python3 scripts/roby-tokiwagi-master-registry.py --json --skip-local-llm
```

Result:

- generated successfully
- counts:
  - `databases=2`
  - `documents=247`
  - `projects=11`

The generated registry now contains better project metadata.

Observed examples:

- `ボーネルンド`
  - aliases include `株式会社ボーネルンド`, `KIDKID`, `キドキド`
  - `client_name = 株式会社ボーネルンド`
  - `related_entities = [KIDKID, キドキド]`
- `BT振興会-Mooovi`
  - aliases now cleaner than before
  - no explanation-style phrase remained after parser cleanup

## Most important open problems

### 1. `wrong_project` is still the top minutes-side precision problem

Current top reason remains:

- `wrong_project: 46`

This is still the highest-signal next area.

### 2. Heartbeat is not green

Open ops issues:

- drill failures
- stale `notion_sync`
- many audit errors

### 3. Some minutes runs still show unstable LLM behavior

During forced target runs, repeated messages appeared:

- `LLM returned an empty summary (model google/gemini-2.5-pro).`

Fallbacks handled this, but it is still a quality/stability smell.

## Recommended next actions

If resuming this work, the best next sequence is:

1. Review the current uncommitted minutes/classification changes and commit them as a focused `wrong_project` reduction checkpoint.
2. Inspect the actual tasks created in the recent successful minutes runs:
   - run ids seen: `roby:minutes:8b2a531770b0`, `roby:minutes:8dd931f4e604`
3. Compare those created tasks against Neuronic feedback to see whether the new project hinting reduced misclassification in practice.
4. Investigate the unresolved heartbeat items:
   - drill failure source
   - `notion_sync` staleness
   - audit error spike
5. If needed, tune the minutes final gate so good candidates are not over-dropped after classification improves.

## Notes for the next chat

Good restart prompt:

```text
Open /Users/shu/OpenClaw/docs/pbs_handoff_2026-06-02.md and continue the PBS wrong_project reduction work.
Current uncommitted files are minutes/context-seed/registry related only, plus MEMORY.md and HEARTBEAT.md snapshots.
Start by reviewing the uncommitted changes and the recent successful minutes runs:
- roby:minutes:8b2a531770b0
- roby:minutes:8dd931f4e604
```
