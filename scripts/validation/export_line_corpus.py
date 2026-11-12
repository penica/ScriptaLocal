"""Export committed line PNGs for offline model comparison; no queue mutations."""
import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID

from scriptalocal.contracts import JobResult
from scriptalocal.job_artifacts import ArtifactRecord,ArtifactStore
from scriptalocal.job_queue import JobQueue


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--dsn-file',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    receipt=json.loads((args.run/'job-receipt.json').read_text());job_id=UUID(receipt['job_id'])
    queue=JobQueue(args.dsn_file.read_text().strip(),ArtifactStore(args.run/'artifacts'))
    with queue.connect() as db:
        db.execute('SET TRANSACTION READ ONLY')
        identity=db.execute('SELECT current_database() AS database,current_user AS role').fetchone()
        assert identity=={'database':'scriptalocal_queue_validation','role':'scriptalocal_test'}
        owner=db.execute('SELECT owner FROM sl_jobs WHERE id=%s',(job_id,)).fetchone()
        assert owner and owner['owner']==receipt['owner']
        rows=db.execute("SELECT a.* FROM sl_stages s JOIN sl_artifacts a ON a.stage_id=s.id WHERE s.job_id=%s AND s.name='segmentation' AND s.status='succeeded'",(job_id,)).fetchall()
    records={str(row['id']):ArtifactRecord(row['id'],row['logical_key'],row['relative_path'],row['sha256'],row['bytes']) for row in rows}
    primary=next(record for record in records.values() if record.logical_key=='output')
    segmentation=json.loads(queue.artifacts.read(primary))
    result=JobResult.model_validate_json(queue.artifacts.read(queue.result_reference(job_id,receipt['owner'])))
    assert result.revision==segmentation['revision'] and result.source.sha256==segmentation['source']['sha256']
    result_lines={str(line.id):line for cell in result.cells for line in cell.lines}
    args.output.mkdir(parents=True,exist_ok=True,mode=0o700)
    entries=[]
    for cell in segmentation['cells']:
        for line in cell['lines']:
            record=records[line['image_artifact_id']];content=queue.artifacts.read(record)
            reading=result_lines[line['id']];filename=line['id']+'.png';target=args.output/filename
            if target.exists():assert target.read_bytes()==content
            else:target.write_bytes(content);target.chmod(0o600)
            entries.append({'line_id':line['id'],'cell_order':cell['order'],'line_order':line['order'],
                'filename':filename,'sha256':hashlib.sha256(content).hexdigest(),'baseline_raw_text':reading.raw_text,'baseline_raw_state':reading.raw_state})
    document={'schema':'scriptalocal-private-line-comparison-v1','source_sha256':result.source.sha256,
              'baseline_provenance':result.provenance.model_dump(mode='json'),'entries':entries,
              'ground_truth':'not supplied; baseline output is not truth'}
    target=args.output/'corpus.json';target.write_text(json.dumps(document,indent=2)+'\n');target.chmod(0o600)
    print(json.dumps({'committed_lines_exported':len(entries),'png_bytes_unchanged':True,'database_writes':False,'raw_text':'private corpus.json only'}))

if __name__=='__main__':main()
