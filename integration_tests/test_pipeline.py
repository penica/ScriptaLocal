from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image,ImageDraw

from integration_tests import test_jobs as fixtures
from scriptalocal.contracts import JobResult
from scriptalocal.images import decode_image,persist_original
from scriptalocal.pipeline import ImagePipeline,PipelineSettings,source_metadata
from scriptalocal.recognition import Generation,RecognitionOOM
from scriptalocal.segmentation import DetectedLine,SegmentationSettings
from scriptalocal.worker import StageWorker
from scriptalocal.job_queue import QueueError


class SyntheticSegmenter:
    reference={'engine':'kraken','model_id':'synthetic-segmenter','revision':'a'*64,'weights_sha256':'a'*64}
    model_height=200
    def __init__(self):self.calls=0
    def __call__(self,canvas):
        self.calls+=1
        gray=np.asarray(canvas.convert('L'));ink=(gray<100).astype('uint8')
        count,_,stats,_=cv2.connectedComponentsWithStats(ink)
        for x,y,width,height,area in stats[1:]:
            if area<2:continue
            box=(int(x),int(y),int(x+width),int(y+height))
            left,top,right,bottom=box
            yield DetectedLine(((left,top),(right,top),(right,bottom),(left,bottom)),
                               ((left,bottom-1),(right,bottom-1)),canvas.crop(box))


class SyntheticRecognizer:
    reference={'engine':'transformers','model_id':'synthetic-recognizer','revision':'b'*40,'weights_sha256':'c'*64}
    manifest_sha256='d'*64
    precision='fp32'
    def __init__(self,mode='success'):self.mode=mode;self.calls=0;self.recoveries=0
    def __call__(self,images,**kwargs):
        self.calls+=1
        if self.mode=='oom':raise RecognitionOOM('injected')
        return [Generation('  EXACT Name.  ',self.mode=='truncated',8) for image in images]
    def recover(self):self.recoveries+=1


class PipelineTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.JobQueueTests.setUpClass.__func__)
    setUp=fixtures.JobQueueTests.setUp
    tearDown=fixtures.JobQueueTests.tearDown
    clear=fixtures.JobQueueTests.clear

    def prepare(self,*,mode='success',settings=None,orientation=1):
        image=Image.new('RGB',(300,100),'white');draw=ImageDraw.Draw(image)
        draw.rectangle((20,20,45,25),fill='black');draw.rectangle((20,50,60,55),fill='black');draw.rectangle((220,35,250,40),fill='black')
        output=BytesIO();exif=Image.Exif();exif[274]=orientation
        image.save(output,format='PNG',exif=exif)
        decoded=decode_image(output.getvalue());root=Path(self.directory.name)/'originals';persist_original(decoded,root)
        self.segmenter=SyntheticSegmenter();self.recognizer=SyntheticRecognizer(mode)
        self.settings=settings or PipelineSettings(segmentation=SegmentationSettings(model_height=200,padding=10,minimum_width=100))
        self.pipeline=ImagePipeline(self.queue,root,self.segmenter,self.recognizer,runtime_image='sha256:'+'0'*64,settings=self.settings)
        # Fixtures use explicit boundaries and synthetic strokes, not real HTR.
        dividers=[{'top':[100,0],'bottom':[100,100]},{'top':[200,0],'bottom':[200,100]}]
        if orientation!=1:dividers=[]
        self.job=self.queue.create('alice',source_metadata(decoded),{'corrected_dividers':dividers},self.pipeline.configuration_sha256)[0]
        self.worker=StageWorker(self.queue,'pipeline-fixture',self.pipeline.handlers,configuration_sha256=self.pipeline.configuration_sha256)
        return decoded

    def finish(self):
        outcomes=[self.worker.run_once() for _ in range(3)]
        self.assertEqual([outcome.status for outcome in outcomes],['completed']*3,outcomes)
        reference=self.queue.result_reference(self.job['id'],'alice')
        return JobResult.model_validate_json(self.store.read(reference))

    def test_complete_row_preserves_multiline_text_and_blank_middle_position(self):
        decoded=self.prepare();result=self.finish()
        self.assertEqual([cell.order for cell in result.cells],[0,1,2])
        self.assertEqual([cell.state for cell in result.cells],['recognized','blank','recognized'])
        self.assertEqual(result.cells[0].text,'  EXACT Name.  \n  EXACT Name.  ')
        self.assertEqual(result.cells[1].text,'');self.assertEqual(result.cells[1].lines,[])
        self.assertEqual(result.source.sha256,decoded.sha256)
        self.assertFalse(result.review_required)
        self.assertEqual(self.segmenter.calls,2);self.assertEqual(self.recognizer.calls,1)
        self.assertEqual(set(result.stage_seconds),{'layout','segmentation','recognition'})
        with self.queue.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS count FROM sl_stages WHERE status='succeeded'").fetchone()['count'],3)
            layout=db.execute("SELECT a.* FROM sl_stages s JOIN sl_artifacts a ON a.id=s.output_artifact_id WHERE s.name='layout'").fetchone()
        from scriptalocal.job_artifacts import ArtifactRecord
        document=json.loads(self.store.read(ArtifactRecord(layout['id'],layout['logical_key'],layout['relative_path'],layout['sha256'],layout['bytes'])))
        self.assertEqual(document['configuration'],self.pipeline.manifest)
        self.assertEqual(result.provenance.configuration_sha256,self.pipeline.configuration_sha256)

    def test_exif_result_geometry_is_mapped_to_encoded_source_once(self):
        decoded=self.prepare(orientation=6);result=self.finish()
        self.assertEqual((result.source.width,result.source.height),(300,100))
        self.assertEqual((result.source.oriented_width,result.source.oriented_height),(100,300))
        self.assertEqual(len(result.cells),1);self.assertEqual(len(result.cells[0].lines),3)
        for line in result.cells[0].lines:
            self.assertTrue(all(0<=x<=300 and 0<=y<=100 for x,y in [*line.polygon,*line.baseline]))
        self.assertEqual(result.source.sha256,decoded.sha256)

    def test_no_valid_layout_does_not_fabricate_a_single_cell(self):
        from scriptalocal.layout import Layout
        self.prepare()
        with patch('scriptalocal.pipeline.detect_layout',return_value=Layout((),(),('ambiguous_crossing_or_narrow_borders',))):
            outcome=self.worker.run_once()
        self.assertEqual((outcome.status,outcome.error_code),('failed','layout_requires_review'))
        self.assertEqual(self.segmenter.calls,0)

    def test_truncated_recognition_keeps_raw_text_and_own_scope_warnings(self):
        self.prepare(mode='truncated');result=self.finish()
        self.assertTrue(result.review_required)
        for index in [0,2]:
            cell=result.cells[index];self.assertEqual(cell.state,'needs-review');self.assertTrue(cell.warnings)
            for line in cell.lines:
                self.assertEqual(line.text,line.raw_text);self.assertTrue(line.warnings)
                self.assertEqual(line.state,'needs-review')
        self.assertEqual(result.cells[1].state,'blank')

    def test_oom_failure_is_explicit_partial_result_and_blank_is_retained(self):
        self.prepare(mode='oom');result=self.finish()
        self.assertEqual([cell.state for cell in result.cells],['failed','blank','failed'])
        self.assertIsNone(result.cells[0].text)
        self.assertTrue(result.review_required)
        self.assertTrue(all(line.text is None and line.warnings for cell in result.cells for line in cell.lines))
        self.assertLessEqual(self.recognizer.recoveries,4)

    def test_segmentation_failure_does_not_become_blank_or_empty_success(self):
        self.prepare()
        with patch.object(SyntheticSegmenter,'__call__',side_effect=RuntimeError('private model failure')):result=self.finish()
        self.assertEqual([cell.state for cell in result.cells],['failed','blank','failed'])
        self.assertEqual(self.recognizer.calls,0);self.assertTrue(result.review_required)
        self.assertIsNone(result.cells[0].text)

    def test_result_and_line_budget_overflow_fail_without_publishing_truncated_json(self):
        settings=PipelineSettings(segmentation=SegmentationSettings(model_height=200),maximum_result_bytes=100)
        self.prepare(settings=settings)
        self.assertEqual(self.worker.run_once().status,'completed')
        self.assertEqual(self.worker.run_once().status,'completed')
        final=self.worker.run_once();self.assertEqual((final.status,final.error_code),('failed','result_limits_exceeded'))
        with self.queue.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS count FROM sl_artifacts WHERE logical_key='result'").fetchone()['count'],0)

    def test_line_budget_overflow_fails_segmentation_stage(self):
        self.prepare(settings=PipelineSettings(segmentation=SegmentationSettings(model_height=200),maximum_line_artifacts=1))
        self.assertEqual(self.worker.run_once().status,'completed')
        outcome=self.worker.run_once();self.assertEqual((outcome.status,outcome.error_code),('failed','result_limits_exceeded'))
        self.assertEqual(self.recognizer.calls,0)

    def test_original_tampering_is_detected_before_layout(self):
        decoded=self.prepare();(self.pipeline.original_root/(decoded.sha256+'.original')).write_bytes(b'corrupt')
        outcome=self.worker.run_once()
        self.assertEqual((outcome.status,outcome.error_code),('failed','source_checksum_mismatch'))
        self.assertEqual(self.segmenter.calls,0);self.assertEqual(self.recognizer.calls,0)

    def test_wrong_model_selection_does_not_silently_use_resident_default(self):
        decoded=self.prepare();self.clear()
        self.job=self.queue.create('alice',source_metadata(decoded),{'model_id':'unavailable'},self.pipeline.configuration_sha256)[0]
        outcome=self.worker.run_once();self.assertEqual((outcome.status,outcome.error_code),('failed','model_unavailable'))
        self.assertEqual(self.segmenter.calls,0)

    def test_unverified_foreign_line_artifact_cannot_be_recognized(self):
        self.prepare();self.worker.run_once();self.worker.run_once()
        # Existing artifact bytes remain immutable; inject only an invalid reader
        # descriptor to exercise binding, without changing committed files.
        original=self.pipeline._inputs
        def tampered(context,kind):
            data,records=original(context,kind)
            data['cells'][0]['lines'][0]['image_artifact_id']='00000000-0000-0000-0000-000000000000'
            return data,records
        with patch.object(self.pipeline,'_inputs',side_effect=tampered):outcome=self.worker.run_once()
        self.assertEqual((outcome.status,outcome.error_code),('failed','stage_input_mismatch'))
        self.assertEqual(self.recognizer.calls,0)


    def test_text_revision_assembles_without_model_calls_and_preserves_raw_history(self):
        self.prepare();self.finish();identifier=self.job['id']
        first_record=self.queue.result_reference(identifier,'alice');original=self.store.read(first_record)
        first=JobResult.model_validate_json(original);line=first.cells[0].lines[0]
        calls=(self.segmenter.calls,self.recognizer.calls)
        row=self.queue.revise(identifier,'alice',{'expected_revision':1,'text_corrections':[{'line_id':str(line.id),'text':'Corrected Name','state':'recognized'}]})
        self.assertEqual(row['current_revision'],2)
        self.assertEqual(self.worker.run_once().status,'completed')
        second=JobResult.model_validate_json(self.store.read(self.queue.result_reference(identifier,'alice')))
        corrected=second.cells[0].lines[0]
        self.assertEqual(corrected.text,'Corrected Name');self.assertEqual(corrected.raw_text,line.raw_text)
        self.assertEqual(corrected.raw_state,line.raw_state);self.assertEqual(corrected.correction.actor_id,'alice')
        self.assertEqual(second.cells[0].text,'Corrected Name\n'+first.cells[0].lines[1].text)
        self.assertEqual((self.segmenter.calls,self.recognizer.calls),calls)
        self.assertEqual(self.store.read(first_record),original)
        self.assertEqual(self.store.read(self.queue.result_reference(identifier,'alice',revision=1)),original)

    def test_unreadable_revision_preserves_null_and_later_reading_resolves_only_human_warning(self):
        self.prepare();self.finish();identifier=self.job['id']
        first=JobResult.model_validate_json(self.store.read(self.queue.result_reference(identifier,'alice')))
        line=first.cells[0].lines[0]
        self.queue.revise(identifier,'alice',{'expected_revision':1,'text_corrections':[{'line_id':str(line.id),'text':None,'state':'unreadable'}]})
        self.assertEqual(self.worker.run_once().status,'completed')
        second=JobResult.model_validate_json(self.store.read(self.queue.result_reference(identifier,'alice')))
        self.assertIsNone(second.cells[0].text);self.assertIsNone(second.cells[0].lines[0].text);self.assertTrue(second.review_required)
        self.queue.revise(identifier,'alice',{'expected_revision':2,'text_corrections':[{'line_id':str(line.id),'text':'Resolved name','state':'recognized'}]})
        self.assertEqual(self.worker.run_once().status,'completed')
        third=JobResult.model_validate_json(self.store.read(self.queue.result_reference(identifier,'alice')))
        self.assertEqual(third.cells[0].lines[0].raw_text,line.raw_text);self.assertEqual(third.cells[0].lines[0].text,'Resolved name')
        self.assertFalse(third.review_required)
        self.assertIsNone(JobResult.model_validate_json(self.store.read(self.queue.result_reference(identifier,'alice',revision=2))).cells[0].text)

    def test_geometry_revision_reruns_pipeline_and_never_carries_old_line_corrections(self):
        self.prepare();self.finish();identifier=self.job['id']
        first=JobResult.model_validate_json(self.store.read(self.queue.result_reference(identifier,'alice')))
        self.queue.revise(identifier,'alice',{'expected_revision':1,'text_corrections':[{'line_id':str(first.cells[0].lines[0].id),'text':'Human reading','state':'recognized'}]})
        self.assertEqual(self.worker.run_once().status,'completed')
        self.queue.revise(identifier,'alice',{'expected_revision':2,'corrected_dividers':[]})
        self.finish()
        third=JobResult.model_validate_json(self.store.read(self.queue.result_reference(identifier,'alice')))
        self.assertEqual(third.revision,3);self.assertEqual(len(third.cells),1)
        self.assertTrue(all(line.correction is None for cell in third.cells for line in cell.lines))
        second=JobResult.model_validate_json(self.store.read(self.queue.result_reference(identifier,'alice',revision=2)))
        self.assertEqual(second.cells[0].lines[0].correction.text,'Human reading')

    def test_concurrent_revision_writers_have_one_winner_and_invalid_geometry_is_atomic(self):
        self.prepare();self.finish();identifier=self.job['id']
        first=JobResult.model_validate_json(self.store.read(self.queue.result_reference(identifier,'alice')))
        for dividers in [[{'top':[200,0],'bottom':[100,100]},{'top':[100,0],'bottom':[200,100]}],[{'top':[301,0],'bottom':[301,100]}]]:
            with self.assertRaises(QueueError) as caught:self.queue.revise(identifier,'alice',{'expected_revision':1,'corrected_dividers':dividers})
            self.assertEqual(caught.exception.code,'invalid_revision')
        def write(value):
            try:
                self.queue.revise(identifier,'alice',{'expected_revision':1,'text_corrections':[{'line_id':str(first.cells[0].lines[0].id),'text':value,'state':'recognized'}]})
                return 'accepted'
            except QueueError as exc:return exc.code
        with ThreadPoolExecutor(max_workers=2) as executor:outcomes=list(executor.map(write,['Writer A','Writer B']))
        self.assertCountEqual(outcomes,['accepted','revision_conflict'])
        self.assertEqual(self.worker.run_once().status,'completed')
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_revision_edits WHERE job_id=%s',(identifier,)).fetchone()['count'],1)

    def test_cancelled_pending_revision_retains_completed_history(self):
        self.prepare();self.finish();identifier=self.job['id']
        original=self.store.read(self.queue.result_reference(identifier,'alice'))
        first=JobResult.model_validate_json(original)
        self.queue.revise(identifier,'alice',{'expected_revision':1,'text_corrections':[{'line_id':str(first.cells[0].lines[0].id),'text':None,'state':'unreadable'}]})
        self.queue.cancel(identifier,'alice');self.assertEqual(self.worker.run_once().status,'idle')
        self.assertEqual(self.store.read(self.queue.result_reference(identifier,'alice',revision=1)),original)
        self.assertEqual(self.queue.get(identifier,'alice')['status'],'cancelled')


if __name__=='__main__':unittest.main()
