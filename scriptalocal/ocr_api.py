"""Authenticated asynchronous OCR job API; inference executes in separate workers."""
import asyncio
from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import stat
from uuid import UUID,uuid4

from fastapi import FastAPI,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse,Response
from pydantic import ValidationError
import psycopg
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .contracts import Health,JobAccepted,JobOptions,JobResult,JobStatus,ModelInventory,SourceImage
from .images import ImageLimits,ImageInputError,decode_image
from .job_artifacts import ArtifactError
from .job_deletions import collect_deletions
from .job_queue import QueueError
from .ocr_access import CredentialStore,ProfileRegistry,ServiceError
from .source_store import SourceStore


@dataclass(frozen=True)
class APISettings:
    image:ImageLimits=ImageLimits()
    maximum_options_bytes:int=65536
    multipart_overhead_bytes:int=131072
    maximum_concurrent_uploads:int=1
    upload_timeout_seconds:int=120
    minimum_free_bytes:int=256*1024*1024

    def __post_init__(self):
        if any(type(value)is not int or value<1 for key,value in vars(self).items() if key!='image'):raise ValueError('API limits must be positive integers')


def error(code,message,status,request_id):
    return JSONResponse(status_code=status,content={'error':{'code':code,'message':message,'retryable':status in {429,503},'request_id':request_id}},headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def source_data(image):
    return SourceImage(sha256=image.sha256,width=image.encoded_size[0],height=image.encoded_size[1],
        oriented_width=image.pixels.width,oriented_height=image.pixels.height,exif_orientation=image.orientation,
        original_to_oriented=image.oriented_from_original.coefficients,mime_type=image.mime_type).model_dump(mode='json')


def status_data(queue,identifier,owner):
    with queue.connect() as db:
        row=db.execute("""SELECT j.*,(SELECT name FROM sl_stages s WHERE s.job_id=j.id AND s.revision=j.current_revision
          ORDER BY s.created_at DESC LIMIT 1) AS stage_name FROM sl_jobs j WHERE j.id=%s AND j.owner=%s""",(identifier,owner)).fetchone()
    if row is None:raise QueueError('not_found','Job not found')
    stage='complete' if row['status']=='succeeded' else 'queued' if row['status']=='queued' else row['stage_name'] or 'queued'
    return JobStatus(job_id=row['id'],status=row['status'],stage=stage,revision=row['current_revision'],
        created_at=row['created_at'],updated_at=row['updated_at'],review_required=row['review_required'],warnings=row['warnings'],
        error_code=row['error_code'],cancel_requested=row['cancel_requested']).model_dump(mode='json')


def create_app(queue,sources:SourceStore,credentials:CredentialStore,profiles:ProfileRegistry,*,settings=APISettings()):
    if sources.root.resolve()==queue.artifacts.root.resolve():raise ValueError('Original and artifact roots must differ')
    app=FastAPI(title='ScriptaLocal OCR',version='1',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.uploads=0

    @app.middleware('http')
    async def access(request,call_next):
        request.state.request_id=str(uuid4())
        if request.url.path!='/health/live':
            try:request.state.caller=credentials.authenticate(request.headers.get('authorization',''))
            except ServiceError as exc:return error(exc.code,str(exc),exc.status,request.state.request_id)
        try:response=await call_next(request)
        except Exception:return error('internal_error','Service request failed',500,request.state.request_id)
        response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request,exc):return error(exc.code,str(exc),exc.status,request.state.request_id)

    @app.exception_handler(ImageInputError)
    async def image_error(request,exc):return error(exc.code,str(exc),exc.status,request.state.request_id)

    @app.exception_handler(QueueError)
    async def queue_error(request,exc):
        code=404 if exc.code=='not_found' else 429 if exc.code=='queue_full' else 409
        return error(exc.code,str(exc),code,request.state.request_id)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):return error('invalid_request','Invalid request parameters',422,request.state.request_id)

    @app.exception_handler(psycopg.Error)
    async def database_error(request,exc):return error('state_unavailable','Job state is unavailable',503,request.state.request_id)

    @app.exception_handler(OSError)
    async def storage_error(request,exc):return error('storage_unavailable','Service storage is unavailable',503,request.state.request_id)

    @app.exception_handler(ArtifactError)
    async def artifact_error(request,exc):return error('artifact_unavailable','Stored artifact is unavailable',503,request.state.request_id)

    @app.exception_handler(Exception)
    async def unexpected(request,exc):return error('internal_error','Service request failed',500,request.state.request_id)

    def caller(request,capability):
        principal=request.state.caller;principal.require(capability);return principal

    def limits():return {'max_upload_bytes':settings.image.max_bytes,'max_pixels':settings.image.max_pixels,'max_side':settings.image.max_side}

    @app.get('/health/live')
    def live():return Health(ready=True,components={'api':'ready'},limits=limits()).model_dump(mode='json')

    @app.get('/health/ready')
    def ready(request:Request):
        caller(request,'read');components={'api':'ready','database':'unavailable','storage':'unavailable','model':'unavailable','cleanup':'unavailable'}
        try:
            if queue.schema_current():components['database']='ready'
            with queue.connect() as db:
                failed=db.execute("SELECT EXISTS(SELECT 1 FROM sl_deletions WHERE status='failed') AS present").fetchone()['present']
            components['cleanup']='unavailable' if failed else 'ready'
        except (psycopg.Error,OSError):pass
        try:
            roots=[sources.root,queue.artifacts.root]
            if all(os.access(root,os.W_OK|os.X_OK) and not os.statvfs(root).f_flag & os.ST_RDONLY and shutil.disk_usage(root).free>=settings.minimum_free_bytes for root in roots):components['storage']='ready'
            default,installed=profiles.snapshot();components['model']=next(item.readiness for item in installed if item.model_id==default)
        except (OSError,ServiceError):pass
        return Health(ready=all(state=='ready' for state in components.values()),components=components,limits=limits()).model_dump(mode='json')

    @app.get('/v1/models')
    def models(request:Request):
        caller(request,'read');_,installed=profiles.snapshot()
        return ModelInventory(models=[{'reference':item.reference,'license':item.license,'scripts':item.scripts,'readiness':item.readiness} for item in installed]).model_dump(mode='json')

    @app.post('/v1/jobs',status_code=202)
    async def create_job(request:Request):
        owner=caller(request,'write').owner
        key=request.headers.get('idempotency-key')
        if key is not None and not re.fullmatch(r'[!-~]{1,128}',key):raise ServiceError('invalid_idempotency_key','Invalid idempotency key')
        if not request.headers.get('content-type','').lower().startswith('multipart/form-data'):
            raise ServiceError('unsupported_request_type','Send multipart image and options',415)
        maximum=settings.image.max_bytes+settings.maximum_options_bytes+settings.multipart_overhead_bytes
        length=request.headers.get('content-length')
        if length is not None:
            try:length=int(length)
            except ValueError:raise ServiceError('invalid_length','Invalid Content-Length')
            if length<0:raise ServiceError('invalid_length','Invalid Content-Length')
            if length>maximum:raise ServiceError('upload_too_large','Upload exceeds byte limit',413)
        if app.state.uploads>=settings.maximum_concurrent_uploads:raise ServiceError('upload_busy','Upload concurrency limit reached',429)
        app.state.uploads+=1;form=None
        try:
            body=bytearray()
            try:
                async with asyncio.timeout(settings.upload_timeout_seconds):
                    async for chunk in request.stream():
                        if len(body)+len(chunk)>maximum:raise ServiceError('upload_too_large','Upload exceeds byte limit',413)
                        body.extend(chunk)
                renewed=credentials.authenticate(request.headers.get('authorization',''));renewed.require('write')
                if renewed.owner!=owner:raise ServiceError('unauthorized','Caller identity changed during upload',401)
                request._body=bytes(body)
                form=await request.form(max_files=1,max_fields=1,max_part_size=settings.maximum_options_bytes)
            except TimeoutError:raise ServiceError('upload_timeout','Upload timed out',503)
            except ServiceError:raise
            except Exception:raise ServiceError('invalid_multipart','Invalid multipart request') from None
            fields=list(form.multi_items())
            if any(name not in {'image','options'} for name,value in fields) or len({name for name,value in fields})!=len(fields):raise ServiceError('invalid_multipart','Only image and options are accepted')
            upload=form.get('image');raw_options=form.get('options')
            if not isinstance(upload,UploadFile) or raw_options is not None and not isinstance(raw_options,str):raise ServiceError('invalid_multipart','Image file and optional options string required')
            try:options=JobOptions() if raw_options is None else JobOptions.model_validate_json(raw_options,strict=True)
            except ValidationError:raise ServiceError('invalid_options','Invalid job options') from None
            payload=await upload.read(settings.image.max_bytes+1)
            image=await run_in_threadpool(lambda:decode_image(payload,declared_mime=upload.content_type,limits=settings.image))
            source=source_data(image)
            old=await run_in_threadpool(queue.find_idempotent,owner,key) if key is not None else None
            if old is not None:configuration=old['configuration_sha256']
            else:configuration=profiles.select(options.model_id).configuration_sha256
            def submit():
                renewed=credentials.authenticate(request.headers.get('authorization',''));renewed.require('write')
                if renewed.owner!=owner:raise ServiceError('unauthorized','Caller identity changed during upload',401)
                with sources.locked(image.sha256):
                    existing=queue.find_idempotent(owner,key) if key is not None else None
                    if old is not None and (existing is None or existing['id']!=old['id']):
                        raise ServiceError('idempotency_state_changed','Previous operation changed; reconcile before retrying',409)
                    if existing is not None:
                        return queue.create(owner,source,options.model_dump(mode='json'),existing['configuration_sha256'],
                            idempotency_key=key,expected_existing_id=existing['id'])
                    with queue.connect() as db:
                        active=db.execute("SELECT count(*) AS count FROM sl_jobs WHERE status IN ('queued','running')").fetchone()['count']
                    if active>=queue.settings.maximum_active_jobs:raise QueueError('queue_full','Job queue has reached its active-job limit')
                    if any(shutil.disk_usage(root).free<settings.minimum_free_bytes+len(payload) for root in [sources.root,queue.artifacts.root]):
                        raise ServiceError('storage_pressure','Insufficient storage for admission',503)
                    try:sources.publish_locked(image)
                    except ValueError:raise ServiceError('source_unavailable','Original storage is unavailable',503) from None
                    return queue.create(owner,source,options.model_dump(mode='json'),configuration,idempotency_key=key)
            row,replayed=await run_in_threadpool(submit)
            return JobAccepted(job_id=row['id'],status=row['status'],revision=row['current_revision'],idempotency_replayed=replayed).model_dump(mode='json')
        finally:
            if form is not None:await form.close()
            app.state.uploads-=1

    @app.get('/v1/jobs/{job_id}')
    def job_status(job_id:UUID,request:Request):return status_data(queue,job_id,caller(request,'read').owner)

    @app.get('/v1/jobs/{job_id}/result')
    def result(job_id:UUID,request:Request):
        record=queue.result_reference(job_id,caller(request,'read').owner)
        model=JobResult.model_validate_json(queue.artifacts.read(record))
        return model.model_dump(mode='json')

    @app.get('/v1/jobs/{job_id}/artifacts/{artifact_id}')
    def artifact(job_id:UUID,artifact_id:UUID,request:Request):
        owner=caller(request,'read').owner
        with queue.connect() as db:
            row=db.execute('SELECT a.* FROM sl_artifacts a JOIN sl_jobs j ON j.id=a.job_id WHERE a.id=%s AND a.job_id=%s AND j.owner=%s',(artifact_id,job_id,owner)).fetchone()
        if row is None:raise QueueError('not_found','Artifact not found')
        from .job_artifacts import ArtifactRecord
        record=ArtifactRecord(row['id'],row['logical_key'],row['relative_path'],row['sha256'],row['bytes'])
        return Response(queue.artifacts.read(record),media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="'+str(record.id)+Path(record.relative_path).suffix+'"'})

    @app.post('/v1/jobs/{job_id}/cancel')
    def cancel(job_id:UUID,request:Request):
        owner=caller(request,'write').owner;queue.cancel(job_id,owner);return status_data(queue,job_id,owner)

    @app.delete('/v1/jobs/{job_id}',status_code=204)
    def delete(job_id:UUID,request:Request):
        identifier=queue.request_deletion(job_id,caller(request,'delete').owner)
        collect_deletions(queue,sources,limit=1,request_id=identifier)
        return Response(status_code=204)

    return app
