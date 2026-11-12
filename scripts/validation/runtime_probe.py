"""Run inside a single-GPU container; fail rather than report inferred support."""
import json
import os
import platform
import subprocess
import time

import numpy as np
import torch

torch.set_num_threads(2)
assert torch.cuda.is_available(), "CUDA is unavailable"
assert torch.cuda.device_count() == 1, "Expected exactly one visible CUDA device"
started = time.monotonic()
torch.manual_seed(17)
a = torch.randn(512, 512)
b = torch.randn(512, 512)
expected = a @ b
actual = (a.cuda() @ b.cuda()).cpu()
torch.testing.assert_close(actual, expected, rtol=1e-3, atol=1e-3)
conv = torch.nn.Conv2d(3, 8, 3).cuda()
assert conv(torch.ones(1, 3, 32, 32, device="cuda")).shape == (1, 8, 30, 30)
assert np.isfinite(np.linalg.svd(np.eye(8))[1]).all()
torch.cuda.synchronize()
report = {
    "python": platform.python_version(),
    "torch": torch.__version__,
    "numpy": np.__version__,
    "cuda_runtime": torch.version.cuda,
    "cudnn": torch.backends.cudnn.version(),
    "visible_cuda_devices": torch.cuda.device_count(),
    "gpu": torch.cuda.get_device_name(0),
    "gpu_identity": subprocess.check_output([
        "nvidia-smi", "--query-gpu=uuid,name", "--format=csv,noheader"
    ], text=True).strip(),
    "peak_cuda_bytes": torch.cuda.max_memory_allocated(),
    "seconds": round(time.monotonic() - started, 3),
    "cpu_matrix_and_svd": "passed",
    "cuda_matrix_and_convolution": "passed",
    "network_disabled_expected": os.environ.get("VALIDATION_OFFLINE") == "1",
}
print(json.dumps(report, indent=2))
