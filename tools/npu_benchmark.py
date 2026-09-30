"""Prove the Snapdragon NPU is being used: time an ONNX model on CPU vs QNN (NPU).

Get a model from Qualcomm AI Hub (https://aihub.qualcomm.com), e.g. any vision model
exported for "Snapdragon X Elite CRD" in ONNX format, then:

    python tools/npu_benchmark.py path\\to\\model.onnx

On x64 (AMD/Intel) machines this compares CPU vs DirectML instead.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from sahay import hardware  # noqa: E402

_TYPES = {"tensor(float)": np.float32, "tensor(float16)": np.float16, "tensor(uint8)": np.uint8,
          "tensor(int8)": np.int8, "tensor(int32)": np.int32, "tensor(int64)": np.int64}


def bench(path: str, provider: str, runs: int = 30) -> float:
    import onnxruntime as ort

    providers, opts = hardware.ort_session_options(provider)
    sess = ort.InferenceSession(path, providers=providers, provider_options=opts)
    feeds = {}
    for i in sess.get_inputs():
        shape = [d if isinstance(d, int) and d > 0 else 1 for d in i.shape]
        feeds[i.name] = np.random.rand(*shape).astype(_TYPES.get(i.type, np.float32))
    for _ in range(3):
        sess.run(None, feeds)
    t = time.perf_counter()
    for _ in range(runs):
        sess.run(None, feeds)
    active = sess.get_providers()[0]
    ms = (time.perf_counter() - t) / runs * 1000
    print(f"{provider:>24} -> ran on {active:<24} {ms:8.2f} ms/inference")
    return ms


def main() -> None:
    hw = hardware.detect()
    print(hw.summary())
    print("ONNX Runtime providers:", hw.providers_available or "onnxruntime not installed")
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cpu = bench(sys.argv[1], "CPUExecutionProvider")
    if hw.best_provider != "CPUExecutionProvider":
        acc = bench(sys.argv[1], hw.best_provider)
        print(f"Speed-up on {hw.accelerator}: {cpu / acc:.1f}x")


if __name__ == "__main__":
    main()
