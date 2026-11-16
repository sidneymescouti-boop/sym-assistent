from concurrent.futures import ThreadPoolExecutor
import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from ai_gateway import attach_ai, gemini_reply, Generate
from server import Database

class AiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'sym.db')
        self.seen = []
        self.settings = {'SYM_AI_KEY':'server-secret-not-in-app', 'SYM_AI_MODEL':'test-model',
            'SYM_AI_ENROLLMENT_OPEN':'true', 'SYM_AI_DAILY_LIMIT':'2', 'SYM_AI_GLOBAL_DAILY_LIMIT':'3'}
        async def provider(key, model, payload):
            self.seen.append(payload.model_dump())
            return 'Assistente da '+payload.profile.name+': '+payload.profile.knowledge
        self.provider = provider
        self.client = self.make()
        self.a, self.b = 'a'*43, 'b'*43
    def make(self):
        app=FastAPI();attach_ai(app,self.db,'owner-secret',settings=self.settings,generate=self.provider)
        return TestClient(app)
    def tearDown(self):self.client.close();self.tmp.cleanup()
    def auth(self,token):return {'Authorization':'Bearer '+token}
    def enroll(self,token):return self.client.post('/ai/enroll',headers=self.auth(token),json={})
    def payload(self,shop='Aurora',index=1):
        return {'request_id':f'request-{index:016d}', 'message':'Como pago?',
            'profile':{'name':shop,'knowledge':'Pix '+shop,'catalogue':'https://example.com/'+shop}}
    def reply(self,token,shop='Aurora',index=1):
        return self.client.post('/ai/reply',headers=self.auth(token),json=self.payload(shop,index))
    def test_missing_auth_and_unknown_installation(self):
        self.assertEqual(self.client.post('/ai/enroll').status_code,401)
        self.assertEqual(self.reply(self.a).status_code,401)
        self.assertFalse(self.seen)
    def test_enroll_is_idempotent_and_only_hash_persisted(self):
        first=self.enroll(self.a);self.assertEqual(first.status_code,200)
        self.assertEqual(first.json(),self.enroll(self.a).json())
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM ai_installations').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT id FROM ai_installations').fetchone()[0],hashlib.sha256(self.a.encode()).hexdigest())
    def test_independent_shops_and_no_message_storage(self):
        for t in (self.a,self.b):self.enroll(t)
        a=self.reply(self.a,'Aurora').json()['reply'];b=self.reply(self.b,'Bruno').json()['reply']
        self.assertNotIn('Bruno',a);self.assertNotIn('Aurora',b)
        self.assertEqual(len(self.seen),2)
        with self.db.connect() as c:
            content=''.join(str(tuple(row)) for table in ('ai_installations','ai_requests','ai_usage') for row in c.execute('SELECT * FROM '+table))
        self.assertNotIn('Aurora',content);self.assertNotIn('Como pago',content)
    def test_daily_quota_survives_restart(self):
        self.enroll(self.a)
        self.assertEqual(self.reply(self.a,index=1).status_code,200)
        self.assertEqual(self.reply(self.a,index=2).status_code,200)
        self.client.close();self.client=self.make()
        self.assertEqual(self.reply(self.a,index=3).status_code,429)
        self.assertEqual(len(self.seen),2)
    def test_global_quota(self):
        for t in (self.a,self.b):self.enroll(t)
        for t,i in ((self.a,1),(self.a,2),(self.b,1)):
            self.assertEqual(self.reply(t,index=i).status_code,200)
        self.assertEqual(self.reply(self.b,index=2).status_code,429)
        self.assertEqual(len(self.seen),3)
    def test_repeated_request_not_billed_twice(self):
        self.enroll(self.a);self.reply(self.a)
        self.assertEqual(self.reply(self.a).status_code,409)
        self.assertEqual(len(self.seen),1)
    def test_revocation_requires_owner_and_cannot_reenroll(self):
        ident=self.enroll(self.a).json()['installation_id'];data={'installation_id':ident}
        self.assertEqual(self.client.post('/admin/ai/revoke',json=data,headers=self.auth(self.a)).status_code,401)
        self.assertEqual(self.client.post('/admin/ai/revoke',json=data,headers=self.auth('owner-secret')).status_code,200)
        self.assertEqual(self.enroll(self.a).status_code,403)
        self.assertEqual(self.reply(self.a).status_code,403)
    def test_oversized_payload_and_extra_fields(self):
        self.enroll(self.a);p=self.payload();p['message']='x'*4001
        self.assertEqual(self.client.post('/ai/reply',headers=self.auth(self.a),json=p).status_code,422)
        p=self.payload();p['model']='attacker'
        self.assertEqual(self.client.post('/ai/reply',headers=self.auth(self.a),json=p).status_code,422)
        self.assertFalse(self.seen)
    def test_closed_enrollment(self):
        self.settings['SYM_AI_ENROLLMENT_OPEN']='false';self.client.close();self.client=self.make()
        self.assertEqual(self.enroll(self.a).status_code,403)
    def test_provider_failure_consumes_reservation_and_returns_generic_error(self):
        async def broken(*args):raise httpx.ReadTimeout('secret debug')
        self.provider=broken;self.client.close();self.client=self.make();self.enroll(self.a)
        r=self.reply(self.a);self.assertEqual(r.status_code,503);self.assertNotIn('secret',r.text)
        self.assertEqual(self.reply(self.a).status_code,409)
    def test_missing_configuration_fails_closed(self):
        self.settings['SYM_AI_KEY']='';self.client.close();self.client=self.make()
        self.assertEqual(self.enroll(self.a).status_code,503)
    def test_concurrent_reservations_do_not_exceed_global_budget(self):
        for t in (self.a,self.b):self.enroll(t)
        def call(i):return self.reply(self.a if i%2 else self.b,index=i+1).status_code
        with ThreadPoolExecutor(max_workers=6) as pool:codes=list(pool.map(call,range(8)))
        self.assertEqual(codes.count(200),3)
        self.assertTrue(all(code in (200,429) for code in codes))
        self.assertEqual(len(self.seen),3)
    def test_gateway_routes_before_mcp_mount(self):
        from server import create_app
        with patch.dict('os.environ',self.settings):
            app=create_app(path=Path(self.tmp.name)/'integrated.db',base='http://127.0.0.1:8765',owner='o'*40,device_token='d'*40,callbacks=[])
        with TestClient(app,base_url='http://127.0.0.1:8765') as client:
            response=client.post('/ai/enroll',headers=self.auth(self.a),json={})
            self.assertEqual(response.status_code,200)
            response=client.post('/ai/reply',headers=self.auth(self.a),json={})
            self.assertEqual(response.status_code,422)
    def test_provider_contract_and_blocked_response(self):
        real_client=httpx.AsyncClient;captured=[]
        async def handler(req):
            captured.append(req)
            return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'segredo de raciocínio','thought':True},{'text':'Olá Aurora'}]}}]})
        def factory(**kwargs):return real_client(transport=httpx.MockTransport(handler),**kwargs)
        with patch('ai_gateway.httpx.AsyncClient',factory):
            answer=asyncio.run(gemini_reply('private-key','test-model',Generate(**self.payload())))
        self.assertEqual(answer,'Olá Aurora')
        self.assertEqual(captured[0].headers['x-goog-api-key'],'private-key')
        body=json.loads(captured[0].content)
        self.assertIn('systemInstruction',body)
        self.assertNotIn('private-key',captured[0].url.query.decode())
        async def blocked(req):return httpx.Response(200,json={'candidates':[{'finishReason':'SAFETY','content':{'parts':[{'text':'blocked'}]}}]})
        def blocked_factory(**kwargs):return real_client(transport=httpx.MockTransport(blocked),**kwargs)
        with patch('ai_gateway.httpx.AsyncClient',blocked_factory):
            with self.assertRaises(HTTPException):asyncio.run(gemini_reply('key','test-model',Generate(**self.payload())))

if __name__=='__main__':unittest.main()
