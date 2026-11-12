"""Version 1 public data contracts. These models do not expose a running API."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .images import Affine, orientation_transform

Integer = Annotated[int, Field(strict=True, ge=0)]
Coordinate = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Point = tuple[Coordinate, Coordinate]
Polygon = Annotated[list[Point], Field(min_length=3)]
ReadingState = Literal['recognized', 'blank', 'unreadable', 'failed', 'needs-review']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class ReviewWarning(Contract):
    code: Annotated[str, Field(min_length=1, max_length=100)]
    message: Annotated[str, Field(min_length=1, max_length=500)]


class ModelReference(Contract):
    engine: Literal['kraken', 'transformers']
    model_id: Annotated[str, Field(min_length=1, max_length=256)]
    revision: Annotated[str, Field(pattern=r'^(?:[a-f0-9]{40}|[a-f0-9]{64})$')]
    weights_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class HumanReading(Contract):
    text: str | None
    state: Literal['recognized','unreadable','needs-review']
    actor_id: Annotated[str, Field(min_length=1,max_length=256)]
    saved_at: AwareDatetime

    @model_validator(mode='after')
    def validate_reading(self):
        if self.state == 'recognized' and (not self.text or not self.text.strip()):
            raise ValueError('Recognized corrections require text')
        if self.state == 'unreadable' and self.text is not None:
            raise ValueError('Unreadable corrections require null text')
        return self


class LineResult(Contract):
    id: UUID
    order: Integer
    polygon: Polygon
    baseline: Annotated[list[Point], Field(min_length=2)]
    state: ReadingState
    text: str | None
    raw_state: ReadingState
    raw_text: str | None
    correction: HumanReading | None = None
    recognition_model: ModelReference | None
    warnings: list[ReviewWarning] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_state(self):
        validate_polygon(self.polygon)
        if len(set(self.baseline)) < 2:
            raise ValueError('A baseline needs at least two distinct points')
        if self.raw_state == 'recognized' and (not self.raw_text or not self.raw_text.strip() or not self.recognition_model):
            raise ValueError('Recognized raw lines require nonempty text and model provenance')
        if self.raw_state in {'failed','unreadable'} and self.raw_text is not None:
            raise ValueError('Failed or unreadable raw lines require null text')
        if self.raw_state == 'blank' and self.raw_text != '':
            raise ValueError('Blank raw lines require empty text')
        expected = ((self.correction.state,self.correction.text) if self.correction
                    else (self.raw_state,self.raw_text))
        if (self.state,self.text) != expected:
            raise ValueError('Effective reading must match raw output or its saved human correction')
        if self.state == 'recognized' and (not self.text or not self.text.strip()):
            raise ValueError('Recognized lines require nonempty text')
        if self.state == 'blank' and self.text != '':
            raise ValueError('Blank lines require empty text')
        if self.state in {'failed','unreadable'} and self.text is not None:
            raise ValueError('Failed or unreadable lines require null text')
        if self.state in {'failed','unreadable','needs-review'} and not self.warnings:
            raise ValueError('Uncertain lines require a review reason')
        return self


class CellResult(Contract):
    id: UUID
    order: Integer
    polygon: Polygon
    state: ReadingState
    text: str | None
    lines: list[LineResult]
    warnings: list[ReviewWarning] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_cell(self):
        validate_polygon(self.polygon)
        validate_sequence(self.lines)
        if self.state == 'blank' and (self.text != '' or self.lines):
            raise ValueError('Blank cells retain an empty string and no detected lines')
        if self.state == 'recognized' and (not self.lines or any(x.state != 'recognized' for x in self.lines)):
            raise ValueError('Recognized cells require recognized lines')
        if not self.lines and self.state != 'blank' and self.text is not None:
            raise ValueError('A nonblank cell with no lines cannot claim transcription text')
        if self.lines:
            expected = None if any(x.text is None for x in self.lines) else '\n'.join(x.text for x in self.lines)
            if self.text != expected:
                raise ValueError('Cell text must preserve line order and line breaks; unknown lines require null text')
        if self.state in {'failed','unreadable','needs-review'} and not self.warnings:
            raise ValueError('Uncertain cells require a review reason')
        return self


def validate_sequence(items):
    if [item.order for item in items] != list(range(len(items))):
        raise ValueError('Orders must be consecutive and match array order')
    if len({item.id for item in items}) != len(items):
        raise ValueError('IDs must be unique within an ordered collection')


def validate_polygon(points):
    area = sum(x1*y2-x2*y1 for (x1,y1),(x2,y2) in zip(points, points[1:]+points[:1]))
    if abs(area) < 1e-9:
        raise ValueError('Polygon must have nonzero area')


class SourceImage(Contract):
    sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    width: Annotated[int, Field(strict=True, gt=0)]
    height: Annotated[int, Field(strict=True, gt=0)]
    oriented_width: Annotated[int, Field(strict=True, gt=0)]
    oriented_height: Annotated[int, Field(strict=True, gt=0)]
    exif_orientation: Annotated[int, Field(strict=True, ge=1, le=8)]
    original_to_oriented: tuple[float, float, float, float, float, float]
    mime_type: Literal['image/png','image/jpeg','image/tiff']

    @model_validator(mode='after')
    def validate_orientation(self):
        expected_size = ((self.width,self.height) if self.exif_orientation <= 4
                         else (self.height,self.width))
        if (self.oriented_width,self.oriented_height) != expected_size:
            raise ValueError('Oriented dimensions must match encoded dimensions and EXIF')
        expected = orientation_transform(self.exif_orientation,self.width,self.height)
        if tuple(self.original_to_oriented) != expected.coefficients:
            raise ValueError('EXIF coordinate mapping must match orientation')
        return self


class CoordinateTransform(Contract):
    from_space: Annotated[str, Field(min_length=1,max_length=256)]
    to_space: Annotated[str, Field(min_length=1,max_length=256)]
    affine: tuple[float,float,float,float,float,float]

    @model_validator(mode='after')
    def validate_transform(self):
        Affine(*self.affine).inverse()
        return self


class Provenance(Contract):
    pipeline_version: Annotated[str, Field(min_length=1, max_length=100)]
    configuration_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    segmentation_model: ModelReference
    recognition_model: ModelReference


class JobResult(Contract):
    api_version: Literal['1'] = '1'
    job_id: UUID
    revision: Annotated[int, Field(strict=True, ge=1)]
    coordinate_space: Literal['original_encoded_pixel_edges'] = 'original_encoded_pixel_edges'
    source: SourceImage
    cells: Annotated[list[CellResult], Field(min_length=1)]
    review_required: bool
    warnings: list[ReviewWarning] = Field(default_factory=list)
    provenance: Provenance
    transforms: list[CoordinateTransform] = Field(default_factory=list)
    stage_seconds: dict[str, Annotated[float, Field(ge=0)]]

    @model_validator(mode='after')
    def validate_result(self):
        validate_sequence(self.cells)
        identifiers = [cell.id for cell in self.cells] + [line.id for cell in self.cells for line in cell.lines]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError('All cell and line IDs must be unique within a result')
        for cell in self.cells:
            points = list(cell.polygon)
            for line in cell.lines:
                points.extend(line.polygon)
                points.extend(line.baseline)
            if any(x > self.source.width or y > self.source.height for x,y in points):
                raise ValueError('Geometry must remain inside the original image')
        uncertain = self.warnings or any(cell.warnings or cell.state not in {'recognized','blank'}
                                         or any(line.warnings or line.state not in {'recognized','blank'} for line in cell.lines)
                                         for cell in self.cells)
        if uncertain and not self.review_required:
            raise ValueError('Uncertain results must require review')
        return self


class Divider(Contract):
    top: Point
    bottom: Point

    @model_validator(mode='after')
    def validate_divider(self):
        if self.top[1] >= self.bottom[1]:
            raise ValueError('Divider must run from top to bottom')
        return self


class JobOptions(Contract):
    client_reference: Annotated[str, Field(max_length=256)] | None = None
    model_id: Annotated[str, Field(min_length=1,max_length=256)] | None = None
    corrected_dividers: Annotated[list[Divider], Field(max_length=256)] | None = None
    keep_reference: bool = False


class JobAccepted(Contract):
    api_version: Literal['1'] = '1'
    job_id: UUID
    status: Literal['queued','running','succeeded','failed','cancelled']
    revision: Annotated[int, Field(strict=True, ge=1)]
    idempotency_replayed: bool


class JobStatus(Contract):
    api_version: Literal['1'] = '1'
    job_id: UUID
    status: Literal['queued','running','succeeded','failed','cancelled']
    stage: Literal['queued','layout','segmentation','recognition','complete']
    revision: Annotated[int, Field(strict=True, ge=1)]
    created_at: AwareDatetime
    updated_at: AwareDatetime
    review_required: bool
    warnings: list[ReviewWarning] = Field(default_factory=list)
    error_code: str | None = None
    cancel_requested: bool = False

    @model_validator(mode='after')
    def validate_status(self):
        if (self.status == 'succeeded') != (self.stage == 'complete'):
            raise ValueError('Only successful jobs have a complete stage')
        if (self.status == 'failed') != bool(self.error_code):
            raise ValueError('Failed jobs require an error code; other jobs must not expose one')
        if self.updated_at < self.created_at:
            raise ValueError('Updated time cannot precede creation')
        return self


class TextCorrection(Contract):
    line_id: UUID
    text: str | None
    state: Literal['recognized','unreadable','needs-review']

    @model_validator(mode='after')
    def validate_correction(self):
        if self.state == 'recognized' and (not self.text or not self.text.strip()):
            raise ValueError('Recognized corrections require text')
        if self.state == 'unreadable' and self.text is not None:
            raise ValueError('Unreadable corrections require null text')
        return self


class RevisionInput(Contract):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    text_corrections: Annotated[list[TextCorrection], Field(max_length=2048)] = Field(default_factory=list)
    corrected_dividers: Annotated[list[Divider], Field(max_length=256)] | None = None

    @model_validator(mode='after')
    def validate_revision(self):
        if not self.text_corrections and self.corrected_dividers is None:
            raise ValueError('Revision must contain text or geometry corrections')
        ids = [x.line_id for x in self.text_corrections]
        if len(ids) != len(set(ids)):
            raise ValueError('A line may be corrected once per revision request')
        if self.text_corrections and self.corrected_dividers is not None:
            raise ValueError('Submit geometry first; old line IDs cannot be corrected during resegmentation')
        return self


class ErrorDetail(Contract):
    code: str
    message: str
    retryable: bool
    request_id: UUID


class ErrorResponse(Contract):
    error: ErrorDetail


class ModelInventoryEntry(Contract):
    reference: ModelReference
    license: str
    scripts: list[str]
    readiness: Literal['ready','warming','unavailable']


class ModelInventory(Contract):
    models: list[ModelInventoryEntry]


class Health(Contract):
    ready: bool
    components: dict[str, Literal['ready','warming','unavailable']]
    limits: dict[str, Annotated[int, Field(ge=1)]]
