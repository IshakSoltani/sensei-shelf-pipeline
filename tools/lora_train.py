"""Run mlx_vlm.lora with a memory cap, so a training run cannot take the whole 16 GB.

mlx-vlm's trainer wires up to the GPU's "max recommended working set" (11.8 GB of 16 on the M5) and MLX keeps every freed
buffer in a cache whose default limit is 15.2 GB, so a long run grows to its peak and stays there. This launcher clamps both
before handing over to the trainer. Same arguments as `python -m mlx_vlm.lora`, plus:

  --wired-gb N   wired (non-pageable) GPU memory cap, default 7
  --cache-gb N   MLX buffer-cache cap, default 1.5

The laptop crashed on 10 Oct at 22:05 with a training run, a checkpoint evaluation and a dataset build running at once;
the caps make a single run leave room for the OS, and a second job degrades to paging instead of a panic. Still: one
heavy GPU job at a time.
"""
import sys
import mlx.core as mx

def _take(flag, default):
    if flag in sys.argv:
        i = sys.argv.index(flag); v = float(sys.argv[i + 1]); del sys.argv[i:i + 2]; return v
    return default

wired_gb = _take("--wired-gb", 7.0)
cache_gb = _take("--cache-gb", 1.5)
GB = 2 ** 30
mx.set_cache_limit(int(cache_gb * GB))
_real_set_wired = mx.set_wired_limit
mx.set_wired_limit = lambda n: _real_set_wired(min(int(n), int(wired_gb * GB)))   # the trainer sets 11.8 GB; clamp it
print(f"memory caps: wired {wired_gb:g} GB, buffer cache {cache_gb:g} GB", flush=True)

import runpy   # noqa: E402
runpy.run_module("mlx_vlm.lora", run_name="__main__", alter_sys=True)   # the trainer builds its parser under __main__
