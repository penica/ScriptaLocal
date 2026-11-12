from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from PIL import Image
from scriptalocal.recognition import Generation,LineInput,RecognitionError,RecognitionOOM,RecognitionSettings,recognize_lines
from scriptalocal.trocr_recognizer import verify_model
from scriptalocal.worker_control import ProcessingInterrupted


class Backend:
    def __init__(self,capacity=100,values=None):self.calls=[];self.recoveries=0;self.capacity=capacity;self.values=values
    def __call__(self,images,**options):
        self.calls.append((len(images),options))
        if len(images)>self.capacity:raise RecognitionOOM('injected')
        return self.values if self.values is not None else [Generation('  Name, Exact.  ',False,8) for _ in images]
    def recover(self):self.recoveries+=1


class RecognitionTests(unittest.TestCase):
    def lines(self,count,size=(100,20)):
        return [LineInput(uuid4(),Image.new('RGB',size,'white')) for _ in range(count)]

    def test_bounded_batching_preserves_order_identity_and_verbatim_text(self):
        lines=self.lines(7);backend=Backend()
        settings=RecognitionSettings(batch_size=3,maximum_line_pixels=2000,maximum_batch_pixels=4000)
        result=recognize_lines(lines,backend,settings=settings)
        self.assertEqual([call[0] for call in backend.calls],[2,2,2,1])
        self.assertEqual([line.id for line in result.lines],[line.id for line in lines])
        self.assertTrue(all(line.text=='  Name, Exact.  ' and line.state=='recognized' for line in result.lines))
        self.assertTrue(all(call[1]=={'max_new_tokens':128,'num_beams':4} for call in backend.calls))

    def test_empty_and_truncated_lines_are_review_not_blank(self):
        backend=Backend(values=[Generation('',False,1),Generation('partial',True,128),Generation('  ',False,2)])
        result=recognize_lines(self.lines(3),backend)
        self.assertEqual([line.state for line in result.lines],['needs-review']*3)
        self.assertEqual([line.text for line in result.lines],['','partial','  '])
        self.assertIn('generation_truncated',result.lines[1].warnings)
        self.assertIn('empty_recognition',result.lines[0].warnings)

    def test_oom_splits_batches_without_reload_or_duplicate_lines(self):
        lines=self.lines(4);backend=Backend(capacity=1)
        result=recognize_lines(lines,backend)
        self.assertEqual([line.id for line in result.lines],[line.id for line in lines])
        self.assertEqual(result.oom_events,3);self.assertEqual(result.recoveries,3)
        self.assertEqual([call[0] for call in backend.calls],[4,2,1,1,2,1,1])
        self.assertTrue(all(line.state=='recognized' for line in result.lines))

    def test_persistent_oom_has_bounded_recovery_and_explicit_failed_outputs(self):
        lines=self.lines(20);backend=Backend(capacity=0)
        result=recognize_lines(lines,backend,settings=RecognitionSettings(maximum_oom_recoveries=2))
        self.assertEqual(result.oom_events,3);self.assertEqual(result.recoveries,2)
        self.assertEqual(result.backend_calls,3)
        self.assertEqual([line.id for line in result.lines],[line.id for line in lines])
        self.assertTrue(all(line.state=='failed' and line.text is None and line.warnings for line in result.lines))

    def test_input_result_limits_and_backend_faults_do_not_become_empty_success(self):
        lines=self.lines(2)
        with self.assertRaises(RecognitionError):recognize_lines([lines[0],lines[0]],Backend())
        with self.assertRaises(RecognitionError):recognize_lines(lines,Backend(),settings=RecognitionSettings(maximum_lines=1))
        with self.assertRaises(RecognitionError):recognize_lines(lines,Backend(),settings=RecognitionSettings(maximum_side=99))
        with self.assertRaises(RecognitionError):recognize_lines(lines,Backend(values=[]))
        def broken(*args,**kwargs):raise RuntimeError('private data')
        with self.assertRaisesRegex(RecognitionError,'^Recognition backend failed$'):recognize_lines(lines,broken)
        self.assertEqual(recognize_lines([],Backend()).backend_calls,0)

    def test_wide_line_is_retained_with_review_and_limits_are_validated(self):
        result=recognize_lines(self.lines(1,size=(4000,30)),Backend())
        self.assertIn('wide_line_requires_review',result.lines[0].warnings)
        self.assertEqual(result.lines[0].text,'  Name, Exact.  ')
        for kwargs in [{'batch_size':0},{'maximum_new_tokens':True},{'maximum_oom_recoveries':-1},{'wide_line_review_ratio':float('nan')}]:
            with self.assertRaises(ValueError):RecognitionSettings(**kwargs)

    def test_checkpoint_cancellation_prevents_next_batch(self):
        backend=Backend();checks=[]
        def checkpoint():
            checks.append(True)
            if backend.calls:raise ProcessingInterrupted('cancel_requested')
        with self.assertRaises(ProcessingInterrupted):
            recognize_lines(self.lines(3),backend,settings=RecognitionSettings(batch_size=1),checkpoint=checkpoint)
        self.assertEqual(len(backend.calls),1)

    def test_pinned_manifest_checks_paths_bytes_hashes_and_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);weights=b'fake safetensors, not loaded'
            (root/'model.safetensors').write_bytes(weights)
            manifest={'files':[{'path':'model.safetensors','bytes':len(weights),'sha256':hashlib.sha256(weights).hexdigest()}]}
            def save():
                blob=json.dumps(manifest).encode();(root/'manifest.json').write_bytes(blob)
                return hashlib.sha256(blob).hexdigest()
            pinned=save();self.assertEqual(verify_model(root,pinned),manifest)
            with self.assertRaises(RecognitionError):verify_model(root,'0'*64)
            (root/'model.safetensors').write_bytes(b'changed')
            with self.assertRaises(RecognitionError):verify_model(root,pinned)
            (root/'target').write_bytes(weights);(root/'model.safetensors').unlink();(root/'model.safetensors').symlink_to(root/'target')
            with self.assertRaises(RecognitionError):verify_model(root,pinned)
            manifest['files'][0]['path']='../model.safetensors';pinned=save()
            with self.assertRaises(RecognitionError):verify_model(root,pinned)


if __name__=='__main__':unittest.main()
