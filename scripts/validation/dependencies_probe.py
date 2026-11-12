"""Exercise CPU image libraries and model architecture without downloading weights."""
import importlib.metadata
import json

import cv2
import numpy as np
import scipy.ndimage
import sklearn
import skimage.measure
import torch
from PIL import Image
from transformers import TrOCRConfig, TrOCRForCausalLM, ViTConfig, ViTModel
from transformers import VisionEncoderDecoderModel
from kraken import blla, binarization
from kraken.lib import segmentation

torch.set_num_threads(2)
image = np.full((160, 400), 255, dtype=np.uint8)
image[:, 198:201] = 0
binary = cv2.adaptiveThreshold(image, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                              cv2.THRESH_BINARY_INV, 15, 5)
vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (1, 60)))
assert np.count_nonzero(vertical[:, 198:201]) == 480
assert skimage.measure.label(vertical > 0).max() == 1
assert scipy.ndimage.label(vertical > 0)[1] == 1
assert binarization.nlbin(Image.fromarray(image)).size == (400, 160)

# A tiny randomly initialized encoder/decoder validates actual generation kernels.
# It is deliberately not used as evidence of handwriting transcription quality.
encoder = ViTModel(ViTConfig(image_size=32, patch_size=16, hidden_size=32,
                           num_hidden_layers=1, num_attention_heads=2,
                           intermediate_size=64))
decoder = TrOCRForCausalLM(TrOCRConfig(vocab_size=32, d_model=32,
                          decoder_layers=1, decoder_attention_heads=2,
                          decoder_ffn_dim=64, max_position_embeddings=32))
model = VisionEncoderDecoderModel(encoder=encoder, decoder=decoder).eval().cuda()
model.config.decoder_start_token_id = 2
model.config.pad_token_id = 1
with torch.inference_mode():
    generated = model.generate(torch.ones(1, 3, 32, 32, device="cuda"),
                               decoder_start_token_id=2, pad_token_id=1,
                               max_new_tokens=4)
assert generated.shape[0] == 1 and 1 < generated.shape[1] <= 5
packages = ['torch', 'torchvision', 'transformers', 'kraken', 'numpy',
            'opencv-python-headless', 'scipy', 'scikit-image', 'scikit-learn', 'Pillow']
print(json.dumps({
    "versions": {p: importlib.metadata.version(p) for p in packages},
    "cpu_opencv_geometry": "passed",
    "cpu_scipy_skimage": "passed",
    "kraken_binarization_and_segmentation_imports": "passed",
    "tiny_random_trocr_cuda_generation": "passed",
    "handwriting_accuracy": "not tested: random weights and synthetic geometry only",
    "generated_shape": list(generated.shape),
}, indent=2))
