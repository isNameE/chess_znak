from uuid import uuid4
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
import plant
import repository
from access import hash_password

ADMIN={'id':'demo-operator','role':'admin','display_name':'Demo','mode':'demo'}


def call(action,data=None,user=ADMIN,key=None,revision=None):
    with repository.connect() as con:s=plant.station_view(con)
    return plant.execute(action,data or {},key or str(uuid4()),s['revision'] if revision is None else revision,user)


def prepare(db,pallet=2,quantity=2):
    assert not call('settings',{'one':quantity,'five':1,'pallet':pallet})['error']
    assert not call('shift-open',{'name':'Тестовая смена'})['error']
    result=call('order-create',{'line':'LINE-2','product':'1L','batch':'ПАРТИЯ-ТЕСТ','targetBoxes':pallet})
    order=result['result']['id']
    assert not call('order-start',{'id':order})['error']
    return order


def box():
    assert not call('camera')['error']
    result=call('box-close');identifier=result['result']['aggregate']
    assert not call('print-open',{'id':identifier})['error']
    assert not call('label-verify',{'id':identifier,'code':identifier})['error']
    return identifier


def test_complete_shift_order_pallet_exchange_without_history_snapshot(db,monkeypatch):
    monkeypatch.setattr(repository,'load',lambda _:(_ for _ in ()).throw(AssertionError('Full history must not be loaded')))
    order=prepare(db)
    for _ in range(2):assert not call('pallet-add',{'id':box()})['error']
    result=call('pallet-close');pallet=result['result']['aggregate']
    call('print-open',{'id':pallet});call('label-verify',{'id':pallet,'code':pallet})
    assert call('exchange-run')['result']['processed']==2
    assert call('exchange-run')['result']['processed']==1
    assert not call('order-complete')['error']
    assert not call('shift-close')['error']
    with db.connect() as con:
        assert con.execute('SELECT state FROM production_orders WHERE id=%s',(order,)).fetchone()['state']=='completed'
        assert con.execute("SELECT count(*) AS n FROM exchange_documents WHERE status='accepted'").fetchone()['n']==3
        assert not plant.station_view(con)['shift']


def test_duplicate_frame_and_failed_frame_are_atomic(db):
    prepare(db)
    frame=str(uuid4());codes=['DEMO-1L-ONE','DEMO-1L-TWO']
    first=call('scan-group',{'codes':codes,'frameId':frame})
    repeated=call('scan-group',{'codes':codes,'frameId':frame})
    assert first['station']['draft']==codes
    assert repeated['result']['frameRepeated']
    call('draft-clear',{'reason':'Проверка атомарного чтения'})
    failed=call('scan-group',{'codes':['DEMO-1L-X','DEMO-1L-X'],'frameId':str(uuid4())})
    assert failed['error'] and not failed['station']['draft']
    with db.connect() as con:
        assert con.execute('SELECT count(*) AS n FROM camera_frames').fetchone()['n']==1
        assert con.execute("SELECT 1 FROM marking_codes WHERE raw_code='DEMO-1L-X'").fetchone() is None


def test_idempotent_close_and_stale_revision(db):
    prepare(db);call('camera')
    with db.connect() as con:revision=plant.station_view(con)['revision']
    key=str(uuid4())
    first=call('box-close',key=key,revision=revision)
    repeated=call('box-close',key=key,revision=revision)
    assert first['result']==repeated['result']
    with pytest.raises(repository.Conflict):call('draft-clear',{'reason':'Старый клиент'},revision=revision)
    with db.connect() as con:assert con.execute('SELECT count(*) AS n FROM aggregates').fetchone()['n']==1


def test_label_quality_hold_and_disassembly_preserve_history(db):
    prepare(db)
    call('camera');result=call('box-close');key=result['result']['aggregate']
    assert call('pallet-add',{'id':key})['error']
    call('print-open',{'id':key});call('label-verify',{'id':key,'code':key})
    call('quarantine',{'id':key,'reason':'Проверка повреждённой упаковки'})
    assert call('pallet-add',{'id':key})['error']
    doc=plant.aggregate_detail(key)['document']['id']
    assert call('exchange-run',{'id':doc})['error']
    call('release',{'id':key,'reason':'Упаковка проверена мастером'})
    codes=[c['code'] for c in plant.aggregate_detail(key)['children']]
    assert not call('disassemble',{'id':key,'reason':'Учебное переформирование короба'})['error']
    detail=plant.aggregate_detail(key)
    assert detail['aggregate']['lifecycle']=='disassembled'
    assert [c['code'] for c in detail['children']]==codes
    assert detail['document']['status']=='cancelled'
    assert not call('scan-code',{'code':codes[0]})['error']


def test_unknown_send_blocks_disassembly_and_no_second_attempt(db):
    prepare(db);key=box();doc=plant.aggregate_detail(key)['document']['id']
    assert not call('exchange-run',{'id':doc,'outcome':'unknown'})['error']
    assert call('disassemble',{'id':key,'reason':'Нельзя обходить неопределённую отправку'})['error']
    assert call('exchange-run',{'id':doc})['error']
    assert not call('exchange-reconcile',{'id':doc})['error']
    assert plant.aggregate_detail(key)['document']['attempts']==1


def test_pallet_remove_middle_then_add_has_unique_positions(db):
    prepare(db,pallet=3,quantity=1)
    boxes=[box() for _ in range(3)]
    for key in boxes:assert not call('pallet-add',{'id':key})['error']
    call('pallet-remove',{'id':boxes[1]})
    assert not call('pallet-add',{'id':boxes[1]})['error']
    assert not call('pallet-close')['error']


def test_order_snapshot_pause_and_plan_limit(db):
    prepare(db)
    call('scan-code',{'code':'DEMO-1L-ONE'})
    assert not call('order-pause',{'reason':'Проверка остановки общего стола'})['error']
    assert call('camera')['error']
    assert call('shift-close')['error']
    call('draft-clear',{'reason':'Смена продукта не производится'})
    call('settings',{'one':15,'five':3,'pallet':50,'shiftHours':8,'targetPallets':30})
    with db.connect() as con:assert plant.station_view(con)['order']['recipe_quantity']==2
    assert not call('order-resume')['error']


def test_keyset_pagination_and_nested_code_search(db):
    prepare(db,pallet=2,quantity=1)
    keys=[box(),box()]
    for key in keys:call('pallet-add',{'id':key})
    pallet=call('pallet-close')['result']['aggregate']
    code=plant.aggregate_detail(keys[0])['children'][0]['code']
    found=plant.listing('aggregates',q=code)['items']
    assert {a['id'] for a in found}=={keys[0],pallet}
    first=plant.listing('aggregates',limit=2)
    second=plant.listing('aggregates',limit=2,cursor=first['next'])
    assert len(first['items'])==2 and len(second['items'])==1
    assert not ({a['id'] for a in first['items']}&{a['id'] for a in second['items']})


def test_personal_sessions_enforce_roles_and_retire_legacy_api(client,db,monkeypatch):
    from config import settings
    monkeypatch.setattr(settings(),'arm_demo_mode',False)
    with db.connect() as con:
        con.execute("INSERT INTO operators(id,display_name,role,password_hash) VALUES('op-test','Operator','operator',%s)",(hash_password('only-a-test-password'),))
    assert client.get('/api/plant/overview').status_code==401
    assert client.post('/api/access/login',json={'username':'op-test','password':'wrong'}).status_code==401
    assert client.post('/api/access/login',json={'username':'op-test','password':'only-a-test-password'}).status_code==200
    user=client.get('/api/access/me').json()['user'];assert user['role']=='operator'
    revision=client.get('/api/plant/station').json()['revision']
    body={'requestId':str(uuid4()),'revision':revision,'data':{'name':'Не разрешённая смена'}}
    assert client.post('/api/plant/action/shift-open',json=body).status_code==403
    assert client.post('/api/action/fill',json=body).status_code==410
    assert client.post('/api/integration/challenge',json={}).status_code==403
    assert client.post('/api/access/logout',json={}).status_code==200
    assert client.get('/api/plant/overview').status_code==401


def test_reports_csv_and_bounded_window(client,db):
    prepare(db);box()
    day=datetime.now(ZoneInfo('Europe/Moscow')).date()
    report=plant.reports(day,day)
    assert sum(r['bottles'] for r in report['items'])==2
    csv=client.get(f'/api/plant/reports.csv?first={day}&last={day}')
    assert csv.status_code==200 and 'text/csv' in csv.headers['content-type']
    assert 'LINE-2' in csv.text
    with pytest.raises(plant.RuleError):plant.reports(day-timedelta(days=32),day)


def test_service_processes_only_ready_documents_and_idle_poll_keeps_revision(db):
    prepare(db);key=box()
    doc=plant.aggregate_detail(key)['document']['id']
    result=plant.process_simulator_batch()
    assert result['processed']==1
    assert plant.aggregate_detail(key)['document']['status']=='accepted'
    with db.connect() as con:revision=plant.station_view(con)['revision']
    assert plant.process_simulator_batch()['processed']==0
    with db.connect() as con:
        assert plant.station_view(con)['revision']==revision
        assert con.execute('SELECT attempts FROM exchange_documents WHERE id=%s',(doc,)).fetchone()['attempts']==1


def test_real_provider_cannot_be_processed_or_reconciled_by_simulator(db):
    prepare(db);key=box();doc=plant.aggregate_detail(key)['document']['id']
    with db.connect() as con:con.execute("UPDATE exchange_documents SET provider='sandbox',status='unknown' WHERE id=%s",(doc,))
    assert call('exchange-run',{'id':doc})['error']
    assert call('exchange-reconcile',{'id':doc})['error']
    assert plant.process_simulator_batch()['processed']==0
    assert plant.aggregate_detail(key)['document']['status']=='unknown'


@pytest.mark.parametrize('token',['expired-test-session','x'*256])
def test_invalid_session_does_not_fall_back_to_demo_administrator(client,token):
    client.cookies.set('arm_session',token)
    assert client.get('/api/access/me').json()['user'] is None
    assert client.get('/api/plant/overview').status_code==401
