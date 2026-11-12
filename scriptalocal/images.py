"""Bounded image decoding and reversible geometry in pixel-edge coordinates."""
from dataclasses import dataclass
import hashlib
from io import BytesIO
import math
import os
from pathlib import Path
import tempfile
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


class ImageInputError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class ImageLimits:
    max_bytes: int = 25 * 1024 * 1024
    max_pixels: int = 40_000_000
    max_side: int = 20_000

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in
               (self.max_bytes, self.max_pixels, self.max_side)):
            raise ValueError('Image limits must be positive integers')


@dataclass(frozen=True)
class Affine:
    """x'=a*x+b*y+c, y'=d*x+e*y+f; coordinates describe pixel edges."""
    a: float = 1
    b: float = 0
    c: float = 0
    d: float = 0
    e: float = 1
    f: float = 0

    def __post_init__(self):
        if not all(math.isfinite(v) for v in self.coefficients):
            raise ValueError('Affine coefficients must be finite')

    @property
    def coefficients(self):
        return (self.a, self.b, self.c, self.d, self.e, self.f)

    def point(self, x: float, y: float):
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError('Coordinates must be finite')
        return (self.a*x + self.b*y + self.c, self.d*x + self.e*y + self.f)

    def inverse(self):
        determinant = self.a*self.e - self.b*self.d
        if abs(determinant) < 1e-12:
            raise ValueError('Singular geometry transform')
        return Affine(self.e/determinant, -self.b/determinant,
                      (self.b*self.f-self.e*self.c)/determinant,
                      -self.d/determinant, self.a/determinant,
                      (self.d*self.c-self.a*self.f)/determinant)

    def after(self, other: 'Affine'):
        """Apply other, then self (matrix product self × other)."""
        return Affine(
            self.a*other.a+self.b*other.d,
            self.a*other.b+self.b*other.e,
            self.a*other.c+self.b*other.f+self.c,
            self.d*other.a+self.e*other.d,
            self.d*other.b+self.e*other.e,
            self.d*other.c+self.e*other.f+self.f,
        )


def orientation_transform(orientation: int, width: int, height: int) -> Affine:
    return {
        1: Affine(), 2: Affine(-1, 0, width, 0, 1, 0),
        3: Affine(-1, 0, width, 0, -1, height),
        4: Affine(1, 0, 0, 0, -1, height),
        5: Affine(0, 1, 0, 1, 0, 0),
        6: Affine(0, -1, height, 1, 0, 0),
        7: Affine(0, -1, height, -1, 0, width),
        8: Affine(0, 1, 0, -1, 0, width),
    }[orientation]


@dataclass(frozen=True)
class DecodedImage:
    original: bytes
    sha256: str
    format: str
    mime_type: str
    encoded_size: tuple[int, int]
    orientation: int
    pixels: Image.Image
    oriented_from_original: Affine

    @property
    def original_from_oriented(self):
        return self.oriented_from_original.inverse()


FORMATS = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'TIFF': 'image/tiff'}


def decode_image(payload: bytes, *, declared_mime: str | None = None,
                 limits: ImageLimits = ImageLimits()) -> DecodedImage:
    """Validate before loading pixels; never use filenames or fetch remote URLs."""
    if not isinstance(payload, bytes):
        raise TypeError('Expected uploaded image bytes')
    if not payload:
        raise ImageInputError('empty_image', 'The image upload is empty')
    if len(payload) > limits.max_bytes:
        raise ImageInputError('image_bytes_exceeded', 'Image exceeds upload byte limit', 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as header:
                image_format = header.format
                if image_format not in FORMATS:
                    raise ImageInputError('unsupported_image_format', 'Use PNG, JPEG or single-image TIFF', 415)
                # TIFF's public .size can already reflect EXIF orientation;
                # tags 256/257 retain the encoded raster dimensions.
                width, height = ((int(header.tag_v2[256]), int(header.tag_v2[257]))
                                 if image_format == 'TIFF' else header.size)
                if width*height > limits.max_pixels or max(width, height) > limits.max_side:
                    raise ImageInputError('image_dimensions_exceeded', 'Image exceeds pixel or dimension limit', 413)
                if getattr(header, 'n_frames', 1) != 1:
                    raise ImageInputError('multiple_image_frames', 'Upload one image rather than multiple frames', 415)
                mime = FORMATS[image_format]
                if declared_mime and declared_mime.split(';', 1)[0].lower().strip() not in {mime, 'application/octet-stream'}:
                    raise ImageInputError('image_mime_mismatch', 'Declared MIME type does not match image bytes', 415)
                orientation = header.getexif().get(274, 1)
                if type(orientation) is not int or orientation not in range(1, 9):
                    raise ImageInputError('invalid_image_orientation', 'Image has an invalid EXIF orientation')
            # Some plugins load while inspecting EXIF or frame counts. Verify
            # a fresh parser rather than relying on the inspected parser state.
            with Image.open(BytesIO(payload)) as verifier:
                verifier.verify()
            with Image.open(BytesIO(payload)) as source:
                # Pillow's TIFF loader normalizes EXIF and removes its tag in
                # load_end. exif_transpose is then a no-op for that format.
                source.load()
                oriented = ImageOps.exif_transpose(source)
                if oriented.mode in {'RGBA', 'LA'} or 'transparency' in oriented.info:
                    rgba = oriented.convert('RGBA')
                    pixels = Image.new('RGB', rgba.size, 'white')
                    pixels.paste(rgba, mask=rgba.getchannel('A'))
                else:
                    pixels = oriented.convert('RGB')
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageInputError('image_dimensions_exceeded', 'Image exceeds safe decoding limits', 413) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            EOFError, OverflowError, KeyError, IndexError) as exc:
        if isinstance(exc, ImageInputError):
            raise
        raise ImageInputError('invalid_image', 'Image is corrupt or cannot be decoded') from exc
    return DecodedImage(payload, hashlib.sha256(payload).hexdigest(), image_format, mime,
                        (width, height), orientation, pixels,
                        orientation_transform(orientation, width, height))


def persist_original(image: DecodedImage, root: Path) -> Path:
    """Publish unchanged bytes by content hash; client filenames never form paths."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / (image.sha256 + '.original')
    if target.is_symlink():
        raise ValueError('Original artifact cannot be a symlink')
    if target.exists():
        with target.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != image.sha256:
                raise ValueError('Existing original artifact checksum mismatch')
        return target
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix='.upload-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(image.original)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
