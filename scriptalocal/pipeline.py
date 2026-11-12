"""Image-to-result stage handlers with immutable intermediate evidence."""
from dataclasses import asdict,dataclass
import hashlib
from importlib import metadata
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import re
import stat
import time
from uuid import UUID

from .contracts import JobResult,ModelReference,SourceImage
from .images import ImageLimits,decode_image
from .layout import Cell,LayoutSettings,detect_layout,extract_cell
from .recognition import LineInput,RecognitionSettings,recognize_lines
from .segmentation import SegmentationError,SegmentationSettings,segment_cell
from .worker import StageFailure
from .job_queue import canonical_hash


@dataclass(frozen=True)
class PipelineSettings:
    image:ImageLimits=ImageLimits()
    layout:LayoutSettings=LayoutSettings()
    segmentation:SegmentationSettings=SegmentationSettings()
    recognition:RecognitionSettings=RecognitionSettings()
    maximum_cells:int=257
    maximum_line_artifacts:int=2048
    maximum_total_line_pixels:int=32_000_000
    maximum_result_bytes:int=2*1024*1024

    def __post_init__(self):
        for name in ['maximum_cells','maximum_line_artifacts','maximum_total_line_pixels','maximum_result_bytes']:
            if type(getattr(self,name))is not int or getattr(self,name)<1:raise ValueError('Pipeline limits must be positive integers')


def source_metadata(image):
    return SourceImage(sha256=image.sha256,width=image.encoded_size[0],height=image.encoded_size[1],
        oriented_width=image.pixels.width,oriented_height=image.pixels.height,exif_orientation=image.orientation,
        original_to_oriented=image.oriented_from_original.coefficients,mime_type=image.mime_type).model_dump(mode='json')


def warning(code):
    return {'code':code,'message':'Review required: '+code.replace('_',' ')+'.'}


def warnings(codes):return [warning(code) for code in sorted(set(codes))]


def encode(document):return json.dumps(document,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


class ImagePipeline:
    def __init__(self,queue,original_root,segmenter,recognizer,*,runtime_image,settings=PipelineSettings()):
        if not re.fullmatch(r'sha256:[a-f0-9]{64}',runtime_image):raise ValueError('Provide the immutable worker image ID')
        self.queue=queue;self.original_root=Path(original_root);self.segmenter=segmenter;self.recognizer=recognizer;self.settings=settings
        self.segmentation_reference=ModelReference.model_validate(segmenter.reference).model_dump(mode='json')
        self.recognition_reference=ModelReference.model_validate(recognizer.reference).model_dump(mode='json')
        if segmenter.model_height!=settings.segmentation.model_height:raise ValueError('Segmentation canvas height must match loaded model')
        if recognizer.precision not in {'fp32','fp16'}:raise ValueError('Unsupported recognition precision')
        names=['images.py','layout.py','segmentation.py','recognition.py','pipeline.py','corrections.py','worker.py','worker_control.py','job_queue.py','job_artifacts.py','contracts.py','kraken_segmenter.py','trocr_recognizer.py']
        hashes={name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in names}
        self.version='pipeline-'+canonical_hash(hashes)[:12]
        self.manifest={'schema':'scriptalocal-pipeline-config-v1','source_sha256':hashes,'settings':asdict(settings),
            'runtime_image':runtime_image,'python':platform.python_version(),
            'packages':dict(sorted((item.metadata['Name'].lower().replace('_','-'),item.version) for item in metadata.distributions() if item.metadata.get('Name'))),
            'models':{'segmentation':self.segmentation_reference,'recognition':self.recognition_reference},
            'recognition_manifest_sha256':recognizer.manifest_sha256,'precision':recognizer.precision,
            'affine_convention':"[a,b,c,d,e,f]; x'=a*x+b*y+c; y'=d*x+e*y+f",
            'recognition_tokenization_cleanup':False}
        self.configuration_sha256=canonical_hash(self.manifest)

    @property
    def handlers(self):return {'layout':self.layout,'segmentation':self.segmentation,'recognition':self.recognition}

    def _check(self,context):
        context.checkpoint()
        if context.lease.configuration_sha256!=self.configuration_sha256:raise StageFailure('configuration_unavailable')
        if context.lease.options['model_id'] not in {None,self.recognition_reference['model_id']}:raise StageFailure('model_unavailable')

    def _original(self,context):
        self._check(context)
        digest=context.lease.source['sha256']
        fd=os.open(self.original_root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:source_fd=os.open(digest+'.original',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        finally:os.close(fd)
        with os.fdopen(source_fd,'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise StageFailure('source_unavailable')
            payload=stream.read(self.settings.image.max_bytes+1)
        if hashlib.sha256(payload).hexdigest()!=digest:raise StageFailure('source_checksum_mismatch')
        image=decode_image(payload,limits=self.settings.image)
        if source_metadata(image)!=context.lease.source:raise StageFailure('source_metadata_mismatch')
        return image

    def _document(self,context,kind,seconds):
        return {'schema':'scriptalocal-stage-v1','kind':kind,'job_id':str(context.lease.job_id),'revision':context.lease.revision,
                'source':context.lease.source,'configuration_sha256':self.configuration_sha256,'stage_seconds':seconds}

    def _inputs(self,context,kind):
        self._check(context);records=context.inputs()
        primary=next((record for record in records if record.logical_key=='output'),None)
        if primary is None:raise StageFailure('stage_input_unavailable')
        data=json.loads(self.queue.artifacts.read(primary))
        expected=self._document(context,kind,data.get('stage_seconds',{}))
        if any(data.get(key)!=value for key,value in expected.items()):raise StageFailure('stage_input_mismatch')
        if kind=='layout' and (data.get('configuration')!=self.manifest or canonical_hash(data['configuration'])!=self.configuration_sha256):
            raise StageFailure('stage_input_mismatch')
        return data,{str(record.id):record for record in records}

    def layout(self,context):
        started=time.monotonic();image=self._original(context)
        dividers=context.lease.options['corrected_dividers']
        corrected=None if dividers is None else [(divider['top'],divider['bottom']) for divider in dividers]
        proposal=detect_layout(image,settings=self.settings.layout,corrected_dividers=corrected)
        if not proposal.cells:raise StageFailure('layout_requires_review')
        if len(proposal.cells)>self.settings.maximum_cells:raise StageFailure('result_limits_exceeded')
        document=self._document(context,'layout',{'layout':time.monotonic()-started})
        document.update(configuration=self.manifest,cells=[asdict(cell) for cell in proposal.cells],
                        warnings=list(proposal.warnings),borders=[asdict(border) for border in proposal.borders],
                        excluded_outer_rules=[asdict(border) for border in proposal.excluded_outer_rules])
        context.checkpoint()
        return [self.queue.artifacts.publish(context.lease,'output',encode(document))]

    def segmentation(self,context):
        started=time.monotonic();image=self._original(context);layout,_=self._inputs(context,'layout')
        if len(layout['cells'])>self.settings.maximum_cells:raise StageFailure('result_limits_exceeded')
        cells=[];records=[];total_pixels=0;line_count=0
        def publish_line(pixels,key):
            nonlocal total_pixels,line_count
            line_count+=1;total_pixels+=pixels.width*pixels.height
            if line_count>self.settings.maximum_line_artifacts or total_pixels>self.settings.maximum_total_line_pixels:
                raise StageFailure('result_limits_exceeded')
            output=BytesIO();pixels.convert('RGB').save(output,format='PNG')
            record=self.queue.artifacts.publish(context.lease,key,output.getvalue(),suffix='.png');records.append(record)
            return str(record.id)
        for item in layout['cells']:
            context.checkpoint();cell=Cell(item['id'],item['order'],tuple(map(tuple,item['polygon'])))
            crop=extract_cell(image,cell,settings=self.settings.layout)
            output={'id':cell.id,'order':cell.order,'polygon':[image.original_from_oriented.point(*point) for point in cell.polygon],
                    'transforms':{'original_from_crop':crop.original_from_crop.coefficients,'oriented_from_crop':crop.oriented_from_crop.coefficients},
                    'lines':[],'unassigned_lines':[],'warnings':[]}
            try:segmented=segment_cell(cell.id,crop,self.segmenter,original_size=image.encoded_size,settings=self.settings.segmentation,checkpoint=context.checkpoint)
            except SegmentationError:
                output.update(state='failed',warnings=['segmentation_failed']);cells.append(output);continue
            output.update(state=segmented.state,warnings=list(segmented.warnings),canvas_size=segmented.canvas_size)
            for line in segmented.lines:
                context.checkpoint()
                output['lines'].append({'id':line.id,'order':line.order,'polygon':line.polygon,'raw_polygon':line.raw_polygon,
                    'baseline':line.baseline,'warnings':list(line.warnings),
                    'image_artifact_id':publish_line(line.pixels,cell.id+'.'+line.id)})
            for index,line in enumerate(segmented.unassigned_lines):
                context.checkpoint()
                output['unassigned_lines'].append({'polygon':line.polygon,'baseline':line.baseline,
                    'image_artifact_id':publish_line(line.pixels,'unassigned.'+cell.id+'.'+str(index))})
            cells.append(output)
        seconds={**layout['stage_seconds'],'segmentation':time.monotonic()-started}
        document=self._document(context,'segmentation',seconds);document.update(cells=cells,warnings=layout['warnings'])
        context.checkpoint();records.append(self.queue.artifacts.publish(context.lease,'output',encode(document)))
        return records

    def recognition(self,context):
        started=time.monotonic();self._check(context)
        edit=self.queue.revision_edit(context.lease)
        if edit is not None:
            from .corrections import apply_text_revision
            details,record=edit;base=JobResult.model_validate_json(self.queue.artifacts.read(record))
            result=apply_text_revision(base,details,context.lease.revision)
            result.stage_seconds['correction_assembly']=time.monotonic()-started
            content=result.model_dump_json().encode()
            if len(content)>self.settings.maximum_result_bytes:raise StageFailure('result_limits_exceeded')
            context.checkpoint()
            return [self.queue.artifacts.publish(context.lease,'result',content)]
        segmentation,artifacts=self._inputs(context,'segmentation')
        inputs=[];seen_artifacts=set();total_pixels=0
        if len(segmentation['cells'])>self.settings.maximum_cells:raise StageFailure('result_limits_exceeded')
        for cell in segmentation['cells']:
            for line in cell['lines']:
                context.checkpoint();identifier=line['image_artifact_id']
                if identifier not in artifacts or identifier in seen_artifacts:raise StageFailure('stage_input_mismatch')
                seen_artifacts.add(identifier)
                if len(inputs)>=self.settings.maximum_line_artifacts:raise StageFailure('result_limits_exceeded')
                image=decode_image(self.queue.artifacts.read(artifacts[identifier]),declared_mime='image/png',
                    limits=ImageLimits(max_pixels=self.settings.recognition.maximum_line_pixels,max_side=self.settings.recognition.maximum_side))
                total_pixels+=image.pixels.width*image.pixels.height
                if total_pixels>self.settings.maximum_total_line_pixels:raise StageFailure('result_limits_exceeded')
                inputs.append(LineInput(UUID(line['id']),image.pixels))
        run=recognize_lines(inputs,self.recognizer,settings=self.settings.recognition,checkpoint=context.checkpoint)
        readings={str(line.id):line for line in run.lines};cells=[]
        for cell in segmentation['cells']:
            lines=[];cell_warnings=list(cell['warnings'])
            for line in cell['lines']:
                reading=readings[line['id']]
                line_warnings=[*line['warnings'],*reading.warnings]
                lines.append({'id':line['id'],'order':line['order'],'polygon':line['polygon'],'baseline':line['baseline'],
                    'state':reading.state,'text':reading.text,'raw_state':reading.state,'raw_text':reading.text,
                    'recognition_model':self.recognition_reference,'warnings':warnings(line_warnings)})
                if line_warnings:cell_warnings.append('line_requires_review')
            if cell['state']=='blank':state='blank';text=''
            elif not lines:state=cell['state'] if cell['state'] in {'failed','needs-review'} else 'needs-review';text=None;cell_warnings.append('no_recognized_lines')
            else:
                text=None if any(line['text'] is None for line in lines) else '\n'.join(line['text'] for line in lines)
                if any(line['state']=='failed' for line in lines):state='failed';cell_warnings.append('line_processing_failed')
                elif cell_warnings or any(line['state']!='recognized' for line in lines):state='needs-review';cell_warnings.append('cell_requires_review')
                else:state='recognized'
            cells.append({'id':cell['id'],'order':cell['order'],'polygon':cell['polygon'],'state':state,'text':text,'lines':lines,'warnings':warnings(cell_warnings)})
        job_warnings=warnings(segmentation['warnings'])
        review=bool(job_warnings or any(cell['warnings'] or cell['state'] not in {'recognized','blank'} for cell in cells))
        result=JobResult.model_validate({'job_id':context.lease.job_id,'revision':context.lease.revision,'source':context.lease.source,
            'cells':cells,'review_required':review,'warnings':job_warnings,
            'provenance':{'pipeline_version':self.version,'configuration_sha256':self.configuration_sha256,
                          'segmentation_model':self.segmentation_reference,'recognition_model':self.recognition_reference},
            'transforms':[{'from_space':'original_encoded_pixel_edges','to_space':'oriented_pixel_edges',
                           'affine':context.lease.source['original_to_oriented']}],
            'stage_seconds':{**segmentation['stage_seconds'],'recognition':time.monotonic()-started}})
        content=result.model_dump_json().encode()
        if len(content)>self.settings.maximum_result_bytes:raise StageFailure('result_limits_exceeded')
        context.checkpoint()
        return [self.queue.artifacts.publish(context.lease,'result',content)]
