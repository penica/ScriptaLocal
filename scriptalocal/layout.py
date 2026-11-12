"""Conservative table geometry. Automatic proposals always require review."""
from dataclasses import dataclass
import math
from numbers import Real
from uuid import NAMESPACE_URL, uuid5

import cv2
import numpy as np
from PIL import Image

from .images import Affine, DecodedImage


@dataclass(frozen=True)
class LayoutSettings:
    minimum_line_fraction: float = 0.50
    minimum_ink_support: float = 0.55
    maximum_gap_fraction: float = 0.22
    maximum_angle_degrees: float = 6.0
    merge_distance: float = 6.0
    support_radius: int = 3
    adaptive_block_size: int = 31
    adaptive_offset: float = 12.0
    outer_border_proximity_fraction: float = 0.025
    outer_rule_support_preference: float = 0.05
    maximum_candidates: int = 512
    maximum_manual_dividers: int = 256
    minimum_cell_width: float = 2.0
    context_padding: int = 12

    def __post_init__(self):
        for name in ['minimum_line_fraction','minimum_ink_support','maximum_gap_fraction']:
            value=getattr(self,name)
            if not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f'{name} must be in (0,1]')
        if not 0 <= self.outer_border_proximity_fraction < .5:
            raise ValueError('Invalid outer border proximity fraction')
        if not 0 <= self.outer_rule_support_preference <= 1:
            raise ValueError('Invalid outer rule support preference')
        if not 0 <= self.maximum_angle_degrees < 45:
            raise ValueError('Invalid maximum border angle')
        for name in ['merge_distance','minimum_cell_width','adaptive_offset']:
            if not math.isfinite(getattr(self,name)) or getattr(self,name) <= 0:
                raise ValueError(f'{name} must be finite and positive')
        for name in ['support_radius','maximum_candidates','maximum_manual_dividers','context_padding','adaptive_block_size']:
            value=getattr(self,name)
            if type(value) is not int or value < 0:
                raise ValueError(f'{name} must be a nonnegative integer')
        if not self.maximum_candidates or not self.maximum_manual_dividers or self.adaptive_block_size < 3 or self.adaptive_block_size % 2 != 1:
            raise ValueError('Candidate limit must be positive and threshold block must be odd and >=3')


@dataclass(frozen=True)
class Border:
    top_x: float
    bottom_x: float
    source: str
    ink_support_fraction: float | None = None


@dataclass(frozen=True)
class Cell:
    id: str
    order: int
    polygon: tuple[tuple[float,float], ...]


@dataclass(frozen=True)
class Layout:
    cells: tuple[Cell, ...]
    borders: tuple[Border, ...]
    warnings: tuple[str, ...]
    coordinate_space: str = 'oriented_pixel_edges'
    excluded_outer_rules: tuple[Border, ...] = ()


@dataclass(frozen=True)
class CellCrop:
    pixels: Image.Image
    oriented_from_crop: Affine
    original_from_crop: Affine
    cell_polygon_in_crop: tuple[tuple[float,float], ...]
    cell_mask: np.ndarray
    context_clipped: bool


def _longest_gap(support):
    missing=np.concatenate(([False],~support,[False])).astype(np.int8)
    changes=np.diff(missing)
    starts=np.flatnonzero(changes==1)
    ends=np.flatnonzero(changes==-1)
    return int(max(ends-starts,default=0))


def _support(binary,top_x,bottom_x,radius):
    height,width=binary.shape
    centers=np.rint(np.linspace(top_x,bottom_x,height)).astype(int)
    rows=np.arange(height)
    supported=np.zeros(height,dtype=bool)
    for offset in range(-radius,radius+1):
        columns=centers+offset
        valid=(columns>=0)&(columns<width)
        supported[valid] |= binary[rows[valid],columns[valid]]>0
    return supported


def _candidate_borders(binary,settings):
    height,width=binary.shape
    vertical=cv2.morphologyEx(binary,cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(3,height//3))))
    vertical=cv2.morphologyEx(vertical,cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(3,int(height*settings.maximum_gap_fraction)))))
    segments=[]
    # Morphology supports broken straight rules; the raw-ink pass retains mild
    # slants that a tall vertical kernel would otherwise discard.
    for evidence in [vertical,binary]:
        found=cv2.HoughLinesP(evidence,1,np.pi/1800,
            threshold=max(12,int(height*.22)),
            minLineLength=max(10,int(height*settings.minimum_line_fraction)),
            maxLineGap=max(2,int(height*settings.maximum_gap_fraction)))
        if found is not None:
            segments.extend(found[:,0,:])
    raw=[]
    for x1,y1,x2,y2 in segments:
        if y2<y1:
            x1,y1,x2,y2=x2,y2,x1,y1
        rise=float(y2-y1)
        if rise<=0 or abs(math.degrees(math.atan2(float(x2-x1),rise)))>settings.maximum_angle_degrees:
            continue
        slope=float(x2-x1)/rise
        top=float(x1)-slope*float(y1)
        bottom=top+slope*height
        if not (0<=top<=width and 0<=bottom<=width):
            continue
        raw.append((rise,top,bottom))
    raw.sort(reverse=True)
    capped=len(raw)>settings.maximum_candidates
    candidates=[]
    for _,top,bottom in raw[:settings.maximum_candidates]:
        supported=_support(binary,top,bottom,settings.support_radius)
        fraction=float(supported.mean())
        if fraction>=settings.minimum_ink_support and _longest_gap(supported)<=height*settings.maximum_gap_fraction:
            candidates.append(Border(top,bottom,'automatic',fraction))
    # Both endpoints must agree before merging: near intersections are not a
    # reason to merge different slanted borders into one column boundary.
    groups=[]
    for candidate in sorted(candidates,key=lambda b:(b.top_x,b.bottom_x)):
        for group in groups:
            top=float(np.median([b.top_x for b in group]))
            bottom=float(np.median([b.bottom_x for b in group]))
            if max(abs(candidate.top_x-top),abs(candidate.bottom_x-bottom))<=settings.merge_distance+.5:
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    merged=[]
    for group in groups:
        top=float(np.median([b.top_x for b in group]))
        bottom=float(np.median([b.bottom_x for b in group]))
        fraction=float(_support(binary,top,bottom,settings.support_radius).mean())
        merged.append(Border(top,bottom,'automatic',fraction))
    merged.sort(key=lambda b:(b.top_x+b.bottom_x)/2)
    # Initial cluster medians can move as further Hough segments join. Merge
    # adjacent final representatives again rather than leaving duplicate rules
    # that cross by a fraction of a pixel and manufacture a zero-width cell.
    changed=True
    while changed:
        changed=False;consolidated=[]
        for border in merged:
            if consolidated and max(abs(border.top_x-consolidated[-1].top_x),
                                    abs(border.bottom_x-consolidated[-1].bottom_x))<=settings.merge_distance+.5:
                previous=consolidated.pop()
                top=(previous.top_x+border.top_x)/2
                bottom=(previous.bottom_x+border.bottom_x)/2
                fraction=float(_support(binary,top,bottom,settings.support_radius).mean())
                consolidated.append(Border(top,bottom,'automatic',fraction));changed=True
            else:
                consolidated.append(border)
        merged=consolidated
    return merged,capped


def _manual_borders(dividers,width,height,maximum_dividers):
    borders=[]
    if len(dividers)>maximum_dividers:
        raise ValueError('Too many manual dividers')
    for top,bottom in dividers:
        x1,y1=top; x2,y2=bottom
        if not all(isinstance(v,Real) and not isinstance(v,bool) and math.isfinite(v) for v in (x1,y1,x2,y2)):
            raise ValueError('Divider coordinates must be finite')
        if not (0<=x1<=width and 0<=x2<=width and 0<=y1<y2<=height):
            raise ValueError('Invalid divider endpoints')
        slope=(x2-x1)/(y2-y1)
        bx1=x1-slope*y1; bx2=bx1+slope*height
        if not (0<bx1<width and 0<bx2<width):
            raise ValueError('Manual divider must remain inside the image at top and bottom')
        borders.append(Border(float(bx1),float(bx2),'manual'))
    return borders


def detect_layout(image:DecodedImage, *, settings:LayoutSettings=LayoutSettings(),
                  corrected_dividers=None,correction_space='original_encoded_pixel_edges')->Layout:
    """Propose cells, or validate corrections in an explicit coordinate frame.

    corrected_dividers=None means detection. An empty list explicitly requests a
    single cell. Corrections replace the complete proposal and are not reordered.
    """
    width,height=image.pixels.size
    warnings=[]
    excluded=[]
    if correction_space not in {'original_encoded_pixel_edges','oriented_pixel_edges'}:
        raise ValueError('Unknown correction coordinate space')
    if corrected_dividers is not None:
        if correction_space=='original_encoded_pixel_edges':
            converted=[]
            encoded_width,encoded_height=image.encoded_size
            for endpoints in corrected_dividers:
                pair=[]
                for x,y in endpoints:
                    if not all(isinstance(v,Real) and not isinstance(v,bool) and math.isfinite(v) for v in (x,y)):
                        raise ValueError('Divider coordinates must be finite numbers')
                    if not (0<=x<=encoded_width and 0<=y<=encoded_height):
                        raise ValueError('Divider endpoints are outside the encoded image')
                    pair.append(image.oriented_from_original.point(x,y))
                converted.append(pair)
            corrected_dividers=converted
        internal=_manual_borders(corrected_dividers,width,height,settings.maximum_manual_dividers)
        left=Border(0,0,'image-edge');right=Border(width,width,'image-edge')
    else:
        gray=cv2.cvtColor(np.array(image.pixels),cv2.COLOR_RGB2GRAY)
        binary=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY_INV,settings.adaptive_block_size,
                                     settings.adaptive_offset)
        internal,capped=_candidate_borders(binary,settings)
        warnings.append('automatic_layout_requires_review')
        if capped:
            warnings.append('candidate_limit_reached')
        left=Border(0,0,'image-edge');right=Border(width,width,'image-edge')
        # Account for subpixel line centers and thickness at the threshold.
        tolerance=width*settings.outer_border_proximity_fraction+settings.support_radius
        # A weak paper-edge/shadow rule outside a stronger table rule must not
        # become a phantom cell. Keep the excluded evidence for review. Equal
        # strength outer/narrow-column rules retain the outermost boundary.
        while len(internal)>1 and max(internal[1].top_x,internal[1].bottom_x)<=tolerance and (
                internal[1].ink_support_fraction-internal[0].ink_support_fraction > settings.outer_rule_support_preference):
            excluded.append(internal.pop(0))
        while len(internal)>1 and min(internal[-2].top_x,internal[-2].bottom_x)>=width-tolerance and (
                internal[-2].ink_support_fraction-internal[-1].ink_support_fraction > settings.outer_rule_support_preference):
            excluded.append(internal.pop())
        if internal and max(internal[0].top_x,internal[0].bottom_x)<=tolerance:
            left=internal.pop(0)
        if internal and min(internal[-1].top_x,internal[-1].bottom_x)>=width-tolerance:
            right=internal.pop()
        if left.source!='image-edge' or right.source!='image-edge':
            warnings.append('outer_rule_bounds_require_review')
        if not internal:
            warnings.append('no_internal_dividers_detected')
    boundaries=[left,*internal,right]
    cells=[]
    for index,(a,b) in enumerate(zip(boundaries,boundaries[1:])):
        if min(b.top_x-a.top_x,b.bottom_x-a.bottom_x)<settings.minimum_cell_width:
            if corrected_dividers is not None:
                raise ValueError('Dividers cross, duplicate, are out of order or form a cell that is too narrow')
            # Do not silently discard a border and fabricate a convenient layout.
            return Layout((),tuple(boundaries),tuple(warnings+['ambiguous_crossing_or_narrow_borders']),
                          excluded_outer_rules=tuple(excluded))
        polygon=((a.top_x,0.),(b.top_x,0.),(b.bottom_x,float(height)),(a.bottom_x,float(height)))
        identity=f'scriptalocal:cell:{image.sha256}:'+repr(tuple(round(v,6) for point in polygon for v in point))
        cells.append(Cell(str(uuid5(NAMESPACE_URL,identity)),index,polygon))
    return Layout(tuple(cells),tuple(boundaries),tuple(warnings),excluded_outer_rules=tuple(excluded))


def extract_cell(image:DecodedImage,cell:Cell,*,settings:LayoutSettings=LayoutSettings())->CellCrop:
    """Preserve all source ink, including context outside a slanted cell polygon."""
    width,height=image.pixels.size
    xs,ys=zip(*cell.polygon)
    if any(not math.isfinite(v) for v in (*xs,*ys)) or min(xs)<0 or min(ys)<0 or max(xs)>width or max(ys)>height:
        raise ValueError('Cell geometry is outside the oriented image')
    desired=(math.floor(min(xs))-settings.context_padding,math.floor(min(ys))-settings.context_padding,
             math.ceil(max(xs))+settings.context_padding,math.ceil(max(ys))+settings.context_padding)
    left,top,right,bottom=max(0,desired[0]),max(0,desired[1]),min(width,desired[2]),min(height,desired[3])
    if left>=right or top>=bottom:
        raise ValueError('Cell crop must have area')
    local=tuple((x-left,y-top) for x,y in cell.polygon)
    pixels=image.pixels.crop((left,top,right,bottom))
    mask=np.zeros((pixels.height,pixels.width),dtype=np.uint8)
    cv2.fillPoly(mask,[np.rint(local).astype(np.int32)],255)
    oriented_from_crop=Affine(1,0,left,0,1,top)
    original_from_crop=image.original_from_oriented.after(oriented_from_crop)
    return CellCrop(pixels,oriented_from_crop,original_from_crop,local,mask,desired!=(left,top,right,bottom))
