"""Plant workflow. Indexed SQL and bounded responses; no full-history snapshots."""
import base64
from datetime import date,datetime,timedelta
import hashlib
import json
import re
from uuid import UUID,uuid4
from zoneinfo import ZoneInfo
from psycopg.types.json import Jsonb
import repository
from domain import RuleError


class Forbidden(RuleError):
    pass


def clean(value):
    return json.loads(json.dumps(value,default=lambda v:v.isoformat() if isinstance(v,(datetime,date)) else str(v),ensure_ascii=False))


def note(value,label='Причина',minimum=5,maximum=500):
    text=str(value or '').strip()
    if not minimum<=len(text)<=maximum or any(ord(c)<32 for c in text):
        raise RuleError(f'{label}: от {minimum} до {maximum} печатных символов.')
    return text


def integer(value,low=1,high=100000):
    if type(value) is not int or not low<=value<=high:raise RuleError(f'Введите целое число от {low} до {high}.')
    return value


def event(con,user,message,level='info'):
    con.execute('INSERT INTO audit_events VALUES(%s,now(),%s,%s,%s)',(uuid4(),level,user['id'],message))


def incident(con,user,station,message,category='operation',reference=None,severity='error'):
    key=uuid4()
    con.execute('INSERT INTO incidents(id,severity,category,reference,message,order_id,shift_id,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                (key,severity,category,reference,message,station.get('order_id'),station.get('shift_id'),user['id']))
    return str(key)


def next_id(con,kind):
    n=con.execute('UPDATE station SET sequence=sequence+1 WHERE id=1 RETURNING sequence').fetchone()['sequence']
    return f'DEMO-{kind}-{n:09d}'


def row_station(con):
    return con.execute('SELECT * FROM station WHERE id=1').fetchone()


def active_order(con,s):
    order=con.execute('SELECT * FROM production_orders WHERE id=%s',(s['order_id'],)).fetchone() if s['order_id'] else None
    if not s['shift_id']:raise RuleError('Сначала откройте рабочую смену.')
    if not order or order['state']!='running':raise RuleError('Запустите производственное задание или снимите его с паузы.')
    return order


def unfinished(con):
    return con.execute('SELECT EXISTS(SELECT 1 FROM draft_codes) OR EXISTS(SELECT 1 FROM draft_pallet_boxes) AS yes').fetchone()['yes']


def station_view(con):
    s=dict(row_station(con))
    order=con.execute('SELECT o.*,b.number AS batch FROM production_orders o JOIN batches b ON b.id=o.batch_id WHERE o.id=%s',(s['order_id'],)).fetchone() if s['order_id'] else None
    shift=con.execute('SELECT * FROM work_shifts WHERE id=%s',(s['shift_id'],)).fetchone() if s['shift_id'] else None
    draft=[r['code'] for r in con.execute('SELECT code FROM draft_codes ORDER BY position LIMIT 100')]
    pallet=[r['box_id'] for r in con.execute('SELECT box_id FROM draft_pallet_boxes ORDER BY position LIMIT 100')]
    recent=list(con.execute('SELECT a.id,a.unit_count,a.lifecycle,p.status AS print_status FROM aggregates a '
                            'LEFT JOIN LATERAL(SELECT status FROM print_jobs WHERE aggregate_id=a.id ORDER BY created_at DESC,id DESC LIMIT 1)p ON true '
                            "WHERE a.order_id=%s AND a.kind='box' AND a.lifecycle<>'disassembled' ORDER BY a.created_at DESC,a.id DESC LIMIT 8",(s['order_id'],)))
    available=list(con.execute("SELECT a.id,a.unit_count FROM aggregates a WHERE a.order_id=%s AND a.kind='box' AND a.lifecycle='closed' "
                               'AND NOT EXISTS(SELECT 1 FROM pallet_members m WHERE m.box_id=a.id) '
                               'AND NOT EXISTS(SELECT 1 FROM draft_pallet_boxes d WHERE d.box_id=a.id) '
                               "AND EXISTS(SELECT 1 FROM print_jobs p WHERE p.aggregate_id=a.id AND p.status='verified') "
                               'ORDER BY a.created_at,a.id LIMIT 50',(s['order_id'],)))
    return clean({'revision':s['revision'],'online':s['online'],'order':order,'shift':shift,'draft':draft,'pallet':pallet,
                  'recent':recent,'available':available,'palletTarget':order['pallet_quantity'] if order else 50})


def day_bounds(day):
    return (f'{day.isoformat()} 00:00:00 Europe/Moscow',f'{(day+timedelta(days=1)).isoformat()} 00:00:00 Europe/Moscow')


def overview(day=None):
    day=day or datetime.now(ZoneInfo('Europe/Moscow')).date()
    start,end=day_bounds(day)
    with repository.connect() as con:
        totals=con.execute("SELECT count(*) FILTER(WHERE kind='box' AND lifecycle<>'disassembled') AS boxes,"
                           "coalesce(sum(unit_count) FILTER(WHERE kind='box' AND lifecycle<>'disassembled'),0) AS bottles,"
                           "count(*) FILTER(WHERE kind='pallet' AND lifecycle<>'disassembled') AS pallets,"
                           "count(*) FILTER(WHERE lifecycle='quarantined') AS held FROM aggregates WHERE created_at>=%s AND created_at<%s",(start,end)).fetchone()
        lines=list(con.execute("SELECT l.id,l.name,count(a.id) FILTER(WHERE a.kind='box' AND a.lifecycle<>'disassembled') AS boxes,"
                               "coalesce(sum(a.unit_count) FILTER(WHERE a.kind='box' AND a.lifecycle<>'disassembled'),0) AS bottles,"
                               "count(a.id) FILTER(WHERE a.kind='pallet') AS pallets FROM production_lines l LEFT JOIN aggregates a ON a.line_id=l.id "
                               'AND a.created_at>=%s AND a.created_at<%s GROUP BY l.id,l.name ORDER BY l.id',(start,end)))
        queues=con.execute("SELECT (SELECT count(*) FROM exchange_documents WHERE status IN ('queued','unknown','rejected')) AS exchange,"
                            "(SELECT count(*) FROM print_jobs WHERE status IN ('ready','preview_opened','failed')) AS printing,"
                            "(SELECT count(*) FROM incidents WHERE state<>'resolved') AS incidents").fetchone()
        orders=list(con.execute("SELECT o.*,b.number AS batch FROM production_orders o JOIN batches b ON b.id=o.batch_id "
                                "WHERE o.state IN ('planned','running','paused') ORDER BY CASE o.state WHEN 'running' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,o.created_at LIMIT 6"))
        latest=list(con.execute("SELECT * FROM incidents WHERE state<>'resolved' ORDER BY created_at DESC,id DESC LIMIT 5"))
        daily=list(con.execute("SELECT (created_at AT TIME ZONE 'Europe/Moscow')::date AS day,count(*) AS pallets FROM aggregates "
                               "WHERE kind='pallet' AND lifecycle<>'disassembled' AND created_at>=%s::timestamptz-interval '6 days' AND created_at<%s GROUP BY 1 ORDER BY 1",(start,end)))
        service=con.execute("SELECT last_seen,alive_until>now() AS active,processed FROM service_heartbeats WHERE id='exchange-simulator'").fetchone()
        return clean({'day':day,'totals':totals,'lines':lines,'queues':queues,'orders':orders,'incidents':latest,'daily':daily,'exchangeService':service,'station':station_view(con)})


def cursor_encode(row,timekey='created_at'):
    return base64.urlsafe_b64encode(json.dumps([str(row[timekey]),str(row['id'])]).encode()).decode()


def cursor_decode(cursor):
    try:
        if len(cursor)>512:raise ValueError()
        value=json.loads(base64.urlsafe_b64decode(cursor))
        if len(value)!=2 or not all(isinstance(v,str) for v in value):raise ValueError()
        datetime.fromisoformat(value[0])
        return value
    except (ValueError,TypeError):raise RuleError('Некорректный указатель страницы.') from None


def listing(view,limit=25,cursor=None,q='',state='',line='',kind=''):
    limit=integer(limit,1,100)
    if len(q)>512:raise RuleError('Слишком длинный поисковый запрос.')
    with repository.connect() as con:
        conditions=[];params=[];timekey='created_at'
        if view=='aggregates':
            query=('SELECT a.*,b.number AS batch,d.status AS exchange_status,p.status AS print_status FROM aggregates a '
                   'JOIN batches b ON b.id=a.batch_id LEFT JOIN exchange_documents d ON d.aggregate_id=a.id '
                   'LEFT JOIN LATERAL(SELECT status FROM print_jobs WHERE aggregate_id=a.id ORDER BY created_at DESC,id DESC LIMIT 1)p ON true')
            alias='a'
            if q:
                boxes=[r['box_id'] for r in con.execute('SELECT box_id FROM box_members WHERE code=%s UNION SELECT box_id FROM membership_history WHERE code=%s LIMIT 10',(q,q))]
                if boxes:
                    parents=[r['pallet_id'] for r in con.execute('SELECT pallet_id FROM pallet_members WHERE box_id=ANY(%s)',(boxes,))]
                    conditions.append('a.id=ANY(%s)');params.append(boxes+parents)
                else:
                    conditions.append('a.id LIKE %s');params.append(q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
            if state:conditions.append('a.lifecycle=%s');params.append(state)
            if line:conditions.append('a.line_id=%s');params.append(line)
            if kind:conditions.append('a.kind=%s');params.append(kind)
        elif view=='orders':
            query='SELECT a.*,b.number AS batch FROM production_orders a JOIN batches b ON b.id=a.batch_id';alias='a'
            if state:conditions.append('a.state=%s');params.append(state)
            if line:conditions.append('a.line_id=%s');params.append(line)
            if q:conditions.append('(a.id=%s OR b.number=%s)');params.extend([q,q])
        elif view=='incidents':
            query='SELECT a.*,o.display_name AS assigned_name FROM incidents a LEFT JOIN operators o ON o.id=a.assigned_to';alias='a'
            if state:conditions.append('a.state=%s');params.append(state)
        elif view=='exchange':
            query='SELECT a.*,g.lifecycle,g.order_id FROM exchange_documents a JOIN aggregates g ON g.id=a.aggregate_id';alias='a'
            if state:conditions.append('a.status=%s');params.append(state)
        elif view=='printing':
            query='SELECT a.*,g.kind,g.print_opens,g.lifecycle FROM print_jobs a JOIN aggregates g ON g.id=a.aggregate_id';alias='a'
            if state:conditions.append('a.status=%s');params.append(state)
        elif view=='audit':
            query='SELECT a.*,o.display_name FROM audit_events a JOIN operators o ON o.id=a.actor';alias='a'
            if state:conditions.append('a.level=%s');params.append(state)
        elif view=='shifts':
            query=("SELECT a.*,a.opened_at AS created_at,t.boxes,t.pallets,t.bottles FROM work_shifts a LEFT JOIN LATERAL("
                   "SELECT count(*) FILTER(WHERE g.kind='box' AND g.lifecycle<>'disassembled') AS boxes,"
                   "count(*) FILTER(WHERE g.kind='pallet' AND g.lifecycle<>'disassembled') AS pallets,"
                   "coalesce(sum(g.unit_count) FILTER(WHERE g.kind='box' AND g.lifecycle<>'disassembled'),0) AS bottles "
                   'FROM aggregates g WHERE g.shift_id=a.id)t ON true');alias='a';timekey='opened_at'
        else:raise RuleError('Неизвестный реестр.')
        if cursor:
            at,identifier=cursor_decode(cursor)
            conditions.append(f'({alias}.{timekey},{alias}.id)<(%s,%s)');params.extend([at,identifier])
        if conditions:query+=' WHERE '+' AND '.join(conditions)
        query+=f' ORDER BY {alias}.{timekey} DESC,{alias}.id DESC LIMIT %s';params.append(limit+1)
        rows=list(con.execute(query,params))
        more=len(rows)>limit;rows=rows[:limit]
        return clean({'items':rows,'next':cursor_encode(rows[-1],timekey if view!='shifts' else 'created_at') if more else None,'limit':limit})


def aggregate_detail(identifier):
    with repository.connect() as con:
        a=con.execute('SELECT a.*,b.number AS batch FROM aggregates a JOIN batches b ON b.id=a.batch_id WHERE a.id=%s',(identifier,)).fetchone()
        if not a:raise RuleError('Упаковка не найдена.')
        if a['kind']=='box':
            children=list(con.execute('SELECT code,position FROM box_members WHERE box_id=%s ORDER BY position LIMIT 100',(identifier,)))
            if a['lifecycle']=='disassembled':children=list(con.execute('SELECT code,position FROM membership_history WHERE box_id=%s ORDER BY position LIMIT 100',(identifier,)))
        else:children=list(con.execute('SELECT box_id AS code,position FROM pallet_members WHERE pallet_id=%s ORDER BY position LIMIT 100',(identifier,)))
        doc=con.execute('SELECT * FROM exchange_documents WHERE aggregate_id=%s',(identifier,)).fetchone()
        job=con.execute('SELECT * FROM print_jobs WHERE aggregate_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(identifier,)).fetchone()
        return clean({'aggregate':a,'children':children,'document':doc,'printJob':job})


def _codes(con,s,order,codes,frame=None):
    if not isinstance(codes,list) or not codes or len(codes)>100:raise RuleError('Камера должна передать от 1 до 100 кодов.')
    if any(not isinstance(c,str) or not re.fullmatch('DEMO-'+order['product_id']+r'-[A-Za-z0-9-]{1,60}',c) for c in codes):
        raise RuleError('Кадр содержит нечитаемый код или код другой продукции.')
    if len(set(codes))!=len(codes):raise RuleError('В одном кадре найден повторный код. Состав не изменён.')
    digest=hashlib.sha256(json.dumps(codes).encode()).hexdigest()
    if frame:
        try:UUID(frame)
        except (ValueError,TypeError):raise RuleError('Некорректный идентификатор кадра.') from None
        previous=con.execute('SELECT * FROM camera_frames WHERE id=%s',(frame,)).fetchone()
        if previous:
            if previous['payload_hash']!=digest or previous['order_id']!=order['id']:raise RuleError('Идентификатор кадра повторён с другим содержимым.')
            return {'frameRepeated':True}
    occupied=con.execute('SELECT count(*) AS n FROM draft_codes').fetchone()['n']
    if occupied+len(codes)>order['recipe_quantity']:raise RuleError('Кодов больше, чем предусмотрено рецептом короба.')
    if con.execute('SELECT 1 FROM box_members WHERE code=ANY(%s) UNION SELECT 1 FROM draft_codes WHERE code=ANY(%s) LIMIT 1',(codes,codes)).fetchone():
        raise RuleError('Код уже включён в короб или присутствует в текущем составе.')
    for i,code in enumerate(codes):
        con.execute("INSERT INTO marking_codes(raw_code,product_id,source) VALUES(%s,%s,'demo') ON CONFLICT DO NOTHING",(code,order['product_id']))
        con.execute('INSERT INTO draft_codes VALUES(%s,%s)',(code,occupied+i))
    if frame:con.execute('INSERT INTO camera_frames(id,order_id,payload_hash,read_count) VALUES(%s,%s,%s,%s)',(frame,order['id'],digest,len(codes)))
    return {'read':len(codes)}


def _close_box(con,s,order,user):
    codes=[r['code'] for r in con.execute('SELECT code FROM draft_codes ORDER BY position')]
    if len(codes)!=order['recipe_quantity']:raise RuleError(f"Необходимо {order['recipe_quantity']} фактически считанных кодов; сейчас {len(codes)}.")
    current=con.execute('SELECT good_boxes FROM production_orders WHERE id=%s',(order['id'],)).fetchone()['good_boxes']
    if current>=order['target_boxes']:raise RuleError('План задания выполнен. Завершите задание или измените план отдельным согласованным действием.')
    identifier=next_id(con,'BOX')
    _aggregate(con,s,order,identifier,'box',len(codes))
    con.execute('DELETE FROM draft_codes')
    for i,code in enumerate(codes):con.execute('INSERT INTO box_members VALUES(%s,%s,%s)',(code,identifier,i))
    con.execute('UPDATE production_orders SET good_boxes=good_boxes+1 WHERE id=%s',(order['id'],))
    event(con,user,f'Закрыт короб {identifier}, задание {order["id"]}, {len(codes)} кодов')
    return identifier


def _aggregate(con,s,order,identifier,kind,count):
    con.execute('INSERT INTO aggregates(id,kind,product_id,line_id,batch_id,created_at,order_id,shift_id,unit_count) VALUES(%s,%s,%s,%s,%s,now(),%s,%s,%s)',
                (identifier,kind,order['product_id'],order['line_id'],order['batch_id'],order['id'],s['shift_id'],count))
    doc=next_id(con,'DOC')
    con.execute("INSERT INTO exchange_documents(id,aggregate_id,kind,status,attempts,created_at,message) VALUES(%s,%s,%s,'queued',0,now(),'Ожидает учебной обработки')",(doc,identifier,kind))
    con.execute("INSERT INTO print_jobs VALUES(%s,%s,'ready',now())",(uuid4(),identifier))


def _add_box(con,order,identifier):
    box=con.execute("SELECT * FROM aggregates WHERE id=%s AND kind='box'",(identifier,)).fetchone()
    if not box or box['order_id']!=order['id'] or box['lifecycle']!='closed':raise RuleError('Короб должен быть доступен и относиться к текущему заданию.')
    if con.execute('SELECT 1 FROM pallet_members WHERE box_id=%s UNION SELECT 1 FROM draft_pallet_boxes WHERE box_id=%s',(identifier,identifier)).fetchone():raise RuleError('Короб уже добавлен в паллету.')
    if not con.execute("SELECT 1 FROM print_jobs WHERE aggregate_id=%s AND status='verified'",(identifier,)).fetchone():raise RuleError('Сначала выполните контрольное считывание этикетки короба.')
    n=con.execute('SELECT count(*) AS n FROM draft_pallet_boxes').fetchone()['n']
    if n>=order['pallet_quantity']:raise RuleError('Паллета заполнена. Закройте её.')
    position=con.execute('SELECT coalesce(max(position),-1)+1 AS n FROM draft_pallet_boxes').fetchone()['n']
    con.execute('INSERT INTO draft_pallet_boxes VALUES(%s,%s)',(identifier,position))


def _exchange(con,s,user,data):
    if not s['online']:raise RuleError('Имитатор связи отключён. Очередь сохранена.')
    identifier=data.get('id')
    outcome=data.get('outcome','accepted')
    if outcome not in ('accepted','rejected','unknown'):raise RuleError('Неизвестный ответ имитатора.')
    if identifier:
        docs=list(con.execute('SELECT * FROM exchange_documents WHERE id=%s FOR UPDATE',(identifier,)))
    else:
        docs=list(con.execute("SELECT d.* FROM exchange_documents d JOIN aggregates a ON a.id=d.aggregate_id WHERE d.status='queued' AND d.provider='simulator' AND a.lifecycle='closed' "
                              "AND EXISTS(SELECT 1 FROM print_jobs j WHERE j.aggregate_id=a.id AND j.status='verified') "
                              "AND (d.kind='box' OR NOT EXISTS(SELECT 1 FROM pallet_members p JOIN aggregates c ON c.id=p.box_id JOIN exchange_documents x ON x.aggregate_id=p.box_id "
                              "WHERE p.pallet_id=a.id AND (x.status<>'accepted' OR c.lifecycle<>'closed'))) "
                              "ORDER BY CASE d.kind WHEN 'box' THEN 0 ELSE 1 END,d.created_at,d.id LIMIT 100 FOR UPDATE OF d SKIP LOCKED"))
    processed=0;skipped=0
    for d in docs:
        if d['provider']!='simulator':raise RuleError('Результат реального документа нельзя подменять ответом имитатора.')
        if d['status'] not in ('queued','rejected'):
            if identifier:raise RuleError('Повторная отправка запрещена: проверьте неопределённый или принятый результат.')
            continue
        ready=con.execute("SELECT 1 FROM aggregates a WHERE a.id=%s AND a.lifecycle='closed' AND EXISTS(SELECT 1 FROM print_jobs p WHERE p.aggregate_id=a.id AND p.status='verified')",(d['aggregate_id'],)).fetchone()
        if d['kind']=='pallet' and con.execute("SELECT 1 FROM pallet_members p JOIN aggregates a ON a.id=p.box_id JOIN exchange_documents c ON c.aggregate_id=p.box_id WHERE p.pallet_id=%s AND (c.status<>'accepted' OR a.lifecycle<>'closed') LIMIT 1",(d['aggregate_id'],)).fetchone():ready=False
        if not ready:
            if identifier:raise RuleError('Проверьте контроль этикетки, карантин и обработку вложенных коробов.')
            skipped+=1;continue
        number=d['attempts']+1
        message={'accepted':'Принят имитатором; не ГИС МТ','rejected':'Учебный отказ: проверьте документ','unknown':'Учебный тайм-аут: требуется уточнение результата'}[outcome]
        con.execute('UPDATE exchange_documents SET status=%s,attempts=%s,message=%s WHERE id=%s',(outcome,number,message,d['id']))
        con.execute('INSERT INTO exchange_attempts(document_id,number,outcome) VALUES(%s,%s,%s)',(d['id'],number,outcome))
        if outcome!='accepted':incident(con,user,s,message,'exchange',d['id'])
        event(con,user,f"{d['id']}: {message}",'info' if outcome=='accepted' else 'warning')
        processed+=1
    return {'processed':processed,'skipped':skipped}


MANAGEMENT={'shift-open','shift-close','order-create','order-start','order-complete','order-plan','order-cancel','quarantine','release','disassemble','incident-resolve','settings','demo-pallet'}


def execute(action,data,request_id,revision,user):
    if action in MANAGEMENT and user['role'] not in ('supervisor','admin'):raise Forbidden('Это действие доступно мастеру смены или администратору.')
    fingerprint=hashlib.sha256(json.dumps([action,data,user['id']],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    UUID(request_id)
    with repository.connect() as con:
        con.execute('SELECT id FROM station WHERE id=1 FOR UPDATE')
        s=row_station(con)
        old=con.execute('SELECT * FROM plant_command_receipts WHERE id=%s',(request_id,)).fetchone()
        if old:
            if old['fingerprint']!=fingerprint:raise repository.Conflict('Идентификатор запроса уже использован другим действием.')
            return {'result':old['result'],'error':old['error'],'station':station_view(con)}
        if revision!=s['revision']:raise repository.Conflict('Состояние изменилось в другом окне. Обновите данные и повторите действие.')
        error=None;result={}
        con.execute('SAVEPOINT business_operation')
        try:result=_dispatch(con,s,user,action,data) or {}
        except RuleError as exc:
            con.execute('ROLLBACK TO SAVEPOINT business_operation')
            error=str(exc)
            incident(con,user,s,error,'operation',str(data.get('id','')) or None,'warning')
            event(con,user,error,'error')
        con.execute('UPDATE station SET revision=revision+1,operator_id=%s WHERE id=1',(user['id'],))
        con.execute('INSERT INTO plant_command_receipts(id,fingerprint,actor,result,error) VALUES(%s,%s,%s,%s,%s)',(request_id,fingerprint,user['id'],Jsonb(clean(result)),error))
        return {'result':clean(result),'error':error,'station':station_view(con)}


def _dispatch(con,s,user,action,data):
    if action=='shift-open':
        if s['shift_id']:raise RuleError('Смена уже открыта.')
        if unfinished(con):raise RuleError('Завершите незавершённую упаковку перед открытием смены.')
        key=next_id(con,'SHIFT');name=note(data.get('name'),'Название смены',3,80)
        con.execute("INSERT INTO work_shifts(id,name,state,opened_by) VALUES(%s,%s,'open',%s)",(key,name,user['id']))
        con.execute('UPDATE station SET shift_id=%s WHERE id=1',(key,));event(con,user,'Открыта смена '+name)
    elif action=='shift-close':
        if not s['shift_id']:raise RuleError('Нет открытой смены.')
        if unfinished(con):raise RuleError('Завершите короб и паллету перед закрытием смены.')
        if s['order_id'] and con.execute("SELECT 1 FROM production_orders WHERE id=%s AND state='running'",(s['order_id'],)).fetchone():raise RuleError('Сначала завершите или приостановите текущее задание.')
        con.execute("UPDATE work_shifts SET state='closed',closed_at=now(),closed_by=%s WHERE id=%s",(user['id'],s['shift_id']))
        con.execute('UPDATE station SET shift_id=NULL,order_id=NULL WHERE id=1');event(con,user,'Закрыта смена '+s['shift_id'])
    elif action=='order-create':
        line=data.get('line');product=data.get('product');batch=note(data.get('batch'),'Партия',1,80)
        if line not in ('LINE-1','LINE-2') or product not in ('1L','5L'):raise RuleError('Выберите линию и продукцию из справочника.')
        target=integer(data.get('targetBoxes'))
        recipes={r['product_id']:r['quantity'] for r in con.execute('SELECT * FROM packing_recipes')}
        pallet=con.execute('SELECT pallet_size FROM station_settings WHERE id=1').fetchone()['pallet_size']
        if target%pallet:raise RuleError(f'В демо план должен быть кратен {pallet} коробам; неполные паллеты требуют отдельного регламента.')
        key=next_id(con,'ORDER');batch_id=repository.batch_id(con,line,batch)
        con.execute('INSERT INTO production_orders(id,line_id,batch_id,product_id,target_boxes,recipe_quantity,pallet_quantity,created_by,note) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (key,line,batch_id,product,target,recipes[product],pallet,user['id'],str(data.get('note',''))[:500]))
        event(con,user,f'Создано задание {key}, {line}, партия {batch}, план {target} коробов');return {'id':key}
    elif action=='order-start':
        if not s['shift_id']:raise RuleError('Сначала откройте смену.')
        if unfinished(con):raise RuleError('Завершите текущую упаковку перед запуском задания.')
        current=con.execute('SELECT state FROM production_orders WHERE id=%s',(s['order_id'],)).fetchone() if s['order_id'] else None
        if current and current['state']=='running':raise RuleError('Сначала приостановите или завершите текущее задание.')
        order=con.execute('SELECT * FROM production_orders WHERE id=%s',(data.get('id'),)).fetchone()
        if not order or order['state'] not in ('planned','paused'):raise RuleError('Задание недоступно для запуска.')
        con.execute("UPDATE production_orders SET state='running',started_at=coalesce(started_at,now()) WHERE id=%s",(order['id'],))
        con.execute('UPDATE station SET order_id=%s,product_id=%s,line_id=%s,batch_id=%s,pallet_target=NULL WHERE id=1',(order['id'],order['product_id'],order['line_id'],order['batch_id']))
        event(con,user,'Запущено задание '+order['id'])
    elif action in ('order-plan','order-cancel'):
        order=con.execute('SELECT * FROM production_orders WHERE id=%s',(data.get('id'),)).fetchone()
        if not order or order['state'] in ('completed','cancelled'):raise RuleError('Задание уже закрыто.')
        reason=note(data.get('reason'))
        if action=='order-plan':
            target=integer(data.get('targetBoxes'))
            if target<order['good_boxes'] or target%order['pallet_quantity']:raise RuleError('План не может быть меньше факта и должен быть кратен размеру паллеты.')
            con.execute('UPDATE production_orders SET target_boxes=%s WHERE id=%s',(target,order['id']))
            event(con,user,f"Изменён план {order['id']}: {order['target_boxes']} → {target}. {reason}")
        else:
            if order['good_boxes'] or (s['order_id']==order['id'] and unfinished(con)):raise RuleError('Задание с выпуском или незавершённым составом нельзя отменить.')
            con.execute("UPDATE production_orders SET state='cancelled',completed_at=now() WHERE id=%s",(order['id'],))
            con.execute('UPDATE station SET order_id=NULL WHERE order_id=%s',(order['id'],));event(con,user,'Отменено задание '+order['id']+': '+reason,'warning')
    elif action in ('order-pause','order-resume','order-complete'):
        if not s['order_id']:raise RuleError('Нет текущего задания.')
        order=con.execute('SELECT * FROM production_orders WHERE id=%s',(s['order_id'],)).fetchone()
        if action=='order-pause':
            if order['state']!='running':raise RuleError('Задание уже не выполняется.')
            reason=note(data.get('reason'),'Причина паузы')
            con.execute("UPDATE production_orders SET state='paused' WHERE id=%s",(order['id'],));event(con,user,'Пауза '+order['id']+': '+reason,'warning')
        elif action=='order-resume':
            if order['state']!='paused' or not s['shift_id']:raise RuleError('Задание нельзя возобновить.')
            con.execute("UPDATE production_orders SET state='running' WHERE id=%s",(order['id'],));event(con,user,'Возобновлено задание '+order['id'])
        else:
            if unfinished(con):raise RuleError('Сначала завершите текущую упаковку.')
            if con.execute("SELECT 1 FROM aggregates a WHERE a.order_id=%s AND a.kind='box' AND a.lifecycle<>'disassembled' AND NOT EXISTS(SELECT 1 FROM pallet_members p WHERE p.box_id=a.id) LIMIT 1",(order['id'],)).fetchone():raise RuleError('В задании остались короба, не включённые в паллеты.')
            if order['good_boxes']<order['target_boxes']:note(data.get('reason'),'Причина досрочного завершения')
            con.execute("UPDATE production_orders SET state='completed',completed_at=now() WHERE id=%s",(order['id'],))
            con.execute('UPDATE station SET order_id=NULL WHERE id=1');event(con,user,'Завершено задание '+order['id']+': '+str(data.get('reason','план выполнен')))
    elif action in ('camera','scan-group','scan-code'):
        order=active_order(con,s)
        if action=='camera':
            n=con.execute('SELECT count(*) AS n FROM draft_codes').fetchone()['n'];needed=order['recipe_quantity']-n
            if needed<=0:raise RuleError('Короб заполнен. Закройте его.')
            scenario=data.get('scenario','valid')
            if scenario not in ('valid','missing','duplicate','wrong'):raise RuleError('Неизвестный сценарий камеры.')
            codes=[next_id(con,order['product_id']) for _ in range(needed)]
            if scenario=='missing':codes=codes[:-1]
            if scenario=='duplicate':codes[-1]=codes[0] if len(codes)>1 else 'unreadable'
            if scenario=='wrong':codes[-1]='DEMO-'+('5L' if order['product_id']=='1L' else '1L')+'-WRONG'
            if not codes:raise RuleError('Камера не считала ни одного кода.')
            result=_codes(con,s,order,codes,str(uuid4()))
            if scenario=='missing':incident(con,user,s,'Неполный кадр: требуется повторное считывание','camera',order['id'],'warning')
            event(con,user,f"Имитатор камеры: прочитано {len(codes)} кодов по заданию {order['id']}")
            return result
        codes=[data.get('code')] if action=='scan-code' else data.get('codes')
        result=_codes(con,s,order,codes,data.get('frameId'));event(con,user,'Получены коды: '+str(result.get('read',0)));return result
    elif action=='draft-clear':
        reason=note(data.get('reason'),'Причина сброса')
        con.execute('DELETE FROM draft_codes');event(con,user,'Сброшен незавершённый короб: '+reason,'warning')
    elif action=='box-close':
        return {'aggregate':_close_box(con,s,active_order(con,s),user)}
    elif action=='pallet-add':
        _add_box(con,active_order(con,s),data.get('id'));event(con,user,'Добавлен в паллету короб '+str(data.get('id')))
    elif action=='pallet-remove':
        active_order(con,s)
        removed=con.execute('DELETE FROM draft_pallet_boxes WHERE box_id=%s RETURNING box_id',(data.get('id'),)).fetchone()
        if not removed:raise RuleError('Короб отсутствует в текущей паллете.')
        event(con,user,'Из незавершённой паллеты удалён '+data['id'])
    elif action=='demo-pallet':
        if user['role'] not in ('supervisor','admin'):raise Forbidden('Массовый учебный сценарий доступен мастеру.')
        order=active_order(con,s)
        if con.execute('SELECT 1 FROM draft_codes LIMIT 1').fetchone():raise RuleError('Сначала завершите текущий короб.')
        for available in station_view(con)['available']:
            n=con.execute('SELECT count(*) AS n FROM draft_pallet_boxes').fetchone()['n']
            if n>=order['pallet_quantity']:break
            _add_box(con,order,available['id'])
        n=con.execute('SELECT count(*) AS n FROM draft_pallet_boxes').fetchone()['n']
        if order['good_boxes']+order['pallet_quantity']-n>order['target_boxes']:raise RuleError('Оставшийся план недостаточен для учебного сценария паллеты.')
        for _ in range(order['pallet_quantity']-n):
            codes=[next_id(con,order['product_id']) for _ in range(order['recipe_quantity'])]
            _codes(con,s,order,codes,str(uuid4()))
            key=_close_box(con,s,order,user)
            con.execute("UPDATE print_jobs SET status='verified' WHERE aggregate_id=%s",(key,))
            _add_box(con,order,key)
        event(con,user,'Массовый УЧЕБНЫЙ сценарий: сгенерированы коды и имитирован контроль этикеток для паллеты','warning')
    elif action=='pallet-close':
        order=active_order(con,s)
        children=[r['box_id'] for r in con.execute('SELECT box_id FROM draft_pallet_boxes ORDER BY position')]
        if len(children)!=order['pallet_quantity']:raise RuleError(f"Необходимо {order['pallet_quantity']} разных коробов.")
        if con.execute("SELECT 1 FROM aggregates a WHERE a.id=ANY(%s) AND a.lifecycle<>'closed' LIMIT 1",(children,)).fetchone():raise RuleError('В составе есть короб на карантине.')
        key=next_id(con,'PAL');_aggregate(con,s,order,key,'pallet',len(children));con.execute('DELETE FROM draft_pallet_boxes')
        for i,child in enumerate(children):con.execute('INSERT INTO pallet_members VALUES(%s,%s,%s)',(child,key,i))
        con.execute('UPDATE production_orders SET pallets=pallets+1 WHERE id=%s',(order['id'],));event(con,user,'Закрыта паллета '+key);return {'aggregate':key}
    elif action in ('print-open','print-fail','label-verify'):
        reference=data.get('id');job=con.execute('SELECT * FROM print_jobs WHERE aggregate_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(reference,)).fetchone()
        if not job or job['status']=='cancelled':raise RuleError('Задание печати недоступно.')
        status={'print-open':'preview_opened','print-fail':'failed','label-verify':'verified'}[action]
        if action=='label-verify':
            if data.get('code')!=reference:raise RuleError('Контрольный код этикетки не совпадает с упаковкой.')
            if job['status'] not in ('preview_opened','verified'):raise RuleError('Сначала откройте макет этикетки или повторите печать после сбоя.')
        con.execute('UPDATE print_jobs SET status=%s WHERE id=%s',(status,job['id']))
        if action=='print-open':con.execute('UPDATE aggregates SET print_opens=print_opens+1 WHERE id=%s',(reference,))
        message={'print-open':'Открыт учебный макет; физическая печать не подтверждена','print-fail':'Имитирован сбой принтера','label-verify':'Выполнено контрольное считывание учебной этикетки'}[action]
        con.execute('INSERT INTO print_job_events VALUES(%s,%s,%s,now(),%s,%s)',(uuid4(),job['id'],status,user['id'],message))
        if action=='print-fail':incident(con,user,s,message,'printing',reference)
        event(con,user,message+': '+reference,'error' if action=='print-fail' else 'info')
    elif action=='exchange-run':return _exchange(con,s,user,data)
    elif action=='exchange-reconcile':
        d=con.execute('SELECT * FROM exchange_documents WHERE id=%s',(data.get('id'),)).fetchone()
        if not d or d['status']!='unknown' or d['provider']!='simulator':raise RuleError('Учебное уточнение доступно только для неопределённого результата имитатора.')
        if not s['online']:raise RuleError('Имитатор связи отключён.')
        con.execute("UPDATE exchange_documents SET status='accepted',message='Имитатор подтвердил предыдущую отправку; не ГИС МТ' WHERE id=%s",(d['id'],))
        event(con,user,'Уточнён результат без повторной отправки '+d['id'])
    elif action=='connection':
        if type(data.get('online')) is not bool:raise RuleError('Укажите состояние связи.')
        con.execute('UPDATE station SET online=%s WHERE id=1',(data['online'],));event(con,user,'Имитатор связи: '+('доступен' if data['online'] else 'отключён'),'warning')
    elif action in ('quarantine','release','disassemble'):
        reason=note(data.get('reason'));a=con.execute('SELECT * FROM aggregates WHERE id=%s',(data.get('id'),)).fetchone()
        if not a or a['lifecycle']=='disassembled':raise RuleError('Упаковка недоступна для изменения.')
        if action=='disassemble':
            d=con.execute('SELECT * FROM exchange_documents WHERE aggregate_id=%s',(a['id'],)).fetchone()
            if a['kind']!='box' or not d or d['status'] not in ('queued','rejected'):raise RuleError('Разборка допустима только до принятия/неопределённой отправки документа короба.')
            if con.execute('SELECT 1 FROM pallet_members WHERE box_id=%s UNION SELECT 1 FROM draft_pallet_boxes WHERE box_id=%s',(a['id'],a['id'])).fetchone():raise RuleError('Короб уже включён в паллету.')
            con.execute('INSERT INTO membership_history(box_id,code,position,actor,reason) SELECT box_id,code,position,%s,%s FROM box_members WHERE box_id=%s',(user['id'],reason,a['id']))
            con.execute('DELETE FROM box_members WHERE box_id=%s',(a['id'],))
            con.execute("UPDATE aggregates SET lifecycle='disassembled' WHERE id=%s",(a['id'],))
            con.execute("UPDATE exchange_documents SET status='cancelled',message=%s WHERE aggregate_id=%s",('Локальная разборка: '+reason,a['id']))
            con.execute("UPDATE print_jobs SET status='cancelled' WHERE aggregate_id=%s",(a['id'],))
            if a['order_id']:con.execute('UPDATE production_orders SET good_boxes=good_boxes-1 WHERE id=%s',(a['order_id'],))
        else:
            if action=='release' and a['kind']=='pallet' and con.execute("SELECT 1 FROM pallet_members p JOIN aggregates a ON a.id=p.box_id WHERE p.pallet_id=%s AND a.lifecycle='quarantined' LIMIT 1",(a['id'],)).fetchone():raise RuleError('Сначала снимите карантин с вложенных коробов.')
            con.execute('UPDATE aggregates SET lifecycle=%s WHERE id=%s',('quarantined' if action=='quarantine' else 'closed',a['id']))
            if action=='quarantine':
                con.execute("UPDATE aggregates SET lifecycle='quarantined' WHERE id IN(SELECT pallet_id FROM pallet_members WHERE box_id=%s)",(a['id'],))
                incident(con,user,s,'Карантин: '+reason,'quality',a['id'])
        event(con,user,{'quarantine':'Карантин','release':'Снятие карантина','disassemble':'Разобран короб'}[action]+' '+a['id']+': '+reason,'warning')
    elif action in ('incident-ack','incident-resolve'):
        try:key=str(UUID(str(data.get('id'))))
        except ValueError:raise RuleError('Некорректный идентификатор инцидента.') from None
        item=con.execute('SELECT * FROM incidents WHERE id=%s',(key,)).fetchone()
        if not item or item['state']=='resolved':raise RuleError('Инцидент уже закрыт или отсутствует.')
        if action=='incident-ack':con.execute("UPDATE incidents SET state='acknowledged',assigned_to=%s WHERE id=%s",(user['id'],key))
        else:
            reason=note(data.get('reason'),'Результат устранения')
            if item['category']=='exchange' and con.execute("SELECT 1 FROM exchange_documents WHERE id=%s AND status IN ('rejected','unknown')",(item['reference'],)).fetchone():raise RuleError('Сначала устраните ошибку обмена или уточните результат.')
            if item['category']=='printing' and con.execute("SELECT 1 FROM print_jobs WHERE aggregate_id=%s AND status='failed'",(item['reference'],)).fetchone():raise RuleError('Сначала восстановите печать.')
            if item['category']=='quality' and con.execute("SELECT 1 FROM aggregates WHERE id=%s AND lifecycle='quarantined'",(item['reference'],)).fetchone():raise RuleError('Сначала снимите карантин либо разберите упаковку по регламенту.')
            con.execute("UPDATE incidents SET state='resolved',resolution=%s,resolved_at=now(),assigned_to=%s WHERE id=%s",(reason,user['id'],key))
        event(con,user,'Инцидент '+key+': '+('взят в работу' if action=='incident-ack' else reason))
    elif action=='settings':
        if unfinished(con):raise RuleError('Завершите незавершённую упаковку.')
        one=integer(data.get('one'),1,100);five=integer(data.get('five'),1,100);pallet=integer(data.get('pallet'),1,100)
        con.execute("UPDATE packing_recipes SET quantity=%s WHERE product_id='1L'",(one,));con.execute("UPDATE packing_recipes SET quantity=%s WHERE product_id='5L'",(five,))
        con.execute('UPDATE station_settings SET pallet_size=%s WHERE id=1',(pallet,));event(con,user,'Изменены рецепты новых заданий; существующие задания сохраняют свои параметры')
        if 'shiftHours' in data or 'targetPallets' in data:
            hours=integer(data.get('shiftHours'),1,24);target=integer(data.get('targetPallets'),1,100)
            con.execute('UPDATE capacity_settings SET shift_hours=%s,target_pallets=%s WHERE id=1',(hours,target))
    else:raise RuleError('Неизвестная операция производственного АРМ.')


def reports(first,last):
    if last<first or (last-first).days>30:raise RuleError('Выберите период от 1 до 31 дня.')
    start=day_bounds(first)[0];end=day_bounds(last)[1]
    with repository.connect() as con:
        rows=list(con.execute("SELECT (a.created_at AT TIME ZONE 'Europe/Moscow')::date AS day,a.line_id,a.product_id,"
                              "count(*) FILTER(WHERE a.kind='box' AND a.lifecycle<>'disassembled') AS boxes,"
                              "coalesce(sum(a.unit_count) FILTER(WHERE a.kind='box' AND a.lifecycle<>'disassembled'),0) AS bottles,"
                              "count(*) FILTER(WHERE a.kind='pallet' AND a.lifecycle<>'disassembled') AS pallets,"
                              "count(*) FILTER(WHERE a.lifecycle='quarantined') AS held FROM aggregates a WHERE a.created_at>=%s AND a.created_at<%s GROUP BY 1,2,3 ORDER BY 1 DESC,2,3",(start,end)))
        return clean({'first':first,'last':last,'items':rows,'basis':'Факт закрытия упаковок; разобранные короба исключены из чистого выпуска, карантин показан отдельно.'})


def catalog():
    with repository.connect() as con:
        return clean({'lines':list(con.execute('SELECT * FROM production_lines ORDER BY id')),
                      'products':list(con.execute('SELECT p.*,r.quantity FROM products p JOIN packing_recipes r ON r.product_id=p.id ORDER BY volume_l')),
                      'palletSize':con.execute('SELECT pallet_size FROM station_settings WHERE id=1').fetchone()['pallet_size'],
                      'capacity':con.execute('SELECT shift_hours,target_pallets FROM capacity_settings WHERE id=1').fetchone()})


def process_simulator_batch(keepalive_seconds=0):
    """Independent service transaction. No-op polls do not invalidate the station revision."""
    with repository.connect() as con:
        con.execute("INSERT INTO service_heartbeats(id,last_seen,alive_until) VALUES('exchange-simulator',now(),now()+%s*interval '1 second') "
                    'ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,alive_until=excluded.alive_until',(keepalive_seconds,))
        locked=con.execute('SELECT * FROM station WHERE id=1 FOR UPDATE SKIP LOCKED').fetchone()
        if not locked or not locked['online']:return {'processed':0,'skipped':0}
        result=_exchange(con,locked,{'id':'simulator-service'}, {})
        if result['processed']:
            con.execute('UPDATE station SET revision=revision+1 WHERE id=1')
            con.execute("UPDATE service_heartbeats SET processed=processed+%s WHERE id='exchange-simulator'",(result['processed'],))
        return result
