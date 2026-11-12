"""Offline executable v1 semantic validation, without inference or HTTP calls."""
import argparse
import json
from pathlib import Path

from pydantic import ValidationError
from scriptalocal import contracts

MODELS={name:getattr(contracts,name) for name in [
    'JobAccepted','JobStatus','JobOptions','JobResult','ErrorResponse','RevisionInput',
    'ModelInventory','Health']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',choices=sorted(MODELS),required=True)
    parser.add_argument('--maximum-bytes',type=int,default=2*1024*1024,
                        help='Local CLI input cap; this is not a deployed service result limit')
    parser.add_argument('document',type=Path);args=parser.parse_args()
    if args.maximum_bytes<1:parser.error('Maximum bytes must be positive')
    with args.document.open('rb') as stream:content=stream.read(args.maximum_bytes+1)
    if len(content)>args.maximum_bytes:
        print(json.dumps({'valid':False,'error':'document_limit_exceeded'}));raise SystemExit(1)
    try:MODELS[args.model].model_validate_json(content)
    except ValidationError as exc:
        print(json.dumps({'valid':False,'model':args.model,'errors':[
            {'location':list(item['loc']),'message':item['msg']} for item in exc.errors(include_input=False)]},indent=2))
        raise SystemExit(1)
    print(json.dumps({'valid':True,'model':args.model,'scope':'structural and service-model semantic checks; no HTTP/inference/app execution'}))


if __name__=='__main__':main()
