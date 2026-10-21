from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from uuid import uuid4
import psycopg
import pytest
from domain import initial_state
from migrate_legacy import import_sqlite


def cmd(db,action,data=None):
    s=db.read_state()
    return db.command(action,data or {},str(uuid4()),s['revision'])


def test_request_id_replay_and_concurrent_close(db):
    cmd(db,'fill')
    revision=db.read_state()['revision']
    key=str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _: db.command('close-box',{},key,revision),range(2)))
    assert all(error is None for _,error in results)
    state=db.read_state()
    assert len(state['boxes'])==len(state['documents'])==len(state['printJobs'])==1
    assert state['draft']==[]
    assert state['revision']==revision+1


def test_stale_revision_and_key_reuse_are_blocked(db):
    s=db.read_state()
    key=str(uuid4())
    db.command('scan',{'code':'DEMO-1L-ONE'},key,s['revision'])
    with pytest.raises(db.Conflict):
        db.command('clear-draft',{},str(uuid4()),s['revision'])
    with pytest.raises(db.Conflict):
        db.command('scan',{'code':'DEMO-1L-TWO'},key,s['revision'])
    assert db.read_state()['draft']==['DEMO-1L-ONE']


def test_sql_constraint_blocks_second_box_and_draft(db):
    cmd(db,'fill');cmd(db,'close-box');cmd(db,'fill');cmd(db,'close-box')
    state=db.read_state()
    code=state['boxes'][0]['codes'][0]
    with pytest.raises(psycopg.Error),db.connect() as con:
        con.execute('INSERT INTO draft_codes VALUES(%s,0)',(code,))
    with pytest.raises(psycopg.Error),db.connect() as con:
        con.execute('INSERT INTO box_members VALUES(%s,%s,99)',(code,state['boxes'][1]['id']))
    assert db.read_state()['draft']==[]


def test_line_batch_pallet_and_exchange_survive_new_connections(db):
    cmd(db,'settings',{'one':2,'five':1,'pallet':2})
    cmd(db,'context',{'line':'LINE-2','batch':'ПАРТИЯ-42'})
    cmd(db,'fill-pallet');cmd(db,'close-pallet');cmd(db,'send-all')
    s=db.read_state()
    assert s['pallets'][0]['line']=='LINE-2'
    assert all(b['batch']=='ПАРТИЯ-42' for b in s['boxes'])
    assert all(d['status']=='accepted' for d in s['documents'])
    with db.connect() as con:
        assert con.execute('SELECT count(*) AS n FROM exchange_attempts').fetchone()['n']==3
        assert con.execute('SELECT count(*) AS n FROM print_jobs').fetchone()['n']==3
    db.migrate()
    assert len(db.read_state()['pallets'])==1


def test_failed_command_keeps_composition_and_records_error(db):
    cmd(db,'scan',{'code':'DEMO-1L-ONE'})
    _,error=cmd(db,'close-box')
    assert error
    s=db.read_state()
    assert s['draft']==['DEMO-1L-ONE'] and not s['boxes']
    assert any(e['level']=='error' and e['actor']=='demo-operator' for e in s['events'])


def test_legacy_import_is_readonly_and_refuses_overwrite(db,tmp_path):
    from domain import apply
    s=apply(apply(initial_state(),'fill',{}),'close-box',{})
    s['version']=1
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE app_state(id integer,payload text)')
        con.execute('INSERT INTO app_state VALUES(1,?)',(json.dumps(s),))
    original=path.read_bytes()
    result=import_sqlite(path)
    assert result['boxes']==1 and path.read_bytes()==original
    with pytest.raises(ValueError):
        import_sqlite(path)
    assert len(db.read_state()['boxes'])==1


def test_api_contract_and_cross_origin_block(client):
    assert client.get('/api/health').json()['database']=='postgresql'
    s=client.get('/api/state').json()
    body={'requestId':str(uuid4()),'revision':s['revision'],'data':{}}
    assert client.post('/api/action/fill',json=body,headers={'Origin':'https://other.example'}).status_code==403
    assert client.post('/api/action/fill',json={}).status_code==422
    response=client.post('/api/action/fill',json=body)
    assert response.status_code==200
    assert len(response.json()['state']['draft'])==15
    assert client.get('/api/integration/status').json()['productionEnabled'] is False
    result=client.post('/api/integration/challenge',json={})
    assert result.status_code==422 and 'отключён' in result.json()['error']


def test_network_demo_access_requires_correct_credentials(client,monkeypatch):
    from config import settings
    cfg=settings()
    monkeypatch.setattr(cfg,'arm_access_user','demo-test')
    monkeypatch.setattr(cfg,'arm_access_password','test-only-password')
    assert client.get('/api/state').status_code==401
    assert client.get('/api/state',auth=('demo-test','wrong')).status_code==401
    assert client.get('/api/state',auth=('demo-test','test-only-password')).status_code==200
