"""Reusable Kraken adapter probe. All image artifacts remain in private storage."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
from uuid import NAMESPACE_URL,uuid5

import kraken
import torch
from scriptalocal.images import decode_image
from scriptalocal.layout import Cell,extract_cell
from scriptalocal.segmentation import SegmentationSettings,segment_cell
from scriptalocal.kraken_segmenter import KrakenSegmenter


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--bounds',type=int,nargs=4,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();torch.set_num_threads(2)
    assert torch.cuda.device_count()==1
    image=decode_image(args.image.read_bytes());left,top,right,bottom=args.bounds
    cell=Cell(str(uuid5(NAMESPACE_URL,image.sha256+repr(args.bounds))),0,((left,top),(right,top),(right,bottom),(left,bottom)))
    crop=extract_cell(image,cell)
    path=Path(kraken.__file__).parent/'blla.mlmodel'
    checksum='77a638a83c9e535620827a09e410ed36391e9e8e8126d5796a0f15b978186056'
    started=time.monotonic();backend=KrakenSegmenter(path,checksum)
    settings=SegmentationSettings(model_height=backend.model_height)
    result=segment_cell(cell.id,crop,backend,original_size=image.encoded_size,settings=settings)
    torch.cuda.synchronize();elapsed=time.monotonic()-started
    args.output.mkdir(parents=True,exist_ok=True)
    crop.pixels.save(args.output/'cell-context.png')
    for line in result.lines:line.pixels.save(args.output/f'line-{line.order:03}.png')
    for index,line in enumerate(result.unassigned_lines):line.pixels.save(args.output/f'unassigned-{index:03}.png')
    details={'unassigned_lines':[{'polygon':line.polygon,'baseline':line.baseline} for line in result.unassigned_lines],'state':result.state,'warnings':result.warnings,'lines':[{'id':line.id,'order':line.order,'polygon':line.polygon,'raw_polygon':line.raw_polygon,'baseline':line.baseline,'warnings':line.warnings,'size':line.pixels.size} for line in result.lines]}
    (args.output/'geometry.json').write_text(json.dumps(details,indent=2)+'\n')
    summary={'image_sha256':image.sha256,'source_rows':1,'manually_bounded_cells':1,'detected_lines':len(result.lines),'state':result.state,'warnings':result.warnings,'seconds_including_model_load':round(elapsed,3),'peak_cuda_bytes':torch.cuda.max_memory_allocated(),'canvas_size':result.canvas_size,'settings':asdict(settings),'weights_sha256':checksum,'accuracy':'not measured: no corrected transcript','source_sha256':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in ['scriptalocal/segmentation.py','scriptalocal/kraken_segmenter.py','scripts/validation/segmentation_probe.py']}}
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
