from dataclasses import asdict
import unittest

import numpy as np
from PIL import Image,ImageDraw
from kraken.containers import BaselineLine,Segmentation

from scriptalocal.kraken_rendering import render_lines


def segment(baseline,boundary):
    line=BaselineLine(id='fixture-line',baseline=baseline,boundary=boundary)
    return Segmentation(type='baselines',imagename='synthetic.png',text_direction='horizontal-lr',script_detection=False,lines=[line])


class LineRenderingTests(unittest.TestCase):
    def fixture(self,curved=False):
        image=Image.new('RGB',(140,100),(235,225,210));draw=ImageDraw.Draw(image)
        draw.line([(30,30),(40,65),(65,25),(90,62)],fill='black',width=5)
        baseline=[(15,65),(110,65)] if not curved else [(15,60),(60,66),(110,60)]
        boundary=[(10,15),(105,20),(120,75),(25,85)]
        return image,segment(baseline,boundary)

    def test_straight_mask_white_fill_preserves_source_and_black_ink(self):
        image,bounds=self.fixture();before=image.tobytes();geometry=asdict(bounds)
        black,_=next(render_lines(image,bounds,background='legacy-black'))
        white,line=next(render_lines(image,bounds,background='white-alpha'))
        a=np.array(black);b=np.array(white)
        self.assertEqual(black.size,white.size)
        self.assertGreater(np.count_nonzero(np.all(b<20,axis=2)),30)
        self.assertGreater(np.count_nonzero(np.all(b==255,axis=2)),30)
        self.assertGreater(np.count_nonzero(np.all(a<20,axis=2)),np.count_nonzero(np.all(b<20,axis=2)))
        self.assertEqual(image.tobytes(),before);self.assertEqual(asdict(bounds),geometry)
        self.assertEqual(line.id,'fixture-line')

    def test_curved_mask_uses_same_geometry_and_keeps_opaque_ink(self):
        image,bounds=self.fixture(curved=True);before=image.tobytes()
        white,line=next(render_lines(image,bounds,background='white-alpha'))
        pixels=np.array(white)
        self.assertEqual(white.mode,'RGB');self.assertGreater(np.count_nonzero(np.all(pixels<20,axis=2)),20)
        self.assertTrue(np.any(np.all(pixels==255,axis=2)))
        self.assertEqual(line.baseline,bounds.lines[0].baseline);self.assertEqual(image.tobytes(),before)

    def test_true_black_source_is_not_erased_as_if_it_were_padding(self):
        image=Image.new('RGB',(100,70),'black')
        bounds=segment([(15,45),(80,45)],[(10,10),(80,15),(90,55),(20,60)])
        white,_=next(render_lines(image,bounds,background='white-alpha'))
        pixels=np.array(white)
        self.assertGreater(np.count_nonzero(np.all(pixels==0,axis=2)),500)
        self.assertGreater(np.count_nonzero(np.all(pixels==255,axis=2)),10)
        self.assertEqual(image.getextrema(),((0,0),(0,0),(0,0)))

    def test_unsupported_mode_is_rejected(self):
        image,bounds=self.fixture()
        with self.assertRaises(ValueError):list(render_lines(image,bounds,background='guess'))


if __name__=='__main__':unittest.main()
