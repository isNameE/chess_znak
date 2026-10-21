from concurrent.futures import ThreadPoolExecutor
import threading
import time
import psycopg


def test_code_cannot_become_draft_and_closed_in_concurrent_transactions(db):
    with db.connect() as con:
        batch=con.execute('SELECT batch_id FROM station WHERE id=1').fetchone()['batch_id']
        con.execute("INSERT INTO marking_codes(raw_code,product_id,source) VALUES('DEMO-1L-RACE','1L','demo')")
        con.execute("INSERT INTO aggregates(id,kind,product_id,line_id,batch_id,created_at) VALUES('DEMO-BOX-RACE','box','1L','LINE-1',%s,now())",(batch,))
    inserted=threading.Event();attempted=threading.Event()
    def draft():
        with db.connect() as con:
            con.execute("INSERT INTO draft_codes VALUES('DEMO-1L-RACE',0)")
            inserted.set();assert attempted.wait(5)
            time.sleep(.1)
    def closed():
        assert inserted.wait(5)
        attempted.set()
        try:
            with db.connect() as con:con.execute("INSERT INTO box_members VALUES('DEMO-1L-RACE','DEMO-BOX-RACE',0)")
        except psycopg.Error:return 'blocked'
        return 'incorrect'
    with ThreadPoolExecutor(max_workers=2) as pool:
        one=pool.submit(draft);two=pool.submit(closed)
        one.result(timeout=10);assert two.result(timeout=10)=='blocked'
    with db.connect() as con:
        assert con.execute('SELECT count(*) AS n FROM draft_codes').fetchone()['n']==1
        assert con.execute('SELECT count(*) AS n FROM box_members').fetchone()['n']==0
