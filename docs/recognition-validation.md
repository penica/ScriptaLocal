# Resident recognition adapter validation

HTR-015 now includes a resident local-only Bern TrOCR adapter and GPU-independent bounded batching. It does not yet deploy a leased PostgreSQL recognition worker or OCR HTTP service.

`TrOCRRecognizer` verifies the externally pinned manifest SHA-256 and every listed file's regular-file status, length and checksum before loading. It uses local_files_only, safetensors and disabled remote code, keeps the processor/model resident, and requires exactly one assigned visible GPU as cuda:0. Precision is explicit FP32 or FP16; recovery never switches precision, model, GPU or provider. No download is attempted during inference.

`recognize_lines` accepts segmented single-line images with unique UUIDs. It enforces line count, side/pixel limits and count/pixel batch budgets before inference. Current configurable defaults are four lines per batch, 4M pixels per line, 8M pixels per batch, 128 generated tokens, four beams and four memory recoveries. These are component settings, not agreed global HTTP result limits.

Text is preserved exactly as decoded with tokenization cleanup disabled. Empty/whitespace-only output is needs-review, never blank; generation without EOS after the initial decoder token is truncated/needs-review. Very wide line images are retained with a review reason rather than split or silently discarded. Recognition doesn't remove segmentation/layout warnings: result assembly must preserve those separately. Sequence scores are not exposed as confidence percentages.

A CUDA memory exception exits its original exception scope before recovery, releasing tensor traceback references. The scheduler clears reclaimable cache through the adapter and splits only into strictly smaller batches. Single-line exhaustion produces explicit failed/null output; exhausted recovery budgets fail remaining work rather than repeatedly invoking the GPU. Original IDs/order are preserved, including failures. Unexpected backend failures remain failures and cannot turn into successful empty text. Model-load failures currently propagate to the future worker startup path; readiness/failure publication still requires integration.

## Verified runs

All runs used the locked GPU validation image, host RTX 3060 device 0 exposed alone, UID 1000, read-only container/model/source mounts, no network and offline library settings. Writable raw output was limited to ignored validation storage. Inputs were three manually segmented lines from one real manuscript cell. This is not a held-out accuracy corpus.

| Precision | First three-line batch | Warm three-line batch | Peak allocated CUDA bytes |
| --- | --- | --- | --- |
| FP32 | 1.211 s | 0.545 s | 1,478,038,528 |
| FP16 | 0.979 s | 0.348 s | 761,414,656 |

Each model instance was reused across two calls and produced identical text on reuse. FP32/FP16 text hashes also matched on these three inputs. Keep FP32 as the initial baseline; optional FP16 runtime support is not accuracy acceptance across handwriting/long-line classes. These are single warm-batch measurements, not p50/p95 service latency or one/two-GPU throughput benchmarks.

A controlled `torch.cuda.OutOfMemoryError` on the first real generation call exercised adapter exception handling and scheduler recovery. It split the three-line batch into one/two lines, completed actual GPU inference with the same resident model, and reproduced baseline text hashes. This is fault injection, not natural VRAM exhaustion. Persistent-error CPU tests verify the global retry bound and explicit failed output.

A separate actual GPU run with maximum_new_tokens=1 generated one token for each input, detected all three as truncated, and returned needs-review without false blank states. Raw manuscript text/images were not published.

Seven new CPU tests cover batching/identity/text, empty/truncated outputs, successful OOM splitting, bounded persistent OOM, input/backend limits, wide-line review and pinned-manifest tampering/path/symlink rejection. `make validation-cpu` now passes 63 checks. Evidence lives in `docs/validation/recognition-*.json` and `recognition-tests.txt`.

## Reproduction

Use the existing locked runtime and read-only model mount. The probe requires three private line PNGs from reusable segmentation. For each precision, run separately:

```bash
docker run --rm --network none --device nvidia.com/gpu=0 \
  --user 1000:1000 --read-only --cap-drop ALL \
  --security-opt no-new-privileges --memory 8g --cpus 2 \
  --tmpfs /tmp:size=256m -e OMP_NUM_THREADS=2 -e OPENBLAS_NUM_THREADS=2 \
  -e PYTHONDONTWRITEBYTECODE=1 -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -v "$PWD/scriptalocal:/app/scriptalocal:ro" -v "$PWD/scripts:/app/scripts:ro" \
  -v "$PWD/data/models/trocr-kurrent-XVI-XVII:/model:ro" \
  -v "$PWD/data/validation/reusable-segmentation:/lines:ro" \
  -v "$PWD/data/validation/recognition-fp32:/output" -w /app \
  scriptalocal-validation:locked python -m scripts.validation.recognition_probe \
  --model /model --lines /lines --output /output --precision fp32
```

Create the output directory first. Use a separate output folder with precision fp16 for comparison, or fp32 plus --inject-oom for the controlled error case. The real truncation probe is `python -m scripts.validation.recognition_truncation_probe` under the same mounts/device. Image identity, pinned models, source hashes and exact settings are recorded in each report. Raw output files contain manuscript readings and must remain private.

Remaining acceptance includes queue claims/heartbeats/cancellation with these actual adapters, full-row assembly/HTTP integration, wider annotated line classes, CER/WER, full service backpressure and two-GPU benchmarking. The current evidence does not complete HTR-015 or HTR-022.
