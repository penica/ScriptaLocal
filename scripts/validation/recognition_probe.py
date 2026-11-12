"""Resident real-weight recognition comparison; raw text stays private."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
from uuid import NAMESPACE_URL,uuid5

from PIL import Image
import torch
from scriptalocal.recognition import LineInput,RecognitionSettings,recognize_lines
from scriptalocal.trocr_recognizer import TrOCRRecognizer


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--lines',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--precision',choices=['fp32','fp16'],required=True);parser.add_argument('--inject-oom',action='store_true')
    args=parser.parse_args();torch.set_num_threads(2)
    assert torch.cuda.device_count()==1
    args.output.mkdir(parents=True,exist_ok=True)
    settings=RecognitionSettings()
    started=time.monotonic()
    backend=TrOCRRecognizer(args.model,'d3fc87c3d4b4d0fc708059b6351874b4e35e4dd4a035358394fb0186fbce5445',precision=args.precision)
    torch.cuda.synchronize();loaded=time.monotonic()
    inputs=[];source_hashes=[]
    for path in sorted(args.lines.glob('line-*.png')):
        digest=hashlib.sha256(path.read_bytes()).hexdigest();source_hashes.append(digest)
        with Image.open(path) as source:pixels=source.convert('RGB')
        inputs.append(LineInput(uuid5(NAMESPACE_URL,path.name+digest),pixels))
    assert inputs
    if args.inject_oom:
        original_generate=backend.model.generate;injected=False
        def interrupted_generate(*args,**kwargs):
            nonlocal injected
            if not injected:injected=True;raise torch.cuda.OutOfMemoryError('Controlled validation fault')
            return original_generate(*args,**kwargs)
        backend.model.generate=interrupted_generate
    runs=[]
    for index in range(2):
        then=time.monotonic();result=recognize_lines(inputs,backend,settings=settings)
        torch.cuda.synchronize()
        seconds=round(time.monotonic()-then,3)
        (args.output/f'raw-run-{index}.json').write_text(json.dumps(asdict(result),default=str,indent=2)+'\n')
        runs.append({'seconds':seconds,'backend_calls':result.backend_calls,'oom_events':result.oom_events,'recoveries':result.recoveries,
                     'lines':[{'id':str(line.id),'state':line.state,'warnings':line.warnings,'generated_tokens':line.generated_tokens,
                               'text_sha256':hashlib.sha256(line.text.encode()).hexdigest() if line.text is not None else None} for line in result.lines]})
    assert backend.loads==1
    assert [line['text_sha256'] for line in runs[0]['lines']]==[line['text_sha256'] for line in runs[1]['lines']]
    assert all(line['state']!='failed' for run in runs for line in run['lines'])
    report={'precision':args.precision,'model_loads':backend.loads,'model_load_and_verification_seconds':round(loaded-started,3),
            'model_id':backend.manifest['repository'],'model_revision':backend.manifest['revision'],
            'manifest_sha256':'d3fc87c3d4b4d0fc708059b6351874b4e35e4dd4a035358394fb0186fbce5445',
            'weights_sha256':next(item['sha256'] for item in backend.manifest['files'] if item['path']=='model.safetensors'),
            'source_line_sha256':source_hashes,'settings':asdict(settings),'peak_cuda_bytes':torch.cuda.max_memory_allocated(),
            'runs':runs,'same_outputs_after_reuse':True,'network':'disabled','oom_fault_injected':args.inject_oom,
            'oom_scope':'controlled CUDA exception then real smaller-batch inference' if args.inject_oom else 'no deliberate GPU memory pressure',
            'accuracy':'not measured: no human ground truth','clean_up_tokenization_spaces':False,
            'source_sha256':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in ['scriptalocal/recognition.py','scriptalocal/trocr_recognizer.py','scripts/validation/recognition_probe.py']}}
    (args.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
