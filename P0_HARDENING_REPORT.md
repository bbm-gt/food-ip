# Food-IP Knowledge Pipeline — P0 Reliability Hardening Report

**Date:** 2026-08-08 (updated 2026-08-09)  
**Pipeline:** 3.1.0  
**Current status:** **P0 Reliability Hardening: DONE (2026-08-09 Final Acceptance = GO).**  
**Formal video run status:** **Do not run the 5-video pilot yet.**

## P0-FINAL Acceptance (2026-08-09)

**Result: `P0 FINAL: GO`**

- **P0 Reliability Hardening: DONE** — Final Acceptance executed against the real codebase (read-only audit → baseline → fault injection → evidence trace → E2E). All 30 matrix items (P0-01..P0-30) verified PASS with code/test evidence; no FAIL / NOT PROVEN items remain.
- **Final Acceptance 日期:** 2026-08-09
- **测试数量:** 113 passed (pytest) / Ran 113 tests — OK (unittest). Added 20 acceptance tests to `test_food_ip_p0.py`: 6 evidence-gap tests (pid<=0 stale, crash-before-mark_done no-dup, reset+rerun idempotency, persistence-failure → failed-not-done, full evidence trace-back) + 6 issue-1/2 closure tests (strict `_load_jsonl` raise, rebuild raises on corrupt data, rebuild recovery, rebuild-failure → failed-not-done, sources-index strict, segment identity stability) + 8 global-snapshot-integrity tests (mid-commit swap failure → previous snapshot preserved + no staged leftovers; failed Source contributes zero; processing Source does not contaminate; done-but-missing-artifact fail-fast; valid-JSON-invalid-schema fail-fast; cross-source artifact fail-fast; committing Source enters its own snapshot; failure→repair→rerun→done + coherent snapshot).
- **关键 crash/recovery 结果:**
  - completed skip gated by artifact validation (done + all 5 per-source artifacts valid → skip, 0 LLM calls; done-but-damaged artifacts → reset + reprocess).
  - stale processing recovery vs live-processing refusal (dead pid reclaimed; live pid never preempted; pid=0 / pid<0 treated as stale).
  - crash before mark_done → reprocess → no duplicate Chunk/Knowledge, global index consistent, ends done.
  - persistence failure mid-save → state=failed, never a bare completed marker.
  - global index rebuild is STRICT: corrupt per-source data raises (never silently omitted from the index); a rebuild failure marks the source failed (never done behind a stale/missing index); fixing the corruption and re-running rebuilds the global index to match per-source truth.
  - the 5 global files are committed as ONE snapshot (stage → backup → swap → rollback on failure; interrupted commit recovered on next rebuild) — a failed rebuild never leaves a mixed generation, it rolls back to the previous complete snapshot; only releasable Sources (done+complete, or the current committing source) contribute, and every line is validated against its persisted Pydantic model + source ownership, not just JSON syntax.
  - reset + full rerun → Source/Segment/Chunk/Knowledge identity stable, no duplicate entities.
- **E2E 结果:** real `food_ip_transcribe.main()` → real `food_ip_refine.main()` consecutive handoff (faster-whisper + LLM mocked, dirs → temp) proves transcription→refine is genuinely connected: authoritative `whisper_segments/{sid}_asr_whisper_segments.json` + `manifests/by_source/{sid}.json`, no `*_corrected.txt` dependency. Evidence chain Card → Chunk → ASRSegment → Whisper-native timestamp → Source verified on persisted artifacts.
- **最终 commit SHA / 工作树基线:** branch `main`, starting SHA `64e9c2982f161b58ba53fb9a01fc42833bed6cbf`. Working tree: clean production code; `test_food_ip_p0.py` +245 (6 acceptance tests, uncommitted).

## P0-FINAL Re-seal (2026-08-09) — independent-audit closure

An independent re-audit raised two reliability concerns. Both were audited and
closed with fault-injection tests (no test weakening, no report-only claims):

1. **Global index rebuild failure semantics — was a BLOCKER, now fixed.**
   - Old behavior: `_persist_source()`/`flush()` caught a `rebuild_global_indices()`
     failure with a WARN and continued to `mark_done()`; `_load_jsonl` silently
     skipped corrupt lines → a rebuild failure left a "done" source behind a
     stale/missing global index, and corrupt authoritative data was silently
     omitted from the index.
   - New contract: `rebuild_global_indices()`, `_load_jsonl()`, and
     `rebuild_sources_index()` are STRICT — corrupt/unreadable per-source data
     raises `RuntimeError` (explicit failure > silent corruption). All per-source
     data is collected before any global file is written, so a failed rebuild
     never writes a partial index. Source `done` now requires per-source
     artifacts saved AND a successful global rebuild; a rebuild failure marks the
     source FAILED (never a bare done marker with a stale index). Contract
     documented in `run_source`/`_persist_source` docstrings and CLAUDE.md §8.
   - P0-26 + Case H re-audit: PASS — corrupt data is loud; recovery is
     deterministic (fix corruption → rerun → global index equals per-source
     truth); a "looks-complete" state cannot form.
2. **Segment identity contract — resolved as the intended design.**
   - Segment ID = `{source_id}-SEG{ordinal}` (ordinal of the Source's
     authoritative Whisper transcription). Namespaced under the stable
     content-derived Source ID (never a bare display sequence), stable across
     reruns of the same transcription, evidence validated strictly against the
     current authoritative ASR (semantic_chunker rejects unknown segment_ids;
     no sentinel evidence). CLAUDE.md §10 updated to document this exact
     contract. No formula change: span/content-based IDs would churn on
     timestamp jitter and do not alter the re-transcription path; a changed
     transcription defines a new Segment set for that Source (default
     transcription `--resume` skips re-transcription of a known Source).

## P0-FINAL Global Snapshot Integrity re-seal (2026-08-09) — independent-audit closure

A third independent fault-injection pass found three remaining integrity gaps in
`rebuild_global_indices()` and they were closed (no test weakening, no
report-only claims; 8 new fault-injection tests added):

1. **Multi-file snapshot atomicity (Issue A).** The five global files were
   replaced sequentially, so a failure after the 1st swap left a mixed
   generation (chunks=new, knowledge/case/anti/format=old) — the old
   "failed rebuild never leaves a partially rebuilt index" claim was false.
   Now the snapshot is committed as a set: all per-source data is collected and
   validated first, the five files are staged (`.tmp`), backed up (`.bak`), and
   swapped in as one unit; a swap failure rolls back every file to the previous
   complete snapshot, and a hard interruption is deterministically recovered to
   the previous snapshot on the next rebuild. Fault-injection test: OSError on
   the 2nd global-file swap → all five files equal the previous snapshot, no
   `.bak`/`.tmp` leftovers, clean rerun commits a coherent snapshot.
2. **Source eligibility (Issue B).** The rebuild read every `atomic/by_source/*`
   dir without checking refine lifecycle state, so a failed/processing Source's
   partial artifacts leaked into the global index. Now only releasable Sources
   contribute: already-`done` with all five artifacts complete/valid, or the
   Source currently committing (passed explicitly as `commit_source_id`, since
   mark_done runs after the rebuild and the Source is still `processing`).
   failed / processing / pending / never-started Sources contribute ZERO; a
   `done` Source with a missing/corrupt artifact and a Source with a corrupt
   state file fail fast (never silently skipped). Tests: failed Source
   contributes zero, processing Source does not contaminate, done-but-missing
   fail-fast, committing Source enters its own snapshot, failure→repair→rerun
   →done with a coherent snapshot.
3. **Artifact validation (Issue C).** `_load_jsonl` enforced only JSON syntax,
   so `{"junk":"still valid json"}` flowed into the global index. The rebuild
   now validates every line against its persisted Pydantic model
   (extra="forbid") and source ownership, reusing `REFINE_ARTIFACTS` + the
   persisted models. Tests: valid-JSON-invalid-schema fail-fast (names the
   rejected model), cross-source artifact fail-fast (names both sources).

**Result: `P0 FINAL: GO`** — the global snapshot can no longer be partially
rebuilt, contaminated by non-releasable Sources, or absorb schema-invalid /
cross-source items; the previous complete snapshot always remains usable.

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

The "Round 1 sealed" scope above only covered transcription + first-hop provenance. The remaining persistence/recovery/identity/provenance/graph/schema/LLM-validation items were subsequently covered by Round 2 tests (per-source persistence, state machine, artifact-validated skip, crash/stale recovery) and closed by the **P0-FINAL Acceptance (2026-08-09, GO)** recorded at the top of this report — all 30 matrix items PASS.

The previous report that claimed full P0 completion has been removed from the repository and should not be treated as the current status.
