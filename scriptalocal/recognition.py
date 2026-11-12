"""Bounded single-line recognition scheduling, independent of GPU imports."""
from dataclasses import dataclass
import math
from uuid import UUID

from PIL import Image


class RecognitionError(ValueError):pass
class RecognitionOOM(RecognitionError):pass


@dataclass(frozen=True)
class RecognitionSettings:
    batch_size:int=4
    maximum_lines:int=4096
    maximum_line_pixels:int=4_000_000
    maximum_batch_pixels:int=8_000_000
    maximum_side:int=16384
    maximum_new_tokens:int=128
    num_beams:int=4
    maximum_oom_recoveries:int=4
    wide_line_review_ratio:float=20.0

    def __post_init__(self):
        for name in ['batch_size','maximum_lines','maximum_line_pixels','maximum_batch_pixels','maximum_side','maximum_new_tokens','num_beams','maximum_oom_recoveries']:
            value=getattr(self,name)
            if type(value)is not int or value<(0 if name=='maximum_oom_recoveries' else 1):raise ValueError('Invalid recognition limits')
        if self.maximum_line_pixels>self.maximum_batch_pixels:raise ValueError('A line must fit the batch pixel budget')
        if not math.isfinite(self.wide_line_review_ratio) or self.wide_line_review_ratio<=0:raise ValueError('Invalid line aspect ratio')


@dataclass(frozen=True)
class LineInput:
    id:UUID
    pixels:Image.Image


@dataclass(frozen=True)
class Generation:
    text:str
    truncated:bool
    generated_tokens:int

    def __post_init__(self):
        if not isinstance(self.text,str) or type(self.truncated)is not bool or type(self.generated_tokens)is not int or self.generated_tokens<0:
            raise RecognitionError('Invalid generation result')


@dataclass(frozen=True)
class LineReading:
    id:UUID
    state:str
    text:str|None
    warnings:tuple[str,...]
    generated_tokens:int


@dataclass(frozen=True)
class RecognitionRun:
    lines:tuple[LineReading,...]
    oom_events:int
    recoveries:int
    backend_calls:int


def recognize_lines(inputs,backend,*,settings=RecognitionSettings()):
    """Accept segmented single-line images only, never a cell/page image.

    Failures remain explicit; batch splitting cannot change input identity/order.
    No provider/model/precision switch is performed during memory recovery.
    """
    lines=[];identifiers=set()
    for line in inputs:
        if len(lines)>=settings.maximum_lines:raise RecognitionError('Line count exceeds limit')
        if not isinstance(line,LineInput) or not isinstance(line.id,UUID) or line.id in identifiers:raise RecognitionError('Line IDs must be unique UUIDs')
        if not isinstance(line.pixels,Image.Image) or min(line.pixels.size)<1:raise RecognitionError('Invalid line image')
        if line.pixels.width*line.pixels.height>settings.maximum_line_pixels or max(line.pixels.size)>settings.maximum_side:
            raise RecognitionError('Line image exceeds limits')
        identifiers.add(line.id);lines.append(line)
    calls=ooms=recoveries=0;outputs=[];exhausted=False
    def failed(batch,code):
        return [LineReading(line.id,'failed',None,(code,),0) for line in batch]
    def generate(batch):
        nonlocal calls,ooms,recoveries,exhausted
        if exhausted:return failed(batch,'gpu_memory_recovery_exhausted')
        calls+=1
        try:
            results=list(backend([line.pixels for line in batch],max_new_tokens=settings.maximum_new_tokens,num_beams=settings.num_beams))
        except RecognitionOOM:
            ooms+=1
            if recoveries>=settings.maximum_oom_recoveries:
                exhausted=True
                return failed(batch,'gpu_memory_recovery_exhausted')
            recoveries+=1
            try:backend.recover()
            except Exception as exc:raise RecognitionError('GPU memory recovery failed') from exc
            if len(batch)==1:return failed(batch,'gpu_memory_exhausted')
            middle=len(batch)//2
            return generate(batch[:middle])+generate(batch[middle:])
        except RecognitionError:raise
        except Exception as exc:raise RecognitionError('Recognition backend failed') from exc
        if len(results)!=len(batch) or any(not isinstance(result,Generation) for result in results):raise RecognitionError('Backend result count/type mismatch')
        readings=[]
        for line,result in zip(batch,results):
            warnings=[]
            if result.truncated:warnings.append('generation_truncated')
            if not result.text.strip():warnings.append('empty_recognition')
            if line.pixels.width/line.pixels.height>settings.wide_line_review_ratio:warnings.append('wide_line_requires_review')
            readings.append(LineReading(line.id,'needs-review' if warnings else 'recognized',result.text,tuple(warnings),result.generated_tokens))
        return readings
    batch=[];pixels=0
    for line in lines:
        area=line.pixels.width*line.pixels.height
        if batch and (len(batch)>=settings.batch_size or pixels+area>settings.maximum_batch_pixels):
            outputs.extend(generate(batch));batch=[];pixels=0
        batch.append(line);pixels+=area
    if batch:outputs.extend(generate(batch))
    return RecognitionRun(tuple(outputs),ooms,recoveries,calls)
