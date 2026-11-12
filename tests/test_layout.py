from io import BytesIO
import unittest

import numpy as np
from PIL import Image, ImageDraw

from scriptalocal.images import decode_image
from scriptalocal.layout import Cell, LayoutSettings, detect_layout, extract_cell


def decoded(image,orientation=1):
    stream=BytesIO();exif=Image.Exif();exif[274]=orientation
    image.save(stream,format='PNG',exif=exif)
    return decode_image(stream.getvalue())


def ruled(borders=(100,220),*,broken=False,faint=False,double=False):
    image=Image.new('RGB',(320,200),'white');draw=ImageDraw.Draw(image)
    for x in borders:
        for delta in ([0,4] if double else [0]):
            color=(190,190,190) if faint else (0,0,0)
            intervals=[(0,75),(105,199)] if broken else [(0,199)]
            for start,end in intervals:
                draw.line((x+delta,start,x+delta,end),fill=color,width=2)
    # Deliberately text-like short marks must not become extra full-height cells.
    draw.line((40,50,40,110),fill='black',width=3)
    draw.line((35,75,65,75),fill='black',width=2)
    return decoded(image)


class DetectionTests(unittest.TestCase):
    def assert_borders(self,layout,expected,tolerance=3):
        self.assertEqual(len(layout.cells),len(expected)+1)
        actual=[border.top_x for border in layout.borders[1:-1]]
        self.assertEqual(len(actual),len(expected))
        for x,wanted in zip(actual,expected):
            self.assertAlmostEqual(x,wanted,delta=tolerance)

    def test_clear_faint_broken_and_double_borders(self):
        for options in [{},{'faint':True},{'broken':True},{'double':True}]:
            with self.subTest(options=options):
                layout=detect_layout(ruled(**options))
                self.assert_borders(layout,[100,220])
                self.assertIn('automatic_layout_requires_review',layout.warnings)

    def test_small_slant_has_quadrilateral_cells(self):
        image=Image.new('RGB',(320,200),'white');draw=ImageDraw.Draw(image)
        draw.line((100,0,110,199),fill='black',width=2)
        draw.line((220,0,210,199),fill='black',width=2)
        layout=detect_layout(decoded(image))
        self.assertEqual(len(layout.cells),3)
        self.assertAlmostEqual(layout.borders[1].top_x,100,delta=3)
        self.assertAlmostEqual(layout.borders[1].bottom_x,110,delta=3)
        self.assertNotEqual(layout.cells[0].polygon[1][0],layout.cells[0].polygon[2][0])

    def test_outer_margins_are_not_extra_cells(self):
        layout=detect_layout(ruled([8,100,220,312]))
        self.assert_borders(layout,[100,220])
        self.assertAlmostEqual(layout.cells[0].polygon[0][0],8,delta=2)
        self.assertAlmostEqual(layout.cells[-1].polygon[1][0],312,delta=2)

    def test_narrow_interior_columns_remain(self):
        layout=detect_layout(ruled([100,118,220]))
        self.assert_borders(layout,[100,118,220])

    def test_narrow_column_inside_a_detected_outer_rule_remains(self):
        layout=detect_layout(ruled([0,10,100,220,319]))
        self.assert_borders(layout,[10,100,220])

    def test_no_dividers_returns_one_reviewable_cell(self):
        for image in [ruled([]),decoded(Image.new('RGB',(50,1),'white'))]:
            with self.subTest(size=image.pixels.size):
                layout=detect_layout(image)
                self.assertEqual(len(layout.cells),1)
                self.assertIn('no_internal_dividers_detected',layout.warnings)

    def test_stable_ids_and_consecutive_order_for_same_geometry(self):
        image=ruled()
        first=detect_layout(image);second=detect_layout(image)
        self.assertEqual([c.id for c in first.cells],[c.id for c in second.cells])
        self.assertEqual([c.order for c in first.cells],[0,1,2])
        self.assertEqual(len({c.id for c in first.cells}),3)

    def test_candidate_limit_is_visible(self):
        layout=detect_layout(ruled(),settings=LayoutSettings(maximum_candidates=1))
        self.assertIn('candidate_limit_reached',layout.warnings)

    def test_invalid_settings(self):
        for settings in [{'maximum_candidates':0},{'minimum_ink_support':float('nan')},
                         {'adaptive_block_size':4},{'context_padding':-1},
                         {'maximum_angle_degrees':90},{'outer_border_proximity_fraction':.5}]:
            with self.subTest(settings=settings),self.assertRaises(ValueError):
                LayoutSettings(**settings)


class CorrectionTests(unittest.TestCase):
    def test_corrections_replace_automatic_proposal_and_empty_means_one_cell(self):
        image=ruled()
        original=detect_layout(image)
        changed=detect_layout(image,corrected_dividers=[((140,0),(145,200))])
        self.assertEqual(len(changed.cells),2)
        self.assertFalse(changed.warnings)
        self.assertEqual(changed.borders[1].source,'manual')
        self.assertNotEqual(original.cells[0].id,changed.cells[0].id)
        self.assertEqual(len(detect_layout(image,corrected_dividers=[]).cells),1)

    def test_partial_height_manual_line_is_extrapolated_consistently(self):
        layout=detect_layout(ruled(),corrected_dividers=[((101,20),(109,180))])
        self.assertAlmostEqual(layout.borders[1].top_x,100)
        self.assertAlmostEqual(layout.borders[1].bottom_x,110)

    def test_crossed_reversed_duplicate_invalid_and_nonfinite_corrections_rejected(self):
        cases=[ [((220,0),(220,200)),((100,0),(100,200))],
                [((100,0),(230,200)),((220,0),(110,200))],
                [((100,0),(100,200)),((100,0),(100,200))],
                [((-1,0),(-1,200))], [((20,100),(20,100))],
                [((float('nan'),0),(20,200))], [((0,0),(0,200))],
                [((True,0),(20,200))], [(("20",0),(20,200))] ]
        for case in cases:
            with self.subTest(case=case),self.assertRaises(ValueError):
                detect_layout(ruled(),corrected_dividers=case)


class InkPreservationTests(unittest.TestCase):
    def test_context_preserves_crossing_ink_and_source_pixels(self):
        image=Image.new('RGB',(240,160),'white');draw=ImageDraw.Draw(image)
        draw.line((120,0,120,159),fill='black',width=2)
        draw.line((105,65,135,65),fill='black',width=4)
        source=decoded(image);before=np.array(source.pixels)
        layout=detect_layout(source,corrected_dividers=[((120,0),(120,160))])
        cell=extract_cell(source,layout.cells[0])
        self.assertEqual(cell.pixels.getpixel((125,65)),(0,0,0))
        self.assertEqual(int(cell.cell_mask[65,125]),0)
        np.testing.assert_array_equal(np.array(cell.pixels),before[:,:132])
        np.testing.assert_array_equal(np.array(source.pixels),before)
        self.assertTrue(cell.context_clipped)

    def test_exif_crop_mapping_reaches_encoded_original_coordinates(self):
        source=decoded(Image.new('RGB',(200,100),'white'),orientation=6)
        # In encoded coordinates the visually vertical divider is horizontal.
        layout=detect_layout(source,corrected_dividers=[((0,50),(200,50))])
        crop=extract_cell(source,layout.cells[1])
        x,y=crop.oriented_from_crop.point(.5,.5)
        self.assertEqual(crop.original_from_crop.point(.5,.5),(y,100-x))

    def test_explicit_oriented_corrections_are_not_rotated_twice(self):
        source=decoded(Image.new('RGB',(200,100),'white'),orientation=6)
        layout=detect_layout(source,corrected_dividers=[((50,0),(50,200))],
                             correction_space='oriented_pixel_edges')
        self.assertEqual(layout.borders[1].top_x,50)
        with self.assertRaises(ValueError):
            detect_layout(source,corrected_dividers=[],correction_space='unknown')

    def test_slanted_mask_does_not_erase_outside_context(self):
        source=decoded(Image.new('RGB',(240,160),'black'))
        layout=detect_layout(source,corrected_dividers=[((100,0),(120,160))])
        crop=extract_cell(source,layout.cells[0])
        self.assertGreater(np.count_nonzero(crop.cell_mask==0),0)
        self.assertEqual(crop.pixels.getextrema(),((0,0),(0,0),(0,0)))

    def test_outside_cell_is_not_silently_clipped(self):
        source=ruled()
        bad=Cell('invalid',0,((-1.,0.),(100.,0.),(100.,200.),(-1.,200.)))
        with self.assertRaises(ValueError):extract_cell(source,bad)


if __name__=='__main__':
    unittest.main()
