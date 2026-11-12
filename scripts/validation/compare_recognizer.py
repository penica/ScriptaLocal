"""Compare a pinned model on frozen crops; AI references remain provisional."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import UUID

from PIL import Image
import torch
from scriptalocal.recognition import LineInput,RecognitionSettings,recognize_lines
from scriptalocal.trocr_recognizer import TrOCRRecognizer


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--manifest-sha256',required=True);parser.add_argument('--corpus',type=Path,required=True)
    parser.add_argument('--reference',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--comparison-kind',choices=['model-on-frozen-crops','rendering-against-cached-baseline'],default='model-on-frozen-crops')
    args=parser.parse_args()
    assert torch.cuda.device_count()==1 and os.environ.get('HF_HUB_OFFLINE')=='1'
    torch.set_num_threads(2)
    corpus_blob=(args.corpus/'corpus.json').read_bytes();corpus=json.loads(corpus_blob)
    reference_blob=args.reference.read_bytes();reference=json.loads(reference_blob)
    assert reference['source_sha256']==corpus['source_sha256'] and reference['human_confirmed'] is False
    inputs=[]
    for item in corpus['entries']:
        assert item['filename']==str(UUID(item['line_id']))+'.png'
        path=args.corpus/item['filename'];assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
        with Image.open(path) as source:pixels=source.convert('RGB')
        inputs.append(LineInput(UUID(item['line_id']),pixels))
    args.output.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();backend=TrOCRRecognizer(args.model,args.manifest_sha256,precision='fp32')
    torch.cuda.synchronize();loaded=time.monotonic()
    recipe={'num_beams':4,'max_new_tokens':128,'length_penalty':2.0,'no_repeat_ngram_size':0,
            'use_cache':False,'do_sample':False,'early_stopping':True}
    author_defaults={key:getattr(backend.model.generation_config,key,None) for key in recipe}
    for key,value in recipe.items():setattr(backend.model.generation_config,key,value)
    result=recognize_lines(inputs,backend,settings=RecognitionSettings())
    torch.cuda.synchronize();finished=time.monotonic()
    rows=[];by_id={str(line.id):line for line in result.lines}
    for item in corpus['entries']:
        line=by_id[item['line_id']]
        rows.append({'line_id':item['line_id'],'cell_order':item['cell_order'],'line_order':item['line_order'],
                     'baseline_text':item['baseline_raw_text'],'candidate':asdict(line),
                     'same_verbatim_output':item['baseline_raw_text']==line.text})
    target=args.output/'private-comparison.json';target.write_text(json.dumps(rows,default=str,indent=2,ensure_ascii=False)+'\n');target.chmod(0o600)
    baseline={item['line_id']:item['baseline_raw_text'] for item in corpus['entries']}
    diagnostic={}
    for label,texts in [('cached_baseline',baseline),('candidate',{key:line.text for key,line in by_id.items()})]:
        text_entries=[item for item in reference['entries'] if item['kind']=='text']
        nontext=[item for item in reference['entries'] if item['kind']=='non_text_ruling']
        diagnostic[label]={'provisional_ai_text_anchors':len(text_entries),
            'matches_after_exterior_whitespace_strip':sum(texts[item['line_id']] is not None and texts[item['line_id']].strip()==item['reading'] for item in text_entries),
            'provisional_nontext_examples':len(nontext),
            'nonempty_outputs_on_provisional_nontext':sum(bool(texts[item['line_id']] and texts[item['line_id']].strip()) for item in nontext)}
    report={'comparison_kind':args.comparison_kind,'corpus_rendering':corpus.get('rendering'),'model':backend.reference,'manifest_sha256':args.manifest_sha256,'line_crops':len(inputs),
            'corpus_sha256':hashlib.sha256(corpus_blob).hexdigest(),'ai_reference_sha256':hashlib.sha256(reference_blob).hexdigest(),
            'source_image_sha256':corpus['source_sha256'],'cached_baseline_provenance':corpus['baseline_provenance'],
            'controlled_decoding_recipe':recipe,'candidate_author_defaults_before_control':author_defaults,
            'clean_up_tokenization_spaces':False,'model_load_and_verification_seconds':round(loaded-started,3),
            'generation_seconds':round(finished-loaded,3),'peak_cuda_bytes':torch.cuda.max_memory_allocated(),
            'backend_calls':result.backend_calls,'oom_events':result.oom_events,
            'identical_verbatim_to_cached_baseline':sum(row['same_verbatim_output'] for row in rows),
            'changed_verbatim_from_cached_baseline':sum(not row['same_verbatim_output'] for row in rows),
            'states':{state:sum(line.state==state for line in result.lines) for state in ['recognized','needs-review','failed']},
            'ai_visual_diagnostics':diagnostic,'human_ground_truth':False,'accuracy_ranking':'not established',
            'network':'disabled; inference files mounted read-only','hardware':'one assigned RTX 3060; host GPU 1',
            'source_sha256':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in ['scripts/validation/compare_recognizer.py','scriptalocal/recognition.py','scriptalocal/trocr_recognizer.py']}}
    (args.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
