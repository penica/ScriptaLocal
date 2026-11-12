"""Kraken polygon rendering without confusing synthetic mask fill with source ink."""
from PIL import Image

from .segmentation import SegmentationError


def render_lines(canvas,segmentation,*,background='legacy-black'):
    from kraken.lib.segmentation import extract_polygons
    if background not in {'legacy-black','white-alpha'}:raise ValueError('Unsupported line mask background')
    if background=='legacy-black':
        yield from extract_polygons(canvas,segmentation)
        return
    # An opaque alpha channel follows the same polygon mask and warp as RGB.
    # Source black ink stays opaque; only synthetic outside-mask fill becomes
    # transparent and is composited onto white. Never replace black RGB pixels.
    for pixels,geometry in extract_polygons(canvas.convert('RGBA'),segmentation):
        if pixels.mode!='RGBA':raise SegmentationError('Line renderer did not preserve alpha')
        white=Image.new('RGBA',pixels.size,(255,255,255,255))
        yield Image.alpha_composite(white,pixels).convert('RGB'),geometry
