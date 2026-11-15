"""Single-owner SYM bridge: REST dashboard/device sync + OAuth-protected MCP."""
import contextlib, hashlib, html, json, os, secrets, sqlite3, time
from pathlib import Path
from urllib.parse import urlparse, urlencode
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.provider import (OAuthAuthorizationServerProvider, AuthorizationCode,
    AccessToken, RefreshToken, RegistrationError, TokenError)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.types import ToolAnnotations


def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def require_secret(value, label):
    if len(value) < 32: raise RuntimeError(label + ': use um segredo aleatório de pelo menos 32 caracteres')
    return value

class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY, revision INTEGER, data TEXT);
            CREATE TABLE IF NOT EXISTS device(id INTEGER PRIMARY KEY, data TEXT);
            CREATE TABLE IF NOT EXISTS clients(id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at INTEGER,action TEXT,revision INTEGER);''')
            c.execute('INSERT OR IGNORE INTO config VALUES (1,1,?)', (json.dumps({'catalogue':'https://linktr.ee/sidneymescouti','knowledge':'SYM CONCEPT: loja online de streetwear de Curitiba. Sidney confirma preços, estoque, medidas e frete.','paused':True}),))
        os.chmod(path, 0o600)
    def connect(self):
        c=sqlite3.connect(self.path, timeout=10)
        c.row_factory=sqlite3.Row
        return c
    def config(self):
        with self.connect() as c:
            r=c.execute('SELECT * FROM config WHERE id=1').fetchone()
            return dict(revision=r['revision'], **json.loads(r['data']))
    def status(self):
        with self.connect() as c:
            r=c.execute('SELECT data FROM device WHERE id=1').fetchone()
            device=json.loads(r['data']) if r else None
        return {'configuration':self.config(),'device':device,'online_recently':bool(device and time.time()-device['received_at']<180)}
    def update(self, expected, changes, actor):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM config WHERE id=1').fetchone()
            if row['revision']!=expected: raise ValueError('Configuração mudou. Consulte a versão atual e tente novamente.')
            data=json.loads(row['data']);data.update(changes); revision=expected+1
            c.execute('UPDATE config SET revision=?,data=? WHERE id=1',(revision,json.dumps(data)))
            c.execute('INSERT INTO audit(at,action,revision) VALUES(?,?,?)',(int(time.time()),actor,revision))
            c.execute('DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 200)')
        return self.config()
    def heartbeat(self, data):
        data['received_at']=int(time.time())
        with self.connect() as c:c.execute('INSERT OR REPLACE INTO device VALUES(1,?)',(json.dumps(data),))
    def audit(self):
        with self.connect() as c:return [dict(r) for r in c.execute('SELECT at,action,revision FROM audit ORDER BY id DESC LIMIT 30')]

class Change(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision: int=Field(ge=1)
    catalogue: str|None=Field(default=None,max_length=2000)
    knowledge: str|None=Field(default=None,max_length=16000)
    paused: bool|None=None
    def changes(self):
        result=self.model_dump(exclude_none=True,exclude={'expected_revision'})
        if not result:raise ValueError('Nenhuma alteração informada.')
        if 'catalogue' in result:
            u=urlparse(result['catalogue'])
            if u.scheme!='https' or not u.hostname or u.username or u.password:raise ValueError('Use um link HTTPS válido, sem credenciais.')
        return result

class Heartbeat(BaseModel):
    model_config=ConfigDict(extra='forbid')
    app_version: str=Field(max_length=30)
    local_enabled: bool
    notification_access: bool
    ai_enabled: bool
    replies_today: int=Field(ge=0,le=100000)
    applied_revision: int=Field(ge=0)

class Provider(OAuthAuthorizationServerProvider):
    """SDK validates redirect binding, PKCE S256 and grant parameters.
    Owner consent is mandatory. Codes/tokens expire and are held in RAM only.
    """
    def __init__(self, db, base, callbacks):
        self.db,self.base,self.callbacks=db,base,set(callbacks)
        self.pending={};self.codes={};self.access={};self.refresh={}
    def clean(self):
        now=time.time()
        for k,v in list(self.pending.items()):
            if v['until']<now:self.pending.pop(k,None)
        for collection in (self.codes,self.access,self.refresh):
            for k,v in list(collection.items()):
                if v.expires_at and v.expires_at<now:collection.pop(k,None)
    async def get_client(self, client_id):
        with self.db.connect() as c:r=c.execute('SELECT data FROM clients WHERE id=?',(client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(r['data']) if r else None
    async def register_client(self, client_info):
        redirects=[str(u) for u in client_info.redirect_uris]
        if not redirects or not all(u in self.callbacks for u in redirects):raise RegistrationError('invalid_redirect_uri','Callback não autorizado pelo dono.')
        with self.db.connect() as c:
            if c.execute('SELECT count(*) FROM clients').fetchone()[0]>=100:raise RegistrationError('invalid_client_metadata','Limite de clientes atingido.')
            c.execute('INSERT OR REPLACE INTO clients VALUES(?,?)',(client_info.client_id,client_info.model_dump_json()))
    async def authorize(self, client, params):
        self.clean()
        if len(self.pending)>=100:raise ValueError('Muitas solicitações pendentes')
        if str(params.redirect_uri) not in self.callbacks:raise ValueError('Callback não autorizado')
        if params.resource!=self.base+'/mcp':raise ValueError('Resource inválido')
        ticket=secrets.token_urlsafe(32)
        self.pending[ticket]={'client':client,'params':params,'until':time.time()+300,'csrf':None}
        return self.base+'/consent?ticket='+ticket
    async def load_authorization_code(self, client, authorization_code):
        self.clean();v=self.codes.get(digest(authorization_code))
        return v if v and v.client_id==client.client_id else None
    def issue(self, client_id, scopes, resource):
        self.clean()
        if len(self.access)>1000:raise TokenError('invalid_grant','Limite de sessões atingido')
        a,r=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        self.access[digest(a)]=AccessToken(token=a,client_id=client_id,scopes=scopes,expires_at=int(time.time())+3600,resource=resource,subject='sidney')
        self.refresh[digest(r)]=RefreshToken(token=r,client_id=client_id,scopes=scopes,expires_at=int(time.time())+86400,subject=resource)
        return OAuthToken(access_token=a,refresh_token=r,expires_in=3600,scope=' '.join(scopes))
    async def exchange_authorization_code(self, client, authorization_code):
        code=self.codes.pop(digest(authorization_code.code),None)
        if not code or code.expires_at<time.time() or code.client_id!=client.client_id:raise TokenError('invalid_grant')
        return self.issue(code.client_id,code.scopes,code.resource)
    async def load_refresh_token(self, client, refresh_token):
        self.clean();v=self.refresh.get(digest(refresh_token))
        return v if v and v.client_id==client.client_id else None
    async def exchange_refresh_token(self, client, refresh_token, scopes):
        r=self.refresh.pop(digest(refresh_token.token),None)
        if not r or r.client_id!=client.client_id or not set(scopes).issubset(r.scopes):raise TokenError('invalid_grant')
        # Rotate and invalidate previous access tokens for this single-owner client.
        for k,v in list(self.access.items()):
            if v.client_id==client.client_id:self.access.pop(k,None)
        return self.issue(client.client_id,scopes,r.subject)
    async def load_access_token(self, token):
        self.clean();v=self.access.get(digest(token))
        return v if v and v.resource==self.base+'/mcp' else None
    async def revoke_token(self, token):
        for collection in (self.access,self.refresh):
            for k,v in list(collection.items()):
                if v.client_id==token.client_id:collection.pop(k,None)

def create_app(path=None, base=None, owner=None, device_token=None, callbacks=None):
    base=(base or os.environ.get('SYM_PUBLIC_URL','http://127.0.0.1:8765')).rstrip('/')
    parsed=urlparse(base)
    if not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:raise RuntimeError('Use somente a origem HTTPS do servidor.')
    if parsed.scheme!='https' and base!='http://127.0.0.1:8765':raise RuntimeError('Produção exige URL HTTPS.')
    owner=require_secret(owner or os.environ.get('SYM_OWNER_TOKEN',''),'SYM_OWNER_TOKEN')
    device_token=require_secret(device_token or os.environ.get('SYM_DEVICE_TOKEN',''),'SYM_DEVICE_TOKEN')
    if secrets.compare_digest(owner,device_token):raise RuntimeError('Use segredos diferentes para dono e aparelho.')
    db=Database(path or os.environ.get('SYM_DB','data/sym.sqlite3'))
    callbacks=callbacks if callbacks is not None else [x.strip() for x in os.environ.get('SYM_OAUTH_REDIRECTS','').split(',') if x.strip()]
    provider=Provider(db,base,callbacks)
    host=urlparse(base).netloc
    mcp=FastMCP('SYM CONCEPT • Gerenciamento',stateless_http=True,json_response=True,
        auth_server_provider=provider,
        auth=AuthSettings(issuer_url=base,resource_server_url=base+'/mcp',required_scopes=['sym:manage'],client_registration_options=ClientRegistrationOptions(enabled=True,valid_scopes=['sym:manage'],default_scopes=['sym:manage']),revocation_options=RevocationOptions(enabled=True)),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,allowed_hosts=[host],allowed_origins=[base]))
    meta={'securitySchemes':[{'type':'oauth2','scopes':['sym:manage']}]}
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,openWorldHint=False),meta=meta)
    def consultar_bot()->dict:
        """Consultar configuração e último estado recebido; não é acesso em tempo real ao celular."""
        return db.status()
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,openWorldHint=False),meta=meta)
    def consultar_alteracoes()->list[dict]:
        """Listar auditoria sem mensagens de clientes ou segredos."""
        return db.audit()
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False,openWorldHint=False),meta=meta)
    def configurar_bot(expected_revision:int, catalogue:str|None=None, knowledge:str|None=None, paused:bool|None=None)->dict:
        """Alterar link, conhecimento ou pausa. Use a revisão atual. Não envia mensagens; alteração pendente até sincronização do celular. Não pode ativar permissão local, IA ou novos contatos."""
        change=Change(expected_revision=expected_revision,catalogue=catalogue,knowledge=knowledge,paused=paused)
        return {'desired_configuration':db.update(change.expected_revision,change.changes(),'mcp'),'delivery':'pending_device_sync'}
    mcp_app=mcp.streamable_http_app()
    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():yield
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.db=db;app.state.provider=provider
    attempts={}
    @app.middleware('http')
    async def boundaries(req,call_next):
        if req.headers.get('host')!=host:return JSONResponse({'error':'Host inválido'},400)
        origin=req.headers.get('origin')
        if origin and origin!=base:return JSONResponse({'error':'Origin inválida'},403)
        try:
            if int(req.headers.get('content-length','0'))>32768:return JSONResponse({'error':'Corpo muito grande'},413)
        except ValueError:return JSONResponse({'error':'Tamanho inválido'},400)
        # Bound requests even if chunked. Never buffer arbitrary client bodies.
        raw=bytearray()
        async for chunk in req.stream():
            raw.extend(chunk)
            if len(raw)>32768:return JSONResponse({'error':'Corpo muito grande'},413)
        req._body=bytes(raw)
        if req.url.path in ('/consent','/register','/token'):
            slot=(req.client.host if req.client else 'unknown',int(time.time()/60))
            attempts[slot]=attempts.get(slot,0)+1
            for k in list(attempts):
                if k[1]<slot[1]-1:attempts.pop(k,None)
            if attempts[slot]>40:return JSONResponse({'error':'Aguarde um minuto'},429)
        res=await call_next(req)
        res.headers['Cache-Control']='no-store';res.headers['X-Content-Type-Options']='nosniff';res.headers['Referrer-Policy']='no-referrer'
        res.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        return res
    def auth(req,secret):
        if not secrets.compare_digest(req.headers.get('authorization',''),'Bearer '+secret):raise HTTPException(401,'Acesso não autorizado')
    @app.get('/health')
    def health():return {'service':'sym-bridge','version':'0.2.0'}
    @app.get('/panel')
    def panel():return HTMLResponse((Path(__file__).parent/'panel.html').read_text())
    @app.get('/panel.js')
    def panel_js():return HTMLResponse((Path(__file__).parent/'panel.js').read_text(),media_type='application/javascript')
    @app.get('/panel.css')
    def panel_css():return HTMLResponse((Path(__file__).parent/'panel.css').read_text(),media_type='text/css')
    @app.get('/admin/status')
    def status(req:Request):auth(req,owner);return db.status()
    @app.get('/admin/audit')
    def audit(req:Request):auth(req,owner);return db.audit()
    @app.patch('/admin/config')
    def update(req:Request,change:Change):
        auth(req,owner)
        try:return db.update(change.expected_revision,change.changes(),'panel')
        except ValueError as e:raise HTTPException(409,str(e))
    @app.post('/device/sync')
    def sync(req:Request,heartbeat:Heartbeat):
        auth(req,device_token);db.heartbeat(heartbeat.model_dump());return db.config()
    @app.get('/consent')
    def consent(req:Request,ticket:str):
        provider.clean();pending=provider.pending.get(ticket)
        if not pending:raise HTTPException(400,'Pedido expirado')
        csrf=secrets.token_urlsafe(32);pending['csrf']=csrf
        callback=html.escape(str(pending['params'].redirect_uri)); name=html.escape(pending['client'].client_name or 'Cliente MCP')
        response=HTMLResponse(f'''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/panel.css"><title>Autorizar SYM</title><main><h1>Conectar SYM CONCEPT</h1><p>Cliente: {name}</p><p>Permite consultar estado e alterar conhecimento, catálogo e pausa. Não permite ler conversas, ativar IA ou liberar contatos.</p><p>Retorno: {callback}</p><form method="post" action="/consent"><input type="hidden" name="ticket" value="{html.escape(ticket)}"><input type="hidden" name="csrf" value="{csrf}"><label>Segredo do proprietário<input name="owner" type="password" required autocomplete="off"></label><button>Autorizar acesso</button></form></main></html>''')
        response.set_cookie('sym_consent',csrf,max_age=300,httponly=True,secure=base.startswith('https:'),samesite='lax')
        return response
    @app.post('/consent')
    async def approve(req:Request):
        form=await req.form();ticket=str(form.get('ticket',''));pending=provider.pending.get(ticket)
        if not pending or pending['until']<time.time():raise HTTPException(400,'Pedido expirado')
        csrf=str(form.get('csrf',''))
        if not pending['csrf'] or not secrets.compare_digest(csrf,pending['csrf']) or not secrets.compare_digest(req.cookies.get('sym_consent',''),csrf):raise HTTPException(403,'Consentimento inválido')
        if not secrets.compare_digest(str(form.get('owner','')),owner):raise HTTPException(403,'Segredo inválido')
        provider.pending.pop(ticket,None);params=pending['params'];client=pending['client']
        code=secrets.token_urlsafe(32)
        provider.codes[digest(code)]=AuthorizationCode(code=code,client_id=client.client_id,scopes=params.scopes or ['sym:manage'],expires_at=time.time()+120,code_challenge=params.code_challenge,redirect_uri=params.redirect_uri,redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,resource=params.resource,subject='sidney')
        # Do not put credentials in redirects. Only short-lived code and original OAuth state.
        target=str(params.redirect_uri);query={'code':code}
        if params.state is not None:query['state']=params.state
        from fastapi.responses import RedirectResponse
        response=RedirectResponse(target+('&' if '?' in target else '?')+urlencode(query),status_code=303)
        response.delete_cookie('sym_consent');return response
    # OAuth routes and /mcp are owned by the official MCP SDK.
    app.mount('/',mcp_app)
    return app

if __name__=='__main__':
    import uvicorn
    uvicorn.run(create_app(),host=os.environ.get('SYM_BIND','127.0.0.1'),port=int(os.environ.get('PORT','8765')),access_log=False)
