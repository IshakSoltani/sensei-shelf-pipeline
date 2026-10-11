"""Cascade test on N records of Ilies' dataset export (ilies/dataset/labels.csv): the fine-tuned Qwen judges first; its
answer is kept only when it says the planned product is there AND its top stock probability clears the bar in
config.json (cascade.escalate); every other area goes to Opus (the frozen three-picture call). Scored against the
export's own labels (human where the sheet has one, Opus-v3 guesses elsewhere) and against our hand labels (labels/dev.csv)
where the two overlap.

    .venv/bin/python tools/cascade_test.py --n 200 --seed 0
"""
import argparse, asyncio, csv, json, random, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).resolve().parent))
import shelfcheck as sc
from tune import load_day, photo_by_name
from local_eval import LocalJudge
from cascade_tune import first_stage_verdict, answer_cost, AREAS_PER_PHOTO

EXPORT = sc.ROOT / "ilies/dataset/labels.csv"
SV = {"empty": 0, "half": 0.5, "full": 1}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--scratch", default="/tmp/cascade_test")
    ap.add_argument("--opus-samples", type=int, default=1, help="Opus answers per area (1 = Sunday config, 3 = the earlier three-call setup)")
    args = ap.parse_args()
    cfg = json.load(open(sc.ROOT / "config.json")); cas = cfg["cascade"]; t = cas["escalate"]
    assert sc.load_env(), "ANTHROPIC_API_KEY missing (.env)"
    photos = sc.shared_photos(); day = load_day(); ours = sc.load_labels()
    rows = [r for r in csv.DictReader(open(EXPORT)) if r["covered"] != "yes" and r["stock_level"] in SV and r["human_stock_level"] != "covered"]
    random.Random(args.seed).shuffle(rows); rows = rows[:args.n]
    targets, lab_export, lab_human, lab_ours = [], {}, {}, {}
    for r in rows:
        ph = photo_by_name(photos, r["camera"], r["photo"]); key = (r["camera"], r["photo"], r["area_id"])
        targets.append((ph, r["area_id"]))
        hs, hp = r["human_stock_level"], r["human_right_product"]
        lab_export[key] = {"stock_value": SV[hs] if hs in SV else SV[r["stock_level"]], "compliant": (hp == "yes") if hp else r["right_product"] == "yes"}
        if hs in SV:
            lab_human[key] = {"stock_value": SV[hs], "compliant": (hp == "yes") if hp else r["right_product"] == "yes"}
        if key in ours:
            lab_ours[key] = ours[key]
    print(f"{len(targets)} records from {EXPORT.name} (seed {args.seed}): {len(lab_human)} with a human label in the sheet, {len(lab_ours)} with one of our hand labels; "
          f"{len({(ph.cam, ph.name) for ph, _ in targets})} photos; bar: keep Qwen only if product matches and top probability >= {t}")
    images = {}
    # 1. the local judge on every record (cached after the first run)
    judge = LocalJudge(cas["first"].replace("local:", ""), adapter=cas.get("adapter")); Path(args.scratch).mkdir(parents=True, exist_ok=True)
    first, t0 = {}, time.time()
    for i, (ph, aid) in enumerate(targets, 1):
        img = images.setdefault(ph.key(), sc.load_image(ph))
        first[(ph.cam, ph.name, aid, 0)] = judge.judge(ph, aid, img, args.scratch)
        if i % 25 == 0: print(f"  local {i}/{len(targets)}, {time.time() - t0:.0f}s", flush=True)
    for r in first.values():   # probabilities as fractions, like Opus's
        if "error" not in r and r["p_empty"] + r["p_half"] + r["p_full"] > 1.5:
            for k in ("p_empty", "p_half", "p_full"): r[k] = r[k] / 100.0
    print(f"local: {len(first)} answers in {time.time() - t0:.0f}s, mean {np.mean(judge.seconds) if judge.seconds else 0:.1f}s per new call")
    # 2. Opus x1 (frozen call) for every record: from the cache, the API for the rest
    opus = sc.Judge(model="claude-opus-5-5", concurrency=16)
    S = tuple(range(args.opus_samples)); opus_ans, missing = {}, set()
    for ph, aid in targets:
        img = images[ph.key()]
        for s_ in S:
            r = opus.cached(sc.build_content(ph, aid, refs=None, swap=(s_ == 1), img=img, with_time=False), s_, cfg["effort"], cfg["version"], "claude-opus-5-5")
            if r and "error" not in r: opus_ans[(ph.cam, ph.name, aid, s_)] = r
            else: missing.add((ph, aid))
    if missing:
        print(f"Opus x{len(S)}: {len(missing)} records with answers not in the cache, calling the API")
        got = asyncio.run(opus.judge_many(sorted(missing, key=lambda x: (x[0].cam, x[0].name, x[1])), lambda ph, aid, s_: sc.build_content(ph, aid, refs=None, swap=(s_ == 1), img=images[ph.key()], with_time=False),
                                          samples=S, effort=cfg["effort"], version=cfg["version"], label="cascade test"))
        opus_ans.update({k: v for k, v in got.items() if v and "error" not in v})
    fallbacks = {(ph.cam, ph.name, aid): sc.nearest_estimate(ph.cam, aid, ph.ts, day, photos) for ph, aid in targets}

    def run(thr, n_opus=1):
        res, esc, cost = {}, 0, 0.0
        for ph, aid in targets:
            key = (ph.cam, ph.name, aid); r = first.get(key + (0,))
            if thr == "never":   # the local model alone, whatever it says
                stock, escalate = (None, False) if r and "error" not in r else (None, True)
            else:
                stock, escalate = first_stage_verdict([r], thr) if thr else (None, True)
            if not escalate:
                res[key + (0,)] = r if thr == "never" else {**r, "matches_planned": "yes"}; continue
            esc += 1; got = False
            for s_ in range(n_opus):
                o = opus_ans.get(key + (s_,))
                if o: res[key + (10 + s_,)] = o; cost += answer_cost(o); got = True
            if not got and r and "error" not in r: res[key + (0,)] = r
        pred, _ = sc.predictions(targets, res, (0, 10, 11, 12), "argmax", "unanimous_no", 1.0, fallbacks)
        return pred, esc, cost

    def report(name, pred, esc, cost):
        out = {"name": name, "escalated": esc, "share": round(esc / len(targets), 3), "cost_per_photo": round(cost / len(targets) * AREAS_PER_PHOTO, 4)}
        for lname, labs in (("export", lab_export), ("human", lab_human), ("ours", lab_ours)):
            if labs: tot, c = sc.score(pred, labs); out[lname] = {"points": tot, "max": 2 * len(labs), "counts": c}
        e = out["export"]["counts"]
        line = f"{name:44} {out['export']['points']:4d}/{out['export']['max']:<4} {out.get('human', {}).get('points', '-'):>5}/{out.get('human', {}).get('max', '-'):<4} {out.get('ours', {}).get('points', '-'):>5}/{out.get('ours', {}).get('max', '-'):<4}  {esc:3d} ({out['share']:4.0%})  ${out['cost_per_photo']:.3f}   {e['under']}/{e['over']:<4} {e['false_flag']}/{e['missed_nc']}"
        print(line); return out
    print(f"\n{'configuration':44} {'export':>9} {'human':>10} {'ours':>10}  {'to Opus':>10}  {'$/photo':>7}  under/over false/missed")
    results = []
    results.append(report("Qwen + adapter alone (never escalates)", *run("never")))
    results.append(report(f"cascade: Qwen if product ok and top p >= {t['full']:.2f}, else Opus x{len(S)}", *run(t, len(S))))
    results.append(report(f"Opus x{len(S)} alone" + (" (three-call setup)" if len(S) == 3 else " (Sunday config)"), *run(None, len(S))))
    import collections
    tops = collections.Counter(round(max(r["p_empty"], r["p_half"], r["p_full"]), 2) for r in first.values() if "error" not in r)
    print("\nthe student's top probability, over the", len(targets), "records:", dict(sorted(tops.items())))
    print("product answer:", dict(collections.Counter(r["matches_planned"] for r in first.values() if "error" not in r)))
    (sc.ROOT / "out/cascade").mkdir(parents=True, exist_ok=True)
    json.dump({"n": len(targets), "seed": args.seed, "bar": t, "results": results}, open(sc.ROOT / f"out/cascade/test{args.n}_seed{args.seed}_opus{len(S)}.json", "w"), indent=1)
    for o in results:
        sc.log_experiment(run="cascade_test", hypothesis=f"{o['name']}, {len(targets)} records of ilies/dataset (seed {args.seed})", model=f"{cas['first']}+{Path(cas['adapter']).name}+opus",
                          version=cfg["version"] + " strict", samples=1, stock_rule="argmax", alpha=1.0, flag_rule="unanimous_no (Opus only)",
                          dataset=f"ilies/dataset/labels.csv ({len(targets)} records; {len(lab_human)} human, {len(lab_ours)} ours)", points=o["export"]["points"], max_points=o["export"]["max"],
                          points_human=o.get("human", {}).get("points"), max_human=o.get("human", {}).get("max"), points_ours=o.get("ours", {}).get("points"), max_ours=o.get("ours", {}).get("max"),
                          counts=o["export"]["counts"], cost_per_photo_usd=o["cost_per_photo"], escalated_share=o["share"])


if __name__ == "__main__":
    main()
