"""Tune the cascade "cheap model first, Opus where unsure" on the labelled dev set.

    .venv/bin/python tools/cascade_tune.py                      # Haiku x3 first stage (strict mode), Opus from the cache
    .venv/bin/python tools/cascade_tune.py --first local:mlx-community/Qwen3.5-4B-MLX-4bit   # a local first stage (answers from cache/judge)

Trigger (from the cascade research): escalate when ANY first-stage sample says the product is not a match
(no / cannot_tell), when the samples disagree on the stock level, or when the averaged top stock probability is
below a per-class threshold t[level] (strictest for "full", whose errors cost 3). Non-escalated areas are
compliant by construction. Escalated areas take Opus's answer (1 or 3 samples, strict mode, from the cache).
Prints the frontier (points vs share escalated vs $ per photo), logs each point as an experiment, writes
out/cascade/frontier.json for the board.
"""
import argparse, asyncio, itertools, json, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shelfcheck as sc
from tune import load_day, photo_by_name

OUT = sc.ROOT / "out/cascade"
AREAS_PER_PHOTO = len(sc.AREAS) / len(sc.CAM_AREAS)


def answer_cost(r):
    t = r.get("_tokens") or {}
    return sc.call_cost(r.get("_model", "claude-opus-5-5"), t) if t and r.get("_model", "").startswith("claude") else 0.0


def first_stage_verdict(rs, t):
    """(stock, escalate?) for one area from the first-stage samples."""
    ok = [r for r in rs if r and "error" not in r]
    if not ok:
        return None, True
    if any(r["matches_planned"] != "yes" for r in ok):
        return None, True
    levels = {sc.decide_stock(r["p_empty"], r["p_half"], r["p_full"], "argmax") for r in ok}
    p = [float(np.mean([r[k] for r in ok])) for k in ("p_empty", "p_half", "p_full")]
    stock = ["empty", "half", "full"][int(np.argmax(p))]
    if len(levels) > 1 or max(p) < t[stock]:
        return stock, True
    return stock, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", default="claude-haiku-5-5", help="first-stage model id, or local:<repo> for cached local answers")
    ap.add_argument("--first-samples", type=int, default=3)
    ap.add_argument("--all-labels", action="store_true", help="include the team-labelled areas that are in the LoRA training pool")
    args = ap.parse_args()
    cfg = json.load(open(sc.ROOT / "config.json"))
    assert sc.load_env(), "ANTHROPIC_API_KEY missing (.env)"
    labels = sc.load_labels(); photos = sc.shared_photos(); day = load_day()
    if args.first.startswith("local:") and not args.all_labels:
        labels = {k: v for k, v in labels.items() if v["source"] != "team_all"}
    targets = [(photo_by_name(photos, cam, file), aid) for (cam, file, aid) in labels]
    human = {k for k, v in labels.items() if v["source"] in ("ilies", "both", "conflict")}
    last2 = {k for k, v in labels.items() if v["last2"]}
    fallbacks = {(ph.cam, ph.name, aid): sc.nearest_estimate(ph.cam, aid, ph.ts, day, photos) for ph, aid in targets}
    images = {}

    # first stage
    first_samples = tuple(range(args.first_samples))
    if args.first.startswith("local:"):
        repo = args.first[6:]
        from local_eval import LocalJudge
        lj = LocalJudge.__new__(LocalJudge); lj.repo = repo; lj.cache = sc.CACHE
        first = {}
        for ph, aid in targets:
            img = images.setdefault(ph.key(), sc.load_image(ph))
            a, p = sc.AREAS[aid], sc.planned(aid)
            import base64
            from local_eval import SHORT
            ctx, zoom = sc.jpg(sc.context_crop(img, a)), sc.jpg(sc.rectify(img, a))
            text = SHORT + f"Planned product: {p['en']} ({p['unit']}). Candidates: {'; '.join(sc.candidates(ph.cam) + ['empty container', 'other produce'])}."
            content = [{"type": "text", "text": text}, {"type": "image", "source": {"data": base64.standard_b64encode(ctx).decode()}},
                       {"type": "image", "source": {"data": base64.standard_b64encode(zoom).decode()}}]
            from local_eval import LOCAL_VERSION
            path = sc.CACHE / f"{sc.Judge.key(None, content, 0, 'local', LOCAL_VERSION, repo)}.json"
            if path.exists():
                first[(ph.cam, ph.name, aid, 0)] = json.load(open(path))
        first_samples = (0,)
        print(f"local first stage {repo}: {len(first)} cached answers of {len(targets)} areas (run tools/local_eval.py for the rest)")
        first_cost = 0.0
    else:
        judge = sc.Judge(model=args.first, concurrency=32)
        first = asyncio.run(judge.judge_many(targets, lambda ph, aid, s: sc.build_content(ph, aid, refs=None, swap=(s == 1), img=images.setdefault(ph.key(), sc.load_image(ph)), with_time=False),
                                             samples=first_samples, effort=cfg["effort"], version=cfg["version"], label=f"{args.first} x{args.first_samples} strict"))
        first_cost = sum(answer_cost(r) for r in first.values() if "error" not in r)

    # second stage: Opus strict, from the cache (sample 0 for x1, all three for x3)
    opus = sc.Judge(model="claude-opus-5-5")
    opus_ans = {}
    for ph, aid in targets:
        img = images.setdefault(ph.key(), sc.load_image(ph))
        for s in (0, 1, 2):
            r = opus.cached(sc.build_content(ph, aid, refs=None, swap=(s == 1), img=img, with_time=False), s, cfg["effort"], cfg["version"], "claude-opus-5-5")
            if r: opus_ans[(ph.cam, ph.name, aid, s)] = r
    print(f"Opus strict answers in cache: {sum(1 for k in opus_ans if k[3] == 0)} of {len(targets)} areas")

    def evaluate(t, second_samples):
        res, esc, cost = {}, 0, first_cost
        for ph, aid in targets:
            key = (ph.cam, ph.name, aid)
            rs = [first.get(key + (s,)) for s in first_samples]
            stock, escalate = first_stage_verdict(rs, t)
            if not escalate:
                # a confident, unanimous "yes": keep the first stage's averaged answer, compliant
                ok = [r for r in rs if r and "error" not in r]
                res[key + (0,)] = {**ok[0], "p_empty": float(np.mean([r["p_empty"] for r in ok])), "p_half": float(np.mean([r["p_half"] for r in ok])),
                                   "p_full": float(np.mean([r["p_full"] for r in ok])), "matches_planned": "yes"}
                continue
            esc += 1
            got = False
            for s in range(second_samples):
                r = opus_ans.get(key + (s,))
                if r: res[key + (10 + s,)] = r; cost += answer_cost(r); got = True
            if not got:
                for s, r in zip(first_samples, rs):
                    if r: res[key + (s,)] = r
        pred, _ = sc.predictions(targets, res, (0, 10, 11, 12), "argmax", "unanimous_no", 1.0, fallbacks)
        tot, c = sc.score(pred, labels)
        th, _ = sc.score(pred, {k: labels[k] for k in human}); tl, _ = sc.score(pred, {k: labels[k] for k in last2})
        return {"t": t, "second_samples": second_samples, "points": tot, "points_human": th, "points_last2": tl, "escalated": esc, "share": round(esc / len(targets), 3),
                "cost_per_photo": round(cost / len(targets) * AREAS_PER_PHOTO, 4), "counts": c}

    # reference points: first stage alone, Opus alone
    pred1, _ = sc.predictions(targets, first, first_samples, "argmax", "never", 1.0, fallbacks)
    base = sc.score(pred1, labels)[0]
    predO, _ = sc.predictions(targets, opus_ans, (0, 1, 2), "argmax", "unanimous_no", 1.0, fallbacks)
    predO1, _ = sc.predictions(targets, opus_ans, (0,), "argmax", "unanimous_no", 1.0, fallbacks)
    opus_cost = sum(answer_cost(r) for r in opus_ans.values()) / len(targets) * AREAS_PER_PHOTO
    print(f"\nfirst stage alone (never flags): {base}/{2*len(labels)}  ${first_cost/len(targets)*AREAS_PER_PHOTO:.3f} per photo")
    print(f"Opus x3 alone: {sc.score(predO, labels)[0]}/{2*len(labels)}  ${opus_cost:.3f} per photo | Opus x1 alone: {sc.score(predO1, labels)[0]}/{2*len(labels)}  ${opus_cost/3:.3f}")

    grid = [0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
    rows = []
    for te, th_, tf in itertools.product(grid, grid, grid):
        if not (te <= th_ <= tf): continue
        for ss in (1, 3):
            rows.append(evaluate({"empty": te, "half": th_, "full": tf}, ss))
    rows.sort(key=lambda r: (-r["points"], r["cost_per_photo"]))
    print(f"\n{'t empty/half/full':18} {'Opus x':6} {'points':>9} {'human':>7} {'evening':>8} {'escalated':>10} {'$/photo':>8}  false/missed  under/over")
    shown = set()
    for r in rows[:12] + sorted(rows, key=lambda r: r["cost_per_photo"])[:6]:
        key = (tuple(r["t"].values()), r["second_samples"])
        if key in shown: continue
        shown.add(key); c = r["counts"]
        print(f"{r['t']['empty']:.1f}/{r['t']['half']:.1f}/{r['t']['full']:.1f}        x{r['second_samples']}   {r['points']:4d}/{2*len(labels)}  {r['points_human']:4d}   {r['points_last2']:5d}   {r['share']:7.0%}   ${r['cost_per_photo']:6.3f}   {c['false_flag']}/{c['missed_nc']:<4}  {c['under']}/{c['over']}")
    # frontier: best points for each share band
    frontier = []
    for lo in np.arange(0, 1.0, 0.1):
        band = [r for r in rows if lo <= r["share"] < lo + 0.1]
        if band: frontier.append(max(band, key=lambda r: r["points"]))
    print("\nfrontier (best per escalation band):")
    for r in frontier:
        print(f"  {r['share']:4.0%} escalated -> {r['points']} points, ${r['cost_per_photo']:.3f} per photo (t {r['t']['empty']}/{r['t']['half']}/{r['t']['full']}, Opus x{r['second_samples']})")
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump({"first": args.first, "first_samples": args.first_samples, "n_areas": len(targets), "max_points": 2 * len(labels),
               "first_alone": base, "opus_x3": sc.score(predO, labels)[0], "opus_x1": sc.score(predO1, labels)[0],
               "first_cost_per_photo": round(first_cost / len(targets) * AREAS_PER_PHOTO, 4), "opus_x3_cost_per_photo": round(opus_cost, 4),
               "frontier": frontier, "top": rows[:12]}, open(OUT / f"frontier_{args.first.replace('/', '_').replace(':', '_')}.json", "w"), indent=1)
    for r in frontier:
        sc.log_experiment(run="cascade_tune", hypothesis=f"{args.first} x{args.first_samples} first, Opus x{r['second_samples']} where unsure (t {r['t']['empty']}/{r['t']['half']}/{r['t']['full']}): {r['share']:.0%} escalated",
                          model=f"{args.first}+opus", version=cfg["version"] + " strict", samples=args.first_samples, stock_rule="argmax", alpha=1.0, flag_rule="unanimous_no (Opus only)",
                          dataset=f"labels/dev.csv ({len(labels)} areas)", points=r["points"], max_points=2 * len(labels), points_human=r["points_human"], max_human=2 * len(human),
                          points_last2=r["points_last2"], max_last2=2 * len(last2), counts=r["counts"], cost_per_photo_usd=r["cost_per_photo"], escalated_share=r["share"])


if __name__ == "__main__":
    main()
