"""Real full-row inference through isolated leased stages; raw result stays private."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import kraken
import torch
from scriptalocal.contracts import JobResult
from scriptalocal.images import decode_image,persist_original
from scriptalocal.job_artifacts import ArtifactStore
from scriptalocal.job_queue import JobQueue,QueueSettings
from scriptalocal.kraken_segmenter import KrakenSegmenter
from scriptalocal.pipeline import ImagePipeline,source_metadata
from scriptalocal.trocr_recognizer import TrOCRRecognizer
from scriptalocal.worker import StageWorker


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dsn-file',type=Path,required=True);parser.add_argument('--runtime-image',required=True)
    parser.add_argument('--precision',choices=['fp32','fp16'],default='fp32')
    parser.add_argument('--lease-seconds',type=int,default=60)
    args=parser.parse_args()
    assert os.environ.get('SCRIPTALOCAL_ALLOW_DATABASE_TESTS')=='1'
    assert os.environ.get('HF_HUB_OFFLINE')=='1' and os.environ.get('TRANSFORMERS_OFFLINE')=='1'
    assert torch.cuda.device_count()==1;torch.set_num_threads(2)
    args.output.mkdir(parents=True,exist_ok=True)
    queue=JobQueue(args.dsn_file.read_text().strip(),ArtifactStore(args.output/'artifacts'),settings=QueueSettings(lease_seconds=args.lease_seconds))
    heartbeat_calls={};heartbeat=queue.heartbeat
    def observed_heartbeat(lease):
        result=heartbeat(lease);heartbeat_calls[lease.stage]=heartbeat_calls.get(lease.stage,0)+1;return result
    queue.heartbeat=observed_heartbeat
    with queue.connect() as db:
        identity=db.execute('SELECT current_database() AS database,current_user AS role').fetchone()
        if identity!={'database':'scriptalocal_queue_validation','role':'scriptalocal_test'}:raise RuntimeError('Refusing mutation outside dedicated validation database')
    queue.migrate()
    with queue.connect() as db:
        if db.execute("SELECT count(*) AS count FROM sl_jobs WHERE status IN ('queued','running')").fetchone()['count']:
            raise RuntimeError('Existing active validation jobs require reconciliation before another run')
    started=time.monotonic()
    segmenter=KrakenSegmenter(Path(kraken.__file__).parent/'blla.mlmodel','77a638a83c9e535620827a09e410ed36391e9e8e8126d5796a0f15b978186056')
    recognizer=TrOCRRecognizer(args.model,'d3fc87c3d4b4d0fc708059b6351874b4e35e4dd4a035358394fb0186fbce5445',precision=args.precision)
    torch.cuda.synchronize();models_loaded=time.monotonic()
    original_root=args.output/'originals';image=decode_image(args.image.read_bytes());persist_original(image,original_root)
    pipeline=ImagePipeline(queue,original_root,segmenter,recognizer,runtime_image=args.runtime_image)
    reference='validation-'+str(uuid4())
    job,replayed=queue.create('gpu-pipeline-validation',source_metadata(image),{'client_reference':reference},pipeline.configuration_sha256,idempotency_key=reference)
    assert not replayed
    receipt={'job_id':str(job['id']),'owner':'gpu-pipeline-validation','configuration_sha256':pipeline.configuration_sha256,'client_reference':reference}
    (args.output/'job-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');(args.output/'job-receipt.json').chmod(0o600)
    worker=StageWorker(queue,'gpu-pipeline-validation-worker',pipeline.handlers,configuration_sha256=pipeline.configuration_sha256)
    outcomes=[]
    for expected in ['layout','segmentation','recognition']:
        before=time.monotonic();outcome=worker.run_once();outcomes.append(asdict(outcome))
        (args.output/'worker-outcomes.json').write_text(json.dumps(outcomes,indent=2)+'\n')
        print(json.dumps({'event':'stage_finished','stage':expected,'outcome':outcome.status,'seconds':round(time.monotonic()-before,3)}),flush=True)
        if outcome.status!='completed' or outcome.stage!=expected:raise RuntimeError('Validation stage failed: '+str(outcome.error_code))
    torch.cuda.synchronize()
    record=queue.result_reference(job['id'],'gpu-pipeline-validation');content=queue.artifacts.read(record)
    result=JobResult.model_validate_json(content)
    assert result.job_id==job['id'] and result.revision==1 and result.source.sha256==image.sha256
    assert result.provenance.configuration_sha256==pipeline.configuration_sha256
    assert hashlib.sha256((original_root/(image.sha256+'.original')).read_bytes()).hexdigest()==image.sha256
    (args.output/'result.json').write_bytes(content);(args.output/'result.json').chmod(0o600)
    with queue.connect() as db:
        stages=db.execute('SELECT name,status,attempts FROM sl_stages WHERE job_id=%s ORDER BY created_at',(job['id'],)).fetchall()
        artifacts=db.execute('SELECT count(*) AS count,coalesce(sum(bytes),0) AS bytes FROM sl_artifacts WHERE job_id=%s',(job['id'],)).fetchone()
    artifacts['bytes']=int(artifacts['bytes'])
    assert len(stages)==3 and all(stage['status']=='succeeded' and stage['attempts']==1 for stage in stages)
    for name,seconds in result.stage_seconds.items():
        if seconds>args.lease_seconds:assert heartbeat_calls.get(name,0)>2
    states={state:sum(cell.state==state for cell in result.cells) for state in ['recognized','blank','unreadable','failed','needs-review']}
    report={'lease_seconds':args.lease_seconds,'heartbeat_calls':heartbeat_calls,'sample_rows':1,'automatic_layout':True,'cells':len(result.cells),'line_count':sum(len(cell.lines) for cell in result.cells),
        'cell_states':states,'review_required':result.review_required,'job_warning_codes':[warning.code for warning in result.warnings],
        'image_sha256':image.sha256,'original_bytes_unchanged':True,'api_result_schema':'passed','http_job_exists':False,
        'database_job_succeeded':True,'stages':stages,'committed_artifacts':artifacts,'result_bytes':len(content),
        'runtime_image':args.runtime_image,'pipeline_version':pipeline.version,'configuration_sha256':pipeline.configuration_sha256,
        'models':pipeline.manifest['models'],'model_load_and_verification_seconds':round(models_loaded-started,3),
        'stage_seconds':result.stage_seconds,'total_probe_seconds':round(time.monotonic()-started,3),
        'peak_cuda_bytes':torch.cuda.max_memory_allocated(),'precision':args.precision,'model_loads':recognizer.loads,
        'network':'internal validation database network only, no external route; offline libraries enforced',
        'source_sha256':pipeline.manifest['source_sha256'],'accuracy':'unmeasured: no corrected transcripts or annotated full-row corpus',
        'acceptance':'one functional full-row queued GPU run; not HTTP/application/quality release'}
    (args.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__':main()
