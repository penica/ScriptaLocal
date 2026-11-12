"""Bounded, ink-preserving per-cell segmentation with explicit review states."""
from dataclasses import dataclass
import math
from statistics import median
from uuid import UUID,uuid5

import cv2
import numpy as np
from PIL import Image

from .images import Affine
from .layout import CellCrop


class SegmentationError(ValueError):pass


@dataclass(frozen=True)
class SegmentationSettings:
    padding:int=32
    minimum_width:int=400
    model_height:int=1800
    maximum_canvas_pixels:int=16_000_000
    maximum_lines:int=256
    maximum_line_pixels:int=4_000_000
    overlap_baseline_distance:float=4.0

    def __post_init__(self):
        for name in ['padding','minimum_width','model_height','maximum_canvas_pixels','maximum_lines','maximum_line_pixels']:
            value=getattr(self,name)
            if type(value)is not int or value<(0 if name=='padding' else 1):raise ValueError('Invalid segmentation limits')
        if not math.isfinite(self.overlap_baseline_distance) or self.overlap_baseline_distance<0:raise ValueError('Invalid baseline distance')


@dataclass(frozen=True)
class DetectedLine:
    polygon:tuple
    baseline:tuple
    pixels:Image.Image


@dataclass(frozen=True)
class SegmentedLine:
    id:str
    order:int
    polygon:tuple
    baseline:tuple
    pixels:Image.Image
    warnings:tuple[str,...]
    raw_polygon:tuple


@dataclass(frozen=True)
class SegmentedCell:
    state:str
    lines:tuple[SegmentedLine,...]
    warnings:tuple[str,...]
    canvas_size:tuple[int,int]|None
    unassigned_lines:tuple[DetectedLine,...]=()


def _points(points,minimum):
    try:
        values=tuple((float(x),float(y)) for x,y in points)
        if len(set(values))<minimum or any(not math.isfinite(v) for point in values for v in point):raise ValueError
    except (ValueError,TypeError,OverflowError) as exc:raise SegmentationError('Invalid line geometry') from exc
    return values


def _clip_polygon(points,width,height):
    # Sutherland-Hodgman clipping against the encoded source rectangle. Raw
    # model vertices are retained separately; clipping always requires review.
    points=list(points)
    for axis,bound,greater in [(0,0,True),(0,width,False),(1,0,True),(1,height,False)]:
        if not points:break
        output=[];previous=points[-1]
        def inside(point):return point[axis]>=bound if greater else point[axis]<=bound
        for current in points:
            if inside(previous)!=inside(current):
                fraction=(bound-previous[axis])/(current[axis]-previous[axis])
                output.append(tuple(previous[i]+fraction*(current[i]-previous[i]) for i in [0,1]))
            if inside(current):output.append(current)
            previous=current
        points=output
    return tuple(points)


def segment_cell(cell_id:str,crop:CellCrop,backend,*,original_size,settings=SegmentationSettings()):
    """Backend receives an unscaled padded canvas; returns bounded DetectedLine items.

    No lines in nonwhite cells is review, never blank. Pure-white cell pixels can
    be blank; any ink/noise/rule in the cell prevents that automatic decision.
    Context pixels remain untouched and are used for line extraction.
    """
    namespace=UUID(cell_id)
    width,height=crop.pixels.size
    if crop.cell_mask.shape!=(height,width):raise SegmentationError('Cell mask dimensions differ')
    selected=crop.cell_mask>0
    gray=np.asarray(crop.pixels.convert('L'))
    if not selected.any():raise SegmentationError('Cell mask has no area')
    if np.all(gray[selected]==255):return SegmentedCell('blank',(),(),None)
    canvas_size=(max(settings.minimum_width,width+2*settings.padding),max(settings.model_height,height+2*settings.padding))
    if canvas_size[0]*canvas_size[1]>settings.maximum_canvas_pixels:raise SegmentationError('Segmentation canvas exceeds limit')
    canvas=Image.new('RGB',canvas_size,'white');canvas.paste(crop.pixels,(settings.padding,settings.padding))
    local_from_canvas=Affine(1,0,-settings.padding,0,1,-settings.padding)
    original_from_canvas=crop.original_from_crop.after(local_from_canvas)
    local_cell=np.array(crop.cell_polygon_in_crop,dtype=np.float32)
    lines=[];warnings=[];unassigned=[]
    try:
        detections=iter(backend(canvas))
        for index,detection in enumerate(detections):
            if index>=settings.maximum_lines:raise SegmentationError('Line count exceeds limit')
            polygon=_points(detection.polygon,3);baseline=_points(detection.baseline,2)
            if any(x<0 or y<0 or x>canvas.width or y>canvas.height for x,y in (*polygon,*baseline)):
                raise SegmentationError('Line geometry exceeds segmentation canvas')
            if abs(cv2.contourArea(np.array(polygon,dtype=np.float32)))<1:raise SegmentationError('Line polygon has no area')
            if not isinstance(detection.pixels,Image.Image) or min(detection.pixels.size)<1 or detection.pixels.width*detection.pixels.height>settings.maximum_line_pixels:
                raise SegmentationError('Line crop exceeds limits')
            local_base=[local_from_canvas.point(*point) for point in baseline]
            inside=[cv2.pointPolygonTest(local_cell,point,False)>=0 for point in local_base]
            # Context can include a neighboring line. Never silently assign it to
            # this cell or discard it as proof that this cell is empty.
            if not any(inside):
                warnings.append('context_line_unassigned')
                unassigned.append(DetectedLine(tuple(original_from_canvas.point(*p) for p in polygon),tuple(original_from_canvas.point(*p) for p in baseline),detection.pixels.copy()))
                continue
            line_warnings=[]
            if not all(inside):line_warnings.append('baseline_crosses_cell_boundary')
            mapped_polygon=tuple(original_from_canvas.point(*point) for point in polygon)
            mapped_baseline=tuple(original_from_canvas.point(*point) for point in baseline)
            raw_polygon=mapped_polygon
            if any(x<0 or y<0 or x>original_size[0] or y>original_size[1] for x,y in mapped_baseline):
                warnings.append('line_baseline_outside_source')
                unassigned.append(DetectedLine(raw_polygon,mapped_baseline,detection.pixels.copy()))
                continue
            if any(x<0 or y<0 or x>original_size[0] or y>original_size[1] for x,y in mapped_polygon):
                mapped_polygon=_clip_polygon(mapped_polygon,*original_size)
                if len(set(mapped_polygon))<3 or abs(cv2.contourArea(np.array(mapped_polygon,dtype=np.float32)))<1:
                    warnings.append('line_polygon_outside_source')
                    unassigned.append(DetectedLine(raw_polygon,mapped_baseline,detection.pixels.copy()))
                    continue
                line_warnings.append('line_polygon_clipped_to_source')
            if any(x<0 or y<0 or x>width or y>height for x,y in (local_from_canvas.point(*point) for point in polygon)):
                line_warnings.append('line_polygon_exceeds_context')
            y=median(point[1] for point in local_base);x=min(point[0] for point in local_base)
            lines.append((y,x,mapped_polygon,mapped_baseline,detection.pixels.copy(),line_warnings,raw_polygon))
    except SegmentationError:raise
    except Exception as exc:raise SegmentationError('Segmentation backend failed') from exc
    lines.sort(key=lambda line:(line[0],line[1],line[2]))
    for previous,current in zip(lines,lines[1:]):
        if abs(current[0]-previous[0])<=settings.overlap_baseline_distance:
            previous[5].append('ambiguous_line_order');current[5].append('ambiguous_line_order')
    output=[]
    for order,(_,_,polygon,baseline,pixels,reasons,raw_polygon) in enumerate(lines):
        identifier=str(uuid5(namespace,repr((polygon,baseline,order))))
        output.append(SegmentedLine(identifier,order,polygon,baseline,pixels,tuple(sorted(set(reasons))),raw_polygon))
    if not output:warnings.append('no_lines_in_nonblank_cell')
    warnings.extend(reason for line in output for reason in line.warnings)
    # "segmented" means ready for recognition, never recognized text.
    return SegmentedCell('needs-review' if warnings else 'segmented',tuple(output),tuple(sorted(set(warnings))),canvas_size,tuple(unassigned))
