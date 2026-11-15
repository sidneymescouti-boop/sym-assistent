'use strict';
let secret='',revision=0;
const el=id=>document.getElementById(id);
const message=text=>{el('message').textContent=text;};
async function api(path,options={}){
 const response=await fetch(path,{...options,headers:{'Authorization':'Bearer '+secret,'Content-Type':'application/json'}});
 if(!response.ok){const data=await response.json().catch(()=>({}));throw Error(response.status===401?'Chave inválida.':response.status===409?'A configuração mudou. Atualize o painel antes de salvar.':typeof data.detail==='string'?data.detail:'Não foi possível concluir.');}
 return response.json();
}
async function refresh(){
 const result=await api('/admin/status');const c=result.configuration;revision=c.revision;
 el('paused').checked=c.paused;el('catalogue').value=c.catalogue;el('knowledge').value=c.knowledge;
 el('revision').textContent='Versão desejada: '+revision;
 el('online').textContent=result.online_recently?'● Sincronização recente':'○ Sem sincronização recente';
 el('device').replaceChildren();const d=result.device;
 const entries=d?{'Último contato':new Date(d.received_at*1000).toLocaleString('pt-BR'),'Versão instalada':d.app_version,'Versão aplicada':d.applied_revision,'Bot local':d.local_enabled?'Ativado':'Pausado','Notificações':d.notification_access?'Permitidas':'Sem permissão','IA no celular':d.ai_enabled?'Ativada':'Desativada','Respostas hoje':d.replies_today}:{'Aparelho':'Ainda não pareado'};
 for(const [key,value]of Object.entries(entries)){let dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=String(value);el('device').append(dt,dd);}
 const records=await api('/admin/audit');el('audit').replaceChildren();
 for(const row of records){let li=document.createElement('li');li.textContent=new Date(row.at*1000).toLocaleString('pt-BR')+' • '+row.action+' • versão '+row.revision;el('audit').append(li);}
}
el('connect').onclick=async()=>{secret=el('secret').value.trim();try{await refresh();el('secret').value='';el('workspace').hidden=false;el('login').hidden=true;message('Conectado.');}catch(e){secret='';message(e.message);}};
el('refresh').onclick=async()=>{try{await refresh();message('Estado atualizado.');}catch(e){message(e.message);}};
el('save').onclick=async()=>{try{await api('/admin/config',{method:'PATCH',body:JSON.stringify({expected_revision:revision,paused:el('paused').checked,catalogue:el('catalogue').value.trim(),knowledge:el('knowledge').value})});await refresh();message('Salvo. Aguardando sincronização do aparelho.');}catch(e){message(e.message);}};
el('logout').onclick=()=>{secret='';revision=0;el('knowledge').value='';el('device').replaceChildren();el('audit').replaceChildren();el('workspace').hidden=true;el('login').hidden=false;message('Sessão encerrada neste navegador.');};
