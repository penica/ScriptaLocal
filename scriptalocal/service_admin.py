"""Bounded health checks and explicit operator-only schema migration."""
import argparse
import math
from pathlib import Path
import time
import urllib.request

from .ocr_access import private_json


def main():
    parser=argparse.ArgumentParser();commands=parser.add_subparsers(dest='command',required=True)
    migrate=commands.add_parser('migrate');migrate.add_argument('--dsn-file',type=Path,required=True);migrate.add_argument('--artifacts',type=Path,required=True)
    api=commands.add_parser('health-api');api.add_argument('--token-file',type=Path,required=True);api.add_argument('--port',type=int,required=True)
    worker=commands.add_parser('health-worker');worker.add_argument('--marker',type=Path,required=True);worker.add_argument('--maximum-age',type=float,default=20)
    args=parser.parse_args()
    try:
        if args.command=='migrate':
            from .job_artifacts import ArtifactStore
            from .job_queue import JobQueue
            from .ocr_server import read_dsn
            queue=JobQueue(read_dsn(args.dsn_file),ArtifactStore(args.artifacts));queue.migrate()
            if not queue.schema_current():raise ValueError('Schema unavailable')
            print('Database schema is current.')
        elif args.command=='health-api':
            import json
            if not 1<=args.port<=65535:raise ValueError('Invalid port')
            token=args.token_file.read_text().strip()
            request=urllib.request.Request('http://127.0.0.1:'+str(args.port)+'/health/ready',headers={'Authorization':'Bearer '+token})
            http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with http.open(request,timeout=2) as response:
                if response.status!=200 or not json.load(response)['ready']:raise ValueError('Not ready')
        else:
            marker=private_json(args.marker,65536);age=time.time()-marker['last_seen']
            if not math.isfinite(args.maximum_age) or args.maximum_age<=0 or not 0<=age<=args.maximum_age or marker['state']!='ready':raise ValueError('Not ready')
    except Exception:raise SystemExit(1) from None


if __name__=='__main__':main()
