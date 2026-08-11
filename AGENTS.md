# Food-IP — AGENTS.md

## Mission

Food-IP helps restaurant owners with little or no short-video experience decide what is worth filming today and quickly turn real business events, ideas, or goals into shoot-ready content that sounds like the owner.

The target product flow is a future product capability, not the current implementation priority:

```text
Owner Input
→ Intent / Business Objective
→ confirmed_facts
→ missing_facts
→ relevant Memory
→ relevant Knowledge
→ Creative Decision
→ Writer
→ Critic
→ Directed Rewrite
→ Shoot-ready Script
```

Keep the user-facing product simple. Prefer a clear structured workflow over unnecessary architectural complexity.

The current mainline is continued development of the complete Food-IP Professional Creative Knowledge System. The existing video pipeline is one implemented and validated knowledge-ingestion path, not the complete definition of all future knowledge sources. Knowledge-source tiers, admission rules, evidence standards, and freshness governance remain open decisions and must not be invented here.

---

## Repository Layout

```text
backend/
    Food-IP product backend and runtime capabilities.

frontend/
    Web client.

knowledge_pipeline/
    Offline Creative Knowledge production system.
    It turns source material into structured, validated, traceable knowledge.

docs/
    Architecture, API, product decisions, deployment, and project documentation.

runtime/
    Local runtime/project data. Do not treat generated runtime data as source code.
```

The knowledge pipeline and the Food-IP runtime belong to the same product but remain logically separated.

```text
knowledge_pipeline
    produces knowledge

backend / frontend
    currently provide the product runtime and legacy script/video capabilities

Creative Decision
    is a future product layer that will decide how knowledge affects the current creative task
```

Do not couple the production application directly to internal ingestion implementation details when a stable knowledge contract is sufficient. Retrieval infrastructure and the future Creative Decision path are deferred.

---

## Core Product Rules

### 1. Fact Boundary (future product capability; Deferred)

Always distinguish:

```text
confirmed_facts
creative_decision
missing_facts
```

`confirmed_facts` are facts explicitly provided by the owner or already confirmed in a trusted memory source.

`creative_decision` contains AI judgments, recommendations, framing choices, and creative suggestions.

`missing_facts` contains information required for a useful creative decision but not yet confirmed.

Knowledge teaches the system **how to judge**. It does not establish **what happened at the current restaurant**.

Never promote knowledge, examples, likely behavior, or creative suggestions into confirmed restaurant facts.

If a useful detail is not confirmed:

* ask for it when necessary;
* mark it as requiring confirmation; or
* express the creative suggestion conditionally.

Do not invent concrete operational details to make a script more vivid.

The long-term principle remains: AI must not invent the owner's real experiences. Ask the owner only the minimum necessary question when a missing fact is genuinely required for the current creative judgment; do not ask for irrelevant details and do not fabricate them.

Fact Contract / Fact Boundary implementation and compatibility changes are Deferred until the Knowledge System is sufficiently mature. When that work is authorized, it must eventually be protected structurally through schemas and validation rather than prompt wording alone.

---

### 2. Creative Architecture

The future architecture is the structured workflow defined above.

Do not introduce Multi-Agent architecture unless there is clear evidence that the simpler workflow cannot satisfy a verified requirement.

Do not add architectural layers for sophistication alone.

Prefer:

```text
simple module
→ explicit contract
→ validation
→ test
```

over additional agents, orchestration layers, or infrastructure.

---

### 3. Legacy Script Generation

The existing script generation system is:

```text
compatibility
+ baseline
+ reusable capabilities
```

It is not the future creative-decision architecture.

Do not delete it.

Do not continue adding unrelated patches to it in an attempt to turn it into the new architecture.

Prefer reuse where appropriate:

* `ResearchProfile` → owner facts / memory source
* `IPProfile` → long-term positioning and expression constraints
* `CreativeConversation` → intent discovery and missing-fact interaction
* `CreativeBrief` → existing reusable contract; schema changes require approval
* `TopicCard` → optional interaction
* existing Writer → future script writing from Creative Decision
* Director Review → candidate for Critic
* `revise_script_candidate` → candidate for Directed Rewrite
* materials / timeline / FFmpeg / export → preserve

Fixed strategies and content buckets may remain for legacy compatibility but must not determine what the owner should film in the new path.

---

## Knowledge System Rules

`knowledge_pipeline/` is the current mainline: the professional Food-IP Creative Knowledge System.

Its supported source is not limited to the existing course-video pipeline. Other source categories, admission rules, evidence standards, and freshness policies are not yet decided; do not encode them as a contract or implementation plan.

The existing video pipeline and its pilot/reliability results are historical validation of one ingestion path. They are not a claim that the Knowledge System has only that source or that all future sources have already been accepted.

Its reliability baseline is already established.

Do not reopen completed reliability work without a concrete regression, failed test, violated invariant, or explicit task.

Preserve:

* timestamp authority
* stable deterministic identities
* evidence and provenance
* strict schema validation
* crash/resume behavior
* idempotency
* per-source persistence
* atomic global snapshot behavior
* fail-fast validation

Unless explicitly approved, do not introduce:

* GraphRAG
* Neo4j
* RAPTOR
* complex vector infrastructure
* Multi-Agent systems
* unrelated knowledge-platform infrastructure

Do not turn an unconfirmed source list, admission policy, scoring scheme, freshness policy, retrieval design, or evaluation standard into an implementation decision. The next discussion is knowledge-source stratification, admission, evidence quality, and freshness governance; implementation waits for explicit confirmation.

Knowledge-pipeline-specific implementation and test rules belong in `knowledge_pipeline/AGENTS.md`.

---

## Decision Authority

Do not silently make product or architecture decisions that materially change:

* architecture
* schemas or data contracts
* Fact Boundary semantics
* storage strategy
* retrieval strategy
* model/provider strategy
* major dependencies
* roadmap or phase boundaries
* operating cost
* validation or acceptance standards
* compatibility guarantees

Also treat updates to the authoritative engineering files and the active project Skill as part of the decision workflow when a confirmed project direction changes. Keep `.codex/agents/*.toml` role assignments unchanged unless explicitly requested.

For such decisions:

1. inspect the current implementation;
2. explain the options and tradeoffs;
3. recommend an option;
4. stop and obtain user approval before implementation.

Routine implementation details inside an already approved boundary do not require separate approval.

---

## Engineering Rules

Prefer modifying and reusing existing modules over creating parallel systems.

Keep the repository tidy.

Avoid:

* duplicate abstractions
* unnecessary helper modules
* temporary scripts committed to the repository
* redundant reports
* unrelated refactors
* directory churn
* speculative infrastructure
* renaming files without a concrete need

Do not overwrite unrelated local changes.

Before modifying code, inspect:

```bash
git status --short
```

Read the code and tests relevant to the task before changing behavior.

Do not delete files, commit, push, modify secrets, or change `.env` files unless explicitly requested.

External paid AI calls must be mocked in automated tests unless the task explicitly requires a controlled integration test.

---

## Compatibility

Preserve existing Food-IP capabilities unless the task explicitly changes their contract.

In particular, do not casually break:

* existing REST API behavior
* existing persisted projects
* ResearchProfile / legacy BossInfo compatibility
* script and script-bundle compatibility
* materials and upload workflows
* timeline behavior
* FFmpeg export
* existing legacy script generation

`backend/app/engine/timeline.py` remains the authoritative source for timeline duration unless an approved architecture decision explicitly changes that contract.

---

## Build and Test

From the repository root, run the checks relevant to the files changed.

Food-IP backend:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/app/tests -q --basetemp .pytest-basetemp
```

Food-IP frontend:

```powershell
cd frontend
npm.cmd run build
```

Knowledge pipeline:

```bash
cd knowledge_pipeline
python -m pytest -q
```

Use the narrowest relevant tests during implementation, then run the appropriate broader validation before declaring the task complete.

Never claim success based only on code inspection when executable validation is available.

---

## Definition of Done

Before reporting completion:

1. run relevant tests;
2. run required build/type/lint checks for affected components;
3. inspect `git status --short`;
4. inspect the final diff;
5. verify compatibility with the requested behavior;
6. check for accidental unrelated changes.

Report:

* what changed;
* which files changed;
* which validations ran;
* their results;
* any compatibility considerations;
* remaining risks or unresolved decisions.

Do not describe unimplemented capabilities as complete.

---

## Documentation

Keep this file concise and focused on durable repository-wide guidance.

Do not turn `AGENTS.md` into a project diary or task history.

Detailed architecture belongs in `docs/`.

Current task state and handoff information should live in the appropriate project documentation rather than accumulating indefinitely here.

When documentation and implementation disagree, inspect code and tests and report the discrepancy instead of silently assuming either side is correct.
