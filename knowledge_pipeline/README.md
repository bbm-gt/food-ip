# Food-IP Knowledge Pipeline

The repository's current mainline and next task are defined only by the parent
`docs/project-status.yaml`; this subsystem README does not duplicate that status.
`knowledge_pipeline/` is an independent Food-IP Professional Creative
Knowledge production subsystem, not the repository's current mainline. It
remains logically independent from `backend/` and `frontend/`: it produces
validated, traceable Knowledge; the product runtime may consume a stable
contract later.

The existing course-video pipeline is one implemented and validated knowledge
ingestion path, not the complete set of future knowledge sources. Source
stratification, admission, evidence quality, and freshness governance remain
open decisions and must not be inferred from this README.

## Run locally

Run commands from this directory so the existing flat-module imports continue
to work:

```powershell
cd C:\Users\HP\food-ip\knowledge_pipeline
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m unittest test_food_ip_p0.py
```

The test suite must not write to production data. Set temporary roots when
running tests outside the suite's own patches:

```powershell
$env:FOOD_IP_SOURCES_DIR = 'C:\temp\food-ip-test-sources'
$env:FOOD_IP_KNOWLEDGE_DIR = 'C:\temp\food-ip-test-knowledge'
```

## Knowledge data and external paths

The deployable Knowledge snapshot is committed under the repository root at
`knowledge_data/`. This is the default `FOOD_IP_KNOWLEDGE_DIR`; set that
environment variable only to use a different knowledge snapshot.

Other runtime data and tools remain external:

They can be overridden with environment variables:

- `FOOD_IP_INPUT_DIR`
- `FOOD_IP_SOURCES_DIR`
- `FOOD_IP_KNOWLEDGE_DIR`
- `FOOD_IP_TRANSCRIBE_BATCH_PATH`
- `FOOD_IP_AUDIO_PREPROCESSOR_PATH`
- `FOOD_IP_WHISPER_VENV`
- `FOOD_IP_FFMPEG_BIN`
- `FOOD_IP_MODEL_DOWNLOAD_ROOT`
- `FOOD_IP_LEGACY_TRANSCRIPTS_DIR` (legacy scripts; default `E:\video_transcripts`)

The legacy CLI `--output` option is not a complete relocation mechanism; use
the environment variables above when source roots must move.

Do not add videos, Whisper models, FFmpeg, virtual environments, generated
runtime data, `.env` files, or external legacy tools to this subtree.

The current validated production path is `food_ip_transcribe` →
`food_ip_refine` → per-source persistence → atomic global snapshot. Preserve
timestamp, identity, provenance, schema, crash/resume, idempotency, and
snapshot atomicity contracts. Fact Contract / Fact Boundary, Creative
Decision, Memory, and Retrieval remain future product capabilities and are
Deferred; do not implement them or infer an unconfirmed admission or retrieval
design from this pipeline.
