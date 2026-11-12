"""Offline real-weight inference smoke test; not an accuracy benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import torch
from PIL import Image, ImageDraw
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

parser = argparse.ArgumentParser()
parser.add_argument('--model', type=Path, required=True)
parser.add_argument('--line-image', type=Path)
args = parser.parse_args()
torch.set_num_threads(2)
assert torch.cuda.device_count() == 1
manifest = json.loads((args.model / 'manifest.json').read_text())
for item in manifest['files']:
    with (args.model / item['path']).open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == item['sha256']
if args.line_image:
    with Image.open(args.line_image) as source:
        image = source.convert('RGB')
else:
    image = Image.new('RGB', (640, 80), 'white')
    ImageDraw.Draw(image).text((20, 25), 'Synthetic runtime validation only', fill='black')
started = time.monotonic()
processor = TrOCRProcessor.from_pretrained(args.model, local_files_only=True)
model = VisionEncoderDecoderModel.from_pretrained(
    args.model, local_files_only=True, use_safetensors=True).eval().cuda()
loaded = time.monotonic()
pixels = processor(images=image, return_tensors='pt').pixel_values.cuda()
with torch.inference_mode():
    tokens = model.generate(pixels, max_new_tokens=16)
torch.cuda.synchronize()
text = processor.batch_decode(tokens, skip_special_tokens=True)[0]
print(json.dumps({
    'model': manifest['repository'], 'revision': manifest['revision'],
    'checksum_verification': 'passed', 'parameters': sum(p.numel() for p in model.parameters()),
    'precision': str(next(model.parameters()).dtype),
    'model_load_seconds': round(loaded-started, 3),
    'generation_seconds': round(time.monotonic()-loaded, 3),
    'peak_cuda_bytes': torch.cuda.max_memory_allocated(),
    'generated_tokens': tokens.shape[1],
    'decoded_output_sha256': hashlib.sha256(text.encode()).hexdigest(),
    'input_type': 'provided single-line image' if args.line_image else 'synthetic printed line',
    'handwriting_accuracy': 'not measured; no ground-truth comparison',
    'offline_real_weight_inference': 'passed',
}, indent=2))
