from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi
from pydantic import ValidationError

from scriptalocal.contracts import (CoordinateTransform, ErrorResponse, JobAccepted,
                                   JobOptions, JobResult, JobStatus, RevisionInput)
from scripts.export_contract import document


EXAMPLES=Path(__file__).resolve().parents[1]/'docs/api/examples'


def example(name):
    return json.loads((EXAMPLES/(name+'.json')).read_text())


class ContractTests(unittest.TestCase):
    def result(self,data):
        return JobResult.model_validate_json(json.dumps(data))

    def rejected(self,data):
        with self.assertRaises(ValidationError):
            self.result(data)

    def test_all_sanitized_examples_validate(self):
        for name,model in [('result',JobResult),('options',JobOptions),('accepted',JobAccepted),
                           ('status',JobStatus),('revision',RevisionInput),('error',ErrorResponse)]:
            with self.subTest(name=name):
                model.model_validate_json((EXAMPLES/(name+'.json')).read_text())

    def test_blank_middle_cell_and_line_breaks_survive_roundtrip(self):
        result=self.result(example('result'))
        encoded=json.loads(result.model_dump_json())
        self.assertEqual([c['order'] for c in encoded['cells']],[0,1,2])
        self.assertEqual(encoded['cells'][1]['text'],'')
        self.assertEqual(encoded['cells'][2]['text'],None)
        self.assertEqual(encoded['cells'][0]['text'],'Example first line\nExample second line')

    def test_cell_and_line_order_and_ids_are_not_silently_repaired(self):
        for mode in ['cell-order','line-order','cell-id','line-id']:
            data=example('result')
            if mode=='cell-order':data['cells'][1]['order']=0
            if mode=='line-order':data['cells'][0]['lines'][0]['order']=1
            if mode=='cell-id':data['cells'][1]['id']=data['cells'][0]['id']
            if mode=='line-id':data['cells'][0]['lines'][1]['id']=data['cells'][0]['lines'][0]['id']
            with self.subTest(mode=mode):self.rejected(data)

    def test_uncertain_readings_cannot_be_successful_empty_text(self):
        for mode in ['review-flag','unknown-as-empty','no-reason','blank-with-lines','empty-recognized']:
            data=example('result')
            if mode=='review-flag':data['review_required']=False
            if mode=='unknown-as-empty':data['cells'][2]['text']=''
            if mode=='no-reason':data['cells'][2]['warnings']=[]
            if mode=='blank-with-lines':data['cells'][0]['state']='blank'
            if mode=='empty-recognized':
                data['cells'][0]['lines'][0].update(text='',raw_text='')
            with self.subTest(mode=mode):self.rejected(data)

    def test_geometry_bounds_finiteness_area_and_orientation(self):
        for mode in ['outside','negative','nan','zero-area','orientation-size','orientation-matrix']:
            data=example('result')
            if mode=='outside':data['cells'][0]['polygon'][0]=[241,0]
            if mode=='negative':data['cells'][0]['polygon'][0]=[-1,0]
            if mode=='nan':data['cells'][0]['polygon'][0]=[float('nan'),0]
            if mode=='zero-area':data['cells'][0]['polygon']=[[0,0],[1,1],[2,2]]
            if mode=='orientation-size':data['source']['exif_orientation']=6
            if mode=='orientation-matrix':data['source']['original_to_oriented']=[0,0,0,0,0,0]
            with self.subTest(mode=mode):self.rejected(data)

    def test_human_correction_preserves_raw_output(self):
        data=example('result');line=data['cells'][0]['lines'][0]
        original=line['raw_text']
        line['correction']={'text':'Corrected example','state':'recognized','actor_id':'reviewer-example',
                            'saved_at':'2026-11-12T12:30:00Z'}
        line['text']='Corrected example'
        data['cells'][0]['text']='Corrected example\nExample second line'
        result=self.result(data)
        self.assertEqual(result.cells[0].lines[0].raw_text,original)
        self.assertEqual(result.cells[0].lines[0].text,'Corrected example')
        line['text']='Unrecorded overwrite'
        self.rejected(data)

    def test_missing_provenance_and_extra_fields_rejected(self):
        data=example('result');data['cells'][0]['lines'][0]['recognition_model']=None
        self.rejected(data)
        data=example('result');data['exception_details']='private stack trace'
        self.rejected(data)

    def test_revision_conflicts_are_explicit_and_old_line_ids_cannot_mix_with_geometry(self):
        for data in [{'expected_revision':1},
                     {'expected_revision':0,'text_corrections':[]},
                     {**example('revision'),'corrected_dividers':[]},
                     {**example('revision'),'text_corrections':example('revision')['text_corrections']*2}]:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                RevisionInput.model_validate_json(json.dumps(data))

    def test_job_status_lifecycle_and_timestamps(self):
        for mode in ['success-incomplete','failed-no-code','running-error','naive-time','time-reversed']:
            data=example('status')
            if mode=='success-incomplete':data['status']='succeeded'
            if mode=='failed-no-code':data['status']='failed'
            if mode=='running-error':data['error_code']='internal'
            if mode=='naive-time':data['created_at']='2026-11-12T12:00:00'
            if mode=='time-reversed':data['updated_at']='2026-11-12T11:59:59Z'
            with self.subTest(mode=mode), self.assertRaises(ValidationError):
                JobStatus.model_validate_json(json.dumps(data))

    def test_geometry_transform_singular_or_infinite_rejected(self):
        for coefficients in [(0,0,0,0,0,0),(1,0,float('inf'),0,1,0)]:
            with self.subTest(coefficients=coefficients), self.assertRaises(ValidationError):
                CoordinateTransform(from_space='cell',to_space='original',affine=coefficients)

    def test_exported_openapi_references_resolve_and_schemas_are_valid(self):
        specification=document()
        validate_openapi(specification)
        self.assertEqual(specification['openapi'],'3.1.0')
        for schema in specification['components']['schemas'].values():
            Draft202012Validator.check_schema(schema)
        def walk(value):
            if isinstance(value,dict):
                for key,child in value.items():
                    if key=='$ref':
                        self.assertTrue(child.startswith('#/'))
                        node=specification
                        for component in child[2:].split('/'):
                            node=node[component]
                    else:walk(child)
            elif isinstance(value,list):
                for child in value:walk(child)
        walk(specification)
        self.assertEqual(len(specification['paths']),9)
        for path,methods in specification['paths'].items():
            for method,operation in methods.items():
                self.assertIn('operationId',operation)
                self.assertIn('responses',operation)
                if method=='post' and path=='/v1/jobs':
                    self.assertIn('multipart/form-data',operation['requestBody']['content'])


if __name__=='__main__':
    unittest.main()
