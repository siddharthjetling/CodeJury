# Code Jury
### The right verification team for every code change.

Code Jury is an independent verification system for AI-generated code. An orchestrator examines each proposed change, selects the relevant specialist agents, and coordinates their investigations.

Each specialist returns evidence. A policy gate uses that evidence to pass the configured checks, block the merge, or request further review.

**Hackathon track:** Trust but Verify  
**Status:** Proposed hackathon MVP

## Hackathon Problem Statement

### Trust but Verify

AI writes code faster than teams can check whether it’s safe to merge. Build an independent verifier that evaluates AI-generated changes, catches defects, and blocks or rolls back anything below the required standard. How can a verifier write its own tests without sharing the implementer’s blind spots, and stay independent, reproducible and hard to game?

## Our Approach

Code Jury assembles an independent jury of specialist agents for each proposed change.

The orchestrator selects the relevant specialists. Each specialist investigates independently and presents evidence. A deterministic policy gate delivers the final verdict.

**Every blocking finding needs proof.**

## The Problem

A patch can pass existing tests while introducing a behavioral regression, exposing customer data, or making an endpoint significantly more expensive to run.

Different changes need different checks. Code Jury selects specialists based on what changed while preserving a mandatory baseline of verification.

## How It Works

1. **Receive a change:** Accept a repository, base commit, candidate commit, and task requirements.
2. **Run baseline checks:** Execute existing tests and repository-required checks.
3. **Select the jury:** Inspect the diff and select relevant specialists.
4. **Investigate independently:** Specialists generate checks and execute them in isolated environments.
5. **Present evidence:** Collect reproducible failures, measurements, and advisory findings.
6. **Deliver a verdict:** Apply policy to produce PASS, BLOCK, or INCONCLUSIVE.
7. **Show the reasoning:** Display which checks ran, why they ran, and the evidence behind each finding.

## The Jury

| Specialist | Responsibility | Expected evidence |
|---|---|---|
| Correctness | Check requirements, edge cases, and regressions | Executable tests and failing examples |
| Security | Verify relevant access and data-handling rules | A reproducible violation of a defined rule |
| Performance | Compare both versions under the same workload | Query counts, timing, or resource measurements |
| Readability | Identify maintainability and clarity concerns | Specific, actionable suggestions |

The initial MVP focuses on **correctness, security, and performance**. Readability is a planned extension and is advisory by default.

## Intelligent Routing

The orchestrator selects specialists using the changed files, affected components, task requirements, and repository policies.

Examples:

- Changes to access-control logic require security verification.
- Changes to database operations trigger relevant correctness and performance checks.
- Changes to public behavior trigger contract and regression tests.

The routing decision is visible in the dashboard.

**Routing cannot disable mandatory checks.** Repository policy defines the minimum required verification, even when the orchestrator does not identify a particular risk.

## Independence by Design

Specialists receive separate investigation contexts and do not see one another’s conclusions before producing their initial findings.

Verification expectations come from requirements, contracts, and protected test fixtures. Repository content and patch comments are treated as untrusted input, rather than instructions to the verifier.

Where practical, behavioral tests are generated from requirements before the specialist inspects the candidate patch. Patch-aware checks are then added to investigate specific risks.

The implementing agent cannot modify the verifier’s protected tests or acceptance policy.

Multiple agents do not guarantee independent reasoning. Code Jury makes independence inspectable through context separation, protected artifacts, and executable evidence.

## Evidence Before Verdicts

A finding should include:

- The affected commit and code location.
- The requirement or policy being checked.
- What was observed and what remains inferred.
- A reproducible test or measurement procedure.
- Expected and actual behavior.
- Execution logs and relevant artifacts.
- Whether the finding is blocking or advisory.

An AI-generated suspicion is not automatically a confirmed defect.

Performance findings require a defined workload and acceptance threshold. Timing comparisons should use repeated runs; deterministic measurements such as database-call counts are preferred when appropriate.

## Verdict Policy

| Verdict | Meaning | Action |
|---|---|---|
| PASS | All required checks completed and met configured standards | Permit the configured merge workflow |
| BLOCK | Evidence demonstrates a required standard was violated | Prevent the merge |
| INCONCLUSIVE | A required check failed to execute or evidence is insufficient | Hold the merge for review or rerun |

Readability feedback is advisory unless a repository explicitly makes a particular rule mandatory.

PASS means the configured checks passed. It does not guarantee the absence of defects.

The MVP focuses on blocking defective changes before merge. Controlled rollback is a planned extension.

## Demo Scenario

An AI adds an **Export Orders** endpoint.

The existing test suite passes, but the change contains two defects:

1. A customer can receive another customer’s orders.
2. The implementation performs unnecessary database calls as the order count grows.

Code Jury:

1. Detects the new endpoint and database changes.
2. Activates correctness, security, and performance specialists.
3. Runs their investigations independently.
4. Displays a passing correctness result alongside reproduced security and performance failures.
5. Blocks the merge and exposes the evidence.
6. Reruns the same checks against a corrected patch.

The dashboard shows the entire investigation: jury selection, agent activity, evidence, and the final verdict.

## Technology Stack

| Layer | Planned technology |
|---|---|
| Backend and orchestration | Python, FastAPI |
| Model reasoning | Meta Model API through the OpenAI Python SDK |
| Behavioral testing | pytest |
| Property-based testing | Hypothesis, where useful |
| Test execution | Docker |
| Version comparison | Git |
| Evidence storage | SQLite and JSON artifacts |
| Dashboard | React, Vite, Tailwind CSS |
| Optional conversational interface | Fetch.ai uAgents, Agentverse, ASI:One |

A small Python coordinator is sufficient for the MVP. A separate multi-agent framework is optional.

## Reproducible Verification

Each verification run should preserve:

- Base and candidate commit identifiers.
- Requirements and policy versions.
- Generated test files and input datasets.
- Container image and dependency versions.
- Random seeds, commands, and execution limits.
- Model identifiers and generation settings.
- Logs, measurements, and the final verdict.

Saved tests can be replayed without asking the model to generate them again. Model generation itself may remain nondeterministic.

## Execution Boundaries

Candidate code runs in disposable test environments with:

- No model API keys or production credentials.
- Network access disabled during test execution.
- CPU, memory, and execution-time limits.
- Separate data stores for baseline and candidate runs.
- Protected verification artifacts.

Container isolation is one boundary, not a guarantee against hostile code. The hackathon MVP targets controlled demonstration repositories.

## Optional Fetch.ai Integration

Code Jury can be registered on Agentverse and accessed through ASI:One using the Agent Chat Protocol.

Example interaction:

> “Verify this pull request and show me any blocking findings.”

The agent starts verification, provides progress, and returns the verdict and evidence inside the conversation.

Meta powers the verification reasoning. Fetch.ai provides discovery and conversational access to the service.

## MVP Scope

- One supported Python demonstration repository.
- Comparison of a base and candidate commit.
- Mandatory baseline checks.
- Dynamic selection of three specialist agents.
- Independent investigations with executable evidence.
- A deterministic policy gate.
- A dashboard showing routing, progress, and results.
- Merge enforcement in a controlled demonstration workflow.

## Planned Extensions

- GitHub pull-request checks and branch-protection integration.
- Additional languages and specialist agents.
- Organization-specific verification policies.
- Fetch.ai conversational access.
- Controlled rollback workflows.
- Broader evaluation using defective and correct patches.

## Evaluation

Evaluate Code Jury using a mix of defective and correct changes:

- Does it catch the intended defects?
- Does it incorrectly block correct patches?
- Can findings be reproduced from saved artifacts?
- Does routing select the necessary specialists?
- Does it resist weakened or deleted repository tests?
- Does it hold the merge when required verification cannot complete?

---

**Code Jury — Every blocking finding needs proof.**