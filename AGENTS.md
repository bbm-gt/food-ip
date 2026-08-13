# Food-IP — AGENTS.md

## Mission

Food-IP is the AI content director for restaurant owners. It helps an owner identify what is most worth saying now, deepen the real material that makes it worth watching, create the content, diagnose quality problems, and reach a shoot-ready result.

The current product mainline is:

```text
EXPLORE
→ DEEPEN
→ CREATE
→ REVIEW
→ READY
```

`REVIEW` must diagnose the root cause before choosing the next action:

```text
Writing Problem → CREATE
Material Problem → DEEPEN
Direction Problem → EXPLORE
```

The workflow controls only critical boundaries. The LLM makes the concrete creative judgments inside those boundaries.

---

## Product Workflow Rules

- `EXPLORE`: find the content direction most worth continuing now.
- `DEEPEN`: obtain only the real material that most affects the final content quality.
- `DEEPEN → CREATE`: proceed when the available real material can support the core expression without invention, templates, or empty filler.
- `CREATE`: turn the chosen direction and real material into content that sounds like the owner and can be produced.
- `REVIEW`: identify whether the root problem is writing, material, or direction; do not default directly to rewrite.
- `READY`: the result is coherent, factually grounded, and shoot-ready.

Memory, Knowledge, historical content, uploaded materials, and external information are on-demand capabilities, not mandatory workflow nodes. External trends may reveal a content opportunity, but they cannot replace the owner's own real content.

Do not make fixed questionnaires, fixed scoring systems, complex Routers, Multi-Agent systems, or fixed question trees the core product logic. Do not add unconfirmed workflow states, schemas, persistence designs, scoring mechanisms, Retrieval architecture, Routers, or Agents.

---

## Fact and Knowledge Boundary

Knowledge teaches the AI **how to judge**. It does not establish **what happened at the owner's restaurant**.

Owner Facts must come from the owner or another explicitly trusted and confirmed source. Never promote examples, general knowledge, historical patterns, external information, or creative suggestions into facts about the current owner.

When a useful fact is missing:

- ask the minimum necessary question if it materially affects the current creative decision;
- otherwise mark the detail as unconfirmed or express the suggestion conditionally;
- never invent operational details to make the content more vivid.

Creative judgment may recommend an angle, framing, structure, or expression, but it must remain distinguishable from Owner Facts.

---

## Repository Layout

```text
backend/
    Product backend and runtime capabilities.

frontend/
    Web client.

knowledge_pipeline/
    Independent Creative Knowledge production subsystem.

docs/
    Architecture, API, product decisions, deployment, and project documentation.

runtime/
    Local runtime/project data; not source code.
```

The runtime product and `knowledge_pipeline/` belong to the same product but remain logically separated. Do not couple the application to ingestion internals when an approved stable contract is sufficient.

---

## Legacy and Reuse

The existing Script Engine, `ResearchProfile`, legacy `BossInfo`, `IPProfile`, `CreativeConversation`, `CreativeBrief`, `TopicCard`, `ScriptBundle`, Writer/Review utilities, materials, timeline, FFmpeg, and export flows are legacy, compatibility, or reusable capabilities. They are not the future product mainline.

Preserve them unless an approved change explicitly alters their contract. Prefer reusing sound existing capabilities over parallel implementations, but never sacrifice the final product effect merely to reuse or preserve an old flow. Do not extend legacy structures into the new mainline by default.

Fixed strategies, content buckets, TopicCard selection, multi-candidate bundles, and fixed review scores may remain for legacy compatibility, but they must not determine the new workflow's creative judgment or routing.

### Current Implementation Strategy

The old creative core is frozen as Legacy: `CreativeConversation`, `CreativeBrief`, `TopicCard`, `ScriptBundle`, the old Writer, and fixed-score Review. Do not implement the new product mainline by continuing to modify these objects.

The approved target is an independent Director Core:

```text
DirectorSession
→ Director Orchestrator
→ EXPLORE
→ DEEPEN
→ CREATE
→ REVIEW
→ READY
→ ReadyContent
```

The new and legacy creative cores do not share a core state machine. When reuse is needed, connect through an explicit Adapter or stable boundary; do not let legacy `ScriptModel` or `ScriptBundle` contracts constrain the new core.

Continue to reuse and protect general project/file persistence capabilities, message-idempotency patterns, Materials / Upload, Timeline, FFmpeg, Export, and applicable programmatic safety checks.

The current formal development stage is **Phase 1 — Director Core minimum skeleton**. Phase 1 does not include a frontend rewrite, Knowledge Retrieval, complex Memory, Multi-Agent, complete CREATE or REVIEW prompts, or editing-pipeline refactoring. This is an approved implementation direction, not a claim that Director Core already exists.

---

## Knowledge Pipeline Rules

`knowledge_pipeline/` remains an independent knowledge-production subsystem; it is not the product's only current mainline. Its existing video pipeline is one implemented and validated ingestion path, not the definition of all future knowledge sources.

Do not reopen completed reliability work without a concrete regression, failed test, violated invariant, or explicit task. Preserve:

- timestamp authority;
- stable deterministic identities;
- evidence and provenance;
- strict schema validation;
- crash/resume behavior;
- idempotency;
- per-source persistence;
- atomic global snapshot behavior;
- fail-fast validation.

Knowledge-source tiers, admission rules, evidence standards, freshness governance, Retrieval design, and evaluation standards remain unconfirmed unless the user explicitly approves them. Do not turn them into implementation decisions. Knowledge-pipeline-specific rules belong in `knowledge_pipeline/AGENTS.md`.

---

## Decision Authority

Do not silently make product or architecture decisions that materially change:

- architecture or workflow boundaries;
- schemas or data contracts;
- Fact / Knowledge boundary semantics;
- persistence, storage, or Retrieval strategy;
- model or provider strategy;
- major dependencies;
- roadmap or phase boundaries;
- operating cost;
- validation or acceptance standards;
- compatibility guarantees.

For such decisions:

1. inspect the current implementation;
2. explain the options and tradeoffs;
3. recommend an option;
4. stop and obtain user approval before implementation.

Routine implementation details inside an approved boundary do not require separate approval. Keep authoritative engineering guidance and the active project Skill aligned when a product decision is confirmed. Do not change `.codex/agents/*.toml` role assignments unless explicitly requested.

---

## Engineering Rules

Before modifying code, inspect `git status --short` and read the relevant code and tests. Preserve unrelated user changes.

Prefer focused changes to existing modules. Keep the repository tidy and avoid duplicate abstractions, temporary committed scripts, redundant reports, unrelated refactors, directory churn, speculative infrastructure, and unnecessary renames.

Do not delete files, commit, push, modify secrets, or change `.env` files unless explicitly requested. Mock paid external AI calls in automated tests unless the task explicitly requires a controlled integration test.

When implementation and documentation disagree, use code and tests to establish current implemented behavior, use confirmed product decisions to establish intended direction, and report the discrepancy instead of silently treating either as authoritative for both.

---

## Compatibility

Preserve existing capabilities unless the task explicitly changes their contract, including:

- REST API behavior and persisted projects;
- ResearchProfile / legacy BossInfo compatibility;
- script and ScriptBundle compatibility;
- materials and upload workflows;
- timeline behavior;
- FFmpeg export;
- legacy script generation.

`backend/app/engine/timeline.py` remains authoritative for timeline duration unless an approved architecture decision changes that contract.

---

## Build and Test

Run checks relevant to the files changed, using narrow tests during implementation and the appropriate broader validation before completion.

Backend:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/app/tests -q --basetemp .pytest-basetemp
```

Frontend:

```powershell
cd frontend
npm.cmd run build
```

Knowledge pipeline:

```powershell
cd knowledge_pipeline
python -m pytest -q
```

Do not claim success from inspection alone when executable validation is available.

---

## Definition of Done

Before reporting completion:

1. run relevant tests and build/type/lint checks;
2. inspect `git status --short` and the final diff;
3. verify the requested behavior and compatibility impact;
4. check for accidental unrelated changes.

Report what changed, validations and results, compatibility considerations, and remaining risks or unresolved decisions. Do not describe unimplemented capabilities as complete.

Keep this file concise and durable. Detailed architecture belongs in `docs/`; task history and handoff state do not belong here.
