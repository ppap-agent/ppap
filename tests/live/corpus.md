# Corpus, oracle, and support matrix (#3)

This directory owns sample identity, rights, and the support matrix. [TESTING](../../docs/TESTING.md) owns how proofs aggregate. Do not copy `ppap-agent` `assets/` into this repo.

```sh
python tools/live.py corpus
python tools/legacy_engine_gate.py --case .local/engine.json
python tools/live.py audit
```

`live.py corpus` always reports every promised format. Missing samples or unreviewed oracles are `NOT_PROVEN`. A passing public fixture hash is not Excel/PDF/DOCX PPAP-rule support. Old engine output is a comparison object, not an oracle.

## Two corpora

| Class | Where | Rights | What it can prove |
|---|---|---|---|
| Public format fixtures | [public/](corpus/public/) recorded in [catalog.json](corpus/catalog.json) | Authored here; no customer content; shippable | Bytes exist and SHA-256 matches. Not a human PPAP oracle. |
| Controlled business corpus | Authorized checkout of `ppap-agent` @ `53a5bd2b` (`assets/v2.0plus`, POC grouped samples, PSW `psw.doc`, plus the 4.8 CSV and POC statistics workbook) | Isolated replay only; do not copy bytes into this git tree. Set `PPAP_CORPUS_ROOT`. Not `assets/nio_live` live-fetch, not superseded `assets/V2.0测试`. | Same inputs for old-engine comparison and later in-repo checkers. Named tables without a checkout stay `NOT_PROVEN`. |

Source guide (read-only): [ppap_agent/tests/README.md @53a5bd2b](https://github.com/ppap-agent/ppap-agent/blob/53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9/ppap_agent/tests/README.md).

Human oracles are the V2 4.8 CSV, POC statistics xlsx, and PSW fixture review at that commit. They are not `engine.example.json` expected rows. Product owner authorized those tables for isolated replay on 2026-09-19 (`oracle_revisions`). Revising an oracle still needs a new `oracle_revisions[]` row with a reason; do not edit expected values so a new implementation passes. Named authority without the checkout bytes remains `NOT_PROVEN`.

## Promised formats

`xlsx`, `pdf`, `docx`, `doc`, `scanned`, `cross_file`. This is the current commitment from the source suites, not “all PPAP”. Dropping a row requires changing the catalog, not omitting it from a run.

PDF conversion order in the source guide is `manual_linegrid → pymupdf_find_tables → OCR`. That is not evidence that every PDF path is OCR-first.

## Difference classes

When comparing two runs of the same input, label exactly one of:

- `equivalent`
- `conversion_loss` — missing structure/key fields or conversion diagnostics; not an “expected format quirk”
- `rule_logic_defect` — unexplained PASS/NG/ERR change
- `approved_format_difference` — only allowlisted rule numbers, recorded in the review

`tools/live.py` implements that classifier. It does not write oracles.

## Document candidates

Docling stays a candidate until the same authorized files and human oracle produce per-rule and locator evidence, plus runtime/license notes. No such run exists here, so the matrix keeps `docling: NOT_PROVEN`.

## Local controlled replay

```sh
export PPAP_CORPUS_ROOT=/absolute/path/to/ppap-agent@53a5bd2b
python tools/live.py corpus
```

On an authorized Cloud VM the checkout can live in gitignored `.local/ppap-agent`. `live.py corpus` then checks those bytes; CI without `PPAP_CORPUS_ROOT` still reports `business_oracle_not_proven`. Do not add the originals to this repository.
