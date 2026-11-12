from copy import deepcopy
import hashlib
from io import BytesIO
import json
import unittest

from PIL import Image

from scriptalocal.images import decode_image
from scripts.validation.export_manuscript_result import build_result


def fixture(orientation=1):
    stream=BytesIO();exif=Image.Exif();exif[274]=orientation
    Image.new('RGB',(240,120),'white').save(stream,format='PNG',exif=exif)
    image=decode_image(stream.getvalue())
    config={'manual_cell_bounds':[0,0,80,120],'preserve_scale':True,'canvas_padding':32,
            'minimum_canvas_width':400,'max_new_tokens':128}
    raw={'image_sha256':image.sha256,'original_size':list(image.pixels.size),
         'configuration':config,'configuration_sha256':hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
         'manual_cell_bounds':config['manual_cell_bounds'],'source_script_sha256':'c'*64,
         'model':'example/recognizer','revision':'a'*40,'recognition_weights_sha256':'b'*64,
         'segmentation_weights_sha256':'d'*64,'segmentation_seconds':1.,'total_seconds':2.,
         'lines':[{'order':0,'text':'Example reading','truncated':False,
                   'polygon':[[4,10],[70,10],[70,35],[4,35]],'baseline':[[5,30],[69,30]]}]}
    return image,raw


class ProbeExportTests(unittest.TestCase):
    def test_successful_export_preserves_raw_text_provenance_and_review(self):
        image,raw=fixture();result=build_result(raw,image)
        self.assertEqual(result.cells[0].lines[0].text,'Example reading')
        self.assertEqual(result.cells[0].lines[0].raw_text,'Example reading')
        self.assertTrue(result.review_required)
        self.assertEqual(result.provenance.recognition_model.weights_sha256,'b'*64)
        self.assertEqual(result.provenance.segmentation_model.weights_sha256,'d'*64)
        self.assertEqual(result.job_id,build_result(raw,image).job_id)

    def test_wrong_image_configuration_and_missing_lines_rejected(self):
        for mode in ['image','configuration','zero-lines','order','timing','script']:
            image,raw=fixture()
            if mode=='image':raw['image_sha256']='0'*64
            if mode=='configuration':raw['configuration']['canvas_padding']=99
            if mode=='zero-lines':raw['lines']=[]
            if mode=='order':raw['lines'][0]['order']=1
            if mode=='timing':raw['total_seconds']=.5
            if mode=='script':raw['source_script_sha256']='unknown'
            with self.subTest(mode=mode),self.assertRaises(ValueError):build_result(raw,image)

    def test_empty_and_truncated_output_remains_uncertain_not_blank(self):
        for text,truncated in [('',False),('Partial',True)]:
            image,raw=fixture();raw['lines'][0].update(text=text,truncated=truncated)
            result=build_result(raw,image)
            self.assertEqual(result.cells[0].state,'needs-review')
            self.assertEqual(result.cells[0].lines[0].raw_text,text)
            self.assertNotEqual(result.cells[0].state,'blank')

    def test_exif_line_geometry_maps_back_to_encoded_raster(self):
        image,raw=fixture(orientation=6);result=build_result(raw,image)
        self.assertEqual(result.cells[0].lines[0].polygon[0],(10.,116.))
        self.assertEqual(result.source.width,240)
        self.assertEqual(result.source.oriented_width,120)


if __name__=='__main__':unittest.main()
