"""Private real HTTP manuscript probe; prints counts/states, never readings or tokens."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
from uuid import uuid4


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8092')
    parser.add_argument('--token-file',type=Path,required=True)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--jobs',type=int,default=2)
    args=parser.parse_args()
    if not 1<=args.jobs<=10:raise ValueError('Use one to ten probe jobs')
    token=args.token_file.read_text().strip();image=args.image.read_bytes();digest=hashlib.sha256(image).hexdigest()
    args.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path,*,body=None,headers=None):
        headers=dict(headers or {});headers['Authorization']='Bearer '+token
        req=urllib.request.Request(args.url+path,data=body,headers=headers)
        try:
            with opener.open(req,timeout=15) as response:return response.status,response.read()
        except urllib.error.HTTPError as response:return response.code,response.read()
    deadline=time.monotonic()+120
    while True:
        code,content=request('/health/ready')
        if code==200 and json.loads(content)['ready']:break
        if time.monotonic()>deadline:raise RuntimeError('Worker readiness deadline exceeded')
        time.sleep(1)
    reports=[]
    for index in range(args.jobs):
        boundary='scriptalocal-'+uuid4().hex;key='http-validation-'+uuid4().hex
        body=('--'+boundary+'\r\nContent-Disposition: form-data; name="image"; filename="row.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode()+image+('\r\n--'+boundary+'--\r\n').encode()
        headers={'Content-Type':'multipart/form-data; boundary='+boundary,'Idempotency-Key':key}
        started=time.monotonic();code,content=request('/v1/jobs',body=body,headers=headers)
        if code!=202:raise RuntimeError('HTTP admission failed: '+str(code))
        accepted=json.loads(content);job=accepted['job_id']
        (args.output/f'job-{index+1}-receipt.json').write_text(json.dumps(accepted,indent=2)+'\n')
        code,content=request('/v1/jobs',body=body,headers=headers);replay=json.loads(content)
        assert code==202 and replay['job_id']==job and replay['idempotency_replayed']
        last=None;deadline=time.monotonic()+360
        while True:
            code,content=request('/v1/jobs/'+job);assert code==200
            status=json.loads(content);state=(status['status'],status['stage'])
            if state!=last:
                print(json.dumps({'probe_job':index+1,'status':state[0],'stage':state[1]}),flush=True);last=state
            if state[0] in {'succeeded','failed','cancelled'}:break
            if time.monotonic()>deadline:raise RuntimeError('HTTP completion deadline exceeded')
            time.sleep(1)
        assert status['status']=='succeeded'
        code,content=request('/v1/jobs/'+job+'/result');assert code==200
        result=json.loads(content)
        assert result['job_id']==job and result['source']['sha256']==digest
        assert [cell['order'] for cell in result['cells']]==list(range(len(result['cells'])))
        path=args.output/f'job-{index+1}-result.json';path.write_bytes(content);path.chmod(0o600)
        report={'job_id':job,'seconds':round(time.monotonic()-started,3),'cells':len(result['cells']),
                'lines':sum(len(cell['lines']) for cell in result['cells']),'review_required':result['review_required'],
                'configuration_sha256':result['provenance']['configuration_sha256'],'result_sha256':hashlib.sha256(content).hexdigest(),
                'stage_seconds':result['stage_seconds'],'idempotency_replayed':True}
        reports.append(report);(args.output/'summary.json').write_text(json.dumps({'source_sha256':digest,'jobs':reports},indent=2)+'\n')
        print(json.dumps({'event':'http_job_verified',**report}),flush=True)


if __name__=='__main__':main()
