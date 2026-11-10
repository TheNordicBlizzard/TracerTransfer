"""Dashboard layout contracts and executable synthetic-data interaction regressions."""
import json
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from server.dashboard import HTML


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.labels = []
        self.anchors = []
        self.controls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'label':
            self.labels.append(attrs.get('for'))
        if tag == 'a':
            self.anchors.append(attrs.get('href'))
        if tag in ('input', 'select'):
            self.controls.append(attrs['id'])


def test_semantic_navigation_labels_and_local_palette():
    markup = Markup()
    markup.feed(HTML)
    assert len(markup.ids) == len(set(markup.ids))
    assert set(markup.controls) <= set(markup.labels)
    assert all(href[1:] in markup.ids for href in markup.anchors)
    for color in ('#101416', '#181e21', '#58d6aa', '#edf2f1', '#a5b5b0'):
        assert color in HTML
    assert '"Segoe UI",system-ui' in HTML
    assert 'overflow-x:auto' in HTML
    assert '@media(max-width:720px)' in HTML
    assert 'focus-visible' in HTML
    assert '<link' not in HTML
    assert '<script src=' not in HTML


def test_polling_preserves_user_edits_and_reports_failure(tmp_path):
    if not shutil.which('node'):
        pytest.skip('Node.js is required for dashboard interaction tests')
    source = HTML.split('<script>', 1)[1].split('</script>', 1)[0]
    harness = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(tag='div'){this.tagName=tag;this.children=[];this.textContent='';this.value='';this.hidden=false;this.disabled=false;}
 append(...nodes){this.children.push(...nodes)}
 replaceChildren(...nodes){this.children=nodes;this.textContent=''}
 setAttribute(key,value){this[key]=value}
}
const nodes={},document={getElementById:id=>nodes[id] ||=new Element(),createElement:tag=>new Element(tag)};
// All records in this harness are explicitly synthetic, never product defaults.
let fail=false,role='admin',statusCalls=0,release=null;
const users=[{username:'synthetic-user',role:'operator',enabled:true}];
const status={devices:[],files:[],deliveries:[{receipt_id:'synthetic-receipt',name:'synthetic.oma',device_id:'synthetic-device',status:'pending'}],delivery_mode:'queued',maintenance:{}};
const context=vm.createContext({document,Date,Error,JSON,encodeURIComponent,setInterval(){},fetch:async(path,opts)=>{
 if(path==='/api/status'){
  statusCalls++;
  if(release){const wait=release;release=null;return await wait;}
  if(fail)throw Error('Falha sintética de rede');
 }
 const data=path==='/api/me'?{role,username:'synthetic-admin'}:path==='/api/users'?users:path==='/api/activations'?[]:status;
 return {ok:true,status:200,json:async()=>data};
}});
vm.runInContext(SOURCE,context);
const run=code=>vm.runInContext(code,context);
const walk=node=>[node,...node.children.flatMap(walk)];
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
(async()=>{
 run("token='synthetic-token';sessionVersion++;isAdmin=true");
 await run('refresh()');
 assert.match(nodes.syncstatus.textContent,/Atualizado em/);
 assert.match(nodes.maintenanceinfo.textContent,/Não verificado/);
 assert.equal(nodes.adminnav.hidden,false);
 assert.equal(nodes.refresh.disabled,false);
 assert.match(nodes.filestitle.textContent,/Histórico de recebimentos diretos/);
 assert.match(nodes.statussummary.textContent,/1 recibos do servidor listados/);
 assert.match(nodes.deliverysummary.textContent,/1 pendente/);
 assert.match(text(nodes.deliveries),/synthetic.oma/);
 assert.equal(run("contact({enabled:true,last_seen:new Date(Date.now()+60000).toISOString()}).textContent"),'Horário de contato inconsistente');
 const table=nodes.users.children[0];
 const password=walk(table).find(n=>n.tagName==='input');
 const select=walk(table).find(n=>n.tagName==='select');
 password.value='synthetic-draft-password';select.value='admin';
 document.activeElement=password;
 run("document.getElementById('devicename').value='draft-device';document.getElementById('devicecredential').textContent='synthetic-one-time-code'");
 await run('refresh()');
 assert.equal(nodes.users.children[0],table);
 assert.equal(document.activeElement,password);
 assert.equal(password.value,'synthetic-draft-password');assert.equal(select.value,'admin');
 assert.equal(nodes.devicename.value,'draft-device');
 assert.equal(nodes.devicecredential.textContent,'synthetic-one-time-code');
 fail=true;await run('refresh()');
 assert.match(nodes.syncstatus.textContent,/podem estar desatualizados/);
 assert.match(nodes.message.textContent,/Falha sintética/);
 assert.equal(nodes.refresh.disabled,false);
 assert.equal(nodes.users.children[0],table);
 fail=false;await run('refresh()');
 assert.match(nodes.syncstatus.textContent,/Atualizado em/);
 assert.equal(nodes.message.textContent,'');
 role='operator';await run('refresh()');
 assert.equal(nodes.adminnav.hidden,true);assert.equal(nodes.users.children.length,0);
 assert.equal(nodes.devicecredential.textContent,'');
 role='admin';await run('refresh()');
 assert.notEqual(nodes.users.children[0],table);
 // Concurrent refresh completion may not replace the latest accepted state.
 let resolve;
 release=new Promise(r=>resolve=r);
 const old=run('refresh()');
 await new Promise(r=>setImmediate(r));
 await run('refresh()');
 const current=nodes.syncstatus.textContent;
 resolve({ok:true,status:200,json:async()=>({devices:[{name:'late-synthetic',id:'late',pending:1,enabled:true}],files:[]})});
 await old;
 assert.equal(nodes.syncstatus.textContent,current);
 assert.doesNotMatch(text(nodes.devices),/late-synthetic/);
 run('clearSession()');
 for(const id of ['identity','syncstatus','users','devices','files','devicecredential','maintenanceinfo','deliverysummary'])assert.equal(text(nodes[id]).trim(),'');
 assert.equal(nodes.adminnav.hidden,true);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    script = tmp_path / 'dashboard-redesign.cjs'
    script.write_text('const SOURCE=' + json.dumps(source) + ';\n' + harness, encoding='utf-8')
    checked = subprocess.run(['node', '--check', str(script)], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    result = subprocess.run(['node', str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
