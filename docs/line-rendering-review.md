# Line-mask rendering review

The owner's authorized visual review identified a clear-word failure sensitive to excess crop height and two ruling-only detections. Before changing the production processing path, this experiment isolated Kraken's synthetic outside-polygon mask fill from geometry and model weights.

Kraken 6.0.3 native extract_polygons uses black fill for polygon masking and warping. `scriptalocal/kraken_rendering.py` adds an optional white-alpha mode: the source gets an opaque alpha channel, undergoes the same native mask/warp, then composites onto white. Genuine black source ink remains opaque and is not globally replaced or threshold-erased. Legacy mode is retained unchanged.

Four tests cover straight and curved geometry, source immutability, retained opaque black ink including an entirely black source, white outside-mask fill and rejection of unknown modes. They run without GPU access or network in the locked worker validation image:

```bash
docker run --rm --network none --read-only --user 1000:1000 \
  --cap-drop ALL --security-opt no-new-privileges --memory 2g --cpus 2 \
  --tmpfs /tmp:size=128m -v "$PWD/scriptalocal:/app/scriptalocal:ro" \
  -v "$PWD/integration_tests:/app/integration_tests:ro" \
  scriptalocal-worker-validation:locked python -m unittest discover \
  -s integration_tests -p test_rendering.py -v
```

The read-only database export helper reconstructed each real canvas from the original image, persisted layout/processing settings and committed raw polygons/baselines. Before considering white padding it regenerated all 24 legacy crops byte-for-byte at pixel level, confirming this replay matched the original processing path. All 24 white variants retained the same crop sizes. The raw image, original derivatives and previous results remain unchanged in private storage.

The existing FP32 baseline then read the white variants offline on host GPU 1 with a controlled decoding recipe. Six provisional AI reference cases comprise four text anchors and two ruling-only artifacts. Exact anchor matching, with only exterior whitespace stripped, remained unchanged; both nontext artifacts still produced text. Sixteen of 24 outputs changed, which does not establish better accuracy. No human-ground-truth CER/WER was measured.

Consequently white padding is an experiment, not the pipeline default. It does not solve excessive blank crop height, false rule detections or missed lines. Further crop/baseline geometry work and comparison against independently reviewed readings are needed. Any later renderer selection must bind its configuration/source to provenance and retain original pixels/evidence; this isolated helper is not yet wired into ImagePipeline.

Private experiment data lives under `data/validation/line-rendering/`. Public evidence is in `docs/validation/rendering-{pixels,comparison}.json` and `rendering-tests.txt`. The reusable helpers are `scripts/validation/export_render_cases.py` and the explicit rendering-against-cached-baseline mode of `compare_recognizer.py`. Do not reinterpret cached black-crop readings as ground truth for the white variants.
