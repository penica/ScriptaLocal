from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.validate_contract import main


class SemanticCliTests(unittest.TestCase):
    def invoke(self,document,*options):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'result.json';path.write_text(json.dumps(document))
            output=StringIO();code=0
            with patch('sys.argv',['validate_contract','--model','JobResult',*options,str(path)]),redirect_stdout(output):
                try:main()
                except SystemExit as exc:code=exc.code
            return code,json.loads(output.getvalue())

    def example(self):
        return json.loads((Path(__file__).parents[1]/'docs/api/examples/result.json').read_text())

    def test_valid_result_report_does_not_echo_readings(self):
        example=self.example();code,result=self.invoke(example)
        self.assertEqual(code,0);self.assertTrue(result['valid'])
        self.assertNotIn(example['cells'][0]['text'],json.dumps(result))

    def test_missing_warning_scope_is_semantic_error_without_input_echo(self):
        example=self.example();example['cells'][0]['state']='needs-review';example['cells'][0]['warnings']=[]
        example['cells'][0]['text']='PRIVATE CANARY TRANSCRIPTION'
        example['cells'][0]['lines'][0].update(state='needs-review',raw_state='needs-review',text='PRIVATE CANARY TRANSCRIPTION',raw_text='PRIVATE CANARY TRANSCRIPTION',warnings=[])
        example['cells'][0]['text']='\n'.join(line['text'] for line in example['cells'][0]['lines'])
        example['review_required']=True
        code,result=self.invoke(example)
        self.assertEqual(code,1);self.assertFalse(result['valid'])
        self.assertTrue(any(item['location'][:3]==['cells',0,'lines'] for item in result['errors']))
        self.assertNotIn('PRIVATE CANARY TRANSCRIPTION',json.dumps(result))

    def test_cli_byte_limit_rejects_before_validation(self):
        code,result=self.invoke(self.example(),'--maximum-bytes','10')
        self.assertEqual(code,1);self.assertEqual(result['error'],'document_limit_exceeded')


if __name__=='__main__':unittest.main()
