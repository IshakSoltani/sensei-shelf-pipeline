"""Validation run with the aggressive clearly-full gate in front of Opus.

For every planogram area of a photo:
  1. the gate (pure code) straightens the area to 64 x 64 px and measures
       coverage     share of pixels that differ from the area's emptiest picture of the training day
       fingerprint  hue x saturation histogram of those pixels against the area's usual product
     coverage >= 50 % and fingerprint distance <= 0.2  ->  full + compliant, no model call
  2. every other area goes to Opus exactly as config.json says (v5, x1, fill:35:65, confident-no flag, same kind)
  3. an area Opus did not answer takes the training day's last verdict for that area (else half, compliant)

Thresholds: the "aggressive" full gate of tools/gate_replay_now.py (scored-run replay: 1,379 vs Opus-only 1,385 on the
901 labels, 78 % of the Opus calls). The clearly-empty gate never qualified; covered / nothing-changed need an earlier
photo of the same day, which the scored run may not use.

Opus calls are opt-in: by default an area is answered from the saved answers (cache/judge) only, and an area with no
saved answer takes the fallback. Pass allow_api=True (CLI: --api) to ask Opus for those.

    .venv/bin/python gatepipe.py build                       # reference pictures + fingerprints -> data/gate_refs.npz
    .venv/bin/python gatepipe.py check                       # replay the training day from saved answers, score on labels/dev.csv
    .venv/bin/python gatepipe.py predict <photos dir> --out submission.json [--api] [--score annotations.json]
                                                             (photos in camera folders or loose: the camera is then recognised)
"""
import argparse, asyncio, json, sys, time
from pathlib import Path

import cv2
import numpy as np

import shelfcheck as sc
import gates as G

CFG = json.load(open(sc.ROOT / "config.json"))
REFS = sc.DATA / "gate_refs.npz"
COV_MIN = 0.5      # coverage at or above this ...
FP_MAX = 0.2       # ... and a fingerprint at most this far from the usual product -> the gate answers "full"
_STORE = json.load(open(sc.DATA / "refs/index.json")) if (sc.DATA / "refs/index.json").exists() else {}


# ---------------------------------------------------------------- references (training day only)

def fullness(crop):   # texture x (1 + saturation spread), as in tools/gate_tune.py
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var() * (1 + cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1].std() / 32.0))


def build_refs(all_photos=False):
    """For every area: its emptiest picture of the training day (Lab, 64 x 64) and the mean fingerprint of its product
    on photos where the day pass saw the planned product at >= 50 % fill. Covered photos are left out.

    By default the 64 photos behind the clean labels are left out too, exactly as when the thresholds were measured
    (gatepipe check: 1,378 / 1,802, clean 562 / 812). all_photos=True uses the whole day; that picks emptier evening
    references, the gate fires more often and scores lower (1,361, clean 553): the gate is sensitive to its references."""
    day = json.load(open(sc.DATA / "day_pass.json"))
    photos = sc.shared_photos()
    if not all_photos:
        held = {(c, n) for (c, n, _), v in sc.load_labels().items() if v["source"] != "team_all"}
        photos = {c: [p for p in phs if (c, p.name) not in held] for c, phs in photos.items()}
    crops, scores = {}, {}
    t0 = time.time()
    for cam, phs in sorted(photos.items()):
        for ph in phs:
            img = sc.load_image(ph)
            scores[(cam, ph.name)] = G.photo_score(img, cam)
            for aid in sc.CAM_AREAS[cam]:
                crops.setdefault(aid, []).append((ph.name, G.area_crop(img, sc.AREAS[aid])))
        print(f"  {cam}: {len(phs)} photos ({time.time() - t0:.0f}s)", flush=True)
    med = {}
    for (cam, _), s in scores.items():
        med.setdefault(cam, []).append(s)
    med = {c: float(np.median(v)) for c, v in med.items()}
    covered = {k for k, s in scores.items() if s < G.COVERED_IF_BELOW * med[k[0]]}
    out, info = {}, {}
    for aid, items in crops.items():
        cam = aid.split("_")[0]
        pool = [(n, c) for n, c in items if (cam, n) not in covered]
        e_name, e_crop = min(pool, key=lambda x: fullness(x[1]))
        E = G.lab(e_crop)
        fps = []
        for n, c in pool:
            d = day.get(f"{cam}|{n}|{aid}") or {}
            if n != e_name and d.get("matches_planned") == "yes" and (d.get("fill_percent") or 0) >= 50:
                fp = G.fingerprint(c, G.diff_map(G.lab(c), E) > G.DIFF_T)
                if fp is not None:
                    fps.append(fp)
        out[f"{aid}|empty"] = E
        if fps:
            out[f"{aid}|fp"] = np.mean(fps, axis=0).astype(np.float32)
        info[aid] = {"empty_photo": e_name, "empty_fill": (day.get(f"{cam}|{e_name}|{aid}") or {}).get("fill_percent"), "fingerprint_photos": len(fps)}
    np.savez_compressed(REFS, **out)
    json.dump({"all_photos": all_photos, "covered": sorted("|".join(k) for k in covered), "areas": info}, open(REFS.with_suffix(".json"), "w"), indent=1)
    print(f"-> {REFS}: {len(info)} areas, {sum(1 for v in info.values() if v['fingerprint_photos'])} with a product fingerprint")


_refs = None


def refs():
    global _refs
    if _refs is None:
        if not REFS.exists():
            raise FileNotFoundError(f"{REFS} is missing: run `.venv/bin/python gatepipe.py build` first")
        z = np.load(REFS)
        _refs = {k: z[k] for k in z.files}
    return _refs


# ---------------------------------------------------------------- the gate

def gate(img, aid):
    """The clearly-full gate on one area: {"coverage", "fingerprint", "fired", "why"}."""
    R = refs()
    crop = G.area_crop(img, sc.AREAS[aid])
    L, E = G.lab(crop), R[f"{aid}|empty"]
    cov = G.coverage(L, E)
    ref_fp = R.get(f"{aid}|fp")
    fpd = G.fingerprint_distance(G.fingerprint(crop, G.diff_map(L, E) > G.DIFF_T), ref_fp)
    cov_ok, fp_ok = cov >= COV_MIN, fpd is not None and fpd <= FP_MAX
    if sc.STOCK_TEST == "two_customers" and sc.counts_units(aid):   # coverage cannot count bags, packs or melons: Opus judges these
        return {"coverage": round(cov, 3), "fingerprint": None if fpd is None else round(fpd, 3), "coverage_ok": cov_ok,
                "fingerprint_ok": fp_ok, "fired": False, "why": "sold per pack or large item: Opus counts what is left, coverage cannot"}
    if ref_fp is None:
        why = "no product fingerprint for this area"
    elif fpd is None:
        why = "too little product to fingerprint"
    elif not cov_ok:
        why = f"coverage {cov:.0%} is under {COV_MIN:.0%}"
    elif not fp_ok:
        why = f"colours {fpd:.2f} away from the usual product (limit {FP_MAX})"
    else:
        why = f"{cov:.0%} covered, colours match the usual product ({fpd:.2f})"
    return {"coverage": round(cov, 3), "fingerprint": None if fpd is None else round(fpd, 3),
            "coverage_ok": cov_ok, "fingerprint_ok": fp_ok, "fired": cov_ok and fp_ok, "why": why}


# ---------------------------------------------------------------- Opus (saved answers first)

def content_for(ph, aid, img):
    return sc.build_content(ph, aid, refs=None, img=img, with_time=CFG.get("use_time", False))


def saved_answer(ph, aid, img):
    """The saved Opus answer for this exact area crop under the frozen config, or None. A file lookup, never a call."""
    key = sc.Judge.key(None, content_for(ph, aid, img), 0, CFG["effort"], CFG["version"], CFG["model"])
    p = sc.CACHE / f"{key}.json"
    if p.exists():
        r = json.load(open(p))
        return r if "error" not in r else None
    return None


def opus_decision(r, aid):
    stock, comp, _ = sc.decide([r], CFG["stock_rule"], CFG["flag_rule"], CFG.get("alpha", 1.0))
    if not comp and sc.SAME_KIND_OVERRIDE and sc.same_kind(sc.planned(aid)["en"], r.get("visible_product", "")):
        comp = True
    return sc.two_customer_test(stock, [r], aid), comp


def fallback(aid):
    last = _STORE.get(aid, {}).get("last") or {}
    return (last.get("stock", "half"), True)


async def run_photo(img, cam, name, allow_api=False, gates_on=True, fresh=False):
    """One photo -> {"areas": [...], "submission": {...}, "summary": {...}}. gates_on=False = plain Opus (for comparison).
    fresh=True ignores the saved answers and asks Opus again (throwaway cache), so the run is a real test."""
    allow_api = allow_api or fresh
    t0 = time.time()
    ph = sc.Photo(cam, name, sc.parse_ts(name), "-")
    areas, ask = {}, []
    for aid in sc.CAM_AREAS[cam]:
        g = gate(img, aid) if gates_on else None
        a = {"area_id": aid, "number": int(aid.split("_")[1]), "product": sc.planned(aid)["en"], "product_pt": sc.AREAS[aid]["product_name"],
             "quad": sc.quad(sc.AREAS[aid]).tolist(), "gate": g}
        if g and g["fired"]:
            a.update(stock="full", compliant=True, decided_by="gate")
        else:
            r = None if fresh else saved_answer(ph, aid, img)
            if r:
                a.update(opus=r, source="saved")
            else:
                ask.append(aid)
        areas[aid] = a
    calls, cost = 0, 0.0
    if ask and allow_api:
        if not sc.load_env():
            raise RuntimeError("ANTHROPIC_API_KEY is missing (.env), so Opus cannot be asked")
        import tempfile
        judge = sc.Judge(model=CFG["model"], concurrency=CFG["concurrency"], ramp_start=min(CFG["concurrency"], 16),
                         **({"cache": tempfile.mkdtemp(prefix="fresh_")} if fresh else {}))
        res = await judge.judge_many([(ph, aid) for aid in ask], lambda p, aid, s: content_for(p, aid, img), samples=(0,),
                                     effort=CFG["effort"], version=CFG["version"], quiet=True)
        for aid in ask:
            r = res.get((cam, name, aid, 0))
            if r and "error" not in r:
                areas[aid].update(opus=r, source="asked now")
            elif r:
                areas[aid]["api_error"] = r.get("error", "")[:200]
        u = judge.usage_summary(); calls, cost = u["calls"], u["cost"]
    for aid, a in areas.items():
        if "decided_by" in a:
            continue
        r = a.get("opus")
        if r and r.get("image_usable") != "fully_occluded":
            stock, comp = opus_decision(r, aid)
            a.update(stock=stock, compliant=comp, decided_by="opus", fill=r.get("fill_percent"), seen=r.get("visible_product"),
                     observation=r.get("observations"), matches=r.get("matches_planned"))
        else:
            stock, comp = fallback(aid)
            a.update(stock=stock, compliant=comp, decided_by="fallback",
                     note="view blocked" if r else ("no saved Opus answer" + ("" if allow_api else " (Opus calls are off)")))
        a.pop("opus", None)
    sub = {cam: {name: {aid: {"planogram_compliant": bool(a["compliant"]), "stock_level": a["stock"], "stock_value": sc.STOCK_VALUE[a["stock"]]}
                        for aid, a in areas.items()}}}
    n = len(areas)
    by = {k: sum(a["decided_by"] == k for a in areas.values()) for k in ("gate", "opus", "fallback")}
    per_call = 0.0111   # mean Opus cost per area under the frozen config (docs/cost-report.pdf)
    summary = {"areas": n, "by": by, "api_calls": calls, "api_cost_usd": round(cost, 4),
               "opus_cost_usd": round((by["opus"] + by["fallback"]) * per_call, 3), "opus_only_cost_usd": round(n * per_call, 3),
               "seconds": round(time.time() - t0, 2)}
    return {"camera": cam, "camera_name": sc.camera_name(cam), "file": name, "areas": list(areas.values()), "submission": sub, "summary": summary}


# ---------------------------------------------------------------- CLI

def check():
    """Replay the 181 training-day photos from saved answers only and score both pipelines on the 901 hand labels."""
    labels = sc.load_labels()
    photos = {(c, n) for c, n, _ in labels}
    subs = {"gates": {}, "opus": {}}
    by = {"gate": 0, "opus": 0, "fallback": 0}
    for cam, phs in sorted(sc.shared_photos().items()):
        for ph in phs:
            if (cam, ph.name) not in photos:
                continue
            img = sc.load_image(ph)
            for mode, on in (("gates", True), ("opus", False)):
                out = asyncio.run(run_photo(img, cam, ph.name, allow_api=False, gates_on=on))
                subs[mode].setdefault(cam, {}).update(out["submission"][cam])
                if on:
                    for k, v in out["summary"]["by"].items():
                        by[k] += v
    for mode, sub in subs.items():
        total, c = sc.score(sub, labels)
        clean = {k: v for k, v in labels.items() if v["source"] != "team_all"}
        print(f"{mode:6}: {total}/{2 * len(labels)} (clean {sc.score(sub, clean)[0]}/{2 * len(clean)})  {c}")
    print(f"decided by, on the labelled photos: {by}")


_CAM_FP = None


def detect_camera(img):
    """(camera, margin) from a 64x36 grey thumbnail against data/camera_fingerprints.json (the cameras never move);
    margin = runner-up distance / best distance, so a margin near 1 means the photo could be either camera."""
    global _CAM_FP
    if _CAM_FP is None:
        _CAM_FP = {c: np.array(v, np.float32) for c, v in json.load(open(sc.DATA / "camera_fingerprints.json")).items()}
    t = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = sorted((float(np.mean(np.abs(t - f))), c) for c, f in _CAM_FP.items())
    return d[0][1], round(d[1][0] / max(d[0][0], 1e-6), 2)


def find_photos(folder):
    """Every photo under folder, with its camera: the camera folder it sits in (e.g. 6215/), else recognised from the image."""
    found, seen = [], set()
    for f in sorted(Path(folder).rglob("*")):
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png") or f.name.startswith("."):
            continue
        img = sc.load_image(f)
        if img.shape[:2] != (720, 1280):
            img = cv2.resize(img, (1280, 720), interpolation=cv2.INTER_AREA)
        cam = next((p for p in reversed(f.relative_to(folder).parts[:-1]) if p in sc.CAM_AREAS), None)
        how, margin = "folder", None
        if cam is None:
            cam, margin = detect_camera(img); how = "recognised"
        if (cam, f.name) in seen:
            print(f"  WARNING: {f} repeats {cam}/{f.name}; skipped", flush=True); continue
        seen.add((cam, f.name)); found.append((cam, f, img, how, margin))
    return found


def complete(sub, photos):
    """Problems that would cost points: a photo or an area missing, or a value outside the format. [] = ready."""
    bad = []
    for cam, f, *_ in photos:
        got = sub.get(cam, {}).get(f.name, {})
        for aid in sc.CAM_AREAS[cam]:
            v = got.get(aid)
            if not v or not isinstance(v.get("planogram_compliant"), bool) or v.get("stock_value") not in (0, 0.5, 1) \
                    or v.get("stock_level") not in ("empty", "half", "full"):
                bad.append(f"{cam}/{f.name} {aid}")
    return bad


def predict(folder, out, allow_api, annotations=None):
    sub, t0, cost = {}, time.time(), 0.0
    photos = find_photos(folder)
    print(f"{len(photos)} photos in {folder}", flush=True)
    for cam, f, img, how, margin in photos:
        note = "" if how == "folder" else f", camera recognised from the image (margin {margin})" + ("  CHECK: close call" if margin < 1.3 else "")
        r = asyncio.run(run_photo(img, cam, f.name, allow_api=allow_api))
        sub.setdefault(cam, {}).update(r["submission"][cam])
        cost += r["summary"]["api_cost_usd"]
        print(f"  {cam}/{f.name}: {r['summary']['by']}{note}", flush=True)
    bad = complete(sub, photos)
    json.dump(sub, open(out, "w"), indent=1)
    n = sum(len(v) for c in sub.values() for v in c.values())
    print(f"-> {out}: {len(photos)} photos, {n} areas, {time.time() - t0:.0f}s, ${cost:.2f} on the API")
    print("   complete: every area of every photo has a valid answer" if not bad else f"   INCOMPLETE ({len(bad)}): {bad[:10]}")
    if annotations:
        a = json.load(open(annotations))
        labels = {(c, f, aid): {"stock_value": float(v["stock_value"]), "compliant": bool(v["planogram_compliant"])}
                  for c, fs in a.items() for f, areas in fs.items() for aid, v in areas.items()}
        total, c = sc.score(sub, labels)
        print(f"   score against {annotations}: {total} of {2 * len(labels)}  {c}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "check", "predict"])
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--out", default="submission_gates.json")
    ap.add_argument("--api", action="store_true", help="ask Opus for areas with no saved answer (costs money)")
    ap.add_argument("--all-photos", action="store_true", help="build: references from the whole training day")
    ap.add_argument("--score", metavar="ANNOTATIONS", help="predict: also score the result against an annotations.json")
    a = ap.parse_args()
    {"build": lambda: build_refs(a.all_photos), "check": check, "predict": lambda: predict(a.folder, a.out, a.api, a.score)}[a.cmd]()
