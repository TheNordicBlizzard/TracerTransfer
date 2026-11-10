"""Self-contained panel: credentials stay in memory; data is rendered as text."""
HTML = r'''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>TracerTransfer · Painel operacional</title>
<style>
:root{color-scheme:dark;--bg:#101416;--surface:#181e21;--line:#35413e;--ink:#edf2f1;--muted:#a5b5b0;--accent:#58d6aa;--warning:#f2cc8f}*{box-sizing:border-box}html{scroll-padding-top:24px}body{font:14px/1.5 "Segoe UI",system-ui,sans-serif;margin:0;background:var(--bg);color:var(--ink)}body>header,body>main{max-width:1440px;margin:auto;padding:20px 32px}body>header{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);gap:16px}h1{font-size:21px;letter-spacing:-.5px;margin:0}h2{font-size:18px;margin:0 0 12px;font-weight:600}p{margin:8px 0 16px}small,.hint,label{color:var(--muted)}header .hint{margin:2px 0 0;font-size:12px}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:var(--accent)}section{min-width:0;scroll-margin-top:24px;margin:24px 0;padding:24px;background:var(--surface);border:1px solid var(--line);border-radius:6px}#login{max-width:440px;margin:56px auto}label{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:500;min-width:0}input,select,button{font:inherit;border-radius:4px;min-height:40px;max-width:100%;padding:8px 12px;border:1px solid var(--line)}input,select{background:var(--bg);color:var(--ink);width:100%}button{cursor:pointer;background:var(--accent);color:#101416;font-weight:600;border-color:var(--accent)}button:hover{filter:brightness(1.08)}button:disabled{cursor:wait;opacity:.65}button.secondary,td button{background:transparent;color:var(--ink);border-color:#53645e}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,.scroll:focus-visible{outline:2px solid var(--accent);outline-offset:3px}form{display:flex;align-items:end;flex-wrap:wrap;gap:12px;margin:16px 0 24px}form label{flex:1 1 180px}#loginform{display:grid;gap:16px;margin-bottom:0}#loginform button{width:100%}.toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.actions{display:flex;gap:8px;flex-wrap:wrap}.navigation{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding:8px 0 16px;margin-top:8px}.navigation a{color:var(--muted);text-decoration:none;padding:8px 12px;border-radius:4px}.navigation a:hover,.navigation a:focus-visible{color:var(--ink);background:var(--surface)}.scroll{width:100%;max-width:100%;overflow-x:auto;overscroll-behavior-x:contain}table{width:100%;border-collapse:collapse;font-size:13px;min-width:660px}th,td{text-align:left;padding:12px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere;max-width:340px}th{color:var(--muted);font-weight:600;background:#141a1c;white-space:nowrap}tbody tr:hover{background:#1d2527}td input{width:180px}td select{width:130px}td button{margin-top:8px;white-space:nowrap}td small{font-size:11px}td:last-child{min-width:100px}.stale{color:var(--warning)}code{overflow-wrap:anywhere;font-family:Consolas,monospace}#message{color:var(--warning);overflow-wrap:anywhere;margin:0}#message:not(:empty){padding:12px 16px;margin:16px 0;border:1px solid #796344;border-radius:4px;background:#28251e}#syncstatus{color:var(--muted);font-size:12px}#statussummary{font-size:15px;font-variant-numeric:tabular-nums;padding:12px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}#maintenanceinfo{line-height:1.9;margin-bottom:0}#devicecredential:not(:empty){padding:16px;border:1px solid var(--accent);border-radius:4px;overflow-wrap:anywhere;font-family:Consolas,monospace;user-select:all;color:var(--accent)}.skip{position:absolute;left:16px;top:-100px;color:var(--bg);background:var(--accent);padding:8px;z-index:5}.skip:focus{top:8px}[hidden]{display:none!important}@media(max-width:720px){body>header,body>main{padding:16px}body>header{align-items:start}section{padding:16px;margin:16px 0}form{display:grid;grid-template-columns:minmax(0,1fr)}form button{width:100%}input,select,button,.navigation a{min-height:44px}.navigation{gap:0}.navigation a{padding:10px}h1{font-size:19px}#login{margin:24px auto}.toolbar{align-items:start}.scroll{border:1px solid var(--line)}th,td{padding:10px}#syncstatus{flex-basis:100%}}
.status-badge{display:inline-flex;padding:3px 8px;border:1px solid currentColor;border-radius:4px;white-space:nowrap;font-size:12px}.status-pending,.status-conflict,.status-uncertain{color:var(--warning)}.status-delivered{color:var(--accent)}#deliverysummary{color:var(--muted)}
</style>
<a class="skip" href="#content">Ir para o conteúdo</a><header><div><h1>TracerTransfer</h1><p class="hint">Painel operacional · transferências e entregas</p></div><span class="eyebrow">Servidor</span></header><main id="content">
<p id="message" role="status" aria-live="polite"></p>
<section id="login" aria-labelledby="logintitle"><h2 id="logintitle">Entrar no painel</h2><p class="hint">Use sua conta de operador ou administrador.</p><form id="loginform"><label for="username">Usuário<input id="username" autocomplete="username" required></label><label for="password">Senha<input id="password" type="password" autocomplete="current-password" required></label><button id="loginbutton">Entrar</button></form></section>
<div id="panel" hidden><div class="toolbar"><span id="identity"></span><div class="actions"><button id="refresh" class="secondary">Atualizar agora</button><button id="logout" class="secondary">Sair e limpar painel</button></div><span id="syncstatus" role="status" aria-live="polite">Aguardando atualização.</span></div>
<nav class="navigation" aria-label="Seções do painel"><a href="#overview">Visão geral</a><a href="#devicessection">Dispositivos</a><a id="fileslink" href="#filessection">Arquivos recebidos</a><span id="adminnav" hidden><a href="#userssection">Usuários</a><a href="#activationsection">Ativações</a><a href="#deliverysection">Entregas</a></span></nav>
<section id="overview" aria-labelledby="overviewtitle"><h2 id="overviewtitle">Resumo operacional</h2><p id="statussummary" role="status">Aguardando dados do servidor.</p><p id="maintenanceinfo">Ainda sem verificação de manutenção.</p></section>
<section id="devicessection" aria-labelledby="devicestitle"><h2 id="devicestitle">Dispositivos</h2><p class="hint">Contato recente não comprova conexão ativa. Sem heartbeat há mais de 45 s: desatualizado, não necessariamente desconectado.</p><div class="scroll" id="devices" tabindex="0" role="region" aria-label="Tabela de dispositivos"></div></section>
<section id="filessection" aria-labelledby="filestitle"><h2 id="filestitle">Arquivos recebidos</h2><p class="hint" id="fileshint">Últimos 1.000 registros, do mais recente ao mais antigo. Em telas pequenas, deslize a tabela para consultar todas as colunas.</p><div class="scroll" id="files" tabindex="0" role="region" aria-label="Tabela de arquivos recebidos"></div></section>
<div id="admin" hidden><section id="userssection" aria-labelledby="userstitle"><h2 id="userstitle">Usuários e perfis</h2><p class="hint">Admin gerencia o servidor; operador consulta dispositivos e arquivos. Alterações revogam as sessões do usuário.</p><form id="userform"><label for="newuser">Novo usuário<input id="newuser" autocomplete="off" required></label><label for="newpassword">Senha · mínimo de 12 caracteres<input id="newpassword" type="password" autocomplete="new-password" minlength="12" maxlength="1024" required></label><label for="role">Perfil<select id="role"><option value="operator">Operador</option><option value="admin">Admin</option></select></label><button id="userbutton">Criar usuário</button></form><div class="scroll" id="users" tabindex="0" role="region" aria-label="Tabela de usuários"></div></section>
<section id="activationsection" aria-labelledby="activationtitle"><h2 id="activationtitle">Códigos de ativação</h2><form id="deviceform"><label for="devicename">Nome do dispositivo<input id="devicename" required maxlength="120"></label><label for="activationrole">Papel do dispositivo<select id="activationrole"><option value="sender">Remetente</option><option value="delivery">Entregador</option></select></label><label for="activationhours">Validade em horas<input id="activationhours" type="number" min="1" max="720" value="24" required></label><button id="devicebutton">Gerar código</button></form><p class="hint">Copie o código agora: exibição única. Nenhuma senha administrativa deve ser usada no cliente. Sair limpa o código.</p><p id="devicecredential" role="status" aria-live="polite"></p><div class="scroll" id="activations" tabindex="0" role="region" aria-label="Tabela de códigos de ativação"></div></section>
<section id="deliverysection" aria-labelledby="deliverytitle"><h2 id="deliverytitle">Fila e entregas</h2><p class="hint">Recebido na fila NÃO significa entregue ao FreeForm. Conflitos e estados incertos não são repetidos automaticamente. Últimos 1.000 recibos.</p><p id="deliverysummary" role="status"></p><div class="scroll" id="deliveries" tabindex="0" role="region" aria-label="Tabela de entregas"></div></section></div></div></main>
<script>
'use strict';
let token='',isAdmin=false,sessionVersion=0,refreshSequence=0,usersSnapshot='',refreshError='';
const $=id=>document.getElementById(id);
function message(e){if(!e.stale)$('message').textContent=e.message||String(e)}
function clearSession(){
 token='';isAdmin=false;sessionVersion++;refreshSequence++;usersSnapshot='';refreshError='';
 $('panel').hidden=true;$('admin').hidden=true;$('login').hidden=false;
 for(const id of ['users','devices','files','devicecredential','statussummary','activations','deliveries','deliverysummary','maintenanceinfo','identity','syncstatus'])$(id).replaceChildren();
 $('adminnav').hidden=true;$('refresh').disabled=false;
 for(const id of ['username','password','newuser','newpassword','devicename'])$(id).value='';
 $('role').value='operator';$('activationrole').value='sender';$('activationhours').value='24';$('message').textContent='';
}
function staleResponse(){const error=Error('Sessão alterada');error.stale=true;return error}
async function api(path,method='GET',body){
 const version=sessionVersion;
 const r=await fetch(path,{method,headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
 if(version!==sessionVersion)throw staleResponse();
 if(r.status===401){clearSession();throw Error('Sessão inválida ou credenciais incorretas. Entre novamente.')}
 const j=await r.json();
 if(version!==sessionVersion)throw staleResponse();
 if(!r.ok)throw Error(typeof j.detail==='string'?j.detail:'Não foi possível concluir a operação.');
 return j;
}
function element(tag,text){const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);return node}
function button(label,action){const node=element('button',label);node.type='button';node.onclick=async()=>{if(node.disabled)return;node.disabled=true;const previousMessage=$('message').textContent;try{await action();if(token && $('message').textContent===previousMessage)$('message').textContent='Operação concluída.'}catch(e){message(e)}finally{node.disabled=false}};return node}
function table(id,headers,rows){
 const grid=element('table'),head=element('thead'),heading=element('tr'),body=element('tbody');
 grid.setAttribute('aria-label',headers.join(' · '));
 for(const title of headers){const cell=element('th',title);cell.scope='col';heading.append(cell)}
 head.append(heading);grid.append(head,body);
 for(const values of rows){const row=element('tr');for(const value of values){const cell=element('td');if(value!==null && typeof value==='object')cell.append(value);else cell.textContent=String(value??'—');row.append(cell)}body.append(row)}
 if(!rows.length){const row=element('tr'),cell=element('td','Nenhum registro.');cell.colSpan=headers.length;row.append(cell);body.append(row)}
 $(id).replaceChildren(grid);
}
function timestamp(value){if(!value)return '—';const date=new Date(value);return Number.isNaN(date.getTime())?'Data inválida':date.toLocaleString('pt-BR')}
function contact(device){
 const age=Date.now()-Date.parse(device.last_seen);
 if(!device.enabled)return element('span','Revogado');
 if(!device.last_seen || !Number.isFinite(age))return element('span','Sem contato registrado');
 if(age<0)return element('span','Horário de contato inconsistente');
 const label=element('span',age>45000?'Desatualizado (>45 s)':'Contato recente (≤45 s)');
 if(age>45000)label.className='stale';
 return label;
}
function renderStatus(data){
 const m=data.maintenance||{};
 const state=v=>v===true?'OK':v===false?'Falha':'Não verificado';
 $('maintenanceinfo').textContent='Aplicação: '+state(m.health_ok)+' · Espaço: '+state(m.space_ok)+' · Backup: '+state(m.backup_ok)+' · Último backup: '+timestamp(m.last_backup_at)+' · Verificação: '+timestamp(m.checked_at)+(m.backup_error?' · Erro: '+m.backup_error:'');
 const devices=data.devices||[],files=data.files||[];
 const queued=data.delivery_mode==='queued';
 $('filestitle').textContent=queued?'Histórico de recebimentos diretos':'Arquivos recebidos';
 $('fileslink').textContent=queued?'Histórico':'Arquivos recebidos';
 $('fileshint').textContent=(queued?'Registros do receptor direto anterior. Novos recebimentos estão em Fila e entregas, disponível à administração. ':'')+'Últimos 1.000 registros, do mais recente ao mais antigo. Deslize a tabela para consultar todas as colunas.';
 $('statussummary').textContent=devices.length+' dispositivos · '+devices.reduce((sum,d)=>sum+(Number(d.pending)||0),0)+' pendências declaradas pelos dispositivos · '+files.length+' recebimentos diretos listados'+(queued&&isAdmin?' · '+(data.deliveries||[]).length+' recibos do servidor listados':'');
 table('devices',['Dispositivo / ID','Situação do contato','Último contato','Pendentes','Último erro',...(isAdmin?['Ações']:[])],devices.map(d=>{
   const identity=element('div',d.name);identity.append(element('br'),element('small',d.id+' · '+deviceRole(d.role||'sender')));
   const row=[identity,contact(d),timestamp(d.last_seen),d.pending,d.error||'—'];
   if(isAdmin)row.push(d.enabled?button('Revogar',async()=>{await api('/api/devices/'+encodeURIComponent(d.id),'DELETE');await refresh()}):'Revogado');
   return row;
 }));
 const names=new Map(devices.map(d=>[d.id,d.name]));
 table('files',['Arquivo','Dispositivo','Tamanho (bytes)','Recebido em','SHA-256'],files.map(f=>[f.name,names.get(f.device_id)||f.device_id,f.size,timestamp(f.received_at),f.sha256]));
}
function renderUsers(users){
 // Reuse unchanged controls: polling must not discard a typed password, role or focus.
 const snapshot=JSON.stringify(users);if(snapshot===usersSnapshot)return;usersSnapshot=snapshot;
 table('users',['Usuário','Perfil','Situação','Alterar perfil','Redefinir senha','Acesso'],users.map(user=>{
   const path='/api/users/'+encodeURIComponent(user.username),roles=element('select');
   roles.setAttribute('aria-label','Perfil de '+user.username);
   for(const [value,label] of [['operator','Operador'],['admin','Admin']]){const option=element('option',label);option.value=value;roles.append(option)}
   roles.value=user.role;
   const roleControl=element('div');roleControl.append(roles,button('Salvar perfil',async()=>{await api(path,'PATCH',{role:roles.value});usersSnapshot='';await refresh()}));
   const password=element('input');password.type='password';password.minLength=12;password.maxLength=1024;password.autocomplete='new-password';password.placeholder='Nova senha (mín. 12)';password.setAttribute('aria-label','Nova senha de '+user.username);
   const reset=element('div');reset.append(password,button('Redefinir senha',async()=>{const secret=password.value;password.value='';if(secret.length<12 || secret.length>1024)throw Error('Senha deve ter de 12 a 1024 caracteres.');await api(path,'PATCH',{password:secret});await refresh()}));
   return [user.username,user.role==='admin'?'Admin':'Operador',user.enabled?'Habilitado':'Desabilitado',roleControl,reset,button(user.enabled?'Desativar':'Ativar',async()=>{await api(path,'PATCH',{enabled:!user.enabled});await refresh()})];
 }));
}
async function refresh(){
 if(!token)return;
 const version=sessionVersion,sequence=++refreshSequence;
 $('syncstatus').textContent='Atualizando dados…';$('refresh').disabled=true;
 try{
   const me=await api('/api/me'),data=await api('/api/status');
   const users=me.role==='admin'?await api('/api/users'):[];
   const activations=me.role==='admin'?await api('/api/activations'):[];
   if(version!==sessionVersion || sequence!==refreshSequence || !token)return;
   isAdmin=me.role==='admin';$('admin').hidden=!isAdmin;$('adminnav').hidden=!isAdmin;
   $('identity').textContent=(me.username||'Conta autenticada')+' · '+(isAdmin?'Administrador':'Operador');
   if(!isAdmin){for(const id of ['users','devicecredential','activations','deliveries','deliverysummary'])$(id).replaceChildren();$('newpassword').value='';usersSnapshot=''}
   renderStatus(data);if(isAdmin){renderUsers(users);renderDistribution(activations,data.deliveries||[])}
   if(refreshError && $('message').textContent===refreshError)$('message').textContent='';refreshError='';
   $('syncstatus').textContent='Atualizado em '+new Date().toLocaleTimeString('pt-BR')+' · consulta automática a cada 15 s';
 }catch(e){if(sequence===refreshSequence && version===sessionVersion){message(e);if(token){refreshError=e.message||String(e);$('syncstatus').textContent='Falha na atualização. Os dados exibidos podem estar desatualizados.'}}else if(!e.stale && !token)message(e)}
 finally{if(version===sessionVersion && sequence===refreshSequence)$('refresh').disabled=false}
}
$('loginform').onsubmit=async e=>{
 e.preventDefault();if($('loginbutton').disabled)return;const version=sessionVersion;$('loginbutton').disabled=true;$('message').textContent='Autenticando…';
 try{
   const username=$('username').value,password=$('password').value;$('password').value='';
   const j=await api('/api/login','POST',{username,password});
   if(version!==sessionVersion)return;
   token=j.token;isAdmin=j.role==='admin';sessionVersion++;
   $('login').hidden=true;$('panel').hidden=false;$('admin').hidden=!isAdmin;$('message').textContent='';
   await refresh();
 }catch(e){message(e)}finally{$('loginbutton').disabled=false}
};
$('userform').onsubmit=async e=>{e.preventDefault();if($('userbutton').disabled)return;$('userbutton').disabled=true;const password=$('newpassword').value;$('newpassword').value='';$('message').textContent='Criando usuário…';try{await api('/api/users','POST',{username:$('newuser').value,password,role:$('role').value});$('message').textContent='Usuário criado.';await refresh()}catch(e){message(e)}finally{$('userbutton').disabled=false}};
$('deviceform').onsubmit=async e=>{e.preventDefault();if($('devicebutton').disabled)return;$('devicebutton').disabled=true;$('devicecredential').textContent='';$('message').textContent='Gerando código…';try{const j=await api('/api/activations','POST',{name:$('devicename').value,role:$('activationrole').value,expires_hours:Number($('activationhours').value)});$('devicecredential').textContent=j.code+' · '+deviceRole(j.role)+' · expira '+timestamp(j.expires_at);$('message').textContent='Código gerado. Copie antes de sair.';await refresh()}catch(e){message(e)}finally{$('devicebutton').disabled=false}};
function deviceRole(role){return ({sender:'Remetente',delivery:'Entregador'})[role]||role}
function deliveryState(state){return ({pending:'Pendente',delivered:'Entregue',conflict:'Conflito',uncertain:'Incerto'})[state]||state}
function deliveryBadge(state){const badge=element('span',deliveryState(state));badge.className='status-badge'+(['pending','delivered','conflict','uncertain'].includes(state)?' status-'+state:'');return badge}
function renderDistribution(activations,deliveries){
 table('activations',['Nome / ID','Papel','Validade','Situação','Ação'],activations.map(a=>[a.name+' / '+a.id,deviceRole(a.role),timestamp(a.expires_at),a.device_id?'Usado':a.revoked?'Revogado':Date.parse(a.expires_at)<=Date.now()?'Expirado':'Disponível',!a.device_id&&!a.revoked?button('Revogar código',async()=>{await api('/api/activations/'+encodeURIComponent(a.id),'DELETE');await refresh()}):'—']));
 $('deliverysummary').textContent=['pending','delivered','conflict','uncertain'].map(state=>deliveries.filter(d=>d.status===state).length+' '+deliveryState(state).toLocaleLowerCase('pt-BR')).join(' · ')+' — contagens dos recibos listados, não totais históricos.';
 table('deliveries',['Recibo','Arquivo','Remetente','Estado','Erro','Reserva até'],deliveries.map(d=>[d.receipt_id,d.name,d.device_id,deliveryBadge(d.status),d.error||'—',timestamp(d.lease_until)]));
}
function logout(){
 const credential=token;clearSession();
 // Best effort revocation: UI and pending requests are invalidated immediately.
 if(credential){try{fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+credential},cache:'no-store'}).catch(()=>{})}catch(_){}}
}
$('refresh').onclick=refresh;$('logout').onclick=logout;
setInterval(()=>{if(token)refresh()},15000);
</script></html>'''

