"""Lazy Kraken 6 adapter; resident verified weights, no implicit downloads."""
import hashlib
from pathlib import Path

from .segmentation import DetectedLine,SegmentationError


class KrakenSegmenter:
    def __init__(self,path:Path,sha256:str,*,device='cuda:0'):
        from kraken.lib.vgsl import TorchVGSLModel
        self.path=Path(path)
        with self.path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=sha256:raise SegmentationError('Segmentation model checksum mismatch')
        self.model=TorchVGSLModel.load_model(str(self.path));self.device=device
        self.model_height=self.model.input[2]

    def __call__(self,canvas):
        from kraken import blla
        from kraken.lib.segmentation import extract_polygons
        segmentation=blla.segment(canvas,model=self.model,device=self.device,raise_on_error=True)
        for pixels,geometry in extract_polygons(canvas,segmentation):
            yield DetectedLine(tuple(map(tuple,geometry.boundary)),tuple(map(tuple,geometry.baseline)),pixels)
