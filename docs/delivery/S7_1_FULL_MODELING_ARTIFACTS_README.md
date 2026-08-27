# S7.1 Full Modeling Artifacts

This is the full result package, not the small incremental operations bundle.

## Included

- Final S7 hybrid dataset: clauses, occurrences, candidate facts, manifest.
- Full S7 Arctic-ko embeddings: 145,220 rows, 1,024 dimensions, float16.
- Full embedding chunk texts and pinned model metadata/revision.
- S6 baseline, S7, and S7.1 top-20 retrieval evaluation payloads.
- All locally available reranker outputs, release payloads, and latency records.
- S7.1 human-approved incremental facts, chunks, occurrences, and vectors.
- B8/F4 shadow candidates, human labels, provenance, configuration, code, tests, and reports.

## Model weights

Model weights are not modeling outputs and are not embedded in this archive.

- Arctic-ko is pinned in `data/external/s7_arctic_ko_revisioned/meta.json`:
  - model: `dragonkue/snowflake-arctic-embed-l-v2.0-ko`
  - revision: `55ec6e9358a56d56af759bc8372e970caf8c305f`
- Qwen reranker:
  - model: `Qwen/Qwen3-Reranker-4B`
  - raw scores and verified release payload are included.

The local Arctic-ko Hugging Face cache is about 2.13 GiB and can be packaged separately when an air-gapped handoff is required. Qwen3-Reranker-4B was executed on a remote GPU node and its weight cache is not present on this workstation.

## Integrity and serving boundary

- Active release: `r2026-08-04-clause-s7.1-arctic-ko-ocr-approved`.
- Approved OCR: 850 occurrences / 75 incremental chunks.
- B8/F4: 8,622 facts, shadow-only until human approval.
- Check `/api/health/ready` after applying the package.
