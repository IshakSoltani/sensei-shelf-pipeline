"""Learned versions of the gates, measured honestly.

A) "code-only gate": a gradient-boosted classifier on the image features of gates.py (normalised per shelf area over the
   day), trained on the training-day photos (target = hand label where one exists, Opus's answer elsewhere) and judged
   on the 406 clean areas of the 64 held-out photos. It answers only where its top probability clears a bar.
B) "decision layer": the same features PLUS Opus's own probabilities, fill estimate and product answer, trained on the
   901 hand labels with 5-fold cross-validation grouped by photo. Does it beat Opus's own call?

    .venv/bin/python tools/stack_tune.py      (needs out/gates/ from tools/gate_tune.py)
"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).resolve().parent))
import shelfcheck as sc
from gate_tune import points, opus_verdict, OUT
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold

LEVELS = ["empty", "half", "full"]
IMG = ["cov", "colour", "edges", "lapvar", "sat", "val", "d_empty", "d_full", "fp", "chg_mean", "chg_share", "cov_full_ref"]
LARGE = ("melon", "watermelon", "pineapple", "papaya", "cabbage", "pumpkin", "abóbora", "coconut")


def main():
    F = json.load(open(OUT / "gate_features.json"))
    meta = np.load(OUT / "features.npz", allow_pickle=True)["meta"].item()
    labs = {f"{c}|{f}|{a}": v for (c, f, a), v in sc.load_labels().items()}
    clean_photos = {k.rsplit("|", 1)[0] for k, v in labs.items() if v["source"] != "team_all"}
    keys = sorted(F)
    # per-area normalisation: each feature minus that area's median over the day (cameras and lighting differ)
    per_area = {}
    for k in keys: per_area.setdefault(k.split("|")[2], []).append(k)
    med = {aid: {f: np.nanmedian([F[k].get(f) if F[k].get(f) is not None else np.nan for k in ks]) for f in IMG} for aid, ks in per_area.items()}

    def img_vec(k):
        aid = k.split("|")[2]; v = []
        for f in IMG:
            x = F[k].get(f); x = np.nan if x is None else x
            v += [x, x - med[aid][f]]
        p = sc.planned(aid)["en"].lower(); v.append(float(any(w in p for w in LARGE)))
        return v

    def opus_vec(k):
        a = meta["opus"].get(k)
        if not a: return [np.nan] * 6
        return list(a["p"]) + [a["fill"] if a["fill"] is not None else np.nan, float(a["matches"] == "yes"), float(a["matches"] == "no")]

    y_of = lambda k: LEVELS[int(labs[k]["stock_value"] * 2)]
    # ---------------- A) code-only gate, trained on training-day photos, tested on the clean photos
    train = [k for k in keys if k.rsplit("|", 1)[0] not in clean_photos and (k in labs or k in meta["opus"])]
    ytr = [y_of(k) if k in labs else opus_verdict(meta["opus"][k])[0] for k in train]
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, random_state=0).fit([img_vec(k) for k in train], ytr)
    test = [k for k, v in labs.items() if v["source"] != "team_all" and k in F and k in meta["opus"]]
    P = clf.predict_proba([img_vec(k) for k in test]); cls = list(clf.classes_)
    print(f"A) code-only gate: trained on {len(train)} training-day areas, judged on {len(test)} clean labelled areas")
    print(f"   {'bar':>5} {'answers':>8} {'gate stock acc':>15} {'Opus stock acc (same areas)':>28} {'gate pts':>9} {'Opus pts':>9}")
    for bar in (0.5, 0.7, 0.8, 0.9, 0.95, 0.98):
        sel = [(k, cls[int(np.argmax(p))]) for k, p in zip(test, P) if p.max() >= bar]
        if not sel: print(f"   {bar:5.2f} {0:8d}"); continue
        ga = np.mean([s == y_of(k) for k, s in sel]); oa = np.mean([opus_verdict(meta["opus"][k])[0] == y_of(k) for k, _ in sel])
        gp = sum(points(s, opus_verdict(meta["opus"][k])[1], labs[k]) for k, s in sel); op = sum(points(*opus_verdict(meta["opus"][k]), labs[k]) for k, _ in sel)
        print(f"   {bar:5.2f} {len(sel):4d} ({len(sel)/len(test):3.0%}) {ga:14.1%} {oa:27.1%} {gp:9d} {op:9d}")
    # ---------------- B) decision layer on Opus + features, grouped 5-fold CV over the 901 labels
    lk = [k for k in labs if k in F and k in meta["opus"]]
    X = np.array([img_vec(k) + opus_vec(k) for k in lk]); y = np.array([y_of(k) for k in lk]); groups = [k.rsplit("|", 1)[0] for k in lk]
    Xo = np.array([opus_vec(k) for k in lk])
    pred_full, pred_opus_only = np.empty(len(lk), dtype=object), np.empty(len(lk), dtype=object)
    for tr, te in GroupKFold(n_splits=5).split(X, y, groups):
        for Xs, out in ((X, pred_full), (Xo, pred_opus_only)):
            m = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=7, l2_regularization=1.0, random_state=0).fit(Xs[tr], y[tr])
            # decide by expected points, not the most likely class: over costs 3, under 2
            pr = m.predict_proba(Xs[te]); cl = list(m.classes_)
            for i, p in zip(te, pr):
                pp = {c: p[cl.index(c)] if c in cl else 0 for c in LEVELS}
                ev = {s: sum(pp[t] * (1 if s == t else (-3 if LEVELS.index(s) > LEVELS.index(t) else -2)) for t in LEVELS) for s in LEVELS}
                out[i] = max(ev, key=ev.get)
    def tot(stocks, ks_idx):
        return sum(points(stocks[i], opus_verdict(meta["opus"][lk[i]])[1], labs[lk[i]]) for i in ks_idx)
    allx = range(len(lk)); cleanx = [i for i, k in enumerate(lk) if labs[k]["source"] != "team_all"]
    opus_st = [opus_verdict(meta["opus"][k])[0] for k in lk]
    print(f"\nB) decision layer, 5-fold CV grouped by photo, {len(lk)} labelled areas (product call stays Opus's)")
    for name, st in (("Opus x1 as is (most likely level)", opus_st), ("re-decided from Opus's numbers only", pred_opus_only), ("Opus's numbers + image features", pred_full)):
        acc = np.mean([st[i] == y[i] for i in allx])
        print(f"   {name:38} all {tot(st, allx):5d}/{2*len(lk)}  clean {tot(st, cleanx):4d}/{2*len(cleanx)}  stock acc {acc:.1%}")
    json.dump({"note": "see stdout"}, open(OUT / "stack_tune.json", "w"))


if __name__ == "__main__":
    main()
