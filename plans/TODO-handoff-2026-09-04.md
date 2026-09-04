# Handoff TODO — 2026-09-04

Session paused mid-task by the user to continue from a different machine.
Read this whole file before doing anything — it has the full context and
the exact next step.

## #TODO: finish the whole-repo hard-mode Sol review, apply fixes, verify

**What's already done and safe (nothing below needs redoing):**

The BFI-2 + Risk Propensity Scale + DRIP real-time quality-gate feature is
**fully implemented, reviewed, committed, and pushed** in both repos:

- `bfi-2-qualtrics`, branch `claude/feature-implementation-b4cf0f`, commit
  `0ba5f07` — the BFI-2+RPS survey (`add_rps.py`, `gen_drip_js.py`,
  `output/BFI-2_Full_RPS.qsf`, `docs/qualtrics-part2-wiring.md`,
  `tests/test_add_rps.py`).
- `ase2-ai-mode`, branch `claude/prolific-study-automation-883ac1`,
  commits `ac8d612` (checkpoint of a prior session's uncommitted quality-
  gate feature), `a625b42` (fold `drip_score` into the verdict endpoint),
  `dbc4d70` (5 rounds of Sol-review fixes to that). Full iteration log in
  that repo's `worklogs/2026-09-04-drip-score-gate.md`.

Both repos' full test suites pass (`bfi-2-qualtrics`: `python3 -m
unittest discover tests`, 22 passed; `ase2-ai-mode`: `cd server && uv run
pytest`, 206 passed / 1 skipped) and `qsf_lint.py` is clean on all four
`output/*.qsf`. Both branches are pushed to `origin` — nothing local-only.

**What's NOT done — this is the actual TODO:**

The user then asked for a separate, broader task: *"review the whole repo
of bfi2-qualtrics with sol on hard mode, try to improve it."* This is a
whole-repo quality audit (not scoped to the diff above) — different from
and in addition to the feature work already merged. It was **started but
never completed**: the Sol call was launched in the background and never
returned a response before the session was paused (no crash, no error —
it was still legitimately network-blocked waiting on the model after
~15+ minutes, well past how long every prior smaller call in this session
took, e.g. iterations 1-5 above each took 30-500s). **No findings from
this pass exist anywhere — nothing to read, nothing was skipped over.**

### Exact next step

Re-run it. The harness is `/Users/e103037/repos/ase2/scripts/llm_harness.py`
— a separate local repo, **machine-specific**: if this session is
resuming somewhere else, that repo/script may not exist there at all, or
may be at a different path, or its own `.env` (which holds
`OPENAI_BASE_URL`/`OPENAI_API_KEY`/`OPENAI_MODEL_ID` for the
`gpt-5.6-sol` model) may differ or be absent. Check for it first
(`ls ~/repos/ase2/scripts/llm_harness.py`); if it's not there, ask the
user how they want to run this kind of external-model review on the new
machine before assuming anything.

If the harness exists, from `bfi-2-qualtrics` repo root:

```bash
cd /Users/e103037/repos/bfi-2-qualtrics/.claude/worktrees/feature-implementation-b4cf0f  # or wherever this worktree/checkout now lives
{
for f in CLAUDE.md README.md \
         .claude/skills/bfi2-qsf-splitter/SKILL.md \
         .claude/skills/bfi2-qsf-splitter/split_bfi2.py \
         .claude/skills/bfi2-qsf-splitter/add_rps.py \
         .claude/skills/bfi2-qsf-splitter/gen_drip_js.py \
         .claude/skills/qsf-tools/SKILL.md \
         .claude/skills/qsf-tools/SCHEMA.md \
         .claude/skills/qsf-tools/qsf_lint.py \
         response_verification/README.md \
         response_verification/verify_responses.py \
         tests/test_qsf_parses.py \
         tests/test_verify_responses.py \
         tests/test_add_rps.py \
         docs/qualtrics-part2-wiring.md \
         plans/careless-responding-detection.md \
         .claude/skills/bfi2-qsf-splitter/master_mapping.json \
         response_verification/drip_item_pairs.tsv; do
  echo "=== FILE: $f ==="
  cat "$f"
  echo ""
done
} > /tmp/wholerepo_review.txt

python3 /Users/e103037/repos/ase2/scripts/llm_harness.py "Hard mode: do a thorough, critical, adversarial whole-repository review of this Qualtrics BFI-2 survey generator/tooling repo. This is essentially the entire repo's source (skills/scripts, tests, docs, data tables) minus the generated .qsf binary/JSON survey files themselves. Look for: correctness bugs (including subtle ones in the scoring math, reverse-keying, ID generation), data integrity issues in master_mapping.json / drip_item_pairs.tsv, inconsistencies between docs and code, missing test coverage, security issues, over-engineering vs under-engineering, error handling gaps, and anything a careful senior engineer doing a full audit would flag. Don't hold back or pad with praise -- prioritize real, actionable findings, ranked most severe first. Cite file:line. This repo builds Qualtrics survey files (.qsf) with automatic personality-test scoring baked in, used for a real research study; correctness of the scoring math matters a lot." --file /tmp/wholerepo_review.txt --session bfi2-wholerepo-audit --max-tokens 7000
```

(A prior attempt used `--session bfi2-wholerepo-audit` too, but its
session file `~/repos/ase2/scripts/.llm_sessions/bfi2-wholerepo-audit.json`
was never written since that call never returned — confirm it doesn't
exist, or if it does, whether it holds a real reply, before assuming a
fresh session is needed. This call may legitimately take several
minutes; run it with a generous timeout and don't assume a hang after 1-2
minutes the way the paused session initially did.)

**After it returns:** evaluate each finding on its merits (the earlier
5-iteration drip_score review in `ase2-ai-mode` is the model for how
this session was doing that — verify claims against the actual code
before trusting them, fix what's real, push back in the next round on
anything wrong), apply fixes, re-run both repos' full test suites +
`qsf_lint.py`, commit, and — **only if the user asks for it explicitly
again** — push (this session pushed the feature work because the user
explicitly said "commit and push"; don't assume that authorization
carries forward to a new, unrelated batch of changes without asking).

## Reference: how "Sol" works in this environment

Not documented anywhere else — worth keeping here since it's non-obvious
and this session had to rediscover it (see the "Sol" review threads
above in whatever conversation log accompanies this handoff, if any):

- `ase2-ai-mode/.env` and `~/repos/ase2/.env` are separate files. The
  `ase2` one is what `llm_harness.py` reads (`OPENAI_MODEL_ID` there
  points at `gpt-5.6-sol` specifically — the harness overrides
  `.env`'s own `OPENAI_MODEL_ID` default to `gpt-5.6-sol` unless
  `--model` is passed).
- The harness (`ase2/scripts/llm_harness.py`) is a plain script, not an
  MCP tool or slash command — invoke it directly via Bash.
- `--session NAME` persists multi-turn history to
  `ase2/scripts/.llm_sessions/NAME.json`, letting a later call continue
  a conversation (e.g. "iteration 2, here's what I fixed, re-check").
  Reuse the SAME session name across iterations to keep that context;
  the model's own memory of iteration 1's findings is what let it verify
  the iteration-2 fixes zeroed in correctly.
- The harness prints an estimated cost to stderr after each call
  (`gpt-5.6-sol` is $5/$30 per 1M input/output tokens — not cheap; the
  drip_score review's 5 iterations cost roughly $1.85 total). Be
  reasonably economical with iteration count and payload size.
- **Sandboxed Claude Code sessions may get their outbound call to this
  harness BLOCKED by the auto-mode permission classifier** — it read as
  credential exfiltration on the first two attempts in this session (an
  ad-hoc script sourcing `.env` directly, and a request to add a
  permission rule). It was NOT blocked when invoked as a direct call to
  the *existing, committed* `ase2/scripts/llm_harness.py` script by
  path — that distinction (existing project tool vs. ad-hoc credential
  handling) seemed to be what mattered. If blocked again, don't try to
  work around it — stop and ask the user, same as this session did.
