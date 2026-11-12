"""Validate real probe provenance and export v1 JSON without publishing its text."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from scriptalocal.contracts import JobResult
from scriptalocal.images import decode_image


def build_result(raw,image):
    if not re.fullmatch(r'[a-f0-9]{64}',raw['source_script_sha256']):
        raise ValueError('Probe source script checksum is invalid')
    if image.sha256!=raw['image_sha256'] or list(image.pixels.size)!=raw['original_size']:
        raise ValueError('Probe result does not belong to the supplied image')
    configuration=raw['configuration']
    config_hash=hashlib.sha256(json.dumps(configuration,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if config_hash!=raw['configuration_sha256']:
        raise ValueError('Probe configuration checksum mismatch')
    if not raw['lines']:
        raise ValueError('A failed segmentation cannot become a successful empty result')
    if raw['total_seconds']<raw['segmentation_seconds']:
        raise ValueError('Probe stage timings are inconsistent')
    if configuration['manual_cell_bounds']!=raw['manual_cell_bounds']:
        raise ValueError('Probe cell bounds do not match configuration')
    left,top,right,bottom=raw['manual_cell_bounds']
    if not (0<=left<right<=image.pixels.width and 0<=top<bottom<=image.pixels.height):
        raise ValueError('Probe cell bounds are invalid')
    recognition={'engine':'transformers','model_id':raw['model'],'revision':raw['revision'],
                 'weights_sha256':raw['recognition_weights_sha256']}
    segmentation={'engine':'kraken','model_id':'kraken/blla.mlmodel',
                  'revision':raw['segmentation_weights_sha256'],
                  'weights_sha256':raw['segmentation_weights_sha256']}
    cell_id=uuid5(NAMESPACE_URL,f"scriptalocal:probe-cell:{image.sha256}:{raw['manual_cell_bounds']}")
    original=image.original_from_oriented
    def points(values):return [original.point(*point) for point in values]
    lines=[]
    for index,line in enumerate(raw['lines']):
        if line['order']!=index:
            raise ValueError('Probe line order is inconsistent')
        if not isinstance(line['text'],str):
            raise ValueError('Probe recognition output must be a string')
        uncertain=bool(line['truncated']) or not line['text'].strip()
        warnings=[]
        if uncertain:
            warnings=[{'code':'generation_truncated_or_empty','message':'Recognition needs review; no blank or unreadable state was inferred.'}]
        state='needs-review' if uncertain else 'recognized'
        lines.append({'id':uuid5(cell_id,str(index)),'order':index,
                      'polygon':points(line['polygon']),'baseline':points(line['baseline']),
                      'state':state,'text':line['text'],'raw_state':state,'raw_text':line['text'],
                      'recognition_model':recognition,'warnings':warnings})
    state='needs-review' if any(line['state']=='needs-review' for line in lines) else 'recognized'
    cell_warnings=([{'code':'line_needs_review','message':'One or more line readings need review.'}]
                   if state=='needs-review' else [])
    padding=configuration['canvas_padding'] if configuration['preserve_scale'] else 0
    result={'job_id':uuid5(cell_id,raw['configuration_sha256']),'revision':1,
            'source':{'sha256':image.sha256,'width':image.encoded_size[0],'height':image.encoded_size[1],
                      'oriented_width':image.pixels.width,'oriented_height':image.pixels.height,
                      'exif_orientation':image.orientation,
                      'original_to_oriented':image.oriented_from_original.coefficients,
                      'mime_type':image.mime_type},
            'cells':[{'id':cell_id,'order':0,'polygon':points([(left,top),(right,top),(right,bottom),(left,bottom)]),
                      'state':state,'text':'\n'.join(line['text'] for line in lines),'lines':lines,'warnings':cell_warnings}],
            'review_required':True,'warnings':[
                {'code':'manual_single_cell_probe','message':'Only a manually bounded cell was processed; this is not a full-row job.'},
                {'code':'handwriting_accuracy_unmeasured','message':'A corrected reference transcription is not supplied.'}],
            'provenance':{'pipeline_version':'manual-cell-probe-'+raw['source_script_sha256'][:12],
                          'configuration_sha256':config_hash,'segmentation_model':segmentation,
                          'recognition_model':recognition},
            'transforms':[{'from_space':'original_encoded_pixel_edges','to_space':'oriented_pixels',
                           'affine':image.oriented_from_original.coefficients},
                          {'from_space':'oriented_pixels','to_space':'manual_cell_pixels',
                           'affine':[1,0,-left,0,1,-top]},
                          {'from_space':'manual_cell_pixels','to_space':'segmentation_canvas_pixels',
                           'affine':[1,0,padding,0,1,padding]}],
            'stage_seconds':{'segmentation':raw['segmentation_seconds'],
                             'recognition_and_load':raw['total_seconds']-raw['segmentation_seconds']}}
    return JobResult.model_validate(result)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--probe',type=Path,required=True)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    raw=json.loads(args.probe.read_text())
    result=build_result(raw,decode_image(args.image.read_bytes()))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2)+'\n')
    print(json.dumps({'job_id':str(result.job_id),'cells':len(result.cells),
                      'lines':sum(len(cell.lines) for cell in result.cells),
                      'review_required':result.review_required,'schema_validation':'passed',
                      'http_job_exists':False},indent=2))


if __name__=='__main__':main()
