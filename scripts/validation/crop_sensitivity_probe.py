"""A manually reviewed word tests crop sensitivity, not corpus-level accuracy."""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import time
from uuid import NAMESPACE_URL,uuid5

from PIL import Image
import torch
from scriptalocal.images import decode_image
from scriptalocal.recognition import LineInput,RecognitionSettings,recognize_lines
from scriptalocal.trocr_recognizer import TrOCRRecognizer


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--corpus',type=Path,required=True);parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,required=True);parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    torch.set_num_threads(2);assert torch.cuda.device_count()==1
    reference=json.loads(args.reference.read_text());corpus=json.loads((args.corpus/'corpus.json').read_text())
    image=decode_image(args.image.read_bytes());assert image.sha256==reference['source_sha256']==corpus['source_sha256']
    identifier='7526e4ad-bae1-514a-b6c6-b5e79a6a35fd';entry=next(item for item in corpus['entries'] if item['line_id']==identifier)
    expected=next(item['reading'] for item in reference['entries'] if item['line_id']==identifier)
    path=args.corpus/entry['filename'];assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
    with Image.open(path) as original:automatic=original.convert('RGB')
    bounds=(450,48,650,110)
    cases={'automatic':automatic,'manual_source_rectangle':image.pixels.crop(bounds),
           'manual_rectified_margin_crop':automatic.crop((0,0,automatic.width,70))}
    args.output.mkdir(parents=True,exist_ok=True)
    for label,pixels in cases.items():pixels.save(args.output/(label+'.png'))
    inputs=[LineInput(uuid5(NAMESPACE_URL,'crop-sensitivity:'+label),pixels) for label,pixels in cases.items()]
    results=[];private=[]
    for label,root,pinned in [('baseline',args.baseline,'d3fc87c3d4b4d0fc708059b6351874b4e35e4dd4a035358394fb0186fbce5445'),
                              ('new_candidate',args.candidate,'8196830c100bcf505b01c7ac9f4b400386967a5304a47bc3a997f9005e174367')]:
        backend=TrOCRRecognizer(root,pinned)
        for key,value in {'length_penalty':2.0,'no_repeat_ngram_size':0,'use_cache':False}.items():setattr(backend.model.generation_config,key,value)
        started=time.monotonic();run=recognize_lines(inputs,backend,settings=RecognitionSettings())
        torch.cuda.synchronize()
        items=[]
        for name,reading in zip(cases,run.lines):
            items.append({'case':name,'matches_provisional_ai_word':reading.text is not None and reading.text.strip()==expected,
                          'state':reading.state,'text_sha256':hashlib.sha256((reading.text or '').encode()).hexdigest()})
            private.append({'model':label,'case':name,'text':reading.text,'ai_reference':expected})
        results.append({'model':label,'reference':backend.reference,'generation_seconds':round(time.monotonic()-started,3),'cases':items})
        del backend;gc.collect();torch.cuda.empty_cache()
    p=args.output/'private-readings.json';p.write_text(json.dumps(private,indent=2,ensure_ascii=False)+'\n');p.chmod(0o600)
    report={'source_sha256':image.sha256,'original_unchanged':hashlib.sha256(args.image.read_bytes()).hexdigest()==image.sha256,
            'manual_source_bounds':bounds,'case_sizes':{key:value.size for key,value in cases.items()},'models':results,
            'human_ground_truth':False,'scope':'single AI-reviewed word, manual oracle crop; not an automatic preprocessing fix or accuracy benchmark',
            'network':'disabled','source_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (args.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
