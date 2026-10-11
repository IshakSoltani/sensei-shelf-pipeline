"""Dev run and tuning: judge the labelled areas with references, score every decision rule, log the experiments.

    .venv/bin/python tools/tune.py --model claude-opus-5-5 --samples 3
    .venv/bin/python tools/tune.py --model claude-opus-5-5 --samples 3 --second-model claude-sonnet-5-5
    .venv/bin/python tools/tune.py --model claude-haiku-5-5 --samples 3 --version v4

Needs labels/dev.csv (tools/merge_labels.py) and data/day_pass.json (tools/day_pass.py). References are causal:
only photos earlier than the judged one. Writes out/tune/<run>.json (per-area table for the dashboard),
out/tune/sheets/<cam>_<ts>_<run>.jpg (contact sheets) and appends to out/experiments.jsonl.
"""
import argparse, asyncio, json, sys, time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shelfcheck as sc

OUT = sc.ROOT / "out/tune"
STOCK_RULES = (("ev", 1.0), ("argmax", 1.0), ("argmax", 1.5), ("argmax", 2.0), ("argmax", 2.5), ("argmax", 3.0))  # (rule, p_full boost)
FLAG_RULES = ("unanimous_no", "majority_no", "never")
COLOR = {"empty": (0, 0, 230), "half": (0, 170, 255), "full": (0, 200, 0)}


def load_day():
    raw = json.load(open(sc.DATA / "day_pass.json"))
    return {tuple(k.split("|")) + (0,): v for k, v in raw.items()}


def photo_by_name(photos, cam, name):
    return next(ph for ph in photos[cam] if ph.name == name)


def low_confidence(rs, thresh=0.6):
    """True when the samples disagree on stock or product, or the averaged top stock probability is low."""
    rs = [r for r in rs if r and "error" not in r]
    if not rs:
        return True
    stocks = {sc.decide_stock(r["p_empty"], r["p_half"], r["p_full"]) for r in rs}
    matches = {r["matches_planned"] for r in rs}
    top = max(float(np.mean([r[k] for r in rs])) for k in ("p_empty", "p_half", "p_full"))
    return len(stocks) > 1 or len(matches) > 1 or top < thresh


def sheet(photos, cam, name, results, samples, stock_rule, flag_rule, fallbacks, labels, path, alpha=1.0):
    """Contact sheet: every area outlined in the colour of its verdict; 'WRONG' = flagged; '!' = disagrees with the label."""
    ph = photo_by_name(photos, cam, name)
    im = sc.load_image(ph)
    for aid in sc.CAM_AREAS[cam]:
        rs = [results.get((cam, name, aid, s)) for s in samples]
        if not any(rs):
            continue
        stock, compliant, note = sc.decide(rs, stock_rule, flag_rule, alpha, fallbacks.get((cam, name, aid)))
        q = sc.quad(sc.AREAS[aid]).astype(np.int32)
        cv2.polylines(im, [q], True, COLOR[stock] if not note else (255, 255, 255), 3)
        lab = labels.get((cam, name, aid))
        wrong = lab and (lab["stock_value"] != sc.STOCK_VALUE[stock] or lab["compliant"] != compliant)
        text = aid.split("_")[1] + " " + stock[0].upper() + ("" if compliant else " WRONG") + (" ?" if note else "") + (" !" if wrong else "")
        x, y = q[0]; y = max(y, 14)
        cv2.putText(im, text, (int(x) + 3, int(y) + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 4)
        cv2.putText(im, text, (int(x) + 3, int(y) + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.imwrite(str(path), im, [cv2.IMWRITE_JPEG_QUALITY, 80])


def evaluate(run, targets, results, samples, fallbacks, labels, judge, args, note=""):
    """Score the 2x4 rule grid on all / confident / last-two-frames labels; log each as an experiment; return the table."""
    sure = {k: v for k, v in labels.items() if not v["uncertain"]}
    last2 = {k: v for k, v in labels.items() if v["last2"]}
    n_photos_equiv = len(targets) / (len(sc.AREAS) / len(sc.CAM_AREAS))  # areas judged, in whole-photo equivalents
    usage = judge.usage_summary()
    rows = []
    print(f"\n{run}: {len(labels)} labelled areas (max {2 * len(labels)}), {len(sure)} confident, {len(last2)} on last-two frames")
    print(f"{'stock':11} {'flag':13} {'all':>12} {'95% interval':>14} {'confident':>12} {'last2':>10}   id ok/wrong  false/missed  stock ok/under/over")
    for sr, alpha in STOCK_RULES:
        for fr in FLAG_RULES:
            pred, notes = sc.predictions(targets, results, samples, sr, fr, alpha, fallbacks)
            tot, c = sc.score(pred, labels)
            mean, lo, hi = sc.bootstrap(pred, labels, n=1000)
            ts, _ = sc.score(pred, sure); tl, _ = sc.score(pred, last2)
            name = sr + (f"x{alpha}" if alpha != 1.0 else "")
            print(f"{name:11} {fr:13} {tot:5d}/{2*len(labels):<5} [{lo:4d}, {hi:4d}]   {ts:4d}/{2*len(sure):<5} {tl:4d}/{2*len(last2):<5}"
                  f"   {c['id_ok']}/{c['id_wrong']:<3}      {c['false_flag']}/{c['missed_nc']:<3}     {c['stock_ok']}/{c['under']}/{c['over']}")
            entry = sc.log_experiment(
                run=run, hypothesis=note or f"{args.model} {args.version} x{len(samples)} samples, references causal",
                model=args.model, version=args.version, samples=len(samples), stock_rule=sr, alpha=alpha, flag_rule=fr,
                dataset=f"labels/dev.csv ({len(labels)} areas)", points=tot, max_points=2 * len(labels), ci95=[lo, hi],
                points_confident=ts, max_confident=2 * len(sure), points_last2=tl, max_last2=2 * len(last2),
                counts=c, fallbacks=len(notes), calls=usage["calls"], api_errors=usage["errors"], cost_usd=usage["cost"],
                cost_per_photo_usd=round(usage["cost"] / max(n_photos_equiv, 1e-9), 4) if usage["cost"] else None,
                mean_seconds_per_call=usage["mean_seconds"])
            rows.append(entry)
    return rows


def per_area_table(targets, results, samples, fallbacks, labels, stock_rule, flag_rule, alpha=1.0):
    out = []
    for ph, aid in targets:
        rs = [results.get((ph.cam, ph.name, aid, s)) for s in samples]
        ok = [r for r in rs if r and "error" not in r]
        stock, compliant, note = sc.decide(rs, stock_rule, flag_rule, alpha, fallbacks.get((ph.cam, ph.name, aid)))
        lab = labels.get((ph.cam, ph.name, aid))
        avg = (lambda k: round(float(np.mean([r[k] for r in ok])), 3)) if ok else (lambda k: None)
        out.append({"cam": ph.cam, "file": ph.name, "ts": ph.ts, "hhmm": sc.hhmm(ph.ts), "area": aid, "planned": sc.planned(aid)["en"],
                    "stock": stock, "compliant": compliant, "note": note,
                    "p": [avg("p_empty"), avg("p_half"), avg("p_full")], "fill": avg("fill_percent"), "p_match": avg("p_match"),
                    "matches": [r["matches_planned"] for r in ok], "seen": [r["visible_product"] for r in ok],
                    "usable": [r["image_usable"] for r in ok], "obs": [r["observations"] for r in ok],
                    "low_confidence": low_confidence(rs),
                    "label_stock": next((k for k, v in sc.STOCK_VALUE.items() if lab and v == lab["stock_value"]), None) if lab else None,
                    "label_compliant": lab["compliant"] if lab else None, "label_uncertain": lab["uncertain"] if lab else None,
                    "points": sc.score({ph.cam: {ph.name: {aid: {"planogram_compliant": compliant, "stock_level": stock, "stock_value": sc.STOCK_VALUE[stock]}}}},
                                       {(ph.cam, ph.name, aid): lab})[0] if lab else None})
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--version", default="v4")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--second-model", default=None, help="extra 2 samples from this model on low-confidence areas")
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--no-sheets", action="store_true")
    ap.add_argument("--no-refs", action="store_true", help="strict mode: no reference pictures from other photos")
    ap.add_argument("--no-time", action="store_true", help="no clock times in the prompt")
    ap.add_argument("--photos", type=int, default=None, help="only the last photo of the first N fully labelled cameras")
    args = ap.parse_args()
    assert sc.load_env(), "ANTHROPIC_API_KEY missing (.env)"

    labels = sc.load_labels()
    photos = sc.shared_photos()
    day = load_day()
    if args.photos:
        picks = []
        for cam, phs in sorted(photos.items()):
            last = phs[-1]
            if all((cam, last.name, aid) in labels for aid in sc.CAM_AREAS[cam]):
                picks.append((cam, last.name))
        picks = set(picks[:args.photos])
        labels = {k: v for k, v in labels.items() if (k[0], k[1]) in picks}
    targets = [(photo_by_name(photos, cam, file), aid) for (cam, file, aid) in labels]
    judge = sc.Judge(model=args.model, concurrency=args.concurrency)
    run = f"{args.model.replace('claude-', '').replace('-5-5', '')}_{args.version}_s{args.samples}" + \
          ("_norefs" if args.no_refs else "") + ("_notime" if args.no_time else "") + (f"_{args.photos}ph" if args.photos else "")
    OUT.mkdir(parents=True, exist_ok=True); (OUT / "sheets").mkdir(exist_ok=True)

    print(f"{run}: {len(targets)} labelled areas in {len({(ph.cam, ph.name) for ph, _ in targets})} photos; building causal references")
    refs = {}
    for ph, aid in targets:
        if args.no_refs:
            refs[(ph.cam, ph.name, aid)] = None; continue
        r = sc.build_refs(ph.cam, aid, ph.ts, day, photos) or {}
        r["prev"] = sc.prev_ref(ph.cam, aid, ph.ts, photos)
        refs[(ph.cam, ph.name, aid)] = r
    print(f"  references: {sum(1 for r in refs.values() if r and r.get('empty'))} with an empty anchor, "
          f"{sum(1 for r in refs.values() if r and r.get('full'))} with a full anchor, {sum(1 for r in refs.values() if r and r.get('prev'))} with a previous photo")
    fallbacks = {(ph.cam, ph.name, aid): sc.nearest_estimate(ph.cam, aid, ph.ts, day, photos) for ph, aid in targets}

    samples = tuple(range(args.samples))
    t0 = time.time()
    results = await judge.judge_many(targets, lambda ph, aid, s: sc.build_content(ph, aid, refs=refs[(ph.cam, ph.name, aid)], swap=(s == 1), with_time=not args.no_time),
                                     samples=samples, effort=args.effort, version=args.version, label=run)
    errs = {}
    for c in judge.calls:
        if "error" in c:
            errs[c["error"][:90]] = errs.get(c["error"][:90], 0) + 1
    if errs:
        print("API errors:", errs)
    table = evaluate(run, targets, results, samples, fallbacks, labels, judge, args)
    best = max(table, key=lambda e: e["points"])
    print(f"\nbest: {best['stock_rule']} x{best['alpha']} + {best['flag_rule']} = {best['points']}/{best['max_points']}  "
          f"(cost ${judge.cost():.2f}, {judge.usage_summary()['calls']} calls, {time.time() - t0:.0f}s)")
    cfg = json.load(open(sc.ROOT / "config.json")) if (sc.ROOT / "config.json").exists() else {}
    if cfg.get("model") == args.model and cfg.get("version") == args.version and cfg.get("samples") == args.samples:
        # the run in use: the per-area table and the sheets follow the frozen rules, not the best of the grid
        best = next((e for e in table if e["stock_rule"] == cfg["stock_rule"] and e["flag_rule"] == cfg["flag_rule"] and e["alpha"] == cfg.get("alpha", 1.0)), best)
        print(f"rules in use (config.json): {best['stock_rule']} x{best['alpha']} + {best['flag_rule']} = {best['points']}/{best['max_points']}")
    json.dump({"run": run, "args": vars(args), "best": {"stock_rule": best["stock_rule"], "alpha": best["alpha"], "flag_rule": best["flag_rule"]},
               "areas": per_area_table(targets, results, samples, fallbacks, labels, best["stock_rule"], best["flag_rule"], best["alpha"]),
               "usage": judge.usage_summary()}, open(OUT / f"{run}.json", "w"), indent=0)
    if not args.no_sheets:
        for cam, name in sorted({(ph.cam, ph.name) for ph, _ in targets}):
            sheet(photos, cam, name, results, samples, best["stock_rule"], best["flag_rule"], fallbacks, labels,
                  OUT / "sheets" / f"{cam}_{name.replace('.jpg', '')}_{run}.jpg", best["alpha"])

    if args.second_model:
        low = [(ph, aid) for ph, aid in targets if low_confidence([results.get((ph.cam, ph.name, aid, s)) for s in samples])]
        run2 = f"{run}+{args.second_model.replace('claude-', '').replace('-5-5', '')}x2"
        print(f"\n{run2}: {len(low)} low-confidence areas get 2 extra samples from {args.second_model}")
        judge2 = sc.Judge(model=args.second_model, concurrency=args.concurrency)
        extra = await judge2.judge_many(low, lambda ph, aid, s: sc.build_content(ph, aid, refs=refs[(ph.cam, ph.name, aid)], swap=(s == 11)),
                                        samples=(10, 11), effort=args.effort, version=args.version, label=run2)
        combined = dict(results); combined.update(extra)
        all_samples = samples + (10, 11)
        for c in judge2.calls:
            judge.calls.append(c)
        args2 = argparse.Namespace(**{**vars(args), "model": f"{args.model}+{args.second_model}"})
        table2 = evaluate(run2, targets, combined, all_samples, fallbacks, labels, judge, args2,
                          note=f"low-confidence areas ({len(low)}) get 2 extra samples from {args.second_model}")
        best2 = max(table2, key=lambda e: e["points"])
        print(f"\nsecond model: best {best2['points']}/{best2['max_points']} vs {best['points']} without it "
              f"({'kept' if best2['points'] > best['points'] else 'not kept'})")
        json.dump({"run": run2, "args": vars(args2), "best": {"stock_rule": best2["stock_rule"], "alpha": best2["alpha"], "flag_rule": best2["flag_rule"]},
                   "areas": per_area_table(targets, combined, all_samples, fallbacks, labels, best2["stock_rule"], best2["flag_rule"], best2["alpha"]),
                   "usage": judge.usage_summary(), "low_confidence_areas": len(low)}, open(OUT / f"{run2}.json", "w"), indent=0)


if __name__ == "__main__":
    asyncio.run(main())
