"""The coded gates (gates.py) in front of TODAY's Opus config, replayed offline. No API call is ever made.

    .venv/bin/python tools/gate_replay_now.py        (needs out/gates/ from tools/gate_tune.py)

Opus step = config.json as frozen for Sunday: prompt v5, x1, stock from Opus's own fill estimate (fill:35:65), flag on a
confident "no", same-kind override. Opus answers are read from cache/judge by their content hash (Judge.key), never asked.

Two settings, both measured through the official points on labels/dev.csv (901 areas):
  scored run  only gates that look at the photo under analysis (covered, clearly empty, clearly full, product
              fingerprint); "nothing changed" needs an earlier photo of the same day, which the organisers rule out
  store mode  every gate, cameras replayed in time order (a real store has every earlier photo)
Thresholds are re-chosen against today's Opus verdict on the 495 team labels and checked on the 406 clean ones.
"""
import itertools, json, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).resolve().parent))
import shelfcheck as sc
from gate_tune import points, OUT

CFG = json.load(open(sc.ROOT / "config.json"))
MODEL = CFG["model"]


def cached_answer(ph, aid, img):
    """The saved Opus answer for this area under the frozen config, or None. Pure file lookup."""
    content = sc.build_content(ph, aid, refs=None, img=img, with_time=False)
    path = sc.CACHE / f"{sc.Judge.key(None, content, 0, CFG['effort'], CFG['version'], MODEL)}.json"
    return json.load(open(path)) if path.exists() else None


def load_answers():
    """{key: full Opus answer} for every area of the shared day; cached in out/gates/opus_full.json."""
    cache = OUT / "opus_full.json"
    if cache.exists():
        return json.load(open(cache))
    ans, t0 = {}, time.time()
    for cam, phs in sorted(sc.shared_photos().items()):
        for ph in phs:
            img = sc.load_image(ph)
            for aid in sc.CAM_AREAS[cam]:
                r = cached_answer(ph, aid, img)
                if r and "error" not in r:
                    ans[f"{cam}|{ph.name}|{aid}"] = r
        print(f"  {cam}: {len(ans)} answers found ({time.time() - t0:.0f}s)", flush=True)
    json.dump(ans, open(cache, "w"))
    return ans


def opus_now(r, aid):
    """Today's decision for one area from one Opus answer: (stock, compliant)."""
    stock, comp, _ = sc.decide([r], CFG["stock_rule"], CFG["flag_rule"], CFG["alpha"])
    if not comp and sc.SAME_KIND_OVERRIDE and sc.same_kind(sc.planned(aid)["en"], r.get("visible_product", "")):
        comp = True
    return stock, comp


def main():
    t0 = time.time()
    feats = json.load(open(OUT / "gate_features.json"))
    meta = np.load(OUT / "features.npz", allow_pickle=True)["meta"].item()
    covered = set(json.load(open(OUT / "gate_tune.json"))["covered"])
    raw = load_answers()
    opus = {k: opus_now(r, k.split("|")[2]) for k, r in raw.items()}
    cost = {k: sc.call_cost(r.get("_model", MODEL), r.get("_tokens") or {}) for k, r in raw.items()}
    cost_call = float(np.mean(list(cost.values())))
    keys = sorted(feats)
    labs = {f"{c}|{f}|{a}": v for (c, f, a), v in sc.load_labels().items()}
    tune = [k for k, v in labs.items() if v["source"] == "team_all" and k in opus]
    test = [k for k, v in labs.items() if v["source"] != "team_all" and k in opus]
    n_photos = len(meta["photo_score"])
    print(f"{len(keys)} areas on {n_photos} photos; Opus answers found for {len(opus)}; ${cost_call:.4f} per Opus call")
    print(f"labels: {len(tune)} tuning (team) + {len(test)} clean test")

    ok_empty_ref = lambda f: f["empty_ref_fill"] is not None and f["empty_ref_fill"] <= 20
    GATES = {
        "empty": ([k for k in keys if ok_empty_ref(feats[k])],
                  lambda f, th: f["cov"] <= th[0] and f["colour"] <= th[1] and f["edges"] <= th[2], lambda k: ("empty", True),
                  list(itertools.product([0.03, 0.06, 0.1, 0.15, 0.2, 0.3], [8, 12, 16, 20, 25, 30, 40, 99], [0.05, 0.1, 0.15, 0.2, 0.3, 1.0]))),
        "full": ([k for k in keys if feats[k]["fp"] is not None],
                 lambda f, th: f["cov"] >= th[0] and f["d_full"] <= th[1] and f["fp"] <= th[2], lambda k: ("full", True),
                 list(itertools.product([0.5, 0.6, 0.7, 0.8, 0.9], [10, 14, 18, 22, 26, 99], [0.2, 0.3, 0.4, 0.5, 0.6, 1.0]))),
        "unchanged": ([k for k in keys if "prev" in feats[k] and feats[k]["prev"] in opus],
                      lambda f, th: f["chg_mean"] <= th[0] and f["chg_share"] <= th[1], lambda k: opus[feats[k]["prev"]],
                      list(itertools.product([4, 6, 8, 10, 12, 15, 20], [0.02, 0.05, 0.1, 0.15, 0.2, 0.3]))),
    }

    def sweep(name):
        cands, fires, answer, grid = GATES[name]
        careful = aggressive = None
        for th in grid:
            fired = [k for k in cands if fires(feats[k], th)]
            r = {"th": th, "share": len(fired) / len(keys)}
            for split, ks in (("tune", set(tune)), ("test", set(test))):
                fk = [k for k in fired if k in ks]
                r[split] = {"n": len(fk), "gate": sum(points(*answer(k), labs[k]) for k in fk),
                            "opus": sum(points(*opus[k], labs[k]) for k in fk),
                            "acc": float(np.mean([sc.STOCK_VALUE[answer(k)[0]] == labs[k]["stock_value"] for k in fk])) if fk else None}
            t = r["tune"]
            if t["n"] >= 5 and t["gate"] >= t["opus"] and (t["acc"] or 0) >= 0.97 and (careful is None or r["share"] > careful["share"]):
                careful = r
            if t["n"] >= 5 and t["gate"] >= t["opus"] - 0.1 * t["n"] and (aggressive is None or r["share"] > aggressive["share"]):
                aggressive = r
        for tag, r in (("careful", careful), ("aggressive", aggressive)):
            print(f"  {name:9} {tag:10} " + ("none qualifies" if not r else
                  f"th={r['th']} answers {r['share']:.1%} | tune {r['tune']['n']}: gate {r['tune']['gate']} vs Opus {r['tune']['opus']} "
                  f"| clean {r['test']['n']}: gate {r['test']['gate']} vs Opus {r['test']['opus']}"))
        return careful, aggressive

    print("\nper-gate sweeps against today's Opus (careful: never below Opus on the tuning labels and >= 97 % stock right;"
          " aggressive: within 0.1 point per area of Opus on the tuning labels)")
    th = {g: sweep(g) for g in ("empty", "full", "unchanged")}

    def replay(sel, store):
        ans, by, calls, last = {}, dict.fromkeys(["covered", "unchanged", "empty", "full", "opus", "default"], 0), 0, {}
        for k in sorted(keys, key=lambda k: (k.split("|")[0], meta["ts"][k.rsplit("|", 1)[0]] or 0)):
            aid, f, pk = k.split("|")[2], feats[k], k.rsplit("|", 1)[0]
            if store and pk in covered and aid in last:
                ans[k] = last[aid]; by["covered"] += 1
            elif store and sel.get("unchanged") and "prev" in f and f["prev"] in ans and GATES["unchanged"][1](f, sel["unchanged"]):
                ans[k] = ans[f["prev"]]; by["unchanged"] += 1
            elif sel.get("empty") and ok_empty_ref(f) and GATES["empty"][1](f, sel["empty"]):
                ans[k] = ("empty", True); by["empty"] += 1
            elif sel.get("full") and f["fp"] is not None and GATES["full"][1](f, sel["full"]):
                ans[k] = ("full", True); by["full"] += 1
            elif k in opus:
                ans[k] = opus[k]; by["opus"] += 1; calls += 1
            else:
                ans[k] = ("half", True); by["default"] += 1
            last[aid] = ans[k]
        res = {s: (sum(points(*ans[k], labs[k]) for k in ks), 2 * len(ks)) for s, ks in (("all", tune + test), ("tune", tune), ("clean", test))}
        return res, by, calls

    pick = lambda i, names: {g: th[g][i]["th"] for g in names if th[g][i]}
    variants = [("Opus x1 on every area (today's config)", {}, False),
                ("scored run + careful gates", pick(0, ("empty", "full")), False),
                ("scored run + aggressive gates", pick(1, ("empty", "full")), False),
                ("store mode + careful gates", pick(0, ("empty", "full", "unchanged")), True),
                ("store mode + aggressive gates", pick(1, ("empty", "full", "unchanged")), True)]
    print(f"\nREPLAY ({len(keys)} areas, {n_photos} photos)")
    print(f"{'setting':42} {'all':>10} {'tune':>9} {'clean':>9} {'Opus calls':>12} {'$/photo':>8}")
    rows = []
    for name, sel, store in variants:
        res, by, calls = replay(sel, store)
        cpp = calls * cost_call / n_photos
        print(f"{name:42} " + " ".join(f"{res[s][0]:5d}/{res[s][1]:<4}" for s in ("all", "tune", "clean"))
              + f" {calls:5d} ({calls / len(keys):3.0%}) ${cpp:7.3f}  {by}")
        rows.append({"name": name, "thresholds": sel, "store_mode": store, "points": res, "opus_calls": calls,
                     "opus_share": round(calls / len(keys), 3), "cost_per_photo": round(cpp, 4), "by_gate": by})
        sc.log_experiment(run="gate_replay_now", hypothesis=name, model=f"gates+{MODEL}", version=CFG["version"], samples=1,
                          stock_rule=CFG["stock_rule"], alpha=CFG["alpha"], flag_rule=CFG["flag_rule"],
                          dataset=f"labels/dev.csv ({len(tune) + len(test)} areas)", points=res["all"][0], max_points=res["all"][1],
                          points_clean=res["clean"][0], max_clean=res["clean"][1], cost_per_photo_usd=round(cpp, 4),
                          opus_share=round(calls / len(keys), 3), by_gate=by, api_calls=0)

    # product identification: can the colour fingerprint tell a wrong product without Opus?
    nc = [k for k in tune + test if not labs[k]["compliant"] and feats[k]["fp"] is not None]
    okc = [k for k in tune + test if labs[k]["compliant"] and feats[k]["fp"] is not None]
    a, b = np.array([feats[k]["fp"] for k in nc]), np.array([feats[k]["fp"] for k in okc])
    auc = float(np.mean([(x > y) + 0.5 * (x == y) for x in a for y in b])) if len(a) and len(b) else None
    print(f"\nproduct fingerprint as a wrong-product detector: {len(nc)} wrong / {len(okc)} right areas with a fingerprint; "
          f"AUC {auc:.2f} (0.5 = coin flip); median distance wrong {np.median(a):.2f} vs right {np.median(b):.2f}")
    opus_nc = [k for k in tune + test if not labs[k]["compliant"]]
    print(f"Opus (today's rule) on the same question: {sum(not opus[k][1] for k in opus_nc)} of {len(opus_nc)} wrong products caught, "
          f"{sum(not opus[k][1] for k in tune + test if labs[k]['compliant'])} false alarms on {sum(labs[k]['compliant'] for k in tune + test)} right ones")

    json.dump({"opus_cost_per_call": cost_call, "photos": n_photos, "areas": len(keys), "thresholds": {g: th[g] for g in th},
               "replay": rows, "fingerprint_auc": auc}, open(OUT / "gate_replay_now.json", "w"), indent=1, default=str)
    print(f"\n-> {OUT}/gate_replay_now.json ({time.time() - t0:.0f}s, 0 API calls)")


if __name__ == "__main__":
    main()
