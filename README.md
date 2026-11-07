# CodeJury ⚖️
### AI-Powered Independent Code Verifier

> *"AI writes code 10x faster than humans can review it. 70% of security breaches originate from unreviewed code changes. Every merge is a leap of faith."*

**CodeJury** is a multi-agent system that acts as an independent jury for every AI-generated PR. It reads your spec, generates its own tests without ever looking at the implementation first, runs them, and delivers a verdict with full evidence — before anything reaches production.

🔗 **Try it live:** [@codejury on ASI:One](https://asi1.ai)
📦 **Built for:** AIE Code Summit 2026 — Fetch.ai Challenge
🏆 **Track:** Trust but Verify

---

## The Problem

AI coding tools like GitHub Copilot, Cursor, and Claude generate pull requests faster than any team can safely review them.

The result:
- **70%** of security vulnerabilities originate from insufficiently reviewed code changes
- **60%** of bugs in AI-generated code are caught only after they reach production
- Traditional code review shares the same blind spots as the implementer — reviewers read the code and work backwards, inheriting every assumption the author made

A reviewer who reads the code first can never be truly independent.

---

## The Solution

Talk to **@codejury on ASI:One**:

```
"Verify this PR: github.com/org/repo/pull/42"
```

CodeJury will:
1. **Read your spec/issue** — understand what the code is supposed to do
2. **Route intelligently** — detect what type of change it is (auth? API? async?) and fire the right specialist agents
3. **Generate tests independently** — each agent reads the spec only, never the code, eliminating shared blind spots
4. **Run the tests** — against the actual PR code
5. **Filter by evidence** — only agents that caught something do the deep analysis
6. **Deliver a verdict** — APPROVE or BLOCK with full evidence: which tests failed, why, severity, and what to fix

---

## Architecture

CodeJury runs on **Fetch.ai uAgents** — specialized agents working as an independent jury:

```
You (ASI:One)
     │
     ▼
┌─────────────────────────────────┐
│        Orchestrator             │  ← The only agent you talk to
│      @codejury on ASI:One       │  ← Reads PR, detects type, routes
└──────┬──────────┬───────────────┘
       │          │
  ┌────▼────┐ ┌───▼──────┐
  │Behavior │ │ Security │  ← Fired based on PR type
  │  Agent  │ │  Agent   │  ← Each reads SPEC ONLY, never code
  └────┬────┘ └───┬──────┘
       │          │
       └────┬─────┘
            ▼
     ┌─────────────┐
     │   Verdict   │  ← Synthesizes all results
     │    Agent    │  ← APPROVE / BLOCK + evidence
     └─────────────┘
```

### Agent Breakdown

| Agent | Port | Role |
|-------|------|------|
| **Orchestrator** | 8001 | User-facing. Reads PR diff + linked issue. Detects change type. Routes to relevant specialist agents. |
| **Behavior Agent** | 8002 | Always fires. Reads spec only. Generates edge case tests (null, boundary, empty) + logic tests (branches, conditions). |
| **Security Agent** | 8003 | Fires when auth/token/password/session detected in diff. Generates tests for injection, auth bypass, missing validation. |
| **Verdict Agent** | 8004 | Always fires last. Collects all test results. Agents that found nothing stop. Agents that found issues do deep analysis. Produces final APPROVE/BLOCK with risk score. |

### How Independence is Guaranteed

The core of Trust but Verify is that the verifier **cannot share the implementer's blind spots**.

CodeJury enforces this structurally:
- Spec Agent reads the GitHub issue → generates expected behavior
- Code is never shown to the test-generating agents
- Tests are derived from intent, not from implementation
- An implementer cannot optimize their code to game the verifier because the verifier never read their code

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Framework | Fetch.ai uAgents |
| Agent Registry | Agentverse |
| Chat Interface | ASI:One |
| AI / LLM | ASI-1 (OpenAI-compatible API) |
| GitHub Integration | GitHub REST API v3 |
| Language | Python 3.11 |
| Data Models | Pydantic |
| Session Storage | JSON |

---

## Example Conversation

```
You:        "Verify PR: github.com/org/repo/pull/42"

CodeJury:   Reading spec from linked issue...
            Reading PR diff...
            
            Detected: authentication change
            Firing: Behavior Agent + Security Agent
            
            ⚙️  Behavior Agent generated 6 tests from spec
            ⚙️  Security Agent generated 5 tests from spec
            
            Running 11 tests against PR code...
            
            🔴 BLOCK — Risk Score: 81/100
            
            Security Agent — 2 failures:
              ❌ auth token not validated on refresh
              ❌ missing expiry check allows infinite sessions
            
            Behavior Agent — 1 failure:
              ❌ null user input crashes handler (line 47)
            
            Behavior Agent — 5 passed ✅
            Security Agent — 3 passed ✅
            
            Proof: 11 tests generated from spec before
            any code was read. Verifier had zero knowledge
            of implementation at test generation time.
            
            Fix required before merge.
```

---

## How to Run

### Prerequisites
```bash
pip install uagents uagents-core openai httpx pydantic
```

### Step 1 — Start the sub-agents (each in a separate terminal)

```bash
# Terminal 1
python3 behavior_agent.py
# Copy the printed address → paste into config.py as BEHAVIOR_AGENT_ADDRESS

# Terminal 2
python3 security_agent.py
# Copy the printed address → paste into config.py as SECURITY_AGENT_ADDRESS

# Terminal 3
python3 verdict_agent.py
# Copy the printed address → paste into config.py as VERDICT_AGENT_ADDRESS
```

### Step 2 — Fill in config.py

```python
BEHAVIOR_AGENT_ADDRESS = "agent1q..."
SECURITY_AGENT_ADDRESS = "agent1q..."
VERDICT_AGENT_ADDRESS  = "agent1q..."
```

### Step 3 — Create mailboxes on Agentverse

For each agent, open the inspector URL printed in the terminal and click **Connect**.

### Step 4 — Start the orchestrator

```bash
# Terminal 4
python3 orchestrator.py
```

### Step 5 — Use on ASI:One

Go to [asi1.ai](https://asi1.ai) → New Chat → type `@codejury` → paste a PR URL.

---

## Project Structure

```
CodeJury/
├── orchestrator.py      # Main agent — user-facing, routes to sub-agents
├── behavior_agent.py    # Edge case + logic tests from spec
├── security_agent.py    # Security tests from spec
├── verdict_agent.py     # Final APPROVE/BLOCK verdict with evidence
├── models.py            # Shared Pydantic message models
├── config.py            # Sub-agent addresses
└── sessions.json        # Auto-generated session persistence
```

---

## Why CodeJury Wins on the Fetch.ai Track

| Fetch.ai Criterion | How CodeJury meets it |
|--------------------|----------------------|
| Agents that prove their work | Every verdict ships with the full test suite + per-test pass/fail + risk score |
| Multi-agent collaboration | 4 specialized agents with intelligent routing |
| Meaningful action | Reads GitHub live, runs tests, produces executable verdict |
| Complete workflow in ASI:One | Paste PR URL → get verdict — no frontend needed |
| Hard to game | Verifier derives tests from spec, never from code — implementer cannot optimize for it |
| Could operate post-hackathon | Plugs into any GitHub repo, any team's workflow |

---

## Built by

Manali Gawande & Siddharth Jetling
AIE Code Summit — Fetch.ai Hackathon, November 2026

*AI moves fast. CodeJury makes sure it moves safely.*
