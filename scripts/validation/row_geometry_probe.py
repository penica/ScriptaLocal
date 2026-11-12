"""Exercise reusable table layout; keep images and detailed geometry private."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from scriptalocal.images import decode_image, persist_original
from scriptalocal.layout import LayoutSettings, detect_layout, extract_cell


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--minimum-height-fraction',type=float,default=.5)
    parser.add_argument('--outer-border-proximity-fraction',type=float,default=.025)
    parser.add_argument('--padding',type=int,default=12)
    parser.add_argument('--corrected-dividers',type=Path)
    args=parser.parse_args()
    settings=LayoutSettings(minimum_line_fraction=args.minimum_height_fraction,
                            outer_border_proximity_fraction=args.outer_border_proximity_fraction,
                            context_padding=args.padding)
    image=decode_image(args.image.read_bytes())
    dividers=None
    if args.corrected_dividers:
        proposed=json.loads(args.corrected_dividers.read_text())
        if not isinstance(proposed,list) or any(not isinstance(x,dict) or set(x)!={'top','bottom'} for x in proposed):
            raise ValueError('Corrections must be a JSON list of top/bottom endpoint objects')
        dividers=[(entry['top'],entry['bottom']) for entry in proposed]
    layout=detect_layout(image,settings=settings,corrected_dividers=dividers)
    args.output.mkdir(parents=True,exist_ok=True)
    persist_original(image,args.output/'originals')
    overlay=np.array(image.pixels)
    artifacts=[]
    for cell in layout.cells:
        crop=extract_cell(image,cell,settings=settings)
        crop.pixels.save(args.output/f'cell-{cell.order:03}.png')
        Image.fromarray(crop.cell_mask).save(args.output/f'cell-{cell.order:03}-mask.png')
        cv2.polylines(overlay,[np.rint(cell.polygon).astype(np.int32)],True,(255,0,0),2)
        x,y=cell.polygon[0]
        cv2.putText(overlay,str(cell.order),(round(x)+4,round(y)+24),
                    cv2.FONT_HERSHEY_SIMPLEX,.6,(255,0,0),2)
        artifacts.append({'cell_id':cell.id,'order':cell.order,
                          'oriented_from_crop':crop.oriented_from_crop.coefficients,
                          'original_from_crop':crop.original_from_crop.coefficients,
                          'context_clipped':crop.context_clipped,
                          'original_polygon':[image.original_from_oriented.point(*point) for point in cell.polygon]})
    Image.fromarray(overlay).save(args.output/'divider-overlay.png')
    result={'image_sha256':image.sha256,'encoded_size':image.encoded_size,
            'oriented_size':image.pixels.size,'exif_orientation':image.orientation,
            'settings':asdict(settings),'layout':asdict(layout),'crop_artifacts':artifacts,
            'automatic_layout_acceptance':'not established by one sample'}
    (args.output/'geometry.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'cells':len(layout.cells),'borders':len(layout.borders),
                      'warnings':layout.warnings,'status':'needs-review' if layout.warnings else 'manual-layout',
                      'coordinate_space':layout.coordinate_space},indent=2))
    if not layout.cells:
        raise SystemExit('Ambiguous layout: no cell crops were fabricated')


if __name__=='__main__':
    main()
