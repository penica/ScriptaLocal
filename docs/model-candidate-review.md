# Model selection review, 12 November 2026

The current Bern TrOCR XVI–XVII checkpoint is a tested baseline, not a demonstrated best model. The first queued full-row run used 1,756,423,168 bytes peak allocated VRAM on one RTX 3060. Both 12 GB cards leave room for other candidates. Larger parameter count alone cannot establish better transcription of this user's handwriting; no corrected transcript or held-out user corpus has been supplied.

Priority candidates from author-published model cards:

| Candidate | Why evaluate | Hardware status |
| --- | --- | --- |
| Current dh-unibe/trocr-kurrent-XVI-XVII | Existing pinned early-modern Kurrent baseline | Actual FP32/FP16 and queued GPU inference verified |
| [dh-unibe/trocr-german-kurrent-xvi-xix-from-kurrent-v2](https://huggingface.co/dh-unibe/trocr-german-kurrent-xvi-xix-from-kurrent-v2) | Newer 0.3B derivative with broader historical coverage; MIT | Now verified offline on host GPU 1 with the same 24 frozen line crops |
| [dh-unibe/trocr-kurrent](https://huggingface.co/dh-unibe/trocr-kurrent) | Distinct 19th-century Kurrent training; useful chronology/hand comparison | Similar small architecture; actual local checkpoint not yet tested |
| [dh-unibe/kraken-german-xix-v1](https://huggingface.co/dh-unibe/kraken-german-xix-v1) | Alternative recognition engine with author-reported held-out-document results | Runtime/license/checkpoint-specific acceptance pending |
| [dh-unibe/qwen3.5-4b-german-xix-v2](https://huggingface.co/dh-unibe/qwen3.5-4b-german-xix-v2) | Larger Kurrent-specialized LoRA candidate with published external held-out evaluation | Needs its Qwen3.5-4B base; one-card fit, precision/quantization and old-host CPU/runtime must be measured |
| [dh-unibe/qwen3vl-german-xix-v2](https://huggingface.co/dh-unibe/qwen3vl-german-xix-v2) | Alternative specialized 4B vision model on comparable author benchmark | Needs Qwen3-VL-4B base; separate pinned runtime and actual memory checks pending |

The Qwen3.5 adapter card reports 6.80% CER on 2,751 held-out Swiss Federal Council lines, distinct from its own validation split. This is author evidence on 19th-century material, not expected accuracy on the user's early-modern mixed scripts/names. Both specialized Qwen cards require single-line crops; a larger vision model does not remove their segmentation requirement. They are adapter weights, not standalone models. Model-card example code requesting remote-code execution is not an instruction to enable it; inspect required processor/runtime code and pin audited dependencies first. The initial review used metadata only; the follow-up below records the subsequent pinned TrOCR candidate provisioning and runtime test.

[Medusa0.2Line](https://huggingface.co/ENC-PSL/Medusa0.2Line-9B) was also reviewed: its multilingual medieval 9th–15th-century focus is less closely matched to the current documents, so it is not the first replacement candidate merely because a larger version exists.

Two cards do not automatically provide one contiguous 24 GB allocation. Keep independent one-GPU workers as the tested baseline. Explicit model sharding can use both cards for a larger candidate; it requires a separate tested adapter/runtime and memory budget, as described in [Hugging Face Accelerate big-model inference](https://huggingface.co/docs/accelerate/en/concept_guides/big_model_inference). Parameter storage arithmetic is only a lower bound: vision encoding, activations, caches, precision/quantization and allocator overhead determine actual fit.

HTR-022 should compare these candidates on the exact same verified line crops and user-corrected readings, separately from automatic-layout runs. Measure CER/WER, omitted/invented text, names/dates, blank false positives, memory, latency and review rate. If manual crops work but automatic crops fail, fix segmentation before attributing errors to model size. Keep all untested candidates optional until their licenses, immutable revisions/checksums, runtime, GPU fit and offline behavior pass.

## First local comparison and owner-authorized visual review

The newer Bern TrOCR v2 checkpoint is now provisioned at immutable revision `bd1d96b67225b580846d7759f6ba74c66f56680e`; license/config/tokenizer/safetensors hashes are recorded in `docs/validation/trocr-new-candidate-manifest.json`. It ran offline on host GPU 1 against the same frozen 24 line PNGs, using an explicit common decoding recipe. It changed 22 outputs, but no human-ground-truth ranking follows from disagreement.

The owner authorized direct assistant image reading. A frozen private AI reference contains four legible-word anchors and two ruling-only artifacts, with human_confirmed=false. Both models output nonempty text on those two artifacts. One clear-word diagnostic then compared the original oversized crop, a manually tight original-image rectangle and a manually shortened rectified crop. The baseline matched the provisional AI reading only after tightening; the newer candidate did not match that case. This is evidence of crop sensitivity and a reason to inspect segmentation/rendering before selecting a larger model, not a corpus accuracy score. Raw readings and image cases remain private.
