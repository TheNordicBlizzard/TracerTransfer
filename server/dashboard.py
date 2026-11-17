"""Self-contained panel: credentials stay in memory; data is rendered as text."""
HTML = r"""
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TracerTransfer · Painel Operacional</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #090d11;
      --surface: #121820;
      --surface-hover: #18222d;
      --line: rgba(255, 255, 255, 0.08);
      --ink: #f1f5f9;
      --muted: #94a3b8;
      --accent: #10b981;
      --cyan: #38bdf8;
      --warning: #f59e0b;
      --danger: #f43f5e;
      --radius: 10px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font: 14px/1.5 system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--ink); min-height: 100vh; }
    header { background: var(--surface); border-bottom: 1px solid var(--line); padding: 16px 28px; display: flex; align-items: center; justify-content: space-between; }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand-icon { width: 36px; height: 36px; background: rgba(16,185,129,0.15); border: 1px solid var(--accent); border-radius: 8px; display: flex; align-items: center; justify-content: center; color: var(--accent); font-weight: bold; font-size: 18px; }
    .brand h1 { font-size: 18px; font-weight: 700; letter-spacing: -0.3px; }
    .brand p { font-size: 12px; color: var(--muted); }
    .badge { display: inline-flex; align-items: center; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
    .badge-success { background: rgba(16,185,129,0.15); color: #34d399; border: 1px solid rgba(16,185,129,0.3); }
    .badge-warning { background: rgba(245,158,11,0.15); color: #fbbf24; border: 1px solid rgba(245,158,11,0.3); }
    .badge-danger { background: rgba(244,63,94,0.15); color: #fb7185; border: 1px solid rgba(244,63,94,0.3); }
    main { max-width: 1400px; margin: 0 auto; padding: 24px; }
    #login { max-width: 400px; margin: 80px auto; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 32px; box-shadow: 0 20px 40px rgba(0,0,0,0.5); }
    #login h2 { margin-bottom: 8px; font-size: 20px; font-weight: 600; }
    #login p { color: var(--muted); font-size: 13px; margin-bottom: 24px; }
    #loginform { display: flex; flex-direction: column; gap: 16px; }
    label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
    input, select, button { font: inherit; border-radius: 8px; min-height: 40px; padding: 8px 12px; border: 1px solid var(--line); outline: none; }
    input, select { background: #090d11; color: var(--ink); }
    input:focus, select:focus { border-color: var(--accent); }
    button { background: var(--accent); color: #090d11; font-weight: 600; cursor: pointer; border: none; }
    button:hover { filter: brightness(1.1); }
    button.secondary { background: transparent; color: var(--ink); border: 1px solid var(--line); }
    button.secondary:hover { background: var(--surface-hover); }
    .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin: 24px 0; }
    .kpi-card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 20px; border-left: 4px solid var(--accent); }
    .kpi-card.cyan { border-left-color: var(--cyan); }
    .kpi-card.warning { border-left-color: var(--warning); }
    .kpi-title { font-size: 11px; text-transform: uppercase; color: var(--muted); font-weight: 600; letter-spacing: 0.5px; }
    .kpi-value { font-size: 26px; font-weight: 700; margin: 8px 0; }
    .kpi-desc { font-size: 12px; color: var(--muted); }
    .tab-bar { display: flex; gap: 8px; border-bottom: 1px solid var(--line); margin-bottom: 24px; overflow-x: auto; }
    .tab-btn { background: transparent; border: none; border-bottom: 2px solid transparent; border-radius: 0; color: var(--muted); padding: 12px 16px; cursor: pointer; font-weight: 500; font-size: 14px; }
    .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }
    .tab-content { display: none; }
    .tab-content.active { display: block; }
    .card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 24px; margin-bottom: 24px; }
    .card h2 { font-size: 16px; margin-bottom: 16px; border-bottom: 1px solid var(--line); padding-bottom: 10px; font-weight: 600; }
    .table-container { width: 100%; overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }
    th, td { padding: 12px 16px; border-bottom: 1px solid var(--line); }
    th { background: #0c1117; color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; }
    tbody tr:hover { background: var(--surface-hover); }
    .top-bar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; }
    .user-pill { background: var(--surface); border: 1px solid var(--line); padding: 6px 14px; border-radius: 20px; font-size: 13px; }
    form.inline-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)) auto; gap: 12px; align-items: end; margin-bottom: 16px; }
    #message:not(:empty) { padding: 12px 16px; border-radius: 8px; background: rgba(245,158,11,0.1); border: 1px solid var(--warning); color: var(--warning); margin-bottom: 16px; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-icon">⚡</div>
      <div><h1>TracerTransfer</h1><p>Painel Operacional · Sistema de Monitoramento</p></div>
    </div>
    <div style="font-size:12px; color:var(--accent); display:flex; align-items:center; gap:6px;">● Servidor Online</div>
  </header>
  <main>
    <div id="message"></div>
    <section id="login">
      <h2>Entrar no Painel</h2>
      <p>Acesse com suas credenciais de operador ou administrador.</p>
      <form id="loginform">
        <label>Usuário <input id="username" autocomplete="username" required autofocus></label>
        <label>Senha <input id="password" type="password" autocomplete="current-password" required></label>
        <button type="submit" id="loginbutton">Entrar no Sistema</button>
      </form>
    </section>
    <div id="panel" style="display:none;">
      <div class="top-bar">
        <div class="user-pill">👤 <strong id="identity">...</strong> · <span id="syncstatus" style="color:var(--muted); font-size:12px;">Sincronizando...</span></div>
        <div style="display:flex; gap:8px;"><button id="refresh" class="secondary">🔄 Atualizar</button><button id="logout" class="secondary">🚪 Sair</button></div>
      </div>
      <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-title">Dispositivos Ativos</div><div class="kpi-value" id="kpi-devices">0</div><div class="kpi-desc" id="kpi-devices-desc">Terminais conectados</div></div>
        <div class="kpi-card cyan"><div class="kpi-title">Fila de Entregas</div><div class="kpi-value" id="kpi-queue">0</div><div class="kpi-desc">FreeForm (172.16.9.100)</div></div>
        <div class="kpi-card warning"><div class="kpi-title">Saúde do Spool</div><div class="kpi-value">100%</div><div class="kpi-desc">Persistência íntegra</div></div>
        <div class="kpi-card"><div class="kpi-title">Auto Refresh</div><div class="kpi-value">15s</div><div class="kpi-desc">Tempo real ativo</div></div>
      </div>
      <div class="tab-bar">
        <button class="tab-btn active" id="btn-tab-overview" onclick="switchTab('overview')">📊 Visão Geral</button>
        <button class="tab-btn" id="btn-tab-devices" onclick="switchTab('devices')">🖥️ Dispositivos</button>
        <button class="tab-btn" id="btn-tab-files" onclick="switchTab('files')">📁 Arquivos Recebidos</button>
        <button class="tab-btn admin-only" id="btn-tab-deliveries" onclick="switchTab('deliveries')">📦 Fila e Entregas</button>
        <button class="tab-btn admin-only" id="btn-tab-activations" onclick="switchTab('activations')">🔑 Códigos de Ativação</button>
        <button class="tab-btn admin-only" id="btn-tab-users" onclick="switchTab('users')">👥 Usuários</button>
      </div>
      <div id="tab-overview" class="tab-content active"><div class="card"><h2>Resumo Operacional</h2><p id="statussummary">Carregando dados...</p><p id="maintenanceinfo" style="color:var(--muted); margin-top:8px; font-size:13px;"></p></div></div>
      <div id="tab-devices" class="tab-content"><div class="card"><h2>Dispositivos Conectados</h2><div class="table-container" id="devices"></div></div></div>
      <div id="tab-files" class="tab-content"><div class="card"><h2>Arquivos Recebidos</h2><div class="table-container" id="files"></div></div></div>
      <div id="tab-deliveries" class="tab-content"><div class="card"><h2>Fila de Entrega (Laboratório)</h2><div class="table-container" id="deliveries"></div></div></div>
      <div id="tab-activations" class="tab-content"><div class="card"><h2>Gerar Código de Ativação</h2><form id="deviceform" class="inline-form"><label>Nome da Filial <input id="devicename" required></label><label>Papel <select id="activationrole"><option value="sender">Remetente</option><option value="delivery">Entregador</option></select></label><label>Validade (Horas) <input id="activationhours" type="number" value="24" required></label><button type="submit">Gerar Código</button></form><div id="devicecredential" style="margin-bottom:16px;"></div><div class="table-container" id="activations"></div></div></div>
      <div id="tab-users" class="tab-content"><div class="card"><h2>Usuários</h2><form id="userform" class="inline-form"><label>Usuário <input id="newuser" required></label><label>Senha <input id="newpassword" type="password" required minlength="12"></label><label>Perfil <select id="role"><option value="operator">Operador</option><option value="admin">Administrador</option></select></label><button type="submit">Criar Usuário</button></form><div class="table-container" id="users"></div></div></div>
    </div>
  </main>
  <script>
    'use strict';
    let token='', isAdmin=false, sessionVersion=0, refreshTimer=null;
    const $ = id => document.getElementById(id);
    function message(m){ $('message').textContent = m || ''; }
    function switchTab(name){
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      const btn = $('btn-tab-' + name);
      if(btn) btn.classList.add('active');
      const content = $('tab-' + name);
      if(content) content.classList.add('active');
    }
    function clearSession(){
      token=''; isAdmin=false; sessionVersion++;
      if(refreshTimer) clearInterval(refreshTimer);
      $('panel').style.display='none'; $('login').style.display='block';
    }
    async function api(path, method='GET', body){
      const ver = sessionVersion;
      const r = await fetch(path, { method, headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined, cache: 'no-store' });
      if(ver !== sessionVersion) throw Error('Sessão encerrada');
      if(r.status === 401){ clearSession(); throw Error('Sessão expirada. Entre novamente.'); }
      const j = await r.json();
      if(!r.ok) throw Error(j.detail || 'Erro na requisição');
      return j;
    }
    function renderTable(id, headers, rows){
      const tbl = document.createElement('table');
      const thRow = document.createElement('tr');
      headers.forEach(h => { const th = document.createElement('th'); th.textContent = h; thRow.appendChild(th); });
      tbl.appendChild(document.createElement('thead')).appendChild(thRow);
      const tb = tbl.appendChild(document.createElement('tbody'));
      if(!rows.length){
        const tr = document.createElement('tr'), td = document.createElement('td');
        td.colSpan = headers.length; td.textContent = 'Nenhum registro.'; td.style.color = 'var(--muted)';
        tr.appendChild(td); tb.appendChild(tr);
      } else {
        rows.forEach(r => {
          const tr = document.createElement('tr');
          r.forEach(v => { const td = document.createElement('td'); if(v instanceof Node) td.appendChild(v); else td.textContent = String(v ?? '—'); tr.appendChild(td); });
          tb.appendChild(tr);
        });
      }
      $(id).replaceChildren(tbl);
    }
    function badge(text, type='success'){ const s = document.createElement('span'); s.className = 'badge badge-' + type; s.textContent = text; return s; }
    function formatTime(v){ if(!v) return '—'; const d = new Date(v); return isNaN(d) ? 'Data inválida' : d.toLocaleString('pt-BR'); }
    async function refreshData(){
      if(!token) return;
      $('syncstatus').textContent = 'Sincronizando...';
      try {
        const status = await api('/api/status');
        $('statussummary').textContent = status.summary || 'Sistema operacional normal.';
        $('maintenanceinfo').textContent = 'Banco de Dados: ' + (status.database_ok ? 'Conectado' : 'Atenção') + ' · Modo de Manutenção: ' + (status.maintenance ? 'Ativo' : 'Inativo');
        const devs = await api('/api/devices');
        $('kpi-devices').textContent = devs.length;
        const onlineCount = devs.filter(d => d.last_seen && (Date.now() - Date.parse(d.last_seen) <= 45000)).length;
        $('kpi-devices-desc').textContent = onlineCount + ' terminais online agora';
        renderTable('devices', ['Dispositivo', 'Identificador', 'Último Contato', 'Status', 'Ações'], devs.map(d => {
          const isOnline = d.last_seen && (Date.now() - Date.parse(d.last_seen) <= 45000);
          const b = !d.enabled ? badge('Revogado', 'danger') : isOnline ? badge('Online (≤45s)', 'success') : badge('Sem sinal', 'warning');
          const btn = document.createElement('button'); btn.className = 'secondary'; btn.textContent = 'Revogar'; btn.style.minHeight = '30px';
          btn.onclick = async () => { if(confirm('Revogar ' + d.name + '?')){ await api('/api/devices/' + d.id + '/revoke', 'POST'); refreshData(); } };
          return [d.name, d.id, formatTime(d.last_seen), b, btn];
        }));
        const files = await api('/api/files');
        renderTable('files', ['Arquivo', 'Dispositivo', 'Tamanho', 'Recebido em'], files.slice(0, 100).map(f => [
          f.filename, f.device_name || 'Desconhecido', f.size ? (f.size/1024).toFixed(1) + ' KB' : '—', formatTime(f.received_at)
        ]));
        if(isAdmin){
          const dels = await api('/api/deliveries');
          $('kpi-queue').textContent = dels.filter(x => x.status === 'pending').length;
          renderTable('deliveries', ['Arquivo', 'Situação', 'Destino', 'Data'], dels.slice(0, 100).map(dl => [
            dl.filename, dl.status === 'delivered' ? badge('Entregue', 'success') : badge(dl.status, 'warning'), dl.destination || 'C:\\FreeForm\\Tracer', formatTime(dl.updated_at)
          ]));
          const acts = await api('/api/activations');
          renderTable('activations', ['Nome / Filial', 'Papel', 'Criado em', 'Expira em', 'Status'], acts.map(a => [
            a.device_name, a.role === 'sender' ? 'Remetente' : 'Entregador', formatTime(a.created_at), formatTime(a.expires_at), a.used ? badge('Utilizado', 'warning') : badge('Ativo', 'success')
          ]));
          const usrs = await api('/api/users');
          renderTable('users', ['Usuário', 'Perfil', 'Situação'], usrs.map(u => [
            u.username, u.role === 'admin' ? badge('Administrador', 'success') : badge('Operador', 'cyan'), u.enabled ? 'Habilitado' : 'Desabilitado'
          ]));
        }
        $('syncstatus').textContent = 'Atualizado às ' + new Date().toLocaleTimeString('pt-BR');
      } catch(err){ message(err.message); }
    }
    $('loginform').onsubmit = async (e) => {
      e.preventDefault();
      try {
        const res = await api('/api/login', 'POST', { username: $('username').value, password: $('password').value });
        token = res.token; isAdmin = res.role === 'admin';
        $('identity').textContent = res.username + ' (' + (isAdmin ? 'Admin' : 'Operador') + ')';
        $('login').style.display = 'none'; $('panel').style.display = 'block';
        document.querySelectorAll('.admin-only').forEach(el => el.style.display = isAdmin ? 'inline-block' : 'none');
        await refreshData();
        if(refreshTimer) clearInterval(refreshTimer);
        refreshTimer = setInterval(refreshData, 15000);
      } catch(err){ message(err.message); }
    };
    $('refresh').onclick = refreshData;
    $('logout').onclick = clearSession;
    $('deviceform').onsubmit = async (e) => {
      e.preventDefault();
      try {
        const res = await api('/api/activations', 'POST', { device_name: $('devicename').value, role: $('activationrole').value, valid_hours: parseInt($('activationhours').value, 10) });
        $('devicecredential').innerHTML = '<div style="padding:12px; background:rgba(16,185,129,0.1); border:1px solid var(--accent); border-radius:8px; color:var(--accent);">Código: <code style="font-size:16px; user-select:all;">' + res.code + '</code></div>';
        $('devicename').value = ''; refreshData();
      } catch(err){ message(err.message); }
    };
    $('userform').onsubmit = async (e) => {
      e.preventDefault();
      try {
        await api('/api/users', 'POST', { username: $('newuser').value, password: $('newpassword').value, role: $('role').value });
        $('newuser').value = ''; $('newpassword').value = ''; message('Usuário criado com sucesso.'); refreshData();
      } catch(err){ message(err.message); }
    };
  </script>
</body>
</html>

"""
