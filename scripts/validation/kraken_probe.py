"""Exercise the bundled learned segmentation model offline on a synthetic cell."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

from PIL import Image, ImageDraw, ImageFont
import torch
import kraken
from kraken import blla
from kraken.lib.vgsl import TorchVGSLModel

torch.set_num_threads(2)
model_path = Path(kraken.__file__).parent / 'blla.mlmodel'
with model_path.open('rb') as stream:
    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
model = TorchVGSLModel.load_model(str(model_path))
image = Image.new('RGB', (1100, 300), 'white')
draw = ImageDraw.Draw(image)
font = ImageFont.load_default(size=36)
for y, text in [(35, 'This synthetic line checks the segmentation runtime.'),
                (110, 'A second line is present inside the same cell.'),
                (185, 'Handwriting accuracy requires real manuscripts.')]:
    draw.text((30, y), text, fill='black', font=font)
started = time.monotonic()
result = blla.segment(image, model=model, device='cpu', raise_on_error=True)
assert result.type == 'baselines'
assert len(result.lines) >= 1, 'No line found in synthetic multiline cell'
for line in result.lines:
    assert len(line.baseline) >= 2 and len(line.boundary) >= 3
print(json.dumps({
    'kraken': importlib.metadata.version('kraken'),
    'model': 'kraken/blla.mlmodel (bundled candidate, not manuscript-selected)',
    'model_sha256': checksum, 'model_bytes': model_path.stat().st_size,
    'model_type': model.model_type,
    'model_metadata': {k: model.user_metadata.get(k) for k in
                       ['class_mapping', 'legacy_polygons', 'model_type']},
    'device': 'cpu', 'seconds': round(time.monotonic()-started, 3),
    'detected_lines': len(result.lines), 'synthetic_input_lines': 3,
    'offline_learned_segmentation': 'passed',
    'handwriting_layout_accuracy': 'not measured; synthetic input only',
}, indent=2, default=str))
