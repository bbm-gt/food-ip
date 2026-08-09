Food-IP Knowledge System — Engineering Rules

1. Role

You are the implementation engineer for the Food-IP knowledge system.

Before modifying code:

Read relevant existing code.

Read relevant tests.

Understand current behavior.

Make the smallest necessary change.

Add/update tests.

Run targeted tests and the full relevant test suite.

Do not redesign unrelated architecture or expand task scope without being asked.

2. Project Goal

Food-IP is building a reliable expert knowledge system from restaurant-IP course videos.

Long-term pipeline:

Video
→ Source
→ ASRSegment
→ SemanticChunk
→ Atomic Knowledge
→ Question Graph
→ Retrieval
→ Food-IP Content Engine V2

Current priority is NOT Content Engine V2.

Current phase:

P0 Reliability Hardening

Core principle:

Reliability before scale.

3. Current Production Pipeline

Established pipeline:

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
Atomic Knowledge

Do not create alternative parallel pipelines unless explicitly requested.

4. Timestamp Authority

Whisper native timestamps are the only machine-authoritative timestamps:

segment.start
segment.end

Never reconstruct timestamps from Markdown, text length, token count, sentence position, or other inferred values.

ASRSegment semantics:

raw_text       = original Whisper output
corrected_text = safe text/glossary correction
timestamps     = never changed by correction

5. Evidence Integrity

Evidence must reference real existing segments.

Never create fake fallback evidence such as:

SEG0000

If referenced evidence does not exist:

reject

Never invent evidence or provenance just to make an object valid.

6. LLM Output Is Untrusted

Required validation flow:

LLM JSON
→ strict output schema
→ Pydantic validation
→ extra="forbid"
→ semantic validation
→ deterministic program-generated metadata/IDs
→ persisted model
→ final validation

Unknown fields must not be silently dropped.

Malformed or semantically invalid output must fail visibly.

Do not weaken validation to increase LLM success rate.

7. Configuration Must Fail Fast

Configuration corruption must fail fast.

Tests must continue covering at least:

invalid JSON
duplicate QID
missing required fields

Do not silently replace damaged configuration with defaults unless explicitly designed.

8. Per-Source Persistence

A Source is the primary durable processing unit.

Per-source manifest:

manifests/by_source/SRCxxxx.json

must be the Source-level source of truth.

Global files such as:

sources.jsonl
knowledge.jsonl
cases.jsonl

are rebuildable indexes, not the only durable copy.

Preferred persistence flow:

status = processing
↓
generate complete result
↓
write SRCxxxx.json.tmp
↓
close/flush
↓
os.replace(...)
↓
status = completed

Never expose a partially written completed manifest.

9. Crash / Resume Safety

The system must safely recover from interrupted runs.

Example:

SRC0004 = processing
→ process crashes
→ next run detects stale state
→ reclaim
→ safely reprocess
→ completed

A completed Source should normally skip expensive external processing.

Retries must not accidentally cause duplicate Whisper/LLM calls or duplicate knowledge.

Tests should reproduce real crash/resume behavior where practical, not only isolated helper functions.

10. Stable Identity

Logical identities must remain stable across reruns.

Important identities:

Source ID
Segment ID
Chunk ID
Knowledge ID

Do not use processing order or display sequence numbers as the sole identity.

Renaming the same source file should not automatically create a different logical Source.

Display IDs such as:

K000123

may exist, but must not be the sole Knowledge identity.

Knowledge identity should be derived from stable semantic inputs, conceptually:

source_id
+ chunk_id
+ knowledge_type
+ normalized content hash

Follow the assigned task and existing architecture for exact implementation.

11. Provenance

Strictly distinguish:

explicit
inferred
synthesized

Rules:

explicit
→ requires evidence_segment_ids

inferred
→ requires inference_basis

synthesized
→ requires source_knowledge_ids

Missing required provenance must cause rejection.

Do not let program logic invent missing provenance for LLM output.

12. Knowledge Boundaries

Never mix:

current restaurant facts
expert methodology
course case facts

A restaurant mentioned in a course example is NOT automatically a fact about the current Food-IP user's restaurant.

Examples must not automatically become universal rules.

Preserve conditions, exceptions, and source context.

13. Testing Principles

Reliability fixes require tests.

Important coverage includes:

invalid configuration
invalid LLM output
missing evidence
duplicate IDs
atomic persistence
interrupted execution
resume after crash
stale processing recovery
completed-source skip
stable reruns
idempotency

Prefer integration-style tests when the bug occurs through the real application path.

Do not modify tests merely to make incorrect behavior pass.

External paid AI calls must be mocked in automated tests whenever possible.

Mock the model boundary, not the architecture being tested.

14. Failure Philosophy

Prefer:

explicit failure > silent corruption
reject invalid data > invent missing data
deterministic recovery > manual cleanup
rebuildable indexes > fragile global state

Data correctness is more important than output quantity.

15. Scope Discipline

For each task:

inspect
→ understand
→ implement smallest coherent change
→ test
→ report

Do not perform unrelated refactoring.

If an unrelated bug is discovered, report it separately unless it blocks the assigned task.

Do not add speculative technologies.

16. Currently Forbidden

Until explicitly authorized, do NOT begin:

formal 5-video ingestion
77-video full ingestion
Embedding
Vector Database
GraphRAG
RAPTOR
Neo4j
Food-IP backend integration
Content Engine V2
UI
OCR
multimodal extraction

P0 reliability must be completed first.

17. 5-Video Pilot

After P0 is complete, the next stage is the 5-Video Knowledge Quality Pilot.

Its main question is:

Did the AI learn the expert knowledge correctly?

Evaluate:

whether the teacher actually said the claimed knowledge;

whether conditions/exceptions were preserved;

whether cases were distorted;

whether experience became an unjustified hard rule;

whether explicit/inferred provenance is correct;

whether case facts polluted current restaurant facts;

whether the Question Graph is useful.

Only after knowledge quality is proven should retrieval infrastructure be expanded.

18. Final Engineering Principle

The objective is not merely:

make the pipeline run.

The objective is:

make the pipeline trustworthy.

Always prioritize:

correct evidence
stable identity
strict provenance
atomic persistence
safe recovery
idempotent reruns

before:

more videos
more knowledge
more infrastructure
more features