"""Small same-origin submission UI with revocable server-side browser sessions."""
import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import FileResponse,JSONResponse,RedirectResponse

from .ocr_access import ServiceError,private_json
from .worker_readiness import atomic_json

COOKIE='__Host-scripta_session'
ASSETS=Path(__file__).parent/'web'
CSP="default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


class ManualUI:
    def __init__(self,app,credentials,operator_path=None):
        self.credentials=credentials;self.operator_path=Path(operator_path) if operator_path else None
        self.sessions={};self.lock=threading.Lock();self.attempts=[]
        self.public_get={'/','/ui','/ui/','/ui/app.js','/ui/style.css'}

        @app.get('/')
        def home():return RedirectResponse('/ui',status_code=303)

        @app.get('/ui')
        @app.get('/ui/')
        def page():return FileResponse(ASSETS/'index.html',headers={'Content-Security-Policy':CSP,'Referrer-Policy':'no-referrer'})

        @app.get('/ui/app.js')
        def javascript():return FileResponse(ASSETS/'app.js',media_type='text/javascript')

        @app.get('/ui/style.css')
        def stylesheet():return FileResponse(ASSETS/'style.css',media_type='text/css')

        @app.post('/ui/login')
        async def login(request:Request):
            self.origin(request)
            if request.headers.get('x-scripta-ui')!='1' or request.headers.get('content-type','').split(';')[0]!='application/json':raise ServiceError('invalid_login','Use the sign-in form',415)
            with self.lock:
                now=time.monotonic();self.attempts=[value for value in self.attempts if now-value<60]
                if len(self.attempts)>=20:raise ServiceError('login_busy','Too many sign-in attempts. Try again in one minute.',429)
                self.attempts.append(now)
            body=await self.body(request,1024)
            try:
                data=json.loads(body);token=data['key']
                if set(data)!={'key'} or not isinstance(token,str):raise ValueError
            except (ValueError,KeyError,TypeError):raise ServiceError('invalid_login','Enter an access key') from None
            caller=self.credentials.authenticate('Bearer '+token);caller.require('read')
            identifier=secrets.token_urlsafe(48);session={'token':token,'csrf':secrets.token_urlsafe(32),'expires':time.monotonic()+28800}
            with self.lock:
                self.prune()
                if len(self.sessions)>=128:raise ServiceError('login_busy','Too many active sessions',429)
                self.sessions[hashlib.sha256(identifier.encode()).hexdigest()]=session
            response=JSONResponse({'signed_in':True})
            response.set_cookie(COOKIE,identifier,max_age=28800,secure=True,httponly=True,samesite='strict',path='/')
            return response

        @app.get('/ui/session')
        def session(request:Request):
            active=getattr(request.state,'ui_session',None)
            if active is None:raise ServiceError('unauthorized','Sign in to continue',401)
            caller=request.state.caller
            return {'csrf':active['csrf'],'capabilities':sorted(caller.capabilities),'can_configure':self.operator_path is not None and 'configure' in caller.capabilities}

        @app.post('/ui/logout')
        def logout(request:Request):
            with self.lock:self.sessions.pop(hashlib.sha256(request.cookies.get(COOKIE,'').encode()).hexdigest(),None)
            response=JSONResponse({'signed_in':False});response.delete_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='strict');return response

        @app.get('/ui/settings')
        def settings(request:Request):
            request.state.caller.require('configure')
            if self.operator_path is None:raise ServiceError('settings_unavailable','Settings are not configured',503)
            return {'backup_destination':private_json(self.operator_path,65536).get('backup_destination')}

        @app.post('/ui/settings')
        async def save_settings(request:Request):
            request.state.caller.require('configure')
            if self.operator_path is None:raise ServiceError('settings_unavailable','Settings are not configured',503)
            try:
                data=json.loads(await self.body(request,4096));value=data['backup_destination']
                if set(data)!={'backup_destination'} or not isinstance(value,str) or not 1<=len(value)<=2048 or not Path(value).is_absolute() or any(char in value for char in '\x00\r\n'):raise ValueError
            except (ValueError,KeyError,TypeError):raise ServiceError('invalid_settings','Enter an absolute backup destination') from None
            existing=private_json(self.operator_path,65536);existing['backup_destination']=value
            atomic_json(self.operator_path,existing)
            return {'backup_destination':value}

    def prune(self):
        now=time.monotonic()
        for key in [key for key,value in self.sessions.items() if value['expires']<=now]:del self.sessions[key]

    def public(self,request):return request.method=='GET' and request.url.path in self.public_get or request.method=='POST' and request.url.path=='/ui/login'

    def authorization(self,request):
        explicit=request.headers.get('authorization')
        if explicit:return explicit
        identifier=request.cookies.get(COOKIE,'')
        if len(identifier)>256:raise ServiceError('unauthorized','Sign in to continue',401)
        with self.lock:
            self.prune();session=self.sessions.get(hashlib.sha256(identifier.encode()).hexdigest())
        if session is None:raise ServiceError('unauthorized','Sign in to continue',401)
        if request.method not in {'GET','HEAD','OPTIONS'}:
            self.origin(request)
            if not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):raise ServiceError('csrf_failed','Reload the page and try again',403)
        request.state.ui_session=session
        return 'Bearer '+session['token']

    @staticmethod
    def origin(request):
        origin=request.headers.get('origin')
        if origin:
            value=urlsplit(origin)
            if value.scheme!='https' or value.netloc.lower()!=request.headers.get('host','').lower():raise ServiceError('forbidden_origin','Use this site’s sign-in page',403)

    @staticmethod
    async def body(request,maximum):
        body=bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    if len(body)+len(chunk)>maximum:raise ServiceError('request_too_large','Request exceeds size limit',413)
                    body.extend(chunk)
        except TimeoutError:raise ServiceError('request_timeout','Request timed out',503) from None
        return bytes(body)
