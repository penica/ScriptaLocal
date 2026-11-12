"""Measure candidate divider geometry on a supplied crop, keeping artifacts private."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

parser = argparse.ArgumentParser()
parser.add_argument('--image', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--minimum-height-fraction', type=float, default=0.55)
parser.add_argument('--outer-border-proximity-fraction', type=float, default=0.025)
args = parser.parse_args()
assert 0 < args.minimum_height_fraction <= 1
assert 0 <= args.outer_border_proximity_fraction < 0.5
args.output.mkdir(parents=True, exist_ok=True)
with Image.open(args.image) as source:
    rgb = np.array(ImageOps.exif_transpose(source).convert('RGB'))
height, width = rgb.shape[:2]
gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
threshold = cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY_INV,31,15)
vertical = cv2.morphologyEx(threshold,cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(15,height//3))))
vertical = cv2.morphologyEx(vertical,cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT,(3,max(3,height//12))))
count, labels, stats, centers = cv2.connectedComponentsWithStats(vertical)
candidates=[]
for index in range(1,count):
    x,y,w,h,area = [int(x) for x in stats[index]]
    if h >= args.minimum_height_fraction*height and w < width*0.03:
        candidates.append({'x':int(round(centers[index][0])), 'bounds':[x,y,x+w,y+h],
                           'vertical_support':round(h/height,3)})
candidates.sort(key=lambda x:x['x'])
merged=[]
for candidate in candidates:
    if merged and candidate['x']-merged[-1]['x'] < max(5,width//400):
        if candidate['vertical_support'] > merged[-1]['vertical_support']:
            merged[-1]=candidate
    else:
        merged.append(candidate)
detected=[x['x'] for x in merged if 5 < x['x'] < width-5]
# A real outer rule near the crop edge bounds the table. The small strip outside
# it is a margin, not an extra empty cell. Interior narrow columns stay intact.
start = detected[0] if detected and detected[0] <= width*args.outer_border_proximity_fraction else 0
end = detected[-1] if detected and width-detected[-1] <= width*args.outer_border_proximity_fraction else width
boundaries=[start]+[x for x in detected if start < x < end]+[end]
cells=[]
overlay=rgb.copy()
for index,(left,right) in enumerate(zip(boundaries,boundaries[1:])):
    cells.append({'order':index,'bounds':[left,0,right,height],
                  'review_required':True})
    # Keep original pixels and diagnostic crops; do not erase dividers or ink.
    Image.fromarray(rgb[:,left:right]).save(args.output/f'cell-{index:02}.png')
    cv2.putText(overlay,str(index),(left+4,24),cv2.FONT_HERSHEY_SIMPLEX,.6,(255,0,0),2)
for boundary in boundaries[1:-1]:
    cv2.line(overlay,(boundary,0),(boundary,height-1),(255,0,0),2)
Image.fromarray(overlay).save(args.output/'divider-overlay.png')
Image.fromarray(vertical).save(args.output/'vertical-mask.png')
result={'image_sha256':hashlib.sha256(args.image.read_bytes()).hexdigest(),
        'original_size':[width,height],'candidates':merged,'boundaries':boundaries,
        'excluded_outer_margins':{'left':[0,start], 'right':[end,width]},
        'cells':cells,'automatic_layout_acceptance':'pending human-reviewed divider annotations',
        'method':'adaptive threshold and vertical morphology candidate; no fixed column count'}
(args.output/'geometry.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'detected_borders':len(merged),'candidate_cells':len(cells),
                  'boundaries':boundaries,'status':'needs-review'},indent=2))
