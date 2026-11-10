import json
from fastapi.testclient import TestClient
from server.app import create_app
from server.users import bootstrap_admin
from tests.test_server import PASSWORD


def test_status_exposes_only_nonsecret_maintenance_fields(tmp_path):
    app=create_app(tmp_path)
    bootstrap_admin(app.state.db,'admin',PASSWORD)
    with TestClient(app) as api:
        assert api.get('/api/status').status_code==401
        token=api.post('/api/login',json={'username':'admin','password':PASSWORD}).json()['token']
        (tmp_path/'maintenance-status.json').write_text(json.dumps({'backup_ok':True,'health_ok':True,'space_ok':True,'free_bytes':12345,'last_backup':'DO_NOT_EXPOSE_LOCAL_PATH','password':'DO_NOT_EXPOSE_SECRET'}))
        response=api.get('/api/status',headers={'Authorization':'Bearer '+token})
        assert response.status_code==200
        assert response.json()['maintenance']=={'backup_ok':True,'health_ok':True,'space_ok':True,'free_bytes':12345}
        assert 'DO_NOT_EXPOSE' not in response.text


def test_status_invalid_maintenance_is_unknown_not_healthy(tmp_path):
    app=create_app(tmp_path)
    bootstrap_admin(app.state.db,'admin',PASSWORD)
    with TestClient(app) as api:
        token=api.post('/api/login',json={'username':'admin','password':PASSWORD}).json()['token']
        (tmp_path/'maintenance-status.json').write_text('invalid-json')
        assert api.get('/api/status',headers={'Authorization':'Bearer '+token}).json()['maintenance']=={}
