"""Repeatable bounded-query benchmark in a temporary schema, not application data."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from config import settings
import repository
import plant


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boxes',type=int,default=1500)
    parser.add_argument('--out',type=Path,default=Path('data/benchmark-plant.json'))
    args=parser.parse_args()
    if not 50<=args.boxes<=45000 or args.boxes%50:raise SystemExit('Choose 50..45000 boxes, a multiple of 50.')
    dsn=settings().database_url;schema='bench_arm_'+uuid4().hex
    with psycopg.connect(dsn) as con:con.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    repository.connect=lambda:psycopg.connect(dsn,options='-c search_path='+schema,row_factory=dict_row)
    started=time.perf_counter()
    try:
        repository.migrate()
        n=args.boxes;codes=n*15;pallets=n//50
        print(f'Preparing isolated workload: {n} boxes, {codes} bottle codes, {pallets} pallets',flush=True)
        with repository.connect() as con:
            batches=[repository.batch_id(con,line,'BENCHMARK-SYNTHETIC') for line in ('LINE-1','LINE-2')]
            con.execute("INSERT INTO aggregates(id,kind,product_id,line_id,batch_id,created_at,unit_count) SELECT 'DEMO-LOAD-BOX-'||lpad(n::text,9,'0'),'box','1L',"
                        "CASE WHEN ((n-1)/50)%%2=0 THEN 'LINE-1' ELSE 'LINE-2' END,CASE WHEN ((n-1)/50)%%2=0 THEN %s ELSE %s END,"
                        "now()-((n-1)/1500)*interval '1 day',15 FROM generate_series(1,%s) AS s(n)",(*batches,n))
            con.execute("INSERT INTO aggregates(id,kind,product_id,line_id,batch_id,created_at,unit_count) SELECT 'DEMO-LOAD-PAL-'||lpad(n::text,9,'0'),'pallet','1L',"
                        "CASE WHEN (n-1)%%2=0 THEN 'LINE-1' ELSE 'LINE-2' END,CASE WHEN (n-1)%%2=0 THEN %s ELSE %s END,"
                        "now()-((n-1)/30)*interval '1 day',50 FROM generate_series(1,%s) AS s(n)",(*batches,pallets))
            con.execute("INSERT INTO marking_codes(raw_code,product_id,source) SELECT 'DEMO-1L-LOAD-'||lpad(n::text,10,'0'),'1L','demo' FROM generate_series(1,%s) AS s(n)",(codes,))
            print('Codes stored; linking concrete memberships with database checks enabled...',flush=True)
            con.execute("INSERT INTO box_members(code,box_id,position) SELECT 'DEMO-1L-LOAD-'||lpad(n::text,10,'0'),'DEMO-LOAD-BOX-'||lpad(((n-1)/15+1)::text,9,'0'),(n-1)%%15 FROM generate_series(1,%s) AS s(n)",(codes,))
            con.execute("INSERT INTO pallet_members(box_id,pallet_id,position) SELECT 'DEMO-LOAD-BOX-'||lpad(n::text,9,'0'),'DEMO-LOAD-PAL-'||lpad(((n-1)/50+1)::text,9,'0'),(n-1)%%50 FROM generate_series(1,%s) AS s(n)",(n,))
        with repository.connect() as con:
            con.execute('ANALYZE aggregates');con.execute('ANALYZE box_members');con.execute('ANALYZE pallet_members');con.execute('ANALYZE marking_codes')
        measurements={}
        queries={'overview':lambda:plant.overview(),'registry_page_25':lambda:plant.listing('aggregates',limit=25),
                 'bottle_to_pallet':lambda:plant.listing('aggregates',q='DEMO-1L-LOAD-'+str(codes).zfill(10)),
                 'aggregate_detail':lambda:plant.aggregate_detail('DEMO-LOAD-BOX-'+str(n).zfill(9))}
        for name,fn in queries.items():
            times=[];sizes=[]
            for _ in range(7):
                start=time.perf_counter();result=fn();times.append((time.perf_counter()-start)*1000)
                sizes.append(len(json.dumps(result,ensure_ascii=False).encode()))
            measurements[name]={'median_ms':round(statistics.median(times),1),'max_ms':round(max(times),1),'response_bytes':max(sizes)}
            print(name,measurements[name],flush=True)
        assert len(queries['registry_page_25']()['items'])==25
        assert len(queries['bottle_to_pallet']()['items'])==2
        observed=queries['overview']()['totals']
        assert observed['boxes']==min(n,1500) and observed['bottles']==min(n,1500)*15,observed
        assert measurements['registry_page_25']['response_bytes']<50000
        result={'workload':{'boxes':n,'bottle_codes':codes,'pallets':pallets,'synthetic':True},'observed_today':observed,'measurements':measurements,
                'elapsed_seconds':round(time.perf_counter()-started,1),'note':'Local development machine; not factory acceptance or sustained camera/print load.'}
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        assert schema.startswith('bench_arm_') and len(schema)==42
        with psycopg.connect(dsn) as con:con.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        print('Temporary benchmark schema removed; application data unchanged.',flush=True)


if __name__=='__main__':main()
