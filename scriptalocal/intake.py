"""Private agent file intake, separate from the planned OCR job API."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
import re
import sqlite3
from urllib.parse import unquote
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.requests import ClientDisconnect

from .intake_store import IntakeError, IntakeSettings, IntakeStore, clean_filename, open_destination


class UploadRequestInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    destination:str=Field(min_length=1,max_length=100)
    subfolder:str=Field(default='',max_length=500)
    label:str=Field(min_length=1,max_length=200)
    max_files:int=Field(default=2,ge=1)
    max_file_bytes:int=Field(default=10*1024*1024,ge=1)
    max_total_bytes:int=Field(default=20*1024*1024,ge=1)
    expires_in_seconds:int=Field(default=86400,ge=1)
    allowed_suffixes:list[str]=Field(default_factory=lambda:['.md','.json','.yaml','.yml','.txt'],min_length=1,max_length=40)

    @field_validator('allowed_suffixes')
    @classmethod
    def suffixes(cls,values):
        if any(not re.fullmatch(r'\.[a-z0-9]{1,10}',value) for value in values):
            raise ValueError('Use lowercase filename suffixes such as .json')
        return sorted(set(values))


def error_response(code,message,status):
    return JSONResponse(status_code=status,content={'error':{'code':code,'message':message,'retryable':status in {408,429,503},'request_id':str(uuid4())}})


def bearer(request):
    scheme,separator,token=request.headers.get('authorization','').partition(' ')
    return token if separator and scheme.lower()=='bearer' else ''


def is_admin(request,settings):
    return hmac.compare_digest(bearer(request).encode(),settings.admin_token.encode())


def require_admin(request,settings):
    if not is_admin(request,settings):raise IntakeError('unauthorized','Administrator credentials required',401)


def length_check(request,limit):
    length=request.headers.get('content-length')
    if length is not None:
        try:length=int(length)
        except ValueError:raise IntakeError('invalid_length','Invalid Content-Length header',400)
        if length<0:raise IntakeError('invalid_length','Invalid Content-Length header',400)
        if length>limit:raise IntakeError('file_bytes_exceeded','Upload exceeds its byte limit',413)


async def small_json(request,timeout):
    length_check(request,65536)
    body=bytearray()
    try:
        async with asyncio.timeout(timeout):
            async for chunk in request.stream():
                if len(body)+len(chunk)>65536:raise IntakeError('request_too_large','Metadata request exceeds byte limit',413)
                body.extend(chunk)
        return json.loads(body)
    except (json.JSONDecodeError,UnicodeDecodeError):raise IntakeError('invalid_json','Invalid JSON request')


def create_app(settings:IntakeSettings):
    @asynccontextmanager
    async def lifespan(app):
        store=IntakeStore(settings);app.state.store=store;app.state.active_uploads=0
        try:yield
        finally:store.close()
    app=FastAPI(title='ScriptaLocal agent intake',version='1',lifespan=lifespan,
                docs_url=None,redoc_url=None,openapi_url=None)

    @app.exception_handler(IntakeError)
    async def known_error(request,exc):return error_response(exc.code,str(exc),exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):return error_response('invalid_request','Invalid request parameters',422)

    @app.exception_handler(OSError)
    async def storage_error(request,exc):return error_response('storage_unavailable','Upload storage is unavailable',503)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request,exc):return error_response('state_unavailable','Upload state storage is unavailable',503)

    @app.exception_handler(TimeoutError)
    async def timed_out(request,exc):return error_response('upload_timeout','Upload timed out',408)

    @app.exception_handler(ClientDisconnect)
    async def disconnected(request,exc):return error_response('client_disconnected','Upload disconnected before completion',400)

    @app.get('/health/live')
    async def live():return {'status':'ok'}

    @app.get('/intake/v1/destinations')
    async def destinations(request:Request):
        require_admin(request,settings)
        return {'destinations':sorted(settings.destinations)}

    @app.get('/intake/v1/openapi.json')
    async def specification(request:Request):
        require_admin(request,settings)
        return app.openapi()

    @app.post('/intake/v1/requests',status_code=201)
    async def create_request(request:Request):
        require_admin(request,settings)
        try:options=UploadRequestInput.model_validate(await small_json(request,settings.upload_timeout_seconds))
        except ValidationError:raise IntakeError('invalid_request','Invalid upload request options')
        for name,ceiling in [('max_files',settings.maximum_files),('max_file_bytes',settings.maximum_file_bytes),
                             ('max_total_bytes',settings.maximum_total_bytes),('expires_in_seconds',settings.maximum_expiry_seconds)]:
            if getattr(options,name)>ceiling:raise IntakeError('request_limit_exceeded','Upload request exceeds a configured server limit')
        details,token=app.state.store.create(options.model_dump())
        return {**details,'upload_token':token,
                'upload_url':settings.public_base_url.rstrip('/')+'/intake/v1/requests/'+details['request_id']+'/files'}

    @app.get('/intake/v1/requests/{request_id}')
    async def request_details(request_id:UUID,request:Request):
        app.state.store.request(str(request_id),bearer(request),admin=is_admin(request,settings))
        return app.state.store.describe(str(request_id))

    @app.post('/intake/v1/requests/{request_id}/revoke')
    async def revoke(request_id:UUID,request:Request):
        require_admin(request,settings);app.state.store.revoke(str(request_id))
        return app.state.store.describe(str(request_id))

    @app.put('/intake/v1/requests/{request_id}/files',openapi_extra={
        'requestBody':{'required':True,'content':{'application/octet-stream':{'schema':{'type':'string','format':'binary'}}}}})
    async def upload(request_id:UUID,request:Request):
        store=app.state.store;row=store.request(str(request_id),bearer(request))
        encoded=request.headers.get('x-upload-filename','')
        try:filename=clean_filename(unquote(encoded,encoding='utf-8',errors='strict'))
        except UnicodeError:raise IntakeError('invalid_filename','Invalid UTF-8 filename')
        length_check(request,row['max_file_bytes'])
        if app.state.active_uploads>=settings.maximum_concurrent_uploads:
            raise IntakeError('too_many_uploads','Upload concurrency limit reached',429)
        app.state.active_uploads+=1
        reservation=None;directory_fd=None;output_fd=None;temporary=None;replay=False
        try:
            reservation,replay=store.reserve(row,filename)
            directory_fd=open_destination(store.destination_root(row),row['subfolder'])
            if not replay:
                temporary='.intake-'+reservation['id']+'.part'
                output_fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory_fd)
            size=0;digest=hashlib.sha256()
            async with asyncio.timeout(settings.upload_timeout_seconds):
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>row['max_file_bytes']:raise IntakeError('file_bytes_exceeded','Upload exceeds its byte limit',413)
                    digest.update(chunk)
                    if output_fd is not None:
                        remaining=memoryview(chunk)
                        while remaining:
                            written=os.write(output_fd,remaining)
                            if written<=0:raise OSError('No progress writing upload')
                            remaining=remaining[written:]
            if output_fd is not None:os.fsync(output_fd);os.close(output_fd);output_fd=None
            checksum=digest.hexdigest()
            store.finalize(row,reservation,size,checksum,directory_fd,temporary,replay)
            details=store.describe(row['id'])
            return JSONResponse(status_code=200 if replay else 201,content={
                'request_id':row['id'],'file_id':reservation['id'],'filename':filename,'bytes':size,'sha256':checksum,
                'destination':row['destination'],'subfolder':row['subfolder'],'replayed':replay,
                'remaining_files':details['remaining_files']})
        finally:
            if output_fd is not None:os.close(output_fd)
            if temporary is not None and directory_fd is not None:
                try:os.unlink(temporary,dir_fd=directory_fd)
                except FileNotFoundError:pass
            if directory_fd is not None:os.close(directory_fd)
            if reservation is not None and not replay:store.abort(reservation)
            app.state.active_uploads-=1
    base_openapi=app.openapi
    def documented_schema():
        specification=base_openapi()
        if specification.get('x-intake-auth-described'):
            return specification
        components=specification.setdefault('components',{})
        components.setdefault('schemas',{})['UploadRequestInput']=UploadRequestInput.model_json_schema()
        components['securitySchemes']={
            'Administrator':{'type':'http','scheme':'bearer','description':'Private administrator token; never share with the uploading agent.'},
            'UploadTicket':{'type':'http','scheme':'bearer','description':'Expiring token scoped to one upload request and destination.'}}
        for path,methods in specification['paths'].items():
            for method,operation in methods.items():
                operation['security']=([] if path=='/health/live' else
                    [{'UploadTicket':[]}] if method=='put' else
                    [{'Administrator':[]},{'UploadTicket':[]}] if path=='/intake/v1/requests/{request_id}' else
                    [{'Administrator':[]}])
        specification['paths']['/intake/v1/requests']['post']['requestBody']={
            'required':True,'content':{'application/json':{'schema':{'$ref':'#/components/schemas/UploadRequestInput'}}}}
        specification['paths']['/intake/v1/requests/{request_id}/files']['put'].setdefault('parameters',[]).append({
            'name':'X-Upload-Filename','in':'header','required':True,
            'description':'Percent-encoded UTF-8 basename only, without a folder path.',
            'schema':{'type':'string'}})
        specification['x-intake-auth-described']=True
        return specification
    app.openapi=documented_schema
    return app


def create_default_app():
    config=os.environ.get('SCRIPTALOCAL_INTAKE_CONFIG')
    if not config:raise RuntimeError('Set SCRIPTALOCAL_INTAKE_CONFIG to a private configuration file')
    return create_app(IntakeSettings.load(config))
