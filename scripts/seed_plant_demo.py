"""Explicitly create synthetic plant orders, optionally one fully simulated pallet."""
import argparse
from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import repository
import plant

USER={'id':'demo-operator','role':'admin','mode':'demo','display_name':'Демо'}


def run(action,data=None):
    with repository.connect() as con:revision=plant.station_view(con)['revision']
    result=plant.execute(action,data or {},str(uuid4()),revision,USER)
    if result['error']:raise SystemExit(result['error'])
    return result['result']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-pallet',action='store_true')
    args=parser.parse_args()
    repository.migrate()
    with repository.connect() as con:
        if con.execute('SELECT 1 FROM production_orders LIMIT 1').fetchone():raise SystemExit('Orders already exist; no changes made.')
        station=plant.station_view(con)
        if station['draft'] or station['pallet']:raise SystemExit('Finish the existing draft before generating demo orders.')
    if not station['shift']:run('shift-open',{'name':'Демонстрационная смена · 8 часов'})
    a=run('order-create',{'line':'LINE-1','product':'1L','batch':'ДЕМО-ПАРТИЯ-А','targetBoxes':1000,'note':'Учебный план: 20 паллет по 50 коробов.'})['id']
    run('order-create',{'line':'LINE-2','product':'5L','batch':'ДЕМО-ПАРТИЯ-Б','targetBoxes':500,'note':'Учебный план: 10 паллет по 50 коробов.'})
    run('order-start',{'id':a})
    if args.with_pallet:
        run('demo-pallet')
        pallet=run('pallet-close')['aggregate']
        run('print-open',{'id':pallet});run('label-verify',{'id':pallet,'code':pallet})
        for _ in range(3):
            if not run('exchange-run')['processed']:break
    print('Synthetic orders: 20 + 10 pallets for two lines. One active shared station. No physical equipment or CRPT calls.')
