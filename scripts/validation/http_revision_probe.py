"""Exercise corrections on a disposable real HTTP job; readings stay private."""
import argparse
import hashlib
import json
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.request


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8092')
    parser.add_argument('--token-file',type=Path,required=True);parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--ca-file',type=Path)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    token=args.token_file.read_text().strip();job=json.loads(args.receipt.read_text())['job_id'];path='/v1/jobs/'+job
    context=ssl.create_default_context(cafile=str(args.ca_file)) if args.ca_file else ssl.create_default_context()
    http=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context));args.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    def request(endpoint,body=None):
        headers={'Authorization':'Bearer '+token}
        if body is not None:headers['Content-Type']='application/json';body=json.dumps(body).encode()
        req=urllib.request.Request(args.url+endpoint,headers=headers,data=body)
        try:
            with http.open(req,timeout=15) as response:return response.status,response.read()
        except urllib.error.HTTPError as response:return response.code,response.read()
    def result(revision):
        code,content=request(path+'/result?revision='+str(revision));assert code==200
        saved=args.output/f'revision-{revision}.json';saved.write_bytes(content);saved.chmod(0o600)
        return json.loads(content),hashlib.sha256(content).hexdigest()
    def revise(body):
        started=time.monotonic();code,content=request(path+'/revisions',body);assert code==202,(code,content[:0])
        accepted=json.loads(content);expected=body['expected_revision']+1;assert accepted['revision']==expected
        deadline=time.monotonic()+360;last=None
        while True:
            code,content=request(path);assert code==200
            status=json.loads(content);state=(status['revision'],status['status'],status['stage'])
            if last!=state:print(json.dumps({'revision':state[0],'status':state[1],'stage':state[2]}),flush=True);last=state
            if status['status'] in {'succeeded','failed','cancelled'}:break
            if time.monotonic()>deadline:raise RuntimeError('Revision completion deadline exceeded')
            time.sleep(.25)
        assert status['status']=='succeeded' and status['revision']==expected
        return round(time.monotonic()-started,3)
    first,first_hash=result(1);target=next(line for cell in first['cells'] for line in cell['lines'])
    # This is an explicit synthetic test edit, never a reference transcription.
    body={'expected_revision':1,'text_corrections':[{'line_id':target['id'],'text':'Validation-only saved reading','state':'recognized'}]}
    text_seconds=revise(body);second,second_hash=result(2)
    changed=next(line for cell in second['cells'] for line in cell['lines'] if line['id']==target['id'])
    assert changed['text']==body['text_corrections'][0]['text'] and changed['raw_text']==target['raw_text'] and changed['raw_state']==target['raw_state']
    assert changed['correction']['actor_id'] and changed['correction']['saved_at']
    assert request(path+'/revisions',body)[0]==409
    assert result(1)[1]==first_hash
    dividers=[{'top':cell['polygon'][0],'bottom':cell['polygon'][3]} for cell in first['cells'][1:]]
    geometry_seconds=revise({'expected_revision':2,'corrected_dividers':dividers})
    third,third_hash=result(3)
    assert len(third['cells'])==len(first['cells']) and all(line['correction'] is None for cell in third['cells'] for line in cell['lines'])
    assert result(1)[1]==first_hash and result(2)[1]==second_hash
    report={'job_id':job,'revisions':3,'text_revision_seconds':text_seconds,'geometry_revision_seconds':geometry_seconds,
            'cells_after_geometry':len(third['cells']),'raw_reading_preserved':True,'actor_and_timestamp_server_recorded':True,
            'stale_revision_http':409,'prior_revision_bytes_unchanged':True,'old_corrections_not_applied_after_geometry':True,
            'result_hashes':[first_hash,second_hash,third_hash],'correction_value':'synthetic validation text; not ground truth'}
    (args.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)


if __name__=='__main__':main()
