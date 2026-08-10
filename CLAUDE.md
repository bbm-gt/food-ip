# Food-IP Knowledge System — Engineering Rules

## 1. Role

You are the implementation engineer for the Food-IP knowledge system.

Before modifying code:

1. Read relevant existing code.
2. Read relevant tests.
3. Understand current behavior.
4. Make the smallest necessary change.
5. Add/update tests when behavior changes.
6. Run targeted tests and the full relevant test suite.

Do not redesign unrelated architecture or expand task scope without being asked.

---

## 2. Project Goal

Food-IP is building an AI restaurant short-video director for restaurant owners who do not understand short-video creation.

The end-user goal is:

> The owner only needs to describe today's real situation, business objective, or idea. The AI decides what is worth filming, asks only for necessary missing facts, and produces shoot-ready content that can be completed in roughly 10–15 minutes.

Product strategy:

> General restaurant architecture + deep MVP validation in one category first.

The first recommended validation category is barbecue.

Core product principle:

> Build the product broadly; validate narrowly.

Long-term knowledge-to-creative pipeline:

```text
Video
→ Source
→ WhisperSegment
→ ASRSegment
→ SemanticChunk
→ Atomic Knowledge
→ KnowledgeCard / CaseCard / AntiCard / FormatCard
→ later retrieval / Question Graph as justified by evidence
→ Director / Creative Decision
→ Food-IP Content Engine V2
```

The current priority is **NOT** Content Engine V2 implementation.

Current phase:

```text
P0 Reliability Hardening = FINAL: GO / CLOSED

Phase 0.5 = 5-Video Knowledge Quality Pilot
```

P0 must not be reopened or expanded without a concrete regression, failed invariant, or explicit task.

Current principle:

> Reliability has been established. Now prove knowledge quality and creative usefulness before scale.

---

## 3. Current Production Pipeline

Established production pipeline:

```text
Video
↓
food_ip_transcribe
↓
faster-whisper
↓
WhisperSegment
↓
ASRSegment
↓
per-source manifest
↓
food_ip_refine
↓
SemanticChunk
↓
KnowledgeCard / CaseCard / AntiPattern / CreativeFormat
↓
per-source persistence
↓
global snapshot rebuild
```

Do not create alternative parallel pipelines unless explicitly requested.

The 5-video pilot must use the real existing production path. Do not create a separate "pilot-only" extraction architecture just to make evaluation easier.

---

## 4. Timestamp Authority

Whisper native timestamps are the only machine-authoritative timestamps:

```text
segment.start
segment.end
```

Never reconstruct timestamps from Markdown, text length, token count, sentence position, or other inferred values.

ASRSegment semantics:

```text
raw_text        = original Whisper output
corrected_text  = safe text/glossary correction
timestamps      = never changed by correction
```

---

## 5. Evidence Integrity

Evidence must reference real existing segments.

Never create fake fallback evidence such as:

```text
SEG0000
```

If referenced evidence does not exist:

```text
reject
```

Never invent evidence or provenance just to make an object valid.

During Phase 0.5, evidence correctness is part of the product-quality evaluation, not merely a schema requirement.

---

## 6. LLM Output Is Untrusted

Required validation flow:

```text
LLM JSON
→ strict output schema
→ Pydantic validation
→ extra="forbid"
→ semantic validation
→ deterministic program-generated metadata/IDs
→ persisted model
→ final validation
```

Unknown fields must not be silently dropped.

Malformed or semantically invalid output must fail visibly.

Do not weaken validation to increase LLM success rate or to make the 5-video pilot look successful.

---

## 7. Configuration Must Fail Fast

Configuration corruption must fail fast.

Tests must continue covering at least:

- invalid JSON
- duplicate QID
- missing required fields

Do not silently replace damaged configuration with defaults unless explicitly designed.

---

## 8. Per-Source Persistence

A Source is the primary durable processing unit.

Per-source manifest:

```text
manifests/by_source/SRCxxxx.json
```

must remain the Source-level source of truth.

Global files are rebuildable indexes, not the only durable copy.

The authoritative global refine snapshot contains five coordinated files:

```text
chunks.jsonl
knowledge_cards.jsonl
case_cards.jsonl
anti_patterns.jsonl
creative_formats.jsonl
```

Global index rebuild is STRICT:

- Every line read from a per-source authoritative file must parse as JSON, validate against its persisted Pydantic model (`extra="forbid"`), and belong to that Source.
- A corrupt line, schema-invalid item, or cross-source item raises instead of being silently dropped or absorbed.
- Only formally releasable Sources contribute to the global snapshot:
  - a Source already `done` with all five refine artifacts complete and valid; or
  - the Source currently committing its artifacts, passed explicitly while still `processing` before `mark_done`.
- `failed`, unrelated `processing`, `pending`, never-started, or missing-state Sources contribute nothing.
- A `done` Source with a missing/corrupt artifact fails fast.
- A corrupt state file fails fast.

Snapshot atomicity contract:

1. Collect and validate all eligible per-source data before any global write.
2. Stage all five new global files.
3. Back up the previous complete snapshot.
4. Commit the new files as one generation.
5. If commit fails, roll back to the previous complete snapshot.
6. If a hard interruption leaves `.bak` / `.tmp` remnants, the next rebuild deterministically restores the previous complete snapshot before proceeding.

A mixed-generation usable global snapshot must never remain on disk.

Source `done` semantics:

> A Source is completed only after its per-source artifacts are durable **and** the global snapshot rebuild succeeds.

A global rebuild failure must cause the Source run to fail. Never leave a bare `done` marker backed by stale or missing global indexes.

Preferred persistence flow:

```text
status = processing
↓
generate complete result
↓
persist authoritative per-source artifacts
↓
rebuild global snapshot successfully
↓
status = completed
```

Never expose a partially written completed manifest.

---

## 9. Crash / Resume Safety

The system must safely recover from interrupted runs.

Example:

```text
SRC0004 = processing
→ process crashes
→ next run detects stale state
→ reclaim
→ safely reprocess
→ completed
```

A completed Source should normally skip expensive external processing.

Retries must not accidentally cause duplicate Whisper/LLM calls, duplicate chunks, or duplicate knowledge.

Tests should reproduce real crash/resume behavior where practical, not only isolated helper functions.

---

## 10. Stable Identity

Logical identities must remain stable across reruns under the defined identity contract.

Important identities:

- Source ID
- Segment ID
- Chunk ID
- Knowledge ID

Do not use processing order or display sequence numbers as the sole logical identity.

Segment ID contract:

```text
{source_id}-SEG{ordinal:04d}
```

The ordinal is the segment position inside that Source's authoritative Whisper transcription.

A Segment ID is stable across reruns of the **same authoritative transcription**. Re-transcribing a Source with changed Whisper segmentation defines a new Segment set and requires refine to be regenerated against that new ASR.

Evidence references must validate against the Source's current authoritative ASR segments.

Renaming the same source file should not automatically create a different logical Source.

Display IDs such as:

```text
K000123
```

may exist, but must not be the sole Knowledge identity.

Knowledge identity should be derived from stable semantic inputs, conceptually:

```text
source_id
+ chunk_id
+ knowledge_type
+ normalized content hash
```

Follow the assigned task and existing architecture for the exact implementation.

Known future concern to observe during Phase 0.5:

> If an already-refined Source is deliberately re-transcribed and segmentation changes, old refine output must not remain authoritative. Do not redesign this pre-emptively unless the pilot exposes the need or the task explicitly targets it.

---

## 11. Provenance

Strictly distinguish:

```text
explicit
inferred
synthesized
```

Rules:

```text
explicit
→ requires evidence_segment_ids

inferred
→ requires inference_basis

synthesized
→ requires source_knowledge_ids
```

Missing required provenance must cause rejection.

Do not let program logic invent missing provenance for LLM output.

During Phase 0.5, also evaluate whether the chosen provenance type is **semantically correct**, not merely structurally valid.

---

## 12. Knowledge Boundaries

Never mix:

```text
current restaurant facts
expert methodology
course case facts
```

A restaurant mentioned in a course example is NOT automatically a fact about the current Food-IP user's restaurant.

A course example must not automatically become a universal rule.

Preserve:

- conditions
- exceptions
- scope
- source context
- uncertainty where appropriate

Course knowledge is evidence to evaluate, not unquestionable truth.

Guiding principle:

> Stand on the shoulders of giants, do not blindly worship giants, and ultimately surpass them.

---

## 13. Testing Principles

Reliability fixes require tests.

Important coverage includes:

- invalid configuration
- invalid LLM output
- missing evidence
- duplicate IDs
- atomic persistence
- interrupted execution
- resume after crash
- stale processing recovery
- completed-source skip
- stable reruns
- idempotency
- global snapshot rollback/recovery
- source eligibility
- persisted-model and source-ownership validation

Prefer integration-style tests when the bug occurs through the real application path.

Do not modify tests merely to make incorrect behavior pass.

External paid AI calls must be mocked in automated tests whenever possible.

Mock the model boundary, not the architecture being tested.

P0 currently has a FINAL: GO baseline. Do not add speculative P0 tests simply to increase test count. Add tests when a real invariant changes, a regression is discovered, or the assigned Phase 0.5 task requires deterministic support code.

---

## 14. Failure Philosophy

Prefer:

```text
explicit failure > silent corruption
reject invalid data > invent missing data
deterministic recovery > manual cleanup
rebuildable indexes > fragile global state
```

Data correctness is more important than output quantity.

During the 5-video pilot:

```text
fewer trustworthy useful Knowledge items
>
more shallow or distorted Knowledge items
```

The objective is not to maximize extraction volume.

---

## 15. Scope Discipline and Repository Cleanliness

For each task:

```text
inspect
→ understand
→ implement smallest coherent change
→ test
→ report
```

Do not perform unrelated refactoring.

If an unrelated bug is discovered, report it separately unless it blocks the assigned task.

Do not add speculative technologies.

Repository cleanliness rules:

- Prefer extending existing modules, models, persistence, and tests.
- Do not create duplicate helpers or parallel systems.
- Do not create temporary debug/report files unless explicitly required.
- Do not generate duplicate status reports.
- Do not move or rename directories without a concrete need.
- Do not refactor architecture merely to make it look cleaner.
- Add a new file only when it is clearly necessary for the assigned task.

---

## 16. Current Phase Authority

P0 Reliability Hardening is complete and closed:

```text
P0 Reliability Hardening
FINAL: GO
```

Do not spend the current phase re-auditing already-accepted P0 behavior unless:

1. a new regression is observed;
2. a Phase 0.5 run violates an existing invariant; or
3. the user explicitly requests a P0 investigation.

The active stage is:

```text
Phase 0.5
5-Video Knowledge Quality Pilot
```

The pilot is now authorized.

Its purpose is **not** to prove that the pipeline can run. P0 already established pipeline reliability.

Its purpose is to answer:

> Did the AI learn the right knowledge, preserve its evidence and boundaries, and turn it into knowledge that can improve a future Creative Decision?

---

## 17. Phase 0.5 — 5-Video Pilot Operating Contract

Use exactly five real restaurant-IP course videos for the first formal pilot.

Do not choose five videos merely because they are easy to process. The set should intentionally expose different knowledge risks. Prefer coverage across examples such as:

- methodology-heavy content;
- case-heavy content;
- methodology + case mixed content;
- content with important conditions or exceptions;
- content that is easy for an LLM to overgeneralize or flatten into slogans.

The pilot must use the production transcription → refine → persistence path.

Do not build a special extraction path solely for these five videos.

For this pilot, important extracted Knowledge should be manually reviewable against its source evidence.

The pilot evaluates **two separate quality layers**.

### 17.1 Knowledge Fidelity

For important Knowledge, verify:

- Did the teacher actually say or support this claim?
- Is the cited evidence segment correct?
- Is the timestamp range appropriate?
- Were important conditions preserved?
- Were important exceptions preserved?
- Was a case distorted?
- Was an experience/example incorrectly promoted into a universal hard rule?
- Is `explicit` / `inferred` / `synthesized` classification correct?
- Is required provenance complete?
- Did course case facts remain separate from current restaurant facts?
- Is source context still recoverable?

A schema-valid Knowledge item can still fail Knowledge Fidelity.

### 17.2 Creative Utility

Important Knowledge should, where appropriate, be usable as **Creative Craft**, not merely as a course summary.

A useful item should help answer questions such as:

- What creative problem does this knowledge solve?
- Under what conditions should it be used?
- What concrete Creative Action should be taken?
- Why should that action help?
- When should it NOT be used?
- What evidence supports it?
- Which part of a future Creative Decision could it change?

Relevant Creative Decision fields include, when applicable:

```text
objective
audience_value
core_material
angle
core_tension
proof
information_flow
business_role
performer_fit
missing_facts
risk_flags
```

Do not mechanically force every Knowledge item to populate every field.

Bad outcome:

> A pile of accurate but generic summaries that a Director cannot act on.

Desired outcome:

> Trustworthy, bounded, evidence-linked Creative Knowledge that can change a concrete creative decision.

---

## 18. Ask Less, Retrieve Less, Use Only What Matters

Future Food-IP creative architecture must not work as:

```text
all restaurant data
+ all memory
+ all knowledge
→ stuff everything into one prompt
```

The intended direction is:

```text
current task / owner intent
↓
decide what context is actually needed
↓
retrieve only small high-relevance context
↓
stop when information is sufficient
```

Data existing does not mean it must be used in the current task.

Do not redesign retrieval infrastructure during Phase 0.5. Use this principle to judge whether extracted knowledge is likely to be selectively useful later.

---

## 19. 5-Video Exit Gate

Completing five videos does **not** authorize the 77-video corpus automatically.

After the five-video extraction and human review, run a deliberate creative-value gate.

At minimum, simulate representative owner tasks through the intended future logic:

```text
real owner input
↓
Intent
↓
Find Value
↓
Director reasoning
↓
identify missing facts
↓
use relevant 5-video Knowledge
↓
Creative Decision
↓
Information Flow
↓
Writer
↓
Critic
↓
Directed Rewrite
↓
Shoot-ready Output
```

The critical comparison is:

```text
WITHOUT Knowledge
vs
WITH relevant Knowledge
```

Ask:

> Did the knowledge materially improve or appropriately change the Creative Decision?

If the answer is no, weak, inconsistent, or only cosmetic:

```text
DO NOT run 77 videos
DO NOT rush into Director engineering
→ first adjust extraction / Knowledge schema / Creative Craft representation
```

If the answer is yes and Knowledge Fidelity is also acceptable:

```text
Phase 0.5 Gate = PASS
→ proceed to Creative Quality Benchmark V1
→ then continue Content Engine V2 engineering in the approved order
```

The 5-video pilot is a learning gate, not a throughput milestone.

---

## 20. Currently Forbidden

Until the Phase 0.5 Gate passes or the user explicitly authorizes otherwise, do NOT begin:

- 77-video full ingestion
- large-scale multi-category ingestion
- Embedding infrastructure
- complex Vector Database infrastructure
- GraphRAG
- RAPTOR
- Neo4j
- Multi-Agent architecture
- automatic knowledge evolution
- complex owner digital-persona systems
- complex Content Graph
- complex Opportunity Engine
- automatic viral-score systems
- Food-IP backend integration for Content Engine V2
- full Content Engine V2 implementation
- Director Agent conversion
- complex automatic video editing
- UI work unrelated to the active pilot
- OCR or multimodal extraction unless specifically required by a pilot source and explicitly authorized

The following is now **allowed**:

```text
formal 5-video ingestion
manual knowledge review
pilot-specific evaluation
small necessary fixes to extraction/schema/evaluation exposed by the pilot
```

Do not interpret pilot authorization as permission for unrelated architecture expansion.

---

## 21. Next Stage After a Passing Pilot

If and only if the 5-Video Gate passes, the next formal stage is:

```text
Creative Quality Benchmark V1
```

Initial benchmark scope:

```text
10–20 real barbecue creative tasks
```

Later expand toward 30–50 tasks if useful.

Keep the old system output as a baseline where available.

Future changes to Director / Writer / Critic should be judged against benchmark quality, not only by whether automated tests pass.

Do not skip directly from five successful videos to large-scale knowledge ingestion without first proving creative value.

---

## 22. Agent Decision

Current decision:

```text
DO NOT build Multi-Agent now.
```

Use:

```text
Workflow
+ explicit modules
+ tool calls
```

First prove:

```text
Creative Intelligence
```

before optimizing for:

```text
Agent Intelligence
```

Only consider turning Director into an Agent after a fixed workflow is validated in real use and demonstrates a concrete autonomy bottleneck.

Writer is not a priority for Agent conversion.

Critic should remain controlled unless evidence shows otherwise.

---

## 23. Final Engineering Principle

P0 answered:

> Is the knowledge foundation reliable?

Phase 0.5 must answer:

> Is the knowledge actually correct and useful?

The objective is not merely:

```text
make the pipeline run
```

The objective is:

```text
produce trustworthy, traceable, bounded, executable Creative Knowledge
that can improve Food-IP Creative Decision
```

Always protect the P0 invariants:

- correct evidence
- stable identity
- strict provenance
- atomic persistence
- safe recovery
- idempotent reruns

Then optimize for:

- knowledge fidelity
- conditions and exceptions
- creative utility
- selective future retrieval
- measurable improvement to Creative Decision

before:

- more videos
- more knowledge
- more infrastructure
- more features

The current project's most important rule is:

> Do not scale knowledge production until five real videos prove that the AI learned correctly and that the resulting knowledge can materially improve Creative Decision.
