"""Coded pre-checks ("gates") that look at a shelf area before any model does.

Each gate either answers (when it is clearly sure) or passes the area down the chain:

  1. covered    the whole gondola is behind its fabric cover or blocked: texture x colourfulness inside the planogram
                region far below the camera's usual level (port of ilies/detect_covered.py)
  2. unchanged  the area looks the same as in the previous photo of the same camera: reuse the previous answer
                (store mode only: in the scored run there is no previous photo of the validation day)
  3. empty      the area is close to its own emptiest picture and has little colour or detail
  4. full       the area is far from its emptiest picture and close to its fullest one
  5. product    the colour fingerprint of the product pixels matches the area's usual product (confirm only, never flags)

Everything works on the rectified crop (shelfcheck.rectify) shrunk to SIDE x SIDE. References and fingerprints come from
other photos of the training day (tools/build_gate_refs.py); thresholds are chosen on the labels by tools/gate_tune.py.
"""
import cv2
import numpy as np

import shelfcheck as sc

SIDE = 64          # comparison size (pixels) of a rectified area
DIFF_T = 22.0      # Lab distance above which a pixel counts as "different from the empty picture" (product pixels)
HIST_BINS = (12, 4)  # hue x saturation bins of the product fingerprint


# ---------------------------------------------------------------- per-area features

def area_crop(img, a):
    """The area straightened and shrunk to SIDE x SIDE (BGR, uint8)."""
    return cv2.resize(sc.rectify(img, a), (SIDE, SIDE), interpolation=cv2.INTER_AREA)


def lab(crop):
    return cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)


def colourfulness(crop):
    """Hasler & Suesstrunk colourfulness: high for fruit, low for liners, trays and crate floors."""
    b, g, r = [c.astype(np.float32) for c in cv2.split(crop)]
    rg, yb = r - g, 0.5 * (r + g) - b
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def detail(crop):
    """Edge density (share of Canny edge pixels) and log Laplacian variance: produce has many small edges."""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float((cv2.Canny(g, 60, 150) > 0).mean()), float(np.log1p(cv2.Laplacian(g, cv2.CV_64F).var()))


def own_features(crop):
    e, l = detail(crop)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return {"colour": colourfulness(crop), "edges": e, "lapvar": l, "sat": float(hsv[:, :, 1].mean()), "val": float(hsv[:, :, 2].mean())}


def diff_map(crop_lab, ref_lab):
    """Per-pixel Lab distance after removing the global brightness shift (lighting changes over the day)."""
    shift = np.median(crop_lab[:, :, 0] - ref_lab[:, :, 0])
    d = crop_lab.copy(); d[:, :, 0] -= shift
    return np.sqrt(((d - ref_lab) ** 2).sum(axis=2))


def coverage(crop_lab, empty_lab):
    """Share of the area that differs from its emptiest picture: a code estimate of the fill."""
    return float((diff_map(crop_lab, empty_lab) > DIFF_T).mean())


def distance(crop_lab, ref_lab):
    return float(diff_map(crop_lab, ref_lab).mean())


def fingerprint(crop, mask=None):
    """Hue x saturation histogram of the product pixels (normalised)."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    m = None if mask is None else mask.astype(np.uint8) * 255
    if m is not None and m.sum() < 255 * 20:   # fewer than 20 product pixels: no fingerprint
        return None
    h = cv2.calcHist([hsv], [0, 1], m, list(HIST_BINS), [0, 180, 0, 256]).flatten()
    s = h.sum()
    return h / s if s else None


def fingerprint_distance(fp, ref_fp):
    if fp is None or ref_fp is None:
        return None
    return float(cv2.compareHist(fp.astype(np.float32), ref_fp.astype(np.float32), cv2.HISTCMP_BHATTACHARYYA))


def change(crop_lab, prev_lab):
    """How different the area is from the previous photo: mean Lab distance (brightness-corrected) and the share of
    changed pixels. Low on both = nobody touched the shelf."""
    d = diff_map(crop_lab, prev_lab)
    return float(d.mean()), float((d > DIFF_T).mean())


# ---------------------------------------------------------------- photo-level cover check

def region_mask(cam, shape):
    m = np.zeros(shape[:2], np.uint8)
    for aid in sc.CAM_AREAS[cam]:
        cv2.fillPoly(m, [sc.quad(sc.AREAS[aid]).astype(np.int32)], 255)
    return m


def photo_score(img, cam):
    """texture x (1 + colourfulness) inside the planogram region (ilies/detect_covered.py)."""
    m = region_mask(cam, img.shape) > 0
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    tex = cv2.Laplacian(g, cv2.CV_64F)[m].var()
    b, gg, r = [c.astype(np.float32)[m] for c in cv2.split(img)]
    rg, yb = r - gg, 0.5 * (r + gg) - b
    col = np.sqrt(rg.std() ** 2 + yb.std() ** 2) / 50.0
    return float(tex * (1 + col))


COVERED_IF_BELOW = 0.20   # share of the camera's median score (covers <= 0.12, empty shelves >= 0.28 on the training day)
