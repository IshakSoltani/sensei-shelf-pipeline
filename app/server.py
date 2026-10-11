"""Shelf Check app: upload a shelf photo, see every planogram area judged.

    .venv/bin/python app/server.py [--port 8787]      then open http://127.0.0.1:8787
    gate-first test run + score: http://127.0.0.1:8787/gates (drop the validation folder, with annotations.json when it exists)

The camera is recognised from the photo itself (the cameras never move, so a 64x36 grey thumbnail is enough),
the areas are judged exactly as predict.py does (config.json rules, references from data/refs), and the page
shows the photo with the areas coloured, the to-do list for the staff and the JSON in Sensei's format.
"""
import argparse, asyncio, base64, json, sys, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shelfcheck as sc
import gatepipe

HERE = Path(__file__).resolve().parent
STORE = sc.DATA / "refs"
FINGERPRINTS = sc.DATA / "camera_fingerprints.json"
CFG = json.load(open(sc.ROOT / "config.json"))
STORE_INDEX = json.load(open(STORE / "index.json")) if (STORE / "index.json").exists() else {}


def thumb(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)


def load_fingerprints():
    """One grey 64x36 thumbnail per camera (mean of its last three shared photos); computed once, kept in data/."""
    if FINGERPRINTS.exists():
        return {cam: np.array(v, np.float32) for cam, v in json.load(open(FINGERPRINTS)).items()}
    photos = sc.shared_photos()
    fp = {}
    for cam, phs in photos.items():
        fp[cam] = np.mean([thumb(sc.load_image(ph)) for ph in phs[-3:]], axis=0)
    if fp:
        json.dump({cam: np.round(v).astype(int).tolist() for cam, v in fp.items()}, open(FINGERPRINTS, "w"))
    return fp


FP = load_fingerprints()
SAMPLES = [{"cam": cam, "camera_name": sc.camera_name(cam), "name": ph.name, "hhmm": sc.hhmm(ph.ts), "areas": len(sc.CAM_AREAS[cam])}
           for cam, phs in sorted(sc.shared_photos().items()) for ph in phs[-1:]]


def detect_camera(img):
    """(camera, margin): the camera whose fingerprint is closest; margin = runner-up distance / best distance."""
    if not FP:
        return None, 0
    t = thumb(img)
    d = sorted((float(np.mean(np.abs(t - f))), cam) for cam, f in FP.items())
    best, second = d[0], d[1] if len(d) > 1 else (d[0][0] * 10, None)
    return best[1], round(second[0] / max(best[0], 1e-6), 2)


def ref_from_store(aid, kind):
    e = STORE_INDEX.get(aid, {}).get(kind); p = STORE / aid / f"{kind}.jpg"
    return {"ts": e["ts"], "name": e["name"], "fill": e.get("fill"), "jpg": p.read_bytes()} if e and p.exists() else None


async def analyze(img, cam, name, fresh=False):
    t0 = time.time()
    ts = sc.parse_ts(name)
    ph = sc.Photo(cam, name, ts, "-")
    import tempfile   # fresh = a throwaway cache, so every area is asked again (a real test run)
    judge = sc.Judge(model=CFG["model"], concurrency=CFG["concurrency"], ramp_start=CFG["concurrency"], **({"cache": tempfile.mkdtemp(prefix="fresh_")} if fresh else {}))
    samples = tuple(range(CFG["samples"]))
    refs = {}
    for aid in sc.CAM_AREAS[cam]:
        if not CFG.get("use_references", True):
            refs[aid] = None; continue
        r = {"empty": ref_from_store(aid, "empty"), "full": ref_from_store(aid, "full"), "prev": None}
        last = ref_from_store(aid, "last")
        if last and (ts is None or last["ts"] < ts):
            r["prev"] = last
        refs[aid] = r
    items = [(ph, aid) for aid in sc.CAM_AREAS[cam]]
    with_time = CFG.get("use_time", True)
    results = await judge.judge_many(items, lambda p, aid, s: sc.build_content(p, aid, refs=refs[aid], swap=(s == 1), img=img, with_time=with_time),
                                     samples=samples, effort=CFG["effort"], version=CFG["version"], quiet=True)
    for i, m in enumerate(CFG.get("fallback_models", [])):   # areas with no answer get one try on the next model
        left = [(p, aid) for p, aid in items if not any("error" not in results.get((cam, name, aid, s), {"error": 1}) for s in samples)]
        if not left:
            break
        results.update(await judge.judge_many(left, lambda p, aid, s: sc.build_content(p, aid, refs=refs[aid], img=img, with_time=with_time),
                                              samples=(10 + i,), effort=CFG["effort"], version=CFG["version"], model=m, quiet=True))
    fallbacks = {(cam, name, aid): ((STORE_INDEX.get(aid, {}).get("last") or {}).get("stock", "half"), True) for aid in sc.CAM_AREAS[cam]}
    pred, notes = sc.predictions(items, results, samples + (10, 11), CFG["stock_rule"], CFG["flag_rule"], CFG.get("alpha", 1.0), fallbacks)
    areas = []
    for aid in sc.CAM_AREAS[cam]:
        rs = [results.get((cam, name, aid, s)) for s in samples + (10, 11)]
        ok = [r for r in rs if r and "error" not in r]
        v = pred[cam][name][aid]
        p = [float(np.mean([r[k] for r in ok])) for k in ("p_empty", "p_half", "p_full")] if ok else [0, 1, 0]
        areas.append({"area_id": aid, "number": int(aid.split("_")[1]), "product": sc.planned(aid)["en"], "product_pt": sc.AREAS[aid]["product_name"],
                      "quad": sc.quad(sc.AREAS[aid]).tolist(), "stock": v["stock_level"], "compliant": v["planogram_compliant"],
                      "p": [round(x, 2) for x in p], "confidence": round(max(p), 2),
                      "fill": round(float(np.mean([r["fill_percent"] for r in ok]))) if ok else None,
                      "seen": ok[0]["visible_product"] if ok else None, "observation": ok[0]["observations"] if ok else None,
                      "usable": ok[0]["image_usable"] if ok else None, "answers": len(ok),
                      "note": notes.get((cam, name, aid), "")})
    u = judge.usage_summary()
    return {"camera": cam, "camera_name": sc.camera_name(cam), "file": name, "time_utc": sc.hhmm(ts) if ts else None, "areas": areas, "submission": pred,
            "seconds": round(time.time() - t0, 1), "calls": u["calls"], "cost_usd": round(u["cost"], 3), "model": CFG["model"],
            "samples": CFG["samples"]}


def gate_analysis(img, cam, name, allow_api=False, fresh=False, params=None, collect_all=False, version=None):
    """The gate-first pipeline on one photo (scored against our hand labels when this photo has them)."""
    out = asyncio.run(gatepipe.run_photo(img, cam, name, allow_api=allow_api, fresh=fresh, params=params, collect_all=collect_all,
                                         version=version))
    labels = {k: v for k, v in sc.load_labels().items() if k[0] == cam and k[1] == name}
    for a in out["areas"]:
        lab = labels.get((cam, name, a["area_id"]))
        if lab:
            a["label"] = {"stock": {0: "empty", 0.5: "half", 1: "full"}[lab["stock_value"]], "compliant": lab["compliant"]}
    out["time_utc"] = sc.hhmm(sc.parse_ts(name)) if sc.parse_ts(name) else None
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {fmt % args}", flush=True)

    def send_json(self, obj, status=200):
        b = json.dumps(obj).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/batch", "/gates"):
            b = (HERE / {"/": "index.html", "/batch": "gates.html", "/gates": "gates.html"}[url.path]).read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)
        elif url.path == "/settings":   # the decision settings the test page can tune, and the prompt versions it can ask
            self.send_json({"defaults": gatepipe.DEFAULTS, "default_version": CFG["version"],
                            "versions": {"v5": "v5: fill and product (submitted)", "v9": "v9: also asks the two-customer question"}})
        elif url.path == "/samples":
            self.send_json({"samples": SAMPLES, "cameras": sorted(sc.CAM_AREAS), "camera_names": sc.CAMERA_NAMES,
                            "model": CFG["model"], "samples_per_area": CFG["samples"]})
        elif url.path.startswith("/sample/"):
            cam, name = url.path.split("/")[2:4]
            p = sc.SHARED_IMAGES / cam / name
            if not p.exists():
                self.send_json({"error": "no such sample"}, 404); return
            b = p.read_bytes()
            self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/score":   # {"submission": {...}, "annotations": {...}} in Sensei's format -> official points
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                if d.get("annotations"):
                    labels = {(cam, f, aid): {"stock_value": float(v["stock_value"]), "compliant": bool(v["planogram_compliant"])}
                              for cam, files in d["annotations"].items() for f, areas in files.items() for aid, v in areas.items()}
                    source = "annotations file"
                else:   # our own hand labels (labels/dev.csv), only for the photos that were judged
                    judged = {(cam, f) for cam, files in d["submission"].items() for f in files}
                    labels = {k: v for k, v in sc.load_labels().items() if (k[0], k[1]) in judged}
                    source = "our hand labels"
                if d.get("only_judged"):   # while a batch is still running: score the photos judged so far
                    judged = {(cam, f) for cam, files in d["submission"].items() for f in files}
                    labels = {k: v for k, v in labels.items() if (k[0], k[1]) in judged}
                total, c = sc.score(d["submission"], labels)
                keys = sorted({(k[0], k[1]) for k in labels})
                per_photo = {f"{cam}/{f}": {"points": sc.score(d["submission"], {k: v for k, v in labels.items() if k[:2] == (cam, f)})[0],
                                            "max": 2 * sum(1 for k in labels if k[:2] == (cam, f))} for cam, f in keys}
                self.send_json({"points": total, "max": 2 * len(labels), "areas": len(labels), "photos": len(keys), "source": source,
                                "counts": c, "per_photo": per_photo})
            except Exception as e:
                self.send_json({"error": f"Could not score: {e!r}"[:300]}, 400)
            return
        if url.path == "/redecide":   # {"params": {...}, "photos": [{"camera", "file", "raw": {area: {"opus", "gate"}}}]}
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                out = []
                for p in d["photos"]:
                    sub, by = gatepipe.redecide(p["camera"], p["file"], p["raw"], d.get("params"))
                    out.append({"camera": p["camera"], "file": p["file"], "submission": sub, "decided_by": by})
                self.send_json({"photos": out, "settings": gatepipe.settings(d.get("params"))})
            except Exception as e:
                self.send_json({"error": f"Could not re-decide: {e!r}"[:300]}, 400)
            return
        if url.path not in ("/analyze", "/analyze_gates"):
            self.send_json({"error": "not found"}, 404); return
        q = urllib.parse.parse_qs(url.query)
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        img = cv2.imdecode(np.frombuffer(body, np.uint8), 1)
        if img is None:
            self.send_json({"error": "That file is not an image I can read. Use a JPEG or PNG from a shelf camera."}, 400); return
        if img.shape[:2] != (720, 1280):
            img = cv2.resize(img, (1280, 720), interpolation=cv2.INTER_AREA)
        cam = (q.get("cam") or ["auto"])[0]
        detected, margin = detect_camera(img)
        if cam == "auto":
            cam = detected
        if cam not in sc.CAM_AREAS:
            self.send_json({"error": "I could not tell which camera this is. Pick the camera and try again.", "detected": detected, "margin": margin}, 422); return
        name = (q.get("name") or ["upload.jpg"])[0]
        if url.path == "/analyze_gates":
            try:
                params = json.loads((q.get("params") or ["{}"])[0])
                version = (q.get("version") or [""])[0] or None
                if version and version not in sc.SYSTEMS:
                    raise ValueError(f"unknown prompt version {version}")
                out = gate_analysis(img, cam, name, allow_api=(q.get("api") or ["0"])[0] == "1", fresh=(q.get("fresh") or ["0"])[0] == "1",
                                    params=params, collect_all=(q.get("collect") or ["0"])[0] == "1", version=version)
            except Exception as e:
                self.send_json({"error": f"Checking failed: {e!r}"[:300]}, 500); return
            out["detected"] = detected; out["detect_margin"] = margin
            out["image"] = "data:image/jpeg;base64," + base64.standard_b64encode(sc.jpg(img, 82)).decode()
            self.send_json(out); return
        try:
            out = asyncio.run(analyze(img, cam, name, fresh=(q.get("fresh") or ["0"])[0] == "1"))
        except Exception as e:
            self.send_json({"error": f"Judging failed: {e!r}"[:300]}, 500); return
        out["detected"] = detected; out["detect_margin"] = margin
        out["image"] = "data:image/jpeg;base64," + base64.standard_b64encode(sc.jpg(img, 82)).decode()
        self.send_json(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    if not sc.load_env():
        print("WARNING: ANTHROPIC_API_KEY missing (.env): uploads will fail")
    print(f"Shelf Check app on http://127.0.0.1:{args.port}  (cameras known: {len(FP)}, samples: {len(SAMPLES)}, model {CFG['model']} x{CFG['samples']})")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
