import base64, hashlib, tempfile, unittest
from urllib.parse import urlparse,parse_qs
from fastapi.testclient import TestClient
from server import create_app
OWNER='owner-test-'+'x'*40
DEVICE='device-test-'+'y'*40
CALLBACK='https://chatgpt.com/connector/oauth/sym-test'
BASE='http://127.0.0.1:8765'

class BridgeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.app=create_app(path=self.tmp.name+'/state.db',base=BASE,owner=OWNER,device_token=DEVICE,callbacks=[CALLBACK])
  self.client=TestClient(self.app,base_url=BASE);self.client.__enter__()
 def tearDown(self):self.client.__exit__(None,None,None);self.tmp.cleanup()
 def owner(self):return {'Authorization':'Bearer '+OWNER}
 def device(self):return {'Authorization':'Bearer '+DEVICE}
 def test_role_separation(self):
  self.assertEqual(self.client.get('/admin/status').status_code,401)
  self.assertEqual(self.client.get('/admin/status',headers=self.device()).status_code,401)
  self.assertEqual(self.client.get('/admin/status',headers=self.owner()).status_code,200)
 def test_sync_no_messages(self):
  payload={'app_version':'0.2.0','local_enabled':False,'notification_access':True,'ai_enabled':False,'replies_today':4,'applied_revision':0}
  self.assertEqual(self.client.post('/device/sync',headers=self.owner(),json=payload).status_code,401)
  r=self.client.post('/device/sync',headers=self.device(),json=payload)
  self.assertEqual(r.status_code,200);self.assertTrue(r.json()['paused'])
  state=self.client.get('/admin/status',headers=self.owner()).json();self.assertTrue(state['online_recently']);self.assertEqual(state['device']['replies_today'],4)
  payload['message']='sensitive'
  self.assertEqual(self.client.post('/device/sync',headers=self.device(),json=payload).status_code,422)
 def test_revision_and_validation(self):
  r=self.client.patch('/admin/config',headers=self.owner(),json={'expected_revision':1,'knowledge':'Loja de teste','paused':False})
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['revision'],2)
  self.assertEqual(self.client.patch('/admin/config',headers=self.owner(),json={'expected_revision':1,'paused':True}).status_code,409)
  self.assertEqual(self.client.patch('/admin/config',headers=self.owner(),json={'expected_revision':2,'catalogue':'javascript:alert(1)'}).status_code,409)
  self.assertEqual(self.client.patch('/admin/config',headers=self.owner(),json={'expected_revision':2,'contacts':'any'}).status_code,422)
  self.assertEqual(len(self.client.get('/admin/audit',headers=self.owner()).json()),1)
 def test_web_boundaries(self):
  self.assertEqual(self.client.get('/health',headers={'Host':'evil.example'}).status_code,400)
  self.assertEqual(self.client.get('/admin/status',headers={**self.owner(),'Origin':'https://evil.example'}).status_code,403)
  r=self.client.get('/panel');self.assertEqual(r.status_code,200);self.assertIn("frame-ancestors 'none'",r.headers['content-security-policy'])
  self.assertEqual(self.client.post('/device/sync',headers=self.device(),content='x'*33000).status_code,413)
 def oauth(self):
  r=self.client.post('/register',json={'client_name':'SYM test','redirect_uris':[CALLBACK],'grant_types':['authorization_code','refresh_token'],'response_types':['code'],'token_endpoint_auth_method':'none','scope':'sym:manage'})
  self.assertEqual(r.status_code,201,r.text);client=r.json()['client_id']
  verifier='v'*64;challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
  r=self.client.get('/authorize',params={'response_type':'code','client_id':client,'redirect_uri':CALLBACK,'scope':'sym:manage','state':'original-state','resource':BASE+'/mcp','code_challenge':challenge,'code_challenge_method':'S256'},follow_redirects=False)
  self.assertIn(r.status_code,[302,307],r.text)
  ticket=parse_qs(urlparse(r.headers['location']).query)['ticket'][0]
  self.assertEqual(self.client.get('/consent',params={'ticket':ticket}).status_code,200)
  csrf=self.client.cookies.get('sym_consent')
  self.assertEqual(self.client.post('/consent',data={'ticket':ticket,'csrf':'wrong','owner':OWNER}).status_code,403)
  self.assertEqual(self.client.post('/consent',data={'ticket':ticket,'csrf':csrf,'owner':'wrong'}).status_code,403)
  r=self.client.post('/consent',data={'ticket':ticket,'csrf':csrf,'owner':OWNER},follow_redirects=False)
  self.assertEqual(r.status_code,303,r.text)
  params=parse_qs(urlparse(r.headers['location']).query);self.assertEqual(params['state'][0],'original-state')
  data={'grant_type':'authorization_code','code':params['code'][0],'client_id':client,'code_verifier':verifier,'redirect_uri':CALLBACK,'resource':BASE+'/mcp'}
  bad=dict(data,code_verifier='bad-verifier-'+'q'*50)
  self.assertNotEqual(self.client.post('/token',data=bad).status_code,200)
  r=self.client.post('/token',data=data);self.assertEqual(r.status_code,200,r.text)
  self.assertNotEqual(self.client.post('/token',data=data).status_code,200)
  return r.json(),client
 def test_oauth_and_mcp(self):
  self.assertEqual(self.client.post('/register',json={'redirect_uris':['https://evil.example/cb']}).status_code,400)
  tokens,client=self.oauth()
  headers={'Authorization':'Bearer '+tokens['access_token'],'Accept':'application/json, text/event-stream'}
  r=self.client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'tests','version':'1'}}})
  self.assertEqual(r.status_code,200,r.text)
  r=self.client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(len(r.json()['result']['tools']),3)
  r=self.client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'consultar_bot','arguments':{}}})
  self.assertEqual(r.status_code,200,r.text);self.assertNotIn('isError":true',r.text)
  r=self.client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'configurar_bot','arguments':{'expected_revision':1,'paused':False}}})
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.app.state.db.config()['revision'],2)
  r=self.client.post('/token',data={'grant_type':'refresh_token','refresh_token':tokens['refresh_token'],'client_id':client,'scope':'sym:manage','resource':BASE+'/mcp'})
  self.assertEqual(r.status_code,200,r.text)
  self.assertEqual(self.client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':5,'method':'tools/list'}).status_code,401)
 def test_unauthenticated_mcp_challenge(self):
  r=self.client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
  self.assertEqual(r.status_code,401);self.assertIn('resource_metadata',r.headers['www-authenticate'])

if __name__=='__main__':unittest.main(verbosity=2)
