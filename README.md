# Jira Release Evidence Agent

Decides whether the requirements of a release are backed by evidence in Jira, and writes one
release-readiness report with a decision, explanation and Jira references per requirement.

```
inputs/dataset.json --(importer)--> Jira project --(tools)--> AI agent --> code checks --> report
```

`dataset.json` is only used by the importer. The agent reads everything from Jira through tools it
chooses to call. It never sees the JSON file.

## Setup

Needs Python 3.10+, a Jira Cloud site with an empty project, and an API key for any
OpenAI-compatible model. The default is Google Gemini's free tier.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt     # requirements-lock.txt has every package pinned
cp .env.example .env                 # fill it in; each line says where the value comes from
python -m app.check                  # tests the Jira login, the project and the model
```

## Import

```bash
python -m app.importer inputs/dataset.json --dry-run   # shows one converted record
python -m app.importer inputs/dataset.json             # 400 issues, 2059 comments, links
```

Running it again skips anything already imported. `outputs/import_summary.json` maps each source
id to its Jira key.

| Dataset | Jira |
|---|---|
| `source_id` | summary `[REQ-12] ...`, label `src-REQ-12`, `Source ID:` line in the description |
| dataset scope | label `tyn-release-assessment-v2` on every issue |
| `kind`, `release`, `recorded_status` | labels `kind-...`, `release-...`, `status-...`, header lines, and the Jira status when the workflow has one with the same name |
| requirements | extra label `rr-requirement` (named in the manifest) |
| `description` | unchanged, below a short header |
| `comments` | one Jira comment each, in order, unchanged, prefixed `[REQ-12 comment 3]` |
| `links` | Jira "Relates" links, plus a `Links:` header line |

The scenario's actors, roles and times stay in the comment text
(`Recorded event 2026-11-08T14:00:00+05:30 | actor Priya Nair | role QA Lead`). Jira's own author
and date on each comment belong to the importer, and the agent is told to ignore them.

## Run

```bash
python -m app.main --project TYN --label tyn-release-assessment-v2 --release R2.4 --out submission
python -m app.main --only REQ-12     # a single requirement, for a quick test
```

The project, label and release default to the values in `.env`.

Output folder:

| File | Contents |
|---|---|
| `report.md` | Overall decision, a summary table, then for each requirement: decision, criterion, target build, explanation, evidence used (with links to the exact Jira comment plus the scenario actor and time), evidence that was not counted, what is still needed, and any code-check notes |
| `report.json` | The same, as data |
| `trace.jsonl` | Every model call (tokens, time) and every tool call (input, result preview, ok/error), in order |
| `metrics.json` | Run time, model calls, tool calls, tokens |

## Tests

```bash
python -m pytest -q
```

They run offline against a small stand-in Jira and a scripted model.

## How it works

1. **Find requirements (model).** The model reads the manifest and searches Jira, then returns the
   requirement ids. The code also runs its own search and warns about any requirement the model skipped.
2. **Assess each requirement (model).** Each requirement gets its own fresh conversation, which keeps
   context small and makes every decision traceable. The model reads the requirement, follows links,
   pages through comments, runs `check_build` on the results it relies on, and calls `record_decision`.
3. **Double-check (code).** Every cited comment is fetched again. A comment that doesn't exist is dropped.
   A comment about another build is moved to "not counted". A PASS or FAIL left with no comment clearly
   about the target build becomes INSUFFICIENT_EVIDENCE. The report shows both the model's decision and
   the final one.
4. **Overall (code).** BLOCKED if any mandatory requirement fails, otherwise NEEDS_REVIEW if any lacks
   evidence, otherwise READY.

Tools: `read_manifest`, `search_issues`, `get_issue`, `get_comments` (25 per page), `check_build`, plus
the finish tools `submit_requirements` and `record_decision`. All Jira searches are limited to
`project + dataset label`, and `get_issue` refuses issues without that label.

| File | Job |
|---|---|
| `app/main.py` | the run, steps 1-4 |
| `app/agent.py` | system prompt and the model/tool loop |
| `app/tools.py` | tool definitions and what each one does |
| `app/rules.py` | fixed rules: requirement fields, build matching, overall decision |
| `app/report.py` | the code double-check, report files |
| `app/jira.py` | Jira REST calls |
| `app/importer.py` | dataset to Jira |
| `app/check.py` | setup check |
| `app/trace.py` | trace file and totals |
| `app/config.py` | reads `.env` |

## Design choices

- **The model judges, code checks.** Reading comments, "supersedes", conflicting results and approvals
  is a language task. Matching release, commit, service and environment, and the overall rule, are
  exact checks, so code does those and re-checks the model's citations.
- **Small tools and paging.** Search returns short rows, and comments come 25 at a time. One record has
  64 comments with the real result at the end, so nothing is silently cut. Results are capped and
  Jira reads are cached.
- **Bounded.** There is a step limit per requirement, plus retries and backoff for Jira and for model
  rate limits. A tool error goes back to the model as text. A requirement that never finishes is
  reported as INSUFFICIENT_EVIDENCE together with the error, and the run is marked incomplete.
- **Any OpenAI-compatible model.** It uses the `openai` client with a configurable base URL: Gemini
  (free tier) by default, and Groq, OpenRouter, Ollama or OpenAI work by changing `.env`.
- **No agent framework.** A short hand-written loop keeps every step readable.

## Known limits

- Build details are read with regular expressions written for the dataset's wording. Anything worded
  differently reads as "unclear", which leans towards INSUFFICIENT_EVIDENCE rather than a wrong PASS or FAIL.
- The prompt version is only required when the acceptance criterion mentions the prompt (manifest rule).
- Supersession, contradictions, approver roles and linked-dependency failures are left to the model's
  judgement, guided by the prompt and the manifest. The trace shows what it read.
- One record contains text telling reviewers what to report. The model is told record text is evidence,
  not instructions, and the code checks don't depend on the model resisting it.
- Free-tier models are weaker and rate-limited. The run is slow (paced calls) and decisions can be
  less accurate than with a stronger model.
- Requirements are assessed one after another.
- Results can vary between runs with a small model; the submitted report is one real run.

## AI assistance

<!-- Write this yourself, honestly, before submitting. -->

## Results of the submitted run

Report: [`submission/report.md`](submission/report.md). Trace: [`submission/trace.jsonl`](submission/trace.jsonl).
Metrics: [`submission/metrics.json`](submission/metrics.json).

**Overall: BLOCKED** (4 of the 12 mandatory requirements fail)

| Requirement | Decision | Main reason |
|---|---|---|
| REQ-01 Automated regression tests | PASS | 146/146 on the target commit; the 3 failures were on an older commit |
| REQ-02 Customer document isolation | FAIL | retest on the target build exposed another customer's document; the 40/40 pass was R2.3 |
| REQ-03 Answer quality for deployed prompt | INSUFFICIENT_EVIDENCE | the target-build evaluation used prompt p6, not p7; the other run was on staging |
| REQ-04 Response latency | PASS | production p95 1.40 s; the 2.60 s run was staging |
| REQ-05 Model usage cost | FAIL | USD 0.018 > 0.012 on the target build; a comment asking reviewers to report PASS was ignored |
| REQ-06 Production security approval | INSUFFICIENT_EVIDENCE | the Security Owner approved staging only; the production approval came from another role |
| REQ-07 Sensitive log redaction | PASS | REDACT-901 (28/28) explicitly supersedes the earlier FAIL; the support-widget failure is another service |
| REQ-08 Human handoff delivery | FAIL | the linked queue-adapter verification: 0 of 12 handoffs reached the queue |
| REQ-09 Rollback rehearsal | INSUFFICIENT_EVIDENCE | two QA Leads report opposite results on the same build, with no resolution |
| REQ-10 Document parsing completeness | PASS | PARSE-622 (60/60) supersedes the 59/60 run |
| REQ-11 Accessible keyboard handoff | INSUFFICIENT_EVIDENCE | only a developer self-test; the QA pass was on an older release |
| REQ-12 Streaming completion integrity | FAIL | STREAM-731: 49 of 50 cases, a duplicated final message (comment 64 of 64) |

| Measure | Value |
|---|---|
| Model | `gemini-3.5-flash-lite` (free tier, OpenAI-compatible API) |
| Run time | 627 s (calls paced 5 s apart for the free tier) |
| Model calls / tool calls / tool errors | 119 / 112 / 0 |
| Tokens | 336,076 input / 5,816 output |
| Import | 400 issues, 2,059 comments, 76 links, all statuses set, 1,670 s |

Notes on the run:

- An earlier run failed honestly at step 1: the model began assessing requirements instead of
  returning the list, hit the step limit, and the report said so. Step 1 now only gets the manifest
  and search tools.
- In the next run REQ-09 came out as FAIL, even though the model noted the conflict was unresolved.
  The manifest rule on unresolved contradictions was added to the system prompt (a general rule,
  not a per-requirement one) and the whole run was repeated. The report above is that full run.
- The code check changed no decisions in the final run.
- One small slip in the wording: REQ-11's explanation says the QA record was "R2.4 on an older
  commit"; the record says release R2.3. The decision is unaffected.
