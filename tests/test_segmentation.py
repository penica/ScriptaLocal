from dataclasses import replace
from io import BytesIO
import unittest
from uuid import UUID,uuid4

import numpy as np
from PIL import Image,ImageDraw

from scriptalocal.images import decode_image
from scriptalocal.layout import Cell,LayoutSettings,extract_cell
from scriptalocal.segmentation import DetectedLine,SegmentationError,SegmentationSettings,segment_cell


class SegmentationTests(unittest.TestCase):
    def setUp(self):
        image=Image.new('RGB',(300,160),'white')
        ImageDraw.Draw(image).line((60,50,160,50),fill='black',width=3)
        self.crop=self.prepare(image)
        self.identifier=str(uuid4())
        self.settings=SegmentationSettings(model_height=200,minimum_width=100,padding=10)

    def prepare(self,image,orientation=1):
        output=BytesIO();exif=Image.Exif();exif[274]=orientation
        image.save(output,format='PNG',exif=exif)
        decoded=decode_image(output.getvalue())
        cell=Cell(str(uuid4()),0,((40,20),(200,20),(200,140),(40,140)))
        return extract_cell(decoded,cell,settings=LayoutSettings(context_padding=10))

    def detection(self,y=40,x=30,right=130):
        # Relative to context crop origin at (30,10), then padding (10,10).
        return DetectedLine(((x,y-10),(right,y-10),(right,y+2),(x,y+2)),((x,y),(right,y)),Image.new('RGB',(right-x,12),'white'))

    def run_cell(self,backend,**changes):
        return segment_cell(self.identifier,self.crop,backend,original_size=(300,160),settings=replace(self.settings,**changes))

    def test_preserves_scale_context_pixels_and_orders_baselines(self):
        original=np.array(self.crop.pixels)
        def backend(canvas):
            self.assertEqual(canvas.size,(200,200))
            np.testing.assert_array_equal(np.array(canvas)[10:150,10:190],original)
            return [self.detection(90),self.detection(40),self.detection(65)]
        result=self.run_cell(backend)
        self.assertEqual(result.state,'segmented')
        self.assertEqual([line.order for line in result.lines],[0,1,2])
        self.assertEqual(result.lines[0].baseline,((50.,40.),(150.,40.)))
        self.assertEqual(len(set(line.id for line in result.lines)),3)
        repeated=self.run_cell(backend)
        self.assertEqual([line.id for line in result.lines],[line.id for line in repeated.lines])
        for line in result.lines:UUID(line.id)
        np.testing.assert_array_equal(np.array(self.crop.pixels),original)

    def test_exact_white_blank_bypasses_backend_but_ink_without_lines_is_review(self):
        blank=self.prepare(Image.new('RGB',(300,160),'white'))
        result=segment_cell(self.identifier,blank,lambda _:self.fail('blank called backend'),original_size=(300,160),settings=self.settings)
        self.assertEqual((result.state,result.lines,result.canvas_size),('blank',(),None))
        result=self.run_cell(lambda _:[])
        self.assertEqual(result.state,'needs-review')
        self.assertIn('no_lines_in_nonblank_cell',result.warnings)
        noise=Image.new('RGB',(300,160),'white');noise.putpixel((100,80),(254,254,254))
        crop=self.prepare(noise)
        result=segment_cell(self.identifier,crop,lambda _:[],original_size=(300,160),settings=self.settings)
        self.assertEqual(result.state,'needs-review')

    def test_overlap_and_context_crossing_require_review(self):
        result=self.run_cell(lambda _:[self.detection(40),self.detection(42)])
        self.assertIn('ambiguous_line_order',result.warnings)
        result=self.run_cell(lambda _:[self.detection(40,x=10)])
        self.assertIn('baseline_crosses_cell_boundary',result.warnings)
        result=self.run_cell(lambda _:[self.detection(40,x=1,right=8)])
        self.assertEqual(result.lines,())
        self.assertIn('context_line_unassigned',result.warnings)
        self.assertIn('no_lines_in_nonblank_cell',result.warnings)

    def test_malformed_geometry_backend_error_and_limits_are_failures(self):
        for detection in [replace(self.detection(),baseline=((float('nan'),40),(80,40))),
                          replace(self.detection(),polygon=((1,1),(1,1),(1,1))),
                          replace(self.detection(),baseline=((1,1),(900,1)))]:
            with self.assertRaises(SegmentationError):self.run_cell(lambda _:[detection])
        def broken(_):raise RuntimeError('private exception')
        with self.assertRaisesRegex(SegmentationError,'^Segmentation backend failed$'):self.run_cell(broken)
        with self.assertRaises(SegmentationError):self.run_cell(lambda _:[],maximum_canvas_pixels=1)
        with self.assertRaises(SegmentationError):self.run_cell(lambda _:[self.detection()],maximum_line_pixels=1)
        with self.assertRaises(SegmentationError):self.run_cell(lambda _:[self.detection(),self.detection(90)],maximum_lines=1)

    def test_exif_geometry_returns_to_original_frame_once(self):
        image=Image.new('RGB',(160,300),'white');ImageDraw.Draw(image).rectangle((40,100,90,160),fill='black')
        self.crop=self.prepare(image,orientation=6)
        result=segment_cell(self.identifier,self.crop,lambda _:[self.detection()],original_size=(160,300),settings=self.settings)
        self.assertEqual(result.lines[0].baseline,((40.,250.),(40.,150.)))

    def test_padding_geometry_is_clipped_with_raw_evidence_and_context_is_retained(self):
        line=self.detection()
        line=replace(line,polygon=((30,30),(130,30),(130,190),(30,190)))
        result=self.run_cell(lambda _:[line])
        self.assertEqual(len(result.lines),1)
        self.assertIn('line_polygon_clipped_to_source',result.lines[0].warnings)
        self.assertEqual(max(p[1] for p in result.lines[0].polygon),160)
        self.assertEqual(max(p[1] for p in result.lines[0].raw_polygon),190)
        result=self.run_cell(lambda _:[self.detection(x=1,right=8)])
        self.assertEqual(len(result.unassigned_lines),1)
        self.assertEqual(result.unassigned_lines[0].pixels.size,(7,12))

    def test_long_line_keeps_pixels_without_splitting_or_resizing(self):
        line=replace(self.detection(),pixels=Image.new('RGB',(4000,30),'white'))
        result=self.run_cell(lambda _:[line])
        self.assertEqual(result.lines[0].pixels.size,(4000,30))
        self.assertEqual(len(result.lines),1)


if __name__=='__main__':unittest.main()
