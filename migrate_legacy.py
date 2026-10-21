"""One-way import; the original SQLite file is read-only and retained."""
import argparse
import json
import sqlite3
from pathlib import Path
from uuid import uuid4
from domain import initial_state, log
import repository


def import_sqlite(path):
    path = Path(path).resolve(strict=True)
    with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True) as con:
        row = con.execute("SELECT payload FROM app_state WHERE id=1").fetchone()
    old = json.loads(row[0])
    if old.get("version") != 1:
        raise ValueError("Поддерживается только исходный формат SQLite v1.")
    s = initial_state()
    s.update(old)
    s.update(version=2, revision=1, line="LINE-1", batch="ПЕРЕНОС-ДЕМО", operator="demo-operator", printJobs=[])
    for a in s["boxes"] + s["pallets"]:
        a.update(line=s["line"], batch=s["batch"])
        if "product" not in a:
            a["product"] = next((b["product"] for b in s["boxes"] if b["id"] in a["boxes"]), s["product"])
        s["printJobs"].append({"id":str(uuid4()),"aggregate":a["id"],"status":"preview_opened" if a["prints"] else "ready","created":a["created"]})
    for e in s["events"]:
        e.update(id=str(uuid4()),actor="demo-operator")
    log(s,"Перенесены учебные данные из SQLite v1; оригинал сохранён")
    repository.migrate()
    with repository.connect() as con:
        con.execute("SELECT id FROM station WHERE id=1 FOR UPDATE")
        current = repository.load(con)
        if current["boxes"] or current["draft"] or current["palletDraft"] or current["events"]:
            raise ValueError("Импорт разрешён только в пустую базу. Существующие данные не изменены.")
        repository.save(con,s)
    return {"boxes":len(s["boxes"]),"pallets":len(s["pallets"]),"documents":len(s["documents"])}


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("sqlite",type=Path)
    print(import_sqlite(parser.parse_args().sqlite))
