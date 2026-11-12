from io import BytesIO
import math
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from scriptalocal.images import Affine, ImageInputError, ImageLimits, decode_image, persist_original


def encoded(image=None, image_format='PNG', **kwargs):
    stream = BytesIO()
    (image or Image.new('RGB', (12, 10), 'white')).save(stream, format=image_format, **kwargs)
    return stream.getvalue()


class DecodeTests(unittest.TestCase):
    def test_formats_and_original_bytes(self):
        for kind, mime in [('PNG','image/png'), ('JPEG','image/jpeg'), ('TIFF','image/tiff')]:
            with self.subTest(kind=kind):
                payload = encoded(image_format=kind)
                result = decode_image(payload, declared_mime=mime)
                self.assertEqual(result.original, payload)
                self.assertEqual(result.encoded_size, (12,10))
                self.assertEqual(result.pixels.size, (12,10))
                self.assertEqual(result.pixels.mode, 'RGB')

    def test_exif_geometry_matches_actual_pixels_all_orientations(self):
        source = Image.new('RGB', (4,3))
        for y in range(3):
            for x in range(4):
                source.putpixel((x,y),(x*50,y*70,(x+y)*30))
        for kind in ['PNG','TIFF']:
            for orientation in range(1,9):
                with self.subTest(kind=kind,orientation=orientation):
                    exif = Image.Exif(); exif[274] = orientation
                    result = decode_image(encoded(source,kind,exif=exif))
                    self.assertEqual(result.encoded_size,(4,3))
                    expected_size = (4,3) if orientation <= 4 else (3,4)
                    self.assertEqual(result.pixels.size,expected_size)
                    for y in range(3):
                        for x in range(4):
                            nx,ny = result.oriented_from_original.point(x+.5,y+.5)
                            self.assertEqual(result.pixels.getpixel((int(nx),int(ny))),source.getpixel((x,y)))
                            ox,oy = result.original_from_oriented.point(nx,ny)
                            self.assertAlmostEqual(ox,x+.5)
                            self.assertAlmostEqual(oy,y+.5)

    def test_transparency_is_composited_on_white(self):
        result = decode_image(encoded(Image.new('RGBA',(4,3),(0,0,0,0))))
        self.assertEqual(result.pixels.getpixel((0,0)),(255,255,255))

    def test_byte_and_pixel_limits(self):
        payload = encoded()
        for limits, code in [(ImageLimits(max_bytes=10),'image_bytes_exceeded'),
                             (ImageLimits(max_pixels=100),'image_dimensions_exceeded'),
                             (ImageLimits(max_side=10),'image_dimensions_exceeded')]:
            with self.subTest(code=code):
                with self.assertRaises(ImageInputError) as caught:
                    decode_image(payload,limits=limits)
                self.assertEqual(caught.exception.code,code)
                self.assertEqual(caught.exception.status,413)

    def test_corrupt_and_empty_inputs(self):
        for payload in [b'',b'not an image',encoded()[:30]]:
            with self.subTest(payload_length=len(payload)), self.assertRaises(ImageInputError):
                decode_image(payload)

    def test_unsupported_mismatched_and_multiframe(self):
        cases = [(encoded(image_format='GIF'),None,'unsupported_image_format'),
                 (encoded(),'image/jpeg','image_mime_mismatch'),
                 (encoded(image_format='TIFF',save_all=True,
                          append_images=[Image.new('RGB',(12,10),'black')]),None,'multiple_image_frames'),
                 (encoded(image_format='PNG',save_all=True,
                          append_images=[Image.new('RGB',(12,10),'black')]),None,'multiple_image_frames')]
        for payload,mime,code in cases:
            with self.subTest(code=code):
                with self.assertRaises(ImageInputError) as caught:
                    decode_image(payload,declared_mime=mime)
                self.assertEqual(caught.exception.code,code)
                self.assertEqual(caught.exception.status,415)

    def test_invalid_orientation_is_not_silently_ignored(self):
        exif=Image.Exif();exif[274]=9
        with self.assertRaisesRegex(ImageInputError,'orientation'):
            decode_image(encoded(exif=exif))

    def test_invalid_limits_and_nonbytes(self):
        for kwargs in [{'max_pixels':0},{'max_side':-1},{'max_bytes':True}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ImageLimits(**kwargs)
        with self.assertRaises(TypeError):
            decode_image('https://example.test/image.png')

    def test_corrupt_pixel_stream_not_only_header(self):
        # A JPEG header remains parseable, but the pixel stream is incomplete.
        payload=encoded(Image.new('RGB',(100,100),'black'),'JPEG')
        with self.assertRaises(ImageInputError):
            decode_image(payload[:-30])


class GeometryTests(unittest.TestCase):
    def test_crop_rotate_scale_padding_round_trip(self):
        angle=math.radians(2.7)
        rotation=Affine(math.cos(angle),-math.sin(angle),8,math.sin(angle),math.cos(angle),12)
        scale=Affine(.5,0,0,0,.5,0)
        crop=Affine(1,0,-123,0,1,-41)
        pad=Affine(1,0,32,0,1,32)
        pipeline=pad.after(scale).after(rotation).after(crop)
        for point in [(123,41),(300,80),(511.25,125.5)]:
            recovered=pipeline.inverse().point(*pipeline.point(*point))
            for actual,expected in zip(recovered,point):
                self.assertAlmostEqual(actual,expected,places=8)

    def test_invalid_geometry(self):
        with self.assertRaises(ValueError):
            Affine(0,0,0,0,0,0).inverse()
        with self.assertRaises(ValueError):
            Affine(c=float('nan'))
        with self.assertRaises(ValueError):
            Affine().point(float('inf'),0)


class ArtifactTests(unittest.TestCase):
    def test_original_publication_is_unchanged_and_idempotent(self):
        image=decode_image(encoded())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            first=persist_original(image,root)
            self.assertEqual(first,persist_original(image,root))
            self.assertEqual(first.read_bytes(),image.original)
            self.assertEqual(list(root.iterdir()),[first])
            self.assertEqual(first.stat().st_mode & 0o777,0o600)

    def test_existing_corrupt_or_symlink_artifact_is_rejected(self):
        image=decode_image(encoded())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/(image.sha256+'.original')
            target.write_bytes(b'corrupt')
            with self.assertRaises(ValueError):
                persist_original(image,root)
            target.unlink();target.symlink_to(root/'missing')
            with self.assertRaises(ValueError):
                persist_original(image,root)


if __name__=='__main__':
    unittest.main()
