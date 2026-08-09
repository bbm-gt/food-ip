# Food-IP Knowledge Pipeline — P0 Reliability Hardening Report

**Date:** 2026-08-08  
**Pipeline:** 3.1.0  
**Current status:** **Round 1 sealed by code-level audit; overall P0 is NOT yet declared complete.**  
**Formal video run status:** **Do not run the 5-video pilot yet.**

## Round 1 scope sealed

This round hardened the production path around transcription and first-hop provenance:

1. `FOOD_IP_PROMPT` reaches the real `faster-whisper` `model.transcribe(initial_prompt=...)` call.
2. One selected video is transcribed as one file; no automatic directory/legacy fallback is accepted in the P0 production path.
3. Native Whisper `segment.start/end/text` becomes `WhisperSegment`, then validated `ASRSegment` with preserved `raw_text` and corrected `corrected_text`.
4. `ASRSegment` is mandatory for refinement. Missing, empty, malformed, or cross-source ASR data fails before Semantic Chunking / paid LLM work.
5. Semantic chunks may only reference real ASR `segment_id`s. The former `SRCxxxx-SEG0000` sentinel fallback was removed; unknown evidence rejects the chunk.
6. `SourceManifestEntry` now matches the runtime manifest and records `asr_segments_path` explicitly.
7. Runtime `ASRSegment` records are validated by Pydantic before persistence; glossary audit fields (`asr_fix_count`, `asr_fixes_applied`) are part of the model contract.
8. LLM-output/Persisted-model separation and `extra="forbid"` behavior from the earlier Round 1 work remain in place.
9. **Refine consumes transcription ASR output directly.** `food_ip_refine` no longer depends on a `*_corrected.txt` file (which transcription never produces); corrected text is derived from `whisper_segments/{sid}_asr_whisper_segments.json` and the title from `manifests/by_source/{sid}.json`.
10. **A zero-cost Transcribe→Refine integration test** proves the two CLI pipelines run consecutively on real artifacts: fake video + mocked faster-whisper → `food_ip_transcribe.main()` writes authoritative ASRSegments + per-source manifest → `food_ip_refine.main()` consumes them (mocked LLM) → SemanticChunk-backed KnowledgeCard with intact provenance.

## Tests

```text
77 passed
```

New final-seal coverage includes:

- ASR missing does **not** fall back to raw WhisperSegment.
- `process_source()` does **not** call Semantic Chunker without authoritative ASR data.
- Runtime ASR records validate against `ASRSegment`.
- Runtime source manifest validates against `SourceManifestEntry` and contains `asr_segments_path`.
- Unknown or mixed-valid/unknown `segment_ids` are rejected; no sentinel evidence is fabricated.
- `ASRSegment.corrected_text` is proven to reach the actual Semantic Chunk LLM prompt while `segment_id/start/end` remain unchanged.
- `--limit 1` still invokes transcription only once and now exercises a successful ASR-backed manifest path.
- **Config fail-fast on invalid input** — invalid JSON, duplicate `question_id`, and missing required fields in `question_tree.json` all fail fast (error list + `validate_all_config()` SystemExit). Previously only the valid-config path was automated.
- **Transcribe→Refine CLI handoff** — the integration test drives both `food_ip_transcribe.main()` and `food_ip_refine.main()` on real files with whisper/LLM mocked, asserting the produced KnowledgeCard carries `source_id=SRC0001`, real `evidence_segment_ids`, programmatic chunk time range, and that a run audit report is written.
- **No `*_corrected.txt` dependency** — `pass0_asr_correction` derives text from ASRSegments and fails fast when ASR is missing; the code no longer references `RAW_CORRECTED_DIR`.

## Schemas

`food_ip_schemas/*.json` were regenerated from the current Pydantic models after the runtime-contract changes.

## Not claimed by this report

This report does **not** declare the whole P0 program complete. Later P0 audit rounds still need to cover the remaining persistence/recovery/identity/provenance/graph/schema/LLM-validation items already identified in the project handoff.

The previous report that claimed full P0 completion has been removed from the repository and should not be treated as the current status.
