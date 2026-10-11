# Decision log

Dated, newest first. Every entry says what was decided, on what evidence, and what would change it.
Experiments (numbers) live in `out/experiments.jsonl` and on the dashboard; this file is the reasoning.

## 11 Oct, 01:20: the customer view as extra context (v8): neutral; config stays v5 + fill rule

Ishak: keep everything Opus gets and add the mentors' customer/staff question as extra context. Why v7 (mentor questions only) lost 180 points: it changed ~50 calls, mostly wrongly, in two patterns: leftovers on a bare liner became "half" (a shopper would take the last bunches; labels say empty, 16 cases) and crates of a few large items became "half" (staff would refill; labels say full, 9 cases); it had also dropped the "small gaps are still full" guidance. v8 = v5 unchanged + a cross-check paragraph with those two cases spelled out; v8b = v8 + "in doubt, lower".

| prompt + stock rule | all 901 | clean 406 | evening 304 | stock acc | under / over |
|---|---|---|---|---|---|
| v5, most likely level | 1269 | 558 | 408 | 86.6 % | 41 / 80 |
| **v5, fill:35:65 (config)** | **1349** | **567** | **407** | **88.5 %** | 53 / 51 |
| v8, most likely level | 1294 | 553 | 406 | 87.7 % | 32 / 79 |
| v8, fill:35:65 | 1307 | 533 | 389 | 87.3 % | 57 / 57 |
| v8b, most likely level | 1292 | 533 | 385 | 86.8 % | 53 / 66 |

v8 is within noise of v5 (+25 on all, −5 clean, −2 evening); the lean line costs again on the clean and evening labels. v5 + fill rule stays the best on every label set. Context for "why only ~75 %": where two labellers judged the same area they agreed on 76 % (112 vs 36), so the labels themselves cap what any judge can score; the organisers' own example solution in the brief scores 320/578 (55 %). Cost $19.88.

## 11 Oct, 01:00: prompt v7 = the mentors' two questions only (no coverage), in doubt lower: 1091 of 1802

Ishak: judge each area only by "would a shopper buy what is left (if not: empty)? would staff take the time to replenish (if not: full, else half)? in doubt, under-estimate", not by coverage. Prompt v7 says exactly that. Opus x1 on the 901 labels, most likely level: **1091** (stock 722 right, 101 under, 78 over), against v5 1269 and v5 + fill rule 1349. On our labels the coverage-free reading costs about 180 points; the over-calls do not even fall (78 vs 80), the under-calls more than double. Config NOT switched pending Ishak's decision (v7 is available as `"version": "v7", "stock_rule": "argmax"`). Cost $9.87.

## 11 Oct, 00:45: coded gates measured (a cost story, not an accuracy gain); stock level from Opus's own fill estimate (+80 points)

Ishak proposed coded rules before the model: covered, nothing changed, clearly empty, right product by colour fingerprint, each answering only when clearly sure. Agreed: the scored run stays Opus; the gates are the real-life cost story (a store has every earlier photo). Built `gates.py`, `tools/gate_tune.py` (thresholds tuned on the 495 team labels, checked on the 406 clean ones, references leave-one-photo-out and never from held-out photos) and `tools/stack_tune.py`.

**The gates on this data (hourly photos, 1,962 areas):**
- Covered: the 2 fabric-covered photos are found (they are the first frames of the day, so there is no earlier photo to copy; they go to Opus).
- Nothing changed: even when two consecutive hourly photos look almost identical, the hand label agrees only ~90 % of the time (Opus alone: 87 %). Reusing the previous answer never meets the careful bar; it pays only with frequent photos (minutes, not hours).
- Clearly empty / clearly full: coverage against the shelf's "emptiest" picture does not separate the levels (median 0.40 empty, 0.55 half, 0.50 full): the emptiest picture is often not empty and the light changes over the day. Edge density separates a little. No careful setting holds on the clean photos.
- A learned code-only gate (gradient boosting on all the features, per-shelf normalised) is right 83.6 % of the time where it is "98 % sure", against Opus's 85.6 % on the same areas: it cannot replace Opus at any bar.
- Store-mode replay of the day: careful gates send 93 % of areas to Opus (−30 points); aggressive gates send 23 % ($0.03 per photo instead of $0.12) for −182 points of 1,802. That trade-off is the slide.

**What did gain points: re-deciding Opus's stock level from its own fill estimate.** A small model on Opus's numbers gained +69 to +106 points (paired bootstrap, 5 shuffles, interval above zero every time); image features added nothing on top. Its effect is captured by a two-threshold rule on Opus's `fill_percent`: empty below 35 %, half below 65 %, else full. The thresholds come out at (35, 65) in every cross-validation fold and in both "tune on one label set, test on the other" splits. Through the real scoring code, Opus x1:

| stock rule | all 901 | clean 406 | evening frames 304 | stock acc | under / over |
|---|---|---|---|---|---|
| most likely level (was) | 1269 | 558 | 408 | 86.6 % | 41 / 80 |
| **fill:35:65 (now)** | **1349** | **567** | 407 | 88.5 % | 53 / 51 |

It fixes Opus calling a nearly bare liner "half" (its own fill estimate says 15 %) and trades over-calls (−3) for cheaper under-calls (−2). Uses only Opus's answer about the image under analysis, so it respects the organisers' rule. Config: `"stock_rule": "fill:35:65"` (branch `ishak`). Neutral on the evening frames, so the expected gain on the validation set is modest but never negative on any split we have.

## 11 Oct, 00:05: Opus x3 against the fine-tuned Qwen on 280 random records of Ilies' sheet (560 points)

Ishak asked for the three-call Opus setup and Qwen on 280 random records (seed 0) of `ilies/dataset/labels.csv`. Truth = the sheet (22 human labels, the rest Opus-v3 guesses); 136 of the records also carry our hand labels, but 70 of those are the team's labels on photos the adapter trained on, so Qwen's number on "ours" is optimistic; 164 of the 280 records sit on training photos. Mostly daytime frames (fuller, easier than the evening).

| judge | sheet (560) | human (44) | our labels (272) | $/photo |
|---|---|---|---|---|
| Qwen + adapter alone | 374 (67 %) | 32 | 170 | 0.000 |
| cascade: Qwen if product ok and top p >= 0.9, else Opus x3 | 381 | 32 | 173 | 0.025 |
| Opus x3 alone | 425 (76 %) | 37 | 177 | 0.366 |

Opus x3 beats Qwen by 51 points of 560 (9 % of the maximum) on the sheet and by 5 of 44 on the human labels; the cascade again keeps 93 % local (the student's top probability is 0.90 on 277 of 280) and lands 44 points below Opus. Opus x3 at $0.37 per photo scores the same as Opus x1 within noise (as on the 406 clean areas: 579 vs 587), so the Sunday config stays Opus x1. Cost of the test: $4.82.

## 10 Oct, 23:50: the 0.9 bar cannot make Opus "check often": the student's confidence is all-or-nothing

Ishak asked for the cascade with Qwen's confidence bar at 90 (else Opus checks) and a test on 200 records of Ilies' dataset export (`tools/cascade_test.py --n 200 --seed 0`; 116 photos; truth = the sheet's labels, i.e. 16 human + 184 Opus-v3 guesses; 87 of the records also have our hand labels).

| configuration | sheet (400) | human (32) | our labels (174) | to Opus | $/photo |
|---|---|---|---|---|---|
| Qwen + adapter alone | 250 | 23 | 97 | 0 % | 0.000 |
| cascade, keep Qwen if product ok and top p >= 0.80, 0.85 or 0.90 | 257 | 23 | 100 | 8 % | 0.010 |
| cascade, keep Qwen only if top p > 0.90 | 290 | 28 | 113 | 100 % | 0.124 |
| Opus x1 alone (frozen config) | 290 | 28 | 113 | 100 % | 0.124 |

- **The student prints 0.90 as its top probability on 197 of 200 records** (0.98 on the other 3, all "no product"). The fine-tune taught it the teacher's habit of writing 90/10 and nothing about when it is unsure. So a bar at or below 0.9 keeps every area where it says the product is right (92 %), and any bar above 0.9 keeps none. There is no setting that sends Opus "often": it is all or nothing.
- At the bar that keeps 92 % local the cascade loses 33 of 400 points against Opus (8 % of the maximum; 13 of 174 on our hand labels), for a saving of $0.11 per photo. Under 80 % points / 20 % cost that is a loss. At the literal "above 90" the cascade is Opus with extra work.
- Decision unchanged: Sunday is `"mode": "opus"`. The cascade block stays in config.json (bar 0.9, adapter run1b) as documentation of what was measured. To make a cascade work the student would need a real uncertainty signal (token log-probabilities, or agreement across several samples), not its printed numbers; not a tonight job.

## 10 Oct, 23:30: the fine-tuned Qwen does not reach the bar; Sunday stays Opus x1

Final adapter (`out/lora/run1b`: 500 steps, rank 16, 448 px crops, Opus teacher then people's labels where they exist), scored on the 10 evening photos (109 areas, max 218):

| judge | points | stock under / over | escalated to Opus |
|---|---|---|---|
| Opus x1 (frozen config) | 145 | 7 / 13 | - |
| Qwen untrained, full-size crops | 96 | 8 / 23 | - |
| Qwen + adapter, full-size crops (wrong scale, first run) | 81 | 30 / 8 | - |
| **Qwen + adapter, 448 px crops as trained** | **102** | 23 / 8 | - |
| cascade: adapter first, Opus where unsure | 114 | 23 / 8 | 10 of 109 |

- **The fine-tune helped a little and not enough.** 102 against 96 untrained is within noise on 109 areas; the stock accuracy is 72 % against Opus's 87 %. The human-first targets moved the errors from "too full" to "too empty" (cheaper, but still 23 of them).
- **The hand-over signal is dead.** The student prints probabilities of 0.8 or more on almost every area, right or wrong, so escalating at any bar sends the same 10 areas (the "no product" answers). A cascade that keeps 90 % local scores 114, i.e. 31 points (14 % of the maximum) below Opus, to save about $0.09 per photo. Under the 80 % points / 20 % cost weighting that is a clear loss.
- **Scale matters for the student.** Evaluating the adapter at full crop size (never seen in training) cost 21 points; `tools/local_eval.py` now shrinks the crops to 448 px by default. The remaining chain (all 406 clean areas, cascade tuning, untrained baseline at 448 px) runs for the record, overnight training was considered and set aside: the loss had flattened at 0.12-0.15, the gap is perception at the half/full line in a 4-billion-parameter model, and a night of GPU time on a laptop that already crashed once is a poor bet against a frozen, rehearsed Opus x1.
- **Decision: Sunday runs `"mode": "opus"`, Opus x1, prompt v5, image only ($0.12 per photo).** The local-model study (three open-weight models, the LoRA, the cascade simulator) is the cost-efficiency evidence for the pitch: we measured the cheap route and chose points.

## 10 Oct, 23:05: the organisers' stock definition in the prompt (v6 / v6b): a wash on our labels

Ishak relayed the organisers' rule: empty = a shopper would not buy what is left; half = a staff member would take the time to refill it; full = they would not bother; in doubt, under-estimate. Written into the prompt as v6 (with an explicit "when in doubt choose the lower level" line) and v6b (the definitions only). Opus x1, image only, 901 labelled areas (max 1,802; 95 % interval about +-80):

| prompt, decision rule | all | confident | under / over |
|---|---|---|---|
| v5, argmax | 1269 | 1213 | 41 / 80 |
| v6, argmax | 1196 | 1147 | 84 / 66 |
| v6, argmax with p_full x2 | 1286 | 1239 | 34 / 81 |
| v6b, argmax | 1255 | 1195 | 51 / 76 |
| v6b, argmax with p_full x1.5 | 1265 | 1216 | 33 / 87 |

- The definitions themselves change nothing measurable: v6b matches v5 within noise, with the error mix tilted slightly towards "under" (cheaper, -2 instead of -3).
- The explicit "lean lower" line costs about 70 points on our labels: over-calls fall from 80 to 66 but under-calls double to 84. Our labellers (the team under the mentors' 20/80 % rule, Claude under the 70 % line) do not lean lower; whether the organisers' validation labels do is the open question, and it cannot be measured here.
- Boosting p_full back in the decision rule after the lean (v6 x2.0) gives the best number, 1286, but that is the lean and its undo cancelling out: noise.
- Recommendation: v6b with the plain argmax rule for Sunday, because the prompt should carry the organisers' own definition rather than our 70 % line when the validation labels follow their definition, and it costs nothing on our labels. Changing the frozen config is the team's call. Cost: $9.86 + $9.89.

## 10 Oct, 22:30: laptop crash, step-100 checkpoint scored (no gain yet), training resumed on the human-first set

- **22:05 the laptop crashed** with three GPU jobs running at once (LoRA training, the step-100 checkpoint scoring, the human-first dataset build). mlx-vlm's trainer wires up to 11.8 GB of the 16 and MLX's buffer cache may grow to 15.2 GB, so two Metal jobs cannot be paged out. Rule from now on: **one heavy local job at a time**, and training runs through `tools/lora_train.py` (same arguments as `mlx_vlm.lora`, wired memory capped at 7 GB, buffer cache at 1.5 GB). Measured: the 4B model answering alone has a 5.8 GB footprint (6.2 peak).
- **Step-100 checkpoint on the 10 evening photos (109 areas, max 218):** 90 alone (untrained Qwen 96, Opus 145); 26 over-estimates against 3 under; the cascade escalates only 5–15 areas because the student prints confident probabilities, so it reaches 96–99 instead of Opus's 145. Not a verdict on the fine-tune: 100 steps at batch 1 is 100 examples, 5 % of one pass over the set.
- **Training data does not lean "full" relative to the validation proxy.** The last-two-frame labels are 64 % full / 25 % half / 11 % empty; the human-first training set is 56 / 23 / 20. So the over-estimates are a perception problem (fill level around the 70 % line), not a prior to re-balance; a harder cap on "full" rows was tried and rejected (it would not bind).
- **Resumed from step 100 on `out/lora/data_human`** (people's labels as targets on 351 examples, Opus elsewhere) rather than the Opus-only set: Opus itself over-estimates against people (112 over / 14 under on the 901 areas), and over costs −3. Run `out/lora/run1b`, 400 steps, checkpoint every 100, ends ~23:40. Decision bar unchanged: cascade must match Opus ×1 on the 406 clean areas within noise while keeping a real share local; otherwise Opus ×1 on Sunday.

## 10 Oct, 22:10: the team's blind labels (639 areas) join the dev set; the local-first cascade plan

- Ilies' blind-labelling server was used by Ishak, Ilies and Henri: 639 areas with a real answer (Ilies 323, Henri 219, Ishak 127 answers), 495 of them on photos we had not labelled. Merged with people's labels taking priority over Claude's on the 36 conflicts: **the dev set is now 901 areas** (max 1,802); the evening-frame subset is unchanged (304 areas).
- Opus ×1 strict on the 901: **1,269 / 1,802 (70 %)**, 19 false wrong-product calls / 11 missed, stock 780 right / 41 under / 80 over. On the 406 original areas it was 587 / 812 (72 %). The team labels say "half" more often where Opus says "full", so the over-estimates grew.
- Run slot is one hour; the grade is 80 % points, 20 % cost. Plan agreed with Ishak: Qwen3.5-4B with a LoRA distilled from Opus's answers runs every area locally, Opus takes the unsure ones; switch only if the points match Opus ×1 within noise. The LoRA student is evaluated on the 406 areas whose photos never fed the training set; the 495 team-labelled areas sit on training photos and are reported separately.

## 10 Oct, 21:40: one Opus answer per area is enough; the Haiku-first cascade costs points

Tuned on all 406 labelled areas, strict mode (max 812):

| configuration | points | evening frames (max 608) | $ per photo |
|---|---|---|---|
| Haiku ×3 alone (never flags) | 459 | | 0.010 |
| Opus ×3 | 579 | 421 | 0.36 |
| **Opus ×1 (now in use)** | **587** | **432** | **0.12** |
| Haiku ×3 first, Opus ×1 where unsure, 45 % sent on | 562 | 404 | 0.063 |
| same, 81 % sent on | 586 | 428 | 0.106 |

Three answers averaged never beat one answer on this set (587 vs 579; 145 vs 142 on the 10-photo test), so the frozen config is now one Opus answer per area: three times cheaper, three times faster (289 calls in about a minute), same flag rule (a confident "no"). The cascade frontier is flat: every dollar saved by trusting Haiku's confident answers costs points (−25 at 45 % escalated), because Haiku's confident stock answers are not reliable enough to keep. The cascade stays as the scale story; the test set runs on Opus ×1.

Open-weight models on this laptop (M5, 16 GB, MLX): Qwen3-VL-4B takes 4.7 s per area with two crops (8B: 10 s, 2B: 3 s); a full run would take 20–40 minutes sequentially, so a local first stage cannot meet the 15-minute slot on this machine. Accuracy numbers in `out/experiments.jsonl` (run `local_study`); research notes in `docs/open-source-cascade.md`.

## 10 Oct, 21:00: strict mode frozen: the image alone, no reference pictures, no clock

Measured, Opus v5 ×3, most likely level, unanimous flag:

| | 10 evening photos (max 218) | all 406 areas (max 812) | evening frames (max 608) | $ per photo |
|---|---|---|---|---|
| reference pictures + clock (before) | 140 | 545 | 401 | 0.45 |
| reference pictures, no clock | 139 | | | |
| **image only, no clock (now)** | **142** | **579** (582 before 34 rate-limited answers were filled in) | **421** | **0.37** |

The reference pictures were a Haiku-era crutch; with the mentor rules in the prompt, Opus judges the area better from the photo alone, and it is 20 % cheaper (fewer image tokens). This also satisfies the organisers' "only consider information from the image under analysis" and "not the time of day". The one remaining use of other photos is the fallback for a blocked view (a cart, a person), which the organisers suggested themselves. Strict cost study on the same 10 photos: Opus ×1 145 ($0.12), Haiku ×3 then Opus where unsure 133 ($0.15), Sonnet ×3 then Opus 136 ($0.22), Haiku alone 103 with 8 false alarms. Config: `use_references: false`, `use_time: false`, `prev_from_run_photos: false`.

## 10 Oct, 20:30: what the organisers wrote on Discord (nmoutinhosensei), and what it changes

- "You should only consider information from the image under analysis" (answer to "may the solution use photos taken after the one it predicts?") and "you will get the timestamp but the model should learn from the image itself and not necessarily the time of day."
  - Consequences: no clock times in the prompt; never use another *validation* photo as a reference (the "most recent earlier photo" step now only ever uses the shared training day); and the reference pictures from the training day are being measured: if they buy little, we drop them and run strictly on the image alone. See the next entry for the numbers.
- Stock criteria in their words: empty = nobody would buy what is left; half = staff would take the time to replenish; full = they would not bother; "in case of doubt it is better to under estimate rather than over estimate." Consistent with the mentor rules we use (1–2 small items = empty, a couple of big items = half) and with our lean-low choices.
- `stock_level` and `stock_value` are redundant; they compare one. We keep both consistent.
- The application has to be shown during the 3-minute pitch.
- Open on Discord, unanswered: credits for a vision API; why the brief talks about adding stock up per product (we can show per-product totals in the app).

## 10 Oct, 20:00: cost study, three models on the same 10 evening photos

Cost became a judging criterion. The last photo of 10 cameras (109 labelled areas, max 218) went through Opus, Sonnet and Haiku with the same prompt, references and rules; costs from the tokens actually used.

| configuration | points | wrong-product false / missed | stock under / over | $ per photo |
|---|---|---|---|---|
| Opus ×3 (in use) | 140 | 0 / 0 | 10 / 12 | 0.45 |
| Opus ×1 | 131 | 0 / 0 | 13 / 12 | 0.15 |
| Sonnet ×3 | 123 | 2 / 0 | 15 / 11 | 0.24 |
| Sonnet ×1 | 126 | 2 / 0 | 14 / 11 | 0.08 |
| Haiku ×3 | 97 | 6 / 0 | 13 / 16 | 0.012 |
| Haiku ×1 | 95 | 10 / 0 | 11 / 15 | 0.004 |
| Haiku ×3, Opus ×3 only where Haiku is unsure | 134 | 0 / 0 | 8 / 15 | 0.19 |
| Haiku ×1, Opus ×1 only where Haiku is unsure | 127 | 0 / 0 | 9 / 16 | 0.04 |
| Sonnet ×3, Opus ×3 only where Sonnet is unsure | 143 | 0 / 0 | 9 / 12 | 0.29 |

Reading: the cheap models are fine at stock level but raise false wrong-product alarms (Haiku 6–10 on 109 areas); Opus makes none. Any cascade that lets Opus decide the unsure and the flagged areas keeps the zero false alarms and most of the points. Differences of a few points are noise at this size (95 % interval about ±20). Decision pending Ishak: keep Opus ×3 ($0.45), or switch to a cascade ($0.19–0.29) for the cost criterion.

## 10 Oct, 19:20: full dev set (406 areas) confirms the config

Claude labelled the 198 remaining areas of the last two photos of every camera (Ilies stopped labelling), so every evening frame is now checked by hand: 406 areas, max 812, 304 on the evening frames.

| Run (Opus ×3) | all | evening frames | confident labels | stock under / over |
|---|---|---|---|---|
| **v5, most likely level, unanimous flag (in use)** | **545** | 401 / 608 | **512 / 600** | 25 / 42 |
| v5, leaning towards full ×2 | 558 | 405 / 608 | 501 / 600 | 10 / 50 |
| v5, safest-level rule | 530 | 387 / 608 | 490 / 600 | 38 / 36 |
| v4, most likely level | 475 | 343 / 608 | 427 / 600 | 62 / 31 |

Kept the config: leaning towards "full" gains 13 points on all labels but loses 11 on the confident ones and trades −2 errors for −3 ones; the evening-frame difference is noise. Wrong-product calls: 6 false, 2 missed on 406 areas. The product table got three fixes found while labelling: "cebolinha francesa" is small onions in a net (not chives), banana-pão is plantain, Gold kiwi comes in yellow mesh bags.

## 10 Oct, 18:45: config frozen: Opus v5, three samples, argmax, unanimous flag

Dev set: 208 labelled areas (max 416), 106 of them on the last two shared photos of each camera (the validation proxy).

| Run | all | last-two frames | compliance wrong | stock under / over |
|---|---|---|---|---|
| Opus v4 ×3, argmax | 275 | 139/212 | 6 | 29 / 9 |
| Opus v4 ×3, argmax, p_full ×2 | 306 | 159/212 | 7 | 15 / 11 |
| **Opus v5 ×3, argmax** | **313** | **169/212** | 7 | 10 / 13 |
| Opus v5 ×3, argmax, p_full ×2 | 315 | 162/212 | 7 | 4 / 17 |
| Opus v4 ×3, expected-value rule | 259 | 125/212 | 6 | 37 / 7 |
| Haiku v4 ×3 (flagging off) | 235 | 131/212 | 22 | 25 / 10 |

- **Why v5.** The v4 errors were almost all "model says half, both labellers say full" at 55–75 % coverage: the prompt's 80 % line was stricter than the humans' reading of "a relaxed store". v5 puts the line at about 70 % and names the gaps that still count as full (a few empty pulp cups, an uncovered back row, a bare strip along one edge). Under-estimates fell from 29 to 10 without a decision-layer patch.
- **Why argmax, not expected value.** On these labels the EV rule's lean towards lower levels costs 40–50 points: the model's probabilities are already cautious, so leaning further only adds −2s. Decided on both label sets now (Ilies' and ours agree on this).
- **Why no p_full boost.** +2 points overall is noise (95 % interval ±40) and it trades −2 errors for −3 errors; on the last-two subset it loses 7.
- **Flag rule.** Unanimous "no" across the three samples: 5 false flags, 2 missed, on 208 areas. Three of the five "false flags" look like label mistakes to re-check (6215_7 at 16:24 holds bananas in the orange area; 6594_12 holds onion bags in the chives area at 06:22 and 17:30).
- **Second model on low-confidence areas** (34 areas, Sonnet ×2): no change under argmax (+12 under EV). Logged, not used.
- **Opus latency:** ~3 s per call with references; 624 calls in 98 s at 32 concurrent. 11 of 624 calls hit 429s while a second run shared the limit; the SDK retries, and the time guard plus the fallback chain cover the rest.

## 10 Oct, 18:10: one shared module, Opus for the judging, mentor rules in the prompt

- **Opus 5.5 judges the validation run.** Evidence: on 80 of our labelled areas, Ilies' Opus one-shot answers made 0 false "wrong product" flags and missed 0 real swaps; our Haiku prototype made 5 false flags on the same areas. Compliance errors cost −2 each way, so with Haiku flagging was a wash; with Opus it pays. Cost is not a criterion for us tonight (Ishak, 17:50).
- **Three samples per area, reference order swapped.** Kept from the prototype (it removed three −3 over-estimates on Haiku). Cheaper adaptive sampling is logged as an experiment, not used by default.
- **Prompt v4** carries the mentors' definitions: empty ≤ ~20 % (one or two small items left is empty; one or two large items is half), half 20–80 %, full > ~80 % ("relaxed store"), bare paper/plastic/cardboard = empty, stock counts any product. Compliance stays generic (kind of produce, not variety or packaging), per the organisers ("we care most about whether it's the right fruit in general").
- **Leftover wrong items are non-compliant.** The organisers said one or two leftover items of the wrong product are "for sure non-compliant". The prototype's blanket "empty ⇒ compliant" is replaced by: compliant only when every sample sees no product at all; otherwise the flag rule decides, even when stock is empty.
- **References are causal.** The prototype could show a 17:35 frame an 18:30 "full" reference. Validation frames are the latest of the day, so only earlier photos are ever used as references (also on the dev set, or its score would be optimistic). Reference labels state the time and the measured fill ("emptiest, ~5 % covered") instead of asserting EMPTY/FULL; areas that never went empty get a sentence saying so.
- **Blocked areas** take the verdict of the nearest earlier photo of the same camera (organisers: "the stock behind it"; a shared photo minutes earlier is a good proxy). A cross-camera view via the organisers' shelf map is a later addition.
- **Stock rule (expected value vs most likely) is tuned, not assumed.** On Ilies' labels argmax beat the EV rule for every model; on ours EV beat argmax for the same Opus answers. Decided on the merged dev set with bootstrap intervals; within noise, EV (its errors cost −2, not −3).
- **One module (`shelfcheck.py`)** instead of a `src/` package; the notebook stays as the record of the prototype.

## 10 Oct, 17:50: organisers' answers (via Ishak)

Hand-over: the code is submitted first and frozen; then the organisers give the validation photos, we run our program and hand in the results. Leftover wrong items: non-compliant; "empty" depends on item size (1–2 grapes or bananas: empty; 2 melons: half). Variety: keep it simple, judge the kind of fruit. Blocked view: the stock behind it, via other cameras or the photo taken shortly before. Cost: not a concern now; accuracy and fruit identification first. Dashboards and a testing/decision log requested.

## 10 Oct, 16:00: prototype findings (Haiku 5.5, 105 labelled areas)

Baseline "half, compliant" −64; v1 (no references) 76; v2 (references, 3 samples) 100; v3 (generic product check, unanimous flag) 136; flagging off 139. Stock ≈ 90 % right; false "wrong product" alarms on look-alikes were the main loss. Open question then: does Opus make flagging pay? (Answered above: yes.)

## 10 Oct, 14:00: approach

No training: Claude judges each planogram area from three crops plus same-day references; a cost-aware decision rule turns probabilities into the answer with the best expected points; every area always gets an answer (missing = −5). Open-source detectors (YOLO, DINOv2, SAM) were researched and set aside: nothing transfers to loose produce seen from above without labels.
