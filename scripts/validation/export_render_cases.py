"""Replay committed polygons to isolate native black versus alpha-white rendering."""
import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID

from PIL import Image
from kraken.containers import BaselineLine,Segmentation
from scriptalocal.images import Affine,ImageLimits,decode_image
from scriptalocal.job_artifacts import ArtifactRecord,ArtifactStore
from scriptalocal.job_queue import JobQueue
from scriptalocal.kraken_rendering import render_lines
from scriptalocal.layout import Cell,LayoutSettings,extract_cell


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--image',type=Path,required=True);parser.add_argument('--corpus',type=Path,required=True)
    parser.add_argument('--dsn-file',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    receipt=json.loads((args.run/'job-receipt.json').read_text());job_id=UUID(receipt['job_id'])
    queue=JobQueue(args.dsn_file.read_text().strip(),ArtifactStore(args.run/'artifacts'))
    with queue.connect() as db:
        db.execute('SET TRANSACTION READ ONLY')
        identity=db.execute('SELECT current_database() AS database,current_user AS role').fetchone()
        assert identity=={'database':'scriptalocal_queue_validation','role':'scriptalocal_test'}
        job=db.execute('SELECT owner FROM sl_jobs WHERE id=%s',(job_id,)).fetchone();assert job['owner']==receipt['owner']
        rows=db.execute("SELECT s.name,a.* FROM sl_stages s JOIN sl_artifacts a ON a.id=s.output_artifact_id WHERE s.job_id=%s AND s.name IN ('layout','segmentation') AND s.status='succeeded'",(job_id,)).fetchall()
    documents={row['name']:json.loads(queue.artifacts.read(ArtifactRecord(row['id'],row['logical_key'],row['relative_path'],row['sha256'],row['bytes']))) for row in rows}
    config=documents['layout']['configuration']['settings'];settings=config['segmentation']
    corpus=json.loads((args.corpus/'corpus.json').read_text());by_id={item['line_id']:item for item in corpus['entries']}
    image=decode_image(args.image.read_bytes(),limits=ImageLimits(**config['image']));assert image.sha256==corpus['source_sha256']
    args.output.mkdir(parents=True,exist_ok=True,mode=0o700);entries=[];replayed=0;same_sizes=0
    for cell in documents['segmentation']['cells']:
        oriented=tuple(image.oriented_from_original.point(*point) for point in cell['polygon'])
        crop=extract_cell(image,Cell(cell['id'],cell['order'],oriented),settings=LayoutSettings(**config['layout']))
        assert list(crop.original_from_crop.coefficients)==cell['transforms']['original_from_crop']
        pad=settings['padding']
        canvas=Image.new('RGB',(max(settings['minimum_width'],crop.pixels.width+2*pad),max(settings['model_height'],crop.pixels.height+2*pad)),'white')
        canvas.paste(crop.pixels,(pad,pad))
        transform=Affine(1,0,pad,0,1,pad).after(crop.original_from_crop.inverse())
        def mapped(points):
            result=[]
            for point in points:
                x,y=transform.point(*point)
                assert abs(x-round(x))<1e-6 and abs(y-round(y))<1e-6
                result.append((int(round(x)),int(round(y))))
            return result
        for line in cell['lines']:
            geometry=BaselineLine(id=line['id'],baseline=mapped(line['baseline']),boundary=mapped(line['raw_polygon']))
            bounds=Segmentation(type='baselines',imagename='private-source',text_direction='horizontal-lr',script_detection=False,lines=[geometry])
            black,_=next(render_lines(canvas,bounds,background='legacy-black'))
            white,_=next(render_lines(canvas,bounds,background='white-alpha'))
            entry=by_id[line['id']]
            original=args.corpus/entry['filename'];assert hashlib.sha256(original.read_bytes()).hexdigest()==entry['sha256']
            with Image.open(original) as source:
                assert source.size==black.size and source.convert('RGB').tobytes()==black.convert('RGB').tobytes()
            replayed+=1;same_sizes+=black.size==white.size
            target=args.output/entry['filename'];white.save(target);target.chmod(0o600)
            entries.append({**entry,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    output={**corpus,'entries':entries,'rendering':{'mode':'white-alpha','legacy_black_replayed_exactly':replayed,'same_crop_sizes':same_sizes,
             'original_source_unchanged':True,'renderer_sha256':hashlib.sha256(Path('scriptalocal/kraken_rendering.py').read_bytes()).hexdigest()}}
    target=args.output/'corpus.json';target.write_text(json.dumps(output,indent=2,ensure_ascii=False)+'\n');target.chmod(0o600)
    print(json.dumps(output['rendering']))

if __name__=='__main__':main()
