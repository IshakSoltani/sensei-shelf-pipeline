"""Measure the coded gates (gates.py) on the training day and choose "careful" thresholds.

    .venv/bin/python tools/gate_tune.py            # features (cached in out/gates/), per-gate sweeps, store-mode replay

Truth: labels/dev.csv (901 hand-checked areas). Thresholds are chosen on the 495 team labels (photos that also feed the
references) and checked on the 406 clean areas (64 held-out photos). "Careful" = on the tuning labels the gate scores at
least as many points as Opus x1 on the very same areas, and its stock call is right at least 97 % of the time.
References are leave-one-photo-out: an area's emptiest / fullest picture and product fingerprint never come from the
photo being judged, and never from the 64 held-out photos.
"""
import itertools, json, sys, time
from pathlib import Path
import cv2
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).resolve().parent))
import shelfcheck as sc
import gates as G

OUT = sc.ROOT / "out/gates"
SV = {"empty": 0.0, "half": 0.5, "full": 1.0}
INV = {0.0: "empty", 0.5: "half", 1.0: "full"}


def points(stock, compliant, lab):
    """Official points for one area (+1/-2 product, +1/-2 under/-3 over stock)."""
    p = 1 if compliant == lab["compliant"] else -2
    d = SV[stock] - lab["stock_value"]
    return p + (1 if d == 0 else (-2 if d < 0 else -3))


def fullness(crop):   # ilies/make_references.py: texture x (1 + saturation spread)
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var() * (1 + cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1].std() / 32.0))


def extract():
    """Crops, own features, photo scores and model answers for every (photo, area) of the day; cached."""
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / "features.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        return z["keys"].tolist(), z["crops"], z["meta"].item()
    cfg = json.load(open(sc.ROOT / "config.json")); photos = sc.shared_photos()
    day = json.load(open(sc.ROOT / "data/day_pass.json")); opus = sc.Judge(model="claude-opus-5-5")
    keys, crops, own, pscore, opus_ans, dayp, fulls = [], [], {}, {}, {}, {}, {}
    t0 = time.time()
    for cam, phs in sorted(photos.items()):
        for ph in phs:
            img = sc.load_image(ph); pscore[f"{cam}|{ph.name}"] = G.photo_score(img, cam)
            for aid in sc.CAM_AREAS[cam]:
                k = f"{cam}|{ph.name}|{aid}"; c = G.area_crop(img, sc.AREAS[aid])
                keys.append(k); crops.append(c); own[k] = G.own_features(c); fulls[k] = fullness(c)
                r = opus.cached(sc.build_content(ph, aid, refs=None, img=img, with_time=False), 0, cfg["effort"], cfg["version"], "claude-opus-5-5")
                if r and "error" not in r:
                    opus_ans[k] = {"p": [r["p_empty"], r["p_half"], r["p_full"]], "matches": r["matches_planned"], "fill": r.get("fill_percent"),
                                   "cost": sc.call_cost("claude-opus-5-5", r.get("_tokens") or {})}
                d = day.get(k)
                if d: dayp[k] = {"fill": d.get("fill_percent"), "matches": d.get("matches_planned")}
        print(f"  {cam}: {len(keys)} areas, {time.time() - t0:.0f}s", flush=True)
    meta = {"own": own, "photo_score": pscore, "opus": opus_ans, "day": dayp, "fullness": fulls, "ts": {f"{c}|{ph.name}": ph.ts for c, phs in photos.items() for ph in phs}}
    np.savez_compressed(cache, keys=np.array(keys, dtype=object), crops=np.stack(crops), meta=np.array(meta, dtype=object))
    return keys, np.stack(crops), meta


def opus_verdict(a):
    """Opus x1 as the Sunday config decides it: most likely level; flag only on a confident 'no'."""
    stock = ["empty", "half", "full"][int(np.argmax(a["p"]))]
    return stock, a["matches"] != "no"


def main():
    t0 = time.time()
    keys, crops, meta = extract()
    idx = {k: i for i, k in enumerate(keys)}
    labs = {f"{c}|{f}|{a}": v for (c, f, a), v in sc.load_labels().items()}
    clean_photos = {k.rsplit("|", 1)[0] for k, v in labs.items() if v["source"] != "team_all"}
    tune_keys = [k for k, v in labs.items() if v["source"] == "team_all" and k in idx]
    test_keys = [k for k, v in labs.items() if v["source"] != "team_all" and k in idx]
    print(f"{len(keys)} areas extracted; labels: {len(tune_keys)} tuning (team, training photos), {len(test_keys)} clean test ({len(clean_photos)} photos)")
    LAB = np.stack([G.lab(c) for c in crops])

    # 1. covered photos: score far below the camera's median
    by_cam = {}
    for pk, s in meta["photo_score"].items(): by_cam.setdefault(pk.split("|")[0], []).append(s)
    med = {c: float(np.median(v)) for c, v in by_cam.items()}
    covered = {pk for pk, s in meta["photo_score"].items() if s < G.COVERED_IF_BELOW * med[pk.split("|")[0]]}
    print(f"covered photos: {sorted(covered)}")

    # 2. references, leave-one-photo-out, never from held-out photos or covered photos
    per_area = {}
    for k in keys:
        cam, name, aid = k.split("|"); per_area.setdefault(aid, []).append(k)
    feats = {}
    for aid, ks in per_area.items():
        pool = [k for k in ks if k.rsplit("|", 1)[0] not in clean_photos and k.rsplit("|", 1)[0] not in covered]
        prod_pool = [k for k in pool if (meta["day"].get(k) or {}).get("matches") == "yes" and ((meta["day"].get(k) or {}).get("fill") or 0) >= 50]
        ks_time = sorted(ks, key=lambda k: meta["ts"][k.rsplit("|", 1)[0]] or 0)
        for j, k in enumerate(ks_time):
            others = [o for o in pool if o != k]
            if not others: continue
            e = min(others, key=lambda o: meta["fullness"][o]); f = max(others, key=lambda o: meta["fullness"][o])
            L = LAB[idx[k]]; cov = G.coverage(L, LAB[idx[e]])
            mask = G.diff_map(L, LAB[idx[e]]) > G.DIFF_T
            fps = [G.fingerprint(crops[idx[o]], G.diff_map(LAB[idx[o]], LAB[idx[e]]) > G.DIFF_T) for o in prod_pool if o != k and o != e]
            fps = [x for x in fps if x is not None]
            ref_fp = np.mean(fps, axis=0) if fps else None
            row = {"cov": cov, "d_empty": G.distance(L, LAB[idx[e]]), "d_full": G.distance(L, LAB[idx[f]]),
                   "empty_ref_fill": (meta["day"].get(e) or {}).get("fill"), "cov_full_ref": G.coverage(LAB[idx[f]], LAB[idx[e]]),
                   "fp": G.fingerprint_distance(G.fingerprint(crops[idx[k]], mask), ref_fp), **meta["own"][k]}
            prev = ks_time[j - 1] if j else None
            if prev and prev.rsplit("|", 1)[0] not in covered:
                row["chg_mean"], row["chg_share"] = G.change(L, LAB[idx[prev]]); row["prev"] = prev
            feats[k] = row
    print(f"gate features for {len(feats)} areas ({time.time() - t0:.0f}s)")

    def sweep(name, cands, fires, answer, grid):
        """Try every threshold combination; keep the careful ones; return the best (most areas answered)."""
        best, rows = None, []
        for th in grid:
            fired = [k for k in cands if fires(feats[k], th)]
            for split, ks in (("tune", tune_keys), ("test", test_keys)):
                pass
            res = {}
            for split, ks in (("tune", tune_keys), ("test", test_keys)):
                fk = [k for k in fired if k in set(ks) and k in meta["opus"]]
                g_pts = sum(points(*answer(k), labs[k]) for k in fk)
                o_pts = sum(points(*opus_verdict(meta["opus"][k]), labs[k]) for k in fk)
                ok = sum(1 for k in fk if SV[answer(k)[0]] == labs[k]["stock_value"])
                res[split] = {"n": len(fk), "gate_pts": g_pts, "opus_pts": o_pts, "stock_acc": ok / len(fk) if fk else None}
            share = len(fired) / len(keys)
            r = {"th": th, "share_all": share, **{f"{s}_{m}": v for s, d in res.items() for m, v in d.items()}}
            rows.append(r)
            t = res["tune"]
            if t["n"] >= 5 and t["gate_pts"] >= t["opus_pts"] and (t["stock_acc"] or 0) >= 0.97:
                if best is None or share > best["share_all"]: best = r
        print(f"\n{name}: {len(rows)} settings tried; careful choice:", "none qualifies" if not best else
              f"th={best['th']}, answers {best['share_all']:.1%} of all areas | tune {best['tune_n']} areas: gate {best['tune_gate_pts']} vs Opus {best['tune_opus_pts']} pts, stock {best['tune_stock_acc']:.1%} | "
              f"clean test {best['test_n']} areas: gate {best['test_gate_pts']} vs Opus {best['test_opus_pts']} pts" + (f", stock {best['test_stock_acc']:.1%}" if best['test_stock_acc'] is not None else ""))
        aggr = max((r for r in rows if r["tune_n"] >= 5 and r["tune_gate_pts"] >= r["tune_opus_pts"] - 0.05 * 2 * r["tune_n"]), key=lambda r: r["share_all"], default=None)
        if aggr:
            print(f"  aggressive (lose <= 5 % of the max on its areas): th={aggr['th']}, {aggr['share_all']:.1%} of areas | tune gate {aggr['tune_gate_pts']} vs Opus {aggr['tune_opus_pts']} | clean test gate {aggr['test_gate_pts']} vs Opus {aggr['test_opus_pts']}")
        return best, aggr, rows

    ok_empty_ref = lambda f: f["empty_ref_fill"] is not None and f["empty_ref_fill"] <= 20
    empty_best, empty_aggr, _ = sweep("3. clearly empty", [k for k in feats if ok_empty_ref(feats[k])],
        lambda f, th: f["cov"] <= th[0] and f["colour"] <= th[1] and f["edges"] <= th[2], lambda k: ("empty", True),
        list(itertools.product([0.03, 0.06, 0.1, 0.15, 0.2, 0.3], [8, 12, 16, 20, 25, 30, 40, 99], [0.05, 0.1, 0.15, 0.2, 0.3, 1.0])))
    full_best, full_aggr, _ = sweep("4+5. clearly full, product confirmed by fingerprint", [k for k in feats if feats[k]["fp"] is not None],
        lambda f, th: f["cov"] >= th[0] and f["d_full"] <= th[1] and f["fp"] <= th[2], lambda k: ("full", True),
        list(itertools.product([0.5, 0.6, 0.7, 0.8, 0.9], [10, 14, 18, 22, 26, 99], [0.2, 0.3, 0.4, 0.5, 0.6, 1.0])))
    unch_best, unch_aggr, _ = sweep("2. nothing changed (reuse Opus's previous answer)", [k for k in feats if "prev" in feats[k] and feats[k]["prev"] in meta["opus"]],
        lambda f, th: f["chg_mean"] <= th[0] and f["chg_share"] <= th[1], lambda k: opus_verdict(meta["opus"][feats[k]["prev"]]),
        list(itertools.product([4, 6, 8, 10, 12, 15, 20], [0.02, 0.05, 0.1, 0.15, 0.2, 0.3])))

    # store-mode replay: camera by camera in time order, each area goes cover -> unchanged -> empty -> full -> Opus
    def replay(sel):
        ans, calls, by_gate = {}, 0, {"covered": 0, "unchanged": 0, "empty": 0, "full": 0, "opus": 0}
        order = sorted(keys, key=lambda k: (k.split("|")[0], meta["ts"][k.rsplit("|", 1)[0]] or 0))
        last = {}
        for k in order:
            aid = k.split("|")[2]; f = feats.get(k); pk = k.rsplit("|", 1)[0]
            if pk in covered and aid in last:
                ans[k] = last[aid]; by_gate["covered"] += 1
            elif sel.get("unch") and f and "prev" in f and f["prev"] in ans and f["chg_mean"] <= sel["unch"][0] and f["chg_share"] <= sel["unch"][1]:
                ans[k] = ans[f["prev"]]; by_gate["unchanged"] += 1
            elif sel.get("empty") and f and ok_empty_ref(f) and f["cov"] <= sel["empty"][0] and f["colour"] <= sel["empty"][1] and f["edges"] <= sel["empty"][2]:
                ans[k] = ("empty", True); by_gate["empty"] += 1
            elif sel.get("full") and f and f["fp"] is not None and f["cov"] >= sel["full"][0] and f["d_full"] <= sel["full"][1] and f["fp"] <= sel["full"][2]:
                ans[k] = ("full", True); by_gate["full"] += 1
            elif k in meta["opus"]:
                ans[k] = opus_verdict(meta["opus"][k]); by_gate["opus"] += 1; calls += 1
            else:
                ans[k] = ("half", True); by_gate["opus"] += 1; calls += 1
            last[aid] = ans[k]
        res = {}
        for split, ks in (("all", list(labs)), ("tune", tune_keys), ("test", test_keys)):
            ks = [k for k in ks if k in ans]
            res[split] = (sum(points(*ans[k], labs[k]) for k in ks), 2 * len(ks))
        return res, by_gate, calls

    cost_call = float(np.mean([a["cost"] for a in meta["opus"].values() if a["cost"]]))
    n_photos = len(meta["photo_score"])
    print(f"\nSTORE MODE replay of the day ({len(keys)} areas, {n_photos} photos; Opus answers from the cache, ${cost_call:.4f} per call)")
    print(f"{'setting':34} {'all 901':>10} {'tune 495':>10} {'clean 406':>10}  {'Opus calls':>10}  {'$/photo':>7}  answered by gate")
    variants = [("Opus x1 on every area", {}),
                ("careful gates", {"unch": unch_best and unch_best["th"], "empty": empty_best and empty_best["th"], "full": full_best and full_best["th"]}),
                ("aggressive gates", {"unch": unch_aggr and unch_aggr["th"], "empty": empty_aggr and empty_aggr["th"], "full": full_aggr and full_aggr["th"]})]
    summary = []
    for name, sel in variants:
        res, by_gate, calls = replay(sel)
        line = f"{name:34} " + " ".join(f"{res[s][0]:5d}/{res[s][1]:<4}" for s in ("all", "tune", "test")) + f"  {calls:5d} ({calls/len(keys):4.0%})  ${calls*cost_call/n_photos:.3f}  {by_gate}"
        print(line); summary.append({"name": name, "sel": sel, "points": res, "calls": calls, "by_gate": by_gate, "cost_per_photo": round(calls * cost_call / n_photos, 4)})
        sc.log_experiment(run="gate_replay", hypothesis=f"store mode: {name}", model="gates+claude-opus-5-5", version="v5 strict", samples=1, stock_rule="argmax", alpha=1.0,
                          flag_rule="unanimous_no", dataset=f"labels/dev.csv ({len(labs)} areas)", points=res["all"][0], max_points=res["all"][1],
                          points_clean=res["test"][0], max_clean=res["test"][1], cost_per_photo_usd=round(calls * cost_call / n_photos, 4), opus_share=round(calls / len(keys), 3), by_gate=by_gate)
    json.dump({"thresholds": {"empty": empty_best, "full": full_best, "unchanged": unch_best, "empty_aggr": empty_aggr, "full_aggr": full_aggr, "unchanged_aggr": unch_aggr},
               "covered": sorted(covered), "replay": summary}, open(OUT / "gate_tune.json", "w"), indent=1, default=str)
    json.dump(feats, open(OUT / "gate_features.json", "w"), default=float)
    print(f"\n-> {OUT}/gate_tune.json ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
