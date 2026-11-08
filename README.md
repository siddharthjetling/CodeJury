# CodeJury ⚖️
### The right verification team for every code change.

CodeJury is an independent verifier for AI-generated code. An orchestrator looks at each proposed change, picks the relevant specialist agents, and coordinates their investigations. Each specialist returns evidence. A deterministic policy gate uses that evidence to pass the change, block the merge, or hold it for review.

**Every blocking finding needs proof.**

| | |
|---|---|
| **Primary track** | Trust but Verify |
| **Also submitting to** | Fetch.ai challenge (uAgents, Agentverse, ASI:One) |
| **Reasoning** | Meta Model API |
| **Agent runtime and chat access** | Fetch.ai uAgents, Agentverse, ASI:One |
| **Status** | Hackathon MVP in progress |

---

## Problem Statement: Trust but Verify

AI writes code faster than teams can check whether it's safe to merge. Build an independent verifier that evaluates AI-generated changes, catches defects, and blocks or rolls back anything below the required standard. How can a verifier write its own tests without sharing the implementer's blind spots, and stay independent, reproducible and hard to game?`

## The Problem

A patch can pass existing tests while still introducing a behavioral regression, exposing customer data, or making an endpoint much more expensive to run.

A reviewer who reads the code first tends to inherit the author's assumptions. Different changes also need different checks. CodeJury picks specialists based on what changed, while always running a mandatory baseline of verification.

---

## How It Works

Talk to **@codejury on ASI:One**:

```
"Verify the export-orders change in the demo repo and show me any blocking findings."
```

1. **Receive a change:** repository, base commit, candidate commit, and task requirements (spec or issue).
2. **Run baseline checks:** existing tests and repository-required checks.
3. **Select the jury:** the orchestrator inspects the diff and selects the relevant specialists.
4. **Investigate independently:** each specialist generates checks and runs them in an isolated Docker sandbox.
5. **Present evidence:** reproducible failures, measurements, and advisory findings.
6. **Deliver a verdict:** a deterministic policy gate returns PASS, BLOCK, or INCONCLUSIVE.
7. **Show the reasoning:** which checks ran, why they ran, and the evidence behind each finding, returned in the ASI:One chat.

---

## Architecture

Each specialist is its own Fetch.ai uAgent. Agents use the **Meta Model API** (through the OpenAI Python SDK) for reasoning and test generation. Fetch.ai handles agent messaging, registration on Agentverse, and chat access through ASI:One. No Fetch.ai model is used for reasoning.

```
You (ASI:One chat)
        │  Agent Chat Protocol
        ▼
┌──────────────────────────────┐
│ Orchestrator (8001)          │  Reads diff + requirements, selects the jury,
│ @codejury on Agentverse      │  enforces mandatory checks
└───┬──────────┬──────────┬────┘
    │          │          │      Separate contexts, no shared conclusions
┌───▼──────┐┌──▼───────┐┌─▼───────────┐
│Correctness││ Security ││ Performance │  Meta Model API for reasoning
│  (8002)  ││  (8003)  ││   (8004)    │  Tests run in Docker sandbox
└───┬──────┘└──┬───────┘└─┬───────────┘
    └──────────┼──────────┘
               ▼
┌──────────────────────────────┐
│ Verdict Agent (8005)         │  Deterministic policy gate decides.
│                              │  LLM only writes the human summary.
└──────────────────────────────┘
```

### The Jury

| Agent | Port | Fires when | Expected evidence |
|---|---|---|---|
| **Orchestrator** | 8001 | Always | Routing decision and reasons |
| **Correctness** | 8002 | Always (mandatory baseline) | Executable tests from requirements, failing examples, edge cases |
| **Security** | 8003 | Access control, auth, data handling, or user input changed | A reproducible violation of a defined rule |
| **Performance** | 8004 | Database operations, loops over data, or hot endpoints changed | Query counts, timing, or resource measurements under a fixed workload |
| **Verdict** | 8005 | Always, last | PASS / BLOCK / INCONCLUSIVE plus an evidence summary |
| Readability | – | Planned extension | Advisory suggestions only |

### Intelligent Routing

The orchestrator selects specialists based on changed files, affected components, task requirements, and repository policy. For example:

- Access-control changes require security verification.
- Database changes trigger correctness and performance checks.
- Public behavior changes trigger contract and regression tests.

**Routing cannot disable mandatory checks.** Repository policy sets the minimum verification even when the orchestrator sees no particular risk. The routing decision and its reasons appear in the verdict.

---

## Independence by Design

- **Spec-first tests.** Behavioral tests are generated from requirements and the public interface (routes, function signatures) before the specialist reads the patch implementation. Patch-aware checks are added afterwards to probe specific risks.
- **Separate contexts.** Specialists don't see each other's conclusions before producing their initial findings.
- **Protected artifacts.** The implementing agent can't modify the verifier's protected tests, fixtures, or acceptance policy.
- **Untrusted input.** Repository content and patch comments are treated as data, never as instructions to the verifier.
- **No LLM verdicts.** The final decision comes from a deterministic policy gate, so the verdict can't be argued with through prompt injection.

Multiple agents don't guarantee independent reasoning. CodeJury makes independence inspectable through context separation, protected artifacts, and executable evidence.

---

## Evidence Before Verdicts

Each finding includes:

- The affected commit and code location.
- The requirement or policy being checked.
- What was observed and what is only inferred.
- A reproducible test or measurement procedure.
- Expected and actual behavior.
- Execution logs and artifacts.
- Whether it's blocking or advisory.

An AI-generated suspicion isn't automatically a confirmed defect. Performance findings need a defined workload and threshold. Deterministic measurements such as database-call counts are preferred over timing. Where timing is used, it comes from repeated runs.

## Verdict Policy

| Verdict | Meaning | Action |
|---|---|---|
| **PASS** | All required checks completed and met the configured standards | Allow the configured merge workflow |
| **BLOCK** | Evidence shows a required standard was violated | Prevent the merge |
| **INCONCLUSIVE** | A required check failed to run or the evidence is insufficient | Hold the merge for review or rerun |

PASS means the configured checks passed. It doesn't guarantee the absence of defects. Readability feedback is advisory unless a repository makes a rule mandatory. The MVP blocks defective changes before merge; controlled rollback is a planned extension.

---

## Demo Scenario

An AI adds an **Export Orders** endpoint to the demo repo. The existing test suite passes, but the change has two defects:

1. A customer can receive another customer's orders.
2. The implementation makes one extra database call per order (N+1 queries).

Example conversation on ASI:One:

```
You:       Verify the export-orders change in the demo repo.

CodeJury:  Baseline: existing tests passed (24/24).

           Detected: new endpoint, database access, customer data
           Jury: Correctness (mandatory) + Security + Performance

           Correctness: 7 spec-derived tests, 7 passed ✅
           Security:    4 tests, 1 failed ❌
             Customer A received 3 orders belonging to customer B
             Repro: tests/protected/test_export_isolation.py::test_cross_customer
           Performance: 1 failed ❌
             DB queries: 1 order → 2 queries, 100 orders → 101 queries
             Threshold: ≤ 3 queries regardless of order count

           🔴 BLOCK: 2 required checks failed. Evidence and logs saved to run 0007.
```

CodeJury then reruns the same saved checks against the corrected patch and returns **PASS**.

---

## Reproducible Verification

Each run saves:

- Base and candidate commit IDs.
- Requirements and policy versions.
- Generated test files and input datasets.
- Container image and dependency versions.
- Random seeds, commands, and execution limits.
- Model IDs and generation settings.
- Logs, measurements, and the final verdict.

Saved tests can be replayed without asking the model to generate them again. Model generation itself may stay nondeterministic.

## Execution Boundaries

Candidate code runs in disposable Docker containers with:

- No model API keys or production credentials.
- Network disabled during test execution.
- CPU, memory, and time limits.
- Separate data stores for the baseline and candidate runs.
- Read-only mounts for protected verification artifacts.

Container isolation is one boundary, not a guarantee against hostile code. The MVP targets a controlled demo repository.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Reasoning and test generation | Meta Model API via the OpenAI Python SDK |
| Agent framework | Fetch.ai uAgents |
| Agent registry and chat | Agentverse, ASI:One (Agent Chat Protocol) |
| Message models | Pydantic |
| Behavioral testing | pytest, Hypothesis where useful |
| Test execution | Docker |
| Version comparison | Git |
| Evidence storage | SQLite and JSON artifacts |
| Dashboard (stretch) | React, Vite, Tailwind CSS |

---

## How to Run

> These steps describe the target setup. The files are being built during the hackathon.

### Prerequisites

- Python 3.11
- Docker running locally
- A Meta Model API key

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; use source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill it in. `.env` is gitignored; never commit it.

```bash
LLM_PROVIDER=meta                         # meta (default) or asi1 as a fallback
META_API_KEY=...                          # Meta Model API key
META_API_BASE_URL=https://api.meta.ai/v1  # Meta's OpenAI-compatible endpoint
META_MODEL=muse-spark-1.3                 # Model ID used for reasoning
ORCHESTRATOR_SEED=...                     # One long random value per agent.
CORRECTNESS_AGENT_SEED=...                # A seed is the agent's private key
SECURITY_AGENT_SEED=...                   # and decides its address.
PERFORMANCE_AGENT_SEED=...
VERDICT_AGENT_SEED=...
```

API keys stay in the agent processes and are never passed into the test containers.

Docker must be running. Build the demo repo once:

```bash
python demo/build_demo_repo.py --force
```

To run the whole jury in one process, without uAgents:

```bash
python verify.py ai-export-orders                 # BLOCK (uses the Meta API to write tests)
python verify.py ai-export-orders-fixed           # PASS
python verify.py ai-export-orders --no-llm        # protected tests only, no API key needed
```

Each run saves the spec, diff, generated tests and verdict under `runs/<run_id>/`.

### Step 1: Start the specialist agents (one terminal each)

```bash
python correctness_agent.py   # copy printed address → config.py CORRECTNESS_AGENT_ADDRESS
python security_agent.py      # copy printed address → config.py SECURITY_AGENT_ADDRESS
python performance_agent.py   # copy printed address → config.py PERFORMANCE_AGENT_ADDRESS
python verdict_agent.py       # copy printed address → config.py VERDICT_AGENT_ADDRESS
```

### Step 2: Fill in `config.py`

```python
CORRECTNESS_AGENT_ADDRESS = "agent1q..."
SECURITY_AGENT_ADDRESS    = "agent1q..."
PERFORMANCE_AGENT_ADDRESS = "agent1q..."
VERDICT_AGENT_ADDRESS     = "agent1q..."
```

### Step 3: Connect mailboxes on Agentverse

For each agent, open the inspector URL printed in its terminal and click **Connect**.

### Step 4: Start the orchestrator

```bash
python orchestrator.py
```

### Step 5: Use it on ASI:One

Go to [asi1.ai](https://asi1.ai), start a new chat, type `@codejury`, and paste a pull request from the demo repo:

```
@codejury Verify https://github.com/mkg0908/codejury-demo/pull/1
```

The orchestrator fetches the PR's commit into the local demo repo and runs the full jury on it. Only PRs from `CODEJURY_GITHUB_REPO` (default `mkg0908/codejury-demo`) are accepted, because CodeJury runs the PR's code. The requirements always come from `demo/spec.md`, never from the PR, so a PR author can't weaken them. A local branch name such as `Verify ai-export-orders` also works. `python verify.py <PR link>` runs the same flow without agents.

All agents run on the same machine as the demo repo and Docker. If a specialist doesn't reply within 300 seconds, its checks count as incomplete and the verdict is INCONCLUSIVE. If the Verdict agent doesn't reply within 60 seconds, the orchestrator applies the same policy gate itself.

---

## Project Structure

```
CodeJury/
├── orchestrator.py          # User-facing agent: picks the change, routes, runs baseline, timeouts, chat cards
├── correctness_agent.py     # Mandatory specialist (port 8002)
├── security_agent.py        # Access-control and data-handling specialist (port 8003)
├── performance_agent.py     # Query-count specialist (port 8004)
├── verdict_agent.py         # Applies the policy gate, LLM writes the summary (port 8005)
├── specialist.py            # Shared specialist flow: spec-first test generation + sandbox runs
├── jury.py                  # Routing, baseline, evidence, verdict record (no uAgents)
├── verify.py                # Whole jury in one process, for debugging and demo fallback
├── check_branch.py          # Protected tests + gate only, no LLM
├── policy_gate.py           # Deterministic PASS / BLOCK / INCONCLUSIVE rules
├── policy.json              # Mandatory, blocking and advisory categories
├── sandbox/                 # Docker runner, image, resource limits
├── verifier/                # Verifier-owned fixtures and protected tests
│   └── protected/           # correctness/, security/, performance/
├── demo/                    # Export Orders app source, the two changes, spec.md, repo builder
├── llm.py                   # Meta Model API client (OpenAI SDK)
├── models.py                # uAgents message models
├── config.py                # .env loading and agent addresses
└── runs/                    # Saved run artifacts (gitignored)
```

---

## MVP Scope

- One supported Python demo repository.
- Base vs. candidate commit comparison.
- Mandatory baseline checks.
- Dynamic selection of the Correctness, Security, and Performance agents.
- Independent investigations with executable evidence in Docker.
- A deterministic policy gate with PASS / BLOCK / INCONCLUSIVE.
- Full workflow through ASI:One chat.
- Merge enforcement in a controlled demo workflow.

## Planned Extensions

- Dashboard showing routing, agent progress, and evidence.
- GitHub pull-request checks and branch-protection integration.
- More languages and specialist agents (Readability first).
- Organization-specific verification policies.
- Controlled rollback workflows.
- Broader evaluation with defective and correct patches.

## Evaluation

We test CodeJury on a mix of defective and correct changes:

- Does it catch the intended defects?
- Does it wrongly block correct patches?
- Can findings be reproduced from saved artifacts?
- Does routing select the necessary specialists?
- Does it resist weakened or deleted repository tests?
- Does it hold the merge when required verification can't complete?

## Fetch.ai Challenge Fit

| Criterion | How CodeJury meets it |
|---|---|
| Agents that prove their work | Every verdict ships with reproducible tests, logs, and measurements |
| Multi-agent collaboration | Orchestrator plus specialist agents, each a separate uAgent, with visible routing |
| Meaningful action | Runs real tests in a sandbox and blocks or allows the merge |
| Complete workflow in ASI:One | Ask for a verification, get progress and the verdict in chat |
| Hard to game | Protected tests, spec-first generation, and a deterministic gate instead of an LLM verdict |

---

## Team and Work Split

| Feature | Owner | Deliverables |
|---|---|---|
| Docker sandbox and test runner | Siddharth | Container image, network off, resource limits, log capture |
| Policy gate and verdict rules | Siddharth | `policy_gate.py`, mandatory-check config, PASS / BLOCK / INCONCLUSIVE |
| Correctness agent | Siddharth | Spec-first test generation, regression checks |
| Performance agent | Siddharth | Query-count instrumentation, workload and thresholds |
| Demo repository | Siddharth | Export Orders app, defective patch, fixed patch, protected tests |
| Run storage and replay | Siddharth | SQLite + JSON artifacts, replay of saved tests |
| uAgents foundation | Manali | `models.py`, `config.py`, agent bootstrapping, Agentverse mailboxes |
| Orchestrator and routing | Manali | Diff reading, jury selection, mandatory-check enforcement |
| ASI:One chat flow | Manali | Agent Chat Protocol, progress messages, verdict formatting |
| Security agent | Manali | Access-control and data-isolation checks |
| Verdict agent | Manali | Collects results, calls the policy gate, writes the LLM summary |
| Meta Model API client | Shared | `llm.py`, prompts, model settings recorded per run |
| Demo script and pitch | Shared | End-to-end demo run, slides |
| Dashboard (stretch) | Whoever finishes first | React run viewer |

**Built by** Manali Gawande & Siddharth Jetling

---

**CodeJury: every blocking finding needs proof.**
