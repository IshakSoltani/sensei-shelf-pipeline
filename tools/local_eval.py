"""Local first stage: judge labelled areas with an open-weight vision model on this Mac (MLX), score it, and
simulate the cascade "local model first, Opus where unsure".

    .venv/bin/python tools/local_eval.py --model mlx-community/Qwen3-VL-4B-Instruct-4bit --photos 10

Uses two images per area (context crop + straightened zoom) and a short prompt, which is the speed/accuracy sweet
spot measured on the M5. Answers are cached under cache/judge like the API answers (model = repo id, version
"local1"). The Opus answers for the escalated areas come from the cache of the strict dev run (free) when present.
"""
import argparse, json, re, sys, time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shelfcheck as sc
from tune import load_day, low_confidence

SHORT = ("You judge ONE supermarket shelf area from a ceiling camera. The target area is outlined in magenta in image 1 (its surroundings) "
         "and shown alone, straightened, in image 2. Judge only what rests inside the outline; ignore produce hanging from the tier above and the neighbours.\n"
         "Stock level = share of the container floor covered by product (any product): empty = under about 20% or only one or two small items left; "
         "half = roughly 20-70%; full = more than about 70% (a few small gaps are still full).\n"
         "Products are often misplaced: name what you actually see from the candidate list. matches_planned is \"yes\" if the visible product is the same KIND of "
         "produce as the planned one (variety, size and packaging do not matter), \"no\" if it is a different kind (also when only one or two leftover items of a "
         "different kind remain), \"no_product\" if the container is bare, \"cannot_tell\" if unsure.\n"
         "Reply with ONLY a JSON object: {\"visible_product\": str, \"matches_planned\": \"yes\"|\"no\"|\"cannot_tell\"|\"no_product\", \"p_match\": 0-100, "
         "\"fill_percent\": 0-100, \"p_empty\": int, \"p_half\": int, \"p_full\": int} with p_empty+p_half+p_full = 100.\n")


MAX_SIDE = 448   # the LoRA training crops were shrunk to this (tools/build_lora_dataset.py); 0 = send the crops at full size


def shrink(im):
    if not MAX_SIDE:
        return im
    h, w = im.shape[:2]; sc_ = min(1.0, MAX_SIDE / max(h, w))
    return im if sc_ >= 1.0 else cv2.resize(im, (int(w * sc_), int(h * sc_)), interpolation=cv2.INTER_AREA)


LOCAL_VERSION = "local2"   # local2 = JSON pre-filled; local1 answers (prose, often truncated) are not reused
PREFILL = '{"visible_product": "'


class LocalJudge:
    """mlx-vlm backend with the same answer dict as shelfcheck.Judge (plus _tokens with zero cost)."""

    def __init__(self, repo, cache=sc.CACHE, adapter=None):
        from mlx_vlm import load
        from mlx_vlm.utils import load_config
        t0 = time.time()
        self.repo = repo if not adapter else f"{repo}+{Path(adapter).name}"   # the cache key tells adapters apart
        self.model, self.processor = load(repo, adapter_path=adapter) if adapter else load(repo)
        self.config = load_config(repo)
        self.cache = Path(cache); self.cache.mkdir(parents=True, exist_ok=True)
        self.seconds = []
        print(f"{self.repo} loaded in {time.time() - t0:.0f}s")

    @staticmethod
    def normalise(txt):
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except Exception:
            return None
        mp = d.get("matches_planned")
        if isinstance(mp, bool) or mp in (0, 1, "0", "1", "true", "false", "True", "False"):
            mp = "yes" if mp in (True, 1, "1", "true", "True") else "no"
        if mp not in ("yes", "no", "cannot_tell", "no_product"):
            mp = "cannot_tell"
        out = {"image_usable": "clear", "observations": str(d.get("observations", ""))[:200], "visible_product": str(d.get("visible_product", "")),
               "matches_planned": mp, "p_match": d.get("p_match", 50), "fill_percent": d.get("fill_percent", 50),
               "p_empty": d.get("p_empty", 0), "p_half": d.get("p_half", 0), "p_full": d.get("p_full", 0)}
        for k in ("p_match", "fill_percent", "p_empty", "p_half", "p_full"):
            try: out[k] = int(float(out[k]))
            except Exception: out[k] = 0
        if out["visible_product"].lower() in ("empty container", "none", "nothing", "") and out["matches_planned"] == "no":
            out["matches_planned"] = "no_product"
        return sc._clean(out)

    def judge(self, ph, aid, img, scratch, sample=0):
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        a, p = sc.AREAS[aid], sc.planned(aid)
        ctx, zoom = sc.jpg(shrink(sc.context_crop(img, a))), sc.jpg(shrink(sc.rectify(img, a)))
        text = SHORT + f"Planned product: {p['en']} ({p['unit']}). Candidates: {'; '.join(sc.candidates(ph.cam) + ['empty container', 'other produce'])}."
        content = [{"type": "text", "text": text}, {"type": "image", "source": {"data": __import__('base64').standard_b64encode(ctx).decode()}},
                   {"type": "image", "source": {"data": __import__('base64').standard_b64encode(zoom).decode()}}]
        key = sc.Judge.key(None, content, sample, "local", LOCAL_VERSION, self.repo)
        path = self.cache / f"{key}.json"
        if path.exists():
            return json.load(open(path))
        paths = []
        for name, b in (("ctx.jpg", ctx), ("zoom.jpg", zoom)):
            pth = Path(scratch) / name; pth.write_bytes(b); paths.append(str(pth))
        try:   # Qwen3.5 / Gemma 4 default to a "thinking" preamble that would cost hundreds of tokens per answer
            prompt = apply_chat_template(self.processor, self.config, text, num_images=2, enable_thinking=False)
        except TypeError:
            prompt = apply_chat_template(self.processor, self.config, text, num_images=2)
        # start the answer for the model: small models otherwise write a paragraph first and run out of tokens
        prompt = prompt + PREFILL
        t0 = time.time()
        out = generate(self.model, self.processor, prompt, image=paths, max_tokens=200, temperature=0.0, verbose=False)
        txt = PREFILL + (out.text if hasattr(out, "text") else str(out))
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S)
        dt = time.time() - t0; self.seconds.append(dt)
        d = self.normalise(txt)
        if d is None:
            return {"error": f"bad json: {txt[:120]!r}", "_model": self.repo}
        d["_tokens"] = {"in": getattr(out, "prompt_tokens", 0) or 0, "out": getattr(out, "generation_tokens", 0) or 0, "cache_read": 0, "cache_write": 0}
        d["_model"] = self.repo; d["_seconds"] = round(dt, 2)
        path.write_text(json.dumps(d))
        return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mlx-community/Qwen3-VL-4B-Instruct-4bit")
    ap.add_argument("--photos", type=int, default=10)
    ap.add_argument("--limit", type=int, default=None, help="only the first N areas (speed test)")
    ap.add_argument("--adapter", default=None, help="LoRA adapter directory (adapters.safetensors + adapter_config.json)")
    ap.add_argument("--max-side", type=int, default=448, help="shrink both crops to this max side before the model (448 = as trained); 0 = full size")
    ap.add_argument("--all-labels", action="store_true", help="also score on the team-labelled areas that are in the LoRA training pool (optimistic for an adapter)")
    args = ap.parse_args()
    global MAX_SIDE; MAX_SIDE = args.max_side
    cfg = json.load(open(sc.ROOT / "config.json"))
    labels = sc.load_labels(); photos = sc.shared_photos(); day = load_day()
    if not args.all_labels:   # clean evaluation: only areas whose photos never fed the teacher/LoRA set
        labels = {k: v for k, v in labels.items() if v["source"] != "team_all"}
    picks = []
    for cam, phs in sorted(photos.items()):
        last = phs[-1]
        if all((cam, last.name, aid) in labels for aid in sc.CAM_AREAS[cam]):
            picks.append(last)
    picks = picks[:args.photos]
    targets = [(ph, aid) for ph in picks for aid in sc.CAM_AREAS[ph.cam]][:args.limit]
    scratch = Path("/tmp/shelfcheck_local"); scratch.mkdir(exist_ok=True)
    judge = LocalJudge(args.model, adapter=args.adapter)
    args.model = judge.repo   # experiment rows name the adapter too
    local = {}
    t0 = time.time()
    images = {}
    for i, (ph, aid) in enumerate(targets):
        img = images.setdefault(ph.key(), sc.load_image(ph))
        local[(ph.cam, ph.name, aid, 0)] = judge.judge(ph, aid, img, scratch)
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(targets)} areas, {time.time() - t0:.0f}s, mean {np.mean(judge.seconds) if judge.seconds else 0:.1f}s per new call")
    errs = sum(1 for r in local.values() if "error" in r)
    print(f"local: {len(local)} answers ({errs} unparsable) in {time.time() - t0:.0f}s; mean {np.mean(judge.seconds) if judge.seconds else 0:.1f}s per call")

    sub = {k: v for k, v in labels.items() if any((ph.cam, ph.name, aid) == k for ph, aid in targets)}
    fallbacks = {(ph.cam, ph.name, aid): sc.nearest_estimate(ph.cam, aid, ph.ts, day, photos) for ph, aid in targets}
    pred, _ = sc.predictions(targets, local, (0,), cfg["stock_rule"], cfg["flag_rule"], 1.0, fallbacks)
    tot, c = sc.score(pred, sub)
    print(f"\n{'configuration':46} {'points':>10}  false/missed  under/over  escalated")
    print(f"{'local model alone, 1 answer':46} {tot:4d}/{2*len(sub):<5}   {c['false_flag']}/{c['missed_nc']:<5}  {c['under']}/{c['over']:<5}  -")
    sc.log_experiment(run="local_study", hypothesis=f"{args.model} alone, 2 images <= {MAX_SIDE or 'full'} px, 1 answer, {len(picks)} evening photos ({errs} unparsable)", model=args.model, version=LOCAL_VERSION, samples=1,
                      stock_rule=cfg["stock_rule"], alpha=1.0, flag_rule=cfg["flag_rule"], dataset=f"{len(picks)} evening photos ({len(sub)} areas)",
                      points=tot, max_points=2 * len(sub), counts=c, cost_per_photo_usd=0.0, mean_seconds_per_call=round(float(np.mean(judge.seconds)), 1) if judge.seconds else None)

    # cascade: Opus strict x3 answers from the cache for the escalated areas (no new API calls)
    opus = sc.Judge(model="claude-opus-5-5")
    def opus_cached(ph, aid):
        img = images.setdefault(ph.key(), sc.load_image(ph))
        rs = []
        for s in (0, 1, 2):
            content = sc.build_content(ph, aid, refs=None, swap=(s == 1), img=img, with_time=False)
            r = opus.cached(content, s, cfg["effort"], cfg["version"], "claude-opus-5-5")
            if r: rs.append((s, r))
        return rs
    for name, trigger in [("escalate when unsure (prob < 0.6) or any non-yes product answer", lambda r: low_confidence([r]) or r["matches_planned"] != "yes"),
                          ("escalate only on non-yes product answer", lambda r: r["matches_planned"] != "yes"),
                          ("escalate when top probability < 0.8 or non-yes", lambda r: max(r["p_empty"], r["p_half"], r["p_full"]) < 0.8 or r["matches_planned"] != "yes")]:
        res, esc, missing = {}, 0, 0
        for ph, aid in targets:
            key = (ph.cam, ph.name, aid); r = local.get(key + (0,))
            if r and "error" not in r and not trigger(r):
                res[key + (0,)] = r; continue
            esc += 1
            rs = opus_cached(ph, aid)
            if not rs: missing += 1; res[key + (0,)] = r if r else {"error": "none"}; continue
            for s, rr in rs: res[key + (s,)] = rr
        pred, _ = sc.predictions(targets, res, (0, 1, 2), cfg["stock_rule"], cfg["flag_rule"], 1.0, fallbacks)
        tot, c = sc.score(pred, sub)
        opus_cost = sum(sc.call_cost("claude-opus-5-5", rr.get("_tokens", {})) for k, rr in res.items() if rr.get("_model") == "claude-opus-5-5")
        print(f"{name:46} {tot:4d}/{2*len(sub):<5}   {c['false_flag']}/{c['missed_nc']:<5}  {c['under']}/{c['over']:<5}  {esc}/{len(targets)} areas -> ${opus_cost/len(picks):.3f} per photo" + (f" ({missing} without cached Opus answers)" if missing else ""))
        sc.log_experiment(run="local_study", hypothesis=f"{args.model} first, Opus x3 where {name}", model=f"{args.model}+opus", version=LOCAL_VERSION + "+v5", samples=1,
                          stock_rule=cfg["stock_rule"], alpha=1.0, flag_rule=cfg["flag_rule"], dataset=f"{len(picks)} evening photos ({len(sub)} areas)",
                          points=tot, max_points=2 * len(sub), counts=c, cost_per_photo_usd=round(opus_cost / len(picks), 4), escalated=esc)


if __name__ == "__main__":
    main()
