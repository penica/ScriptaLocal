"""Functional manuscript probe with an explicit manual cell; artifacts stay private."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import torch
from PIL import Image, ImageOps
import kraken
from kraken import blla
from kraken.lib.segmentation import extract_polygons
from kraken.lib.vgsl import TorchVGSLModel
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

parser = argparse.ArgumentParser()
parser.add_argument('--image', type=Path, required=True)
parser.add_argument('--bounds', type=int, nargs=4, required=True, metavar=('LEFT','TOP','RIGHT','BOTTOM'))
parser.add_argument('--model', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--preserve-scale', action='store_true',
                    help='Pad to model canvas height instead of magnifying a short cell')
args = parser.parse_args()
torch.set_num_threads(2)
assert torch.cuda.device_count() == 1
args.output.mkdir(parents=True, exist_ok=True)
with Image.open(args.image) as source:
    original = ImageOps.exif_transpose(source).convert('RGB')
left, top, right, bottom = args.bounds
assert 0 <= left < right <= original.width and 0 <= top < bottom <= original.height
cell = original.crop(tuple(args.bounds))
cell.save(args.output / 'manual-cell.png')
started = time.monotonic()
print('Running Kraken learned segmentation on manual cell', flush=True)
# Kraken 6.0.3's implicit default uses resources.files on a module, which fails
# on Python 3.11. Resolve the bundled candidate explicitly as in kraken_probe.
segmentation_model = TorchVGSLModel.load_model(str(Path(kraken.__file__).parent / 'blla.mlmodel'))
pad_x = pad_y = 32 if args.preserve_scale else 0
canvas = cell
if args.preserve_scale:
    canvas = Image.new('RGB', (max(400, cell.width + 2*pad_x),
                              max(segmentation_model.input[2], cell.height + 2*pad_y)), 'white')
    canvas.paste(cell, (pad_x, pad_y))
canvas.save(args.output / 'segmentation-canvas.png')
segmentation = blla.segment(canvas, model=segmentation_model, device='cuda:0', raise_on_error=True)
segmented = time.monotonic()
print(f'Kraken found {len(segmentation.lines)} lines in {segmented-started:.3f}s', flush=True)
if not segmentation.lines:
    (args.output / 'result.json').write_text(json.dumps({
        'state':'needs-review', 'reason':'No lines detected in supplied nonblank manual cell',
        'lines':[], 'manual_cell_bounds':args.bounds,
        'segmentation_seconds':round(segmented-started,3),
        'recognition_executed':False,
    },indent=2)+'\n')
    raise SystemExit('Segmentation failed to find lines; recognition was not executed')
processor = TrOCRProcessor.from_pretrained(args.model, local_files_only=True)
recognizer = VisionEncoderDecoderModel.from_pretrained(args.model, local_files_only=True,
                                                       use_safetensors=True).eval().cuda()
lines = []
for index, (crop, geometry) in enumerate(extract_polygons(canvas, segmentation)):
    crop.save(args.output / f'line-{index:03}.png')
    pixels = processor(images=crop.convert('RGB'), return_tensors='pt').pixel_values.cuda()
    with torch.inference_mode():
        tokens = recognizer.generate(pixels, max_new_tokens=128)
    text = processor.batch_decode(tokens, skip_special_tokens=True)[0]
    lines.append({'order':index, 'text':text,
                  'baseline':[[x+left-pad_x,y+top-pad_y] for x,y in geometry.baseline],
                  'polygon':[[x+left-pad_x,y+top-pad_y] for x,y in geometry.boundary],
                  'truncated': int(tokens[0,-1]) != recognizer.generation_config.eos_token_id})
torch.cuda.synchronize()
manifest = json.loads((args.model / 'manifest.json').read_text())
result = {'original_size':list(original.size), 'manual_cell_bounds':args.bounds,
          'image_sha256':hashlib.sha256(args.image.read_bytes()).hexdigest(),
          'cell_text':'\n'.join(x['text'] for x in lines), 'lines':lines,
          'model':manifest['repository'], 'revision':manifest['revision'],
          'segmentation_seconds':round(segmented-started,3),
          'total_seconds':round(time.monotonic()-started,3),
          'peak_cuda_bytes':torch.cuda.max_memory_allocated(),
          'accuracy':'unmeasured: human ground truth not provided',
          'preserve_scale':args.preserve_scale, 'segmentation_canvas':list(canvas.size),
          'layout':'manual single cell; automatic divider detection not implemented'}
(args.output / 'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in {'cell_text','lines'}},indent=2))
