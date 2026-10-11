# Sensei shelf check: pipeline, viewer and fine-tuned model

JunctionX Lisbon 2026, Sensei challenge ("Fresh eyes on fresh produce"). For every planogram area in a ceiling-camera photo the pipeline answers `planogram_compliant` and `stock_level` (empty / half / full) in Sensei's annotation format.

## What runs

Claude Opus 5.5 judges each area once (`config.json`: prompt v5, effort low, image only: the whole photo with the area outlined, a close-up, and the area straightened). Two coded rules then turn its answer into the call:

- **Stock level from Opus's own fill estimate** (`"stock_rule": "fill:35:65"`): under 35 % covered is empty, under 65 % half, otherwise full.
- **Same kind of produce counts as compliant** (`shelfcheck.same_kind`): a different variety of the planned fruit (Golden apples where Gala is planned) is not a wrong product. Kiwi is excluded because the labels treat gold and green kiwi as different.

On 901 hand-labelled areas of the training day this scores 1,385 of 1,802 points (77 %); plain Opus scores 1,269. About $0.12 per photo. Every decision and experiment is in `docs/decisions.md`.

## Setup

```
uv venv && uv pip install -r requirements.txt        # add -r requirements-local.txt for the local model
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

The organisers' dataset is not in this repo. Put it at `data/raw/extracted/new_dataset_hackathon_junctionx_10_oct_2026_v2/` (only the tools and the viewer's sample list need it; judging new photos does not).

## Run

- **Viewer**: `.venv/bin/python app/server.py`, then open http://127.0.0.1:8787/batch, drop up to 26 photos and see each photo with its areas outlined and what the model thinks is in each. Tick "Fresh run" to ask Opus again instead of replaying saved answers. http://127.0.0.1:8787 is the single-photo staff view.
- **Submission file**: `.venv/bin/python predict.py <folder of photos> --out submission.json` (a complete fallback file is written first, so no area is ever missing).

## Files

| Path | What |
|---|---|
| `shelfcheck.py` | planogram, crops, prompts (v1 to v8), the Opus judge with its answer cache, decision rules, scoring |
| `predict.py` | the submission run (`"mode": "opus"`; `"cascade"` = local model first, Opus where unsure) |
| `app/` | the viewer (`batch.html`) and the staff view (`index.html`) |
| `gates.py`, `tools/gate_tune.py`, `tools/stack_tune.py` | coded pre-checks (covered, unchanged, clearly empty/full, colour fingerprint) and their measurement: a cost story for store use, not more accurate than Opus |
| `tools/local_eval.py`, `cascade_tune.py`, `cascade_test.py` | the local model and the cascade measurements |
| `tools/build_lora_dataset.py`, `tools/lora_train.py` | the LoRA training set (Opus as teacher, people's labels where they exist) and the memory-capped trainer |
| `tools/tune.py` | scores any prompt/model on `labels/dev.csv` |
| `labels/` | 901 hand-checked areas (`dev.csv`) and their sources |
| `data/` | planogram, English product table, camera fingerprints, day pass, reference crops |
| `models/qwen3.5-4b-shelf-lora/` | the fine-tuned adapter's config; the weights are a release download (below) |

## The fine-tuned model

Qwen3.5-4B (`mlx-community/Qwen3.5-4B-MLX-4bit`) with a LoRA adapter (rank 16, 500 steps, 448 px crops) trained on Opus's answers and the team's labels. The weights (130 MB, over GitHub's file limit) are attached to the `qwen-lora-run1b` release:

```
gh release download qwen-lora-run1b -p adapters.safetensors -D models/qwen3.5-4b-shelf-lora
```

Measured, it is not good enough to replace Opus: 102 of 218 points on 10 evening photos against Opus's 145, and its printed confidence is 0.90 on almost every area, so a cascade cannot tell when to hand over (see `docs/decisions.md`).
