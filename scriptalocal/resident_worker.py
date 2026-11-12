"""Single-GPU resident pipeline runner, configured explicitly by local files."""
import argparse
import json
import os
from pathlib import Path
import signal
import threading

from .job_artifacts import ArtifactStore
from .job_queue import JobQueue,QueueSettings,principal
from .ocr_access import private_json
from .worker import StageWorker
from .worker_readiness import ResidentReadiness,atomic_json


def run(config_path):
    # GPU dependencies remain confined to the worker image.
    import kraken
    import torch
    from .kraken_segmenter import KrakenSegmenter
    from .pipeline import ImagePipeline
    from .trocr_recognizer import TrOCRRecognizer

    config=private_json(config_path,65536)
    required={'dsn_file','artifacts_root','originals_root','model_root','recognition_manifest_sha256',
              'runtime_image','status_root','worker_id'}
    allowed=required|{'precision','queue','poll_seconds','ready_interval_seconds','torch_threads','license','scripts'}
    if not isinstance(config,dict) or not required<=config.keys() or config.keys()-allowed:raise ValueError('Invalid worker configuration keys')
    for name in ['dsn_file','artifacts_root','originals_root','model_root','status_root']:
        if not isinstance(config[name],str) or not Path(config[name]).is_absolute():raise ValueError('Worker paths must be absolute')
    principal(config['worker_id'])
    poll=config.get('poll_seconds',1);threads=config.get('torch_threads',2)
    if type(poll)is not int or poll<1 or type(threads)is not int or threads<1:raise ValueError('Invalid worker scheduling limits')
    if os.environ.get('HF_HUB_OFFLINE')!='1' or os.environ.get('TRANSFORMERS_OFFLINE')!='1':raise ValueError('Worker requires offline inference')
    if torch.cuda.device_count()!=1:raise ValueError('Expose exactly one GPU per resident worker')
    torch.set_num_threads(threads)
    # Read a bounded regular secret without following the final symlink.
    import stat
    fd=os.open(config['dsn_file'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Invalid database secret')
        content=stream.read(16385)
    if not content.strip() or len(content)>16384:raise ValueError('Invalid database secret')
    queue=JobQueue(content.decode().strip(),ArtifactStore(Path(config['artifacts_root'])),settings=QueueSettings(**config.get('queue',{})))
    if not queue.schema_current():raise ValueError('Worker database schema is not current')
    stopping=threading.Event()
    for signum in [signal.SIGTERM,signal.SIGINT]:signal.signal(signum,lambda *_:stopping.set())
    segmenter=KrakenSegmenter(Path(kraken.__file__).parent/'blla.mlmodel','77a638a83c9e535620827a09e410ed36391e9e8e8126d5796a0f15b978186056')
    recognizer=TrOCRRecognizer(Path(config['model_root']),config['recognition_manifest_sha256'],precision=config.get('precision','fp32'))
    pipeline=ImagePipeline(queue,Path(config['originals_root']),segmenter,recognizer,runtime_image=config['runtime_image'])
    worker=StageWorker(queue,config['worker_id'],pipeline.handlers,configuration_sha256=pipeline.configuration_sha256)
    root=Path(config['status_root']);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with ResidentReadiness(root/'ready.json',pipeline.configuration_sha256,interval_seconds=config.get('ready_interval_seconds',5)) as readiness:
        atomic_json(root/'profiles.json',{'default_model':pipeline.recognition_reference['model_id'],'profiles':[
            {'configuration':pipeline.manifest,'license':config.get('license','MIT'),'scripts':config.get('scripts',['German Kurrent']),
             'ready_file':str(root/'ready.json')}]})
        print(json.dumps({'event':'worker_ready','configuration_sha256':pipeline.configuration_sha256,'model_loads':recognizer.loads}),flush=True)
        while not stopping.is_set():
            readiness.check()
            outcome=worker.run_once()
            if outcome.status=='idle':stopping.wait(poll);continue
            print(json.dumps({'event':'stage_finished','stage':outcome.stage,'outcome':outcome.status,'error_code':outcome.error_code}),flush=True)
            if not worker.healthy:raise RuntimeError('Worker became unavailable')
    print(json.dumps({'event':'worker_stopped','model_loads':recognizer.loads}),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);args=parser.parse_args()
    try:run(args.config)
    except Exception:
        # Avoid model paths, DSNs, image content or transcription in supervisor logs.
        raise SystemExit('Resident worker stopped: initialization or execution unavailable') from None


if __name__=='__main__':main()
