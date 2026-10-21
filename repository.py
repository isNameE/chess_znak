"""Normalized PostgreSQL storage. Only the small station row is locked per command."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4
import psycopg
from psycopg.rows import dict_row
from config import settings, ROOT
from domain import initial_state, apply, log


class Conflict(ValueError):
    pass


def connect():
    return psycopg.connect(settings().database_url, row_factory=dict_row, connect_timeout=5)


def migrate():
    with connect() as con:
        con.execute("SELECT pg_advisory_xact_lock(81075601)")
        exists = con.execute("SELECT to_regclass('schema_migrations') AS t").fetchone()["t"]
        versions = {r["version"] for r in con.execute("SELECT version FROM schema_migrations")} if exists else set()
        for file in sorted((ROOT / "migrations").glob("*.sql")):
            version = int(file.name.split("_")[0])
            if version not in versions:
                con.execute(file.read_text(encoding="utf-8"))
                con.execute("INSERT INTO schema_migrations(version) VALUES(%s)", (version,))
        if not con.execute("SELECT 1 FROM station WHERE id=1").fetchone():
            save(con, initial_state())


def batch_id(con, line, number):
    return con.execute("INSERT INTO batches(line_id,number) VALUES(%s,%s) ON CONFLICT(line_id,number) "
                       "DO UPDATE SET number=excluded.number RETURNING id", (line, number)).fetchone()["id"]


def load(con):
    s = initial_state()
    row = con.execute("SELECT s.*, b.number FROM station s JOIN batches b ON b.id=s.batch_id WHERE s.id=1").fetchone()
    if not row:
        return s
    s.update(sequence=row["sequence"], revision=row["revision"], product=row["product_id"], line=row["line_id"],
             batch=row["number"], operator=row["operator_id"], online=row["online"], palletTarget=row["pallet_target"])
    s["settings"] = {"recipes": {r["product_id"]: r["quantity"] for r in con.execute("SELECT * FROM packing_recipes")},
                     "palletSize": con.execute("SELECT pallet_size FROM station_settings WHERE id=1").fetchone()["pallet_size"]}
    s["draft"] = [r["code"] for r in con.execute("SELECT code FROM draft_codes ORDER BY position")]
    s["palletDraft"] = [r["box_id"] for r in con.execute("SELECT box_id FROM draft_pallet_boxes ORDER BY position")]
    members, pallets, parents = {}, {}, {}
    for r in con.execute("SELECT * FROM box_members ORDER BY position"):
        members.setdefault(r["box_id"], []).append(r["code"])
    for r in con.execute("SELECT * FROM pallet_members ORDER BY position"):
        pallets.setdefault(r["pallet_id"], []).append(r["box_id"])
        parents[r["box_id"]] = r["pallet_id"]
    for r in con.execute("SELECT a.*,b.number FROM aggregates a JOIN batches b ON b.id=a.batch_id ORDER BY created_at DESC,id DESC"):
        a = {"id": r["id"], "product": r["product_id"], "line": r["line_id"], "batch": r["number"],
             "created": r["created_at"].isoformat(), "prints": r["print_opens"]}
        if r["kind"] == "box":
            a.update(codes=members.get(a["id"], []), pallet=parents.get(a["id"]))
            s["boxes"].append(a)
        else:
            a["boxes"] = pallets.get(a["id"], [])
            s["pallets"].append(a)
    s["documents"] = [{"id": r["id"], "aggregate": r["aggregate_id"], "kind": r["kind"], "status": r["status"],
                       "attempts": r["attempts"], "created": r["created_at"].isoformat(), "message": r["message"],
                       "provider": r["provider"], "externalId": r["external_id"]}
                      for r in con.execute("SELECT * FROM exchange_documents ORDER BY created_at DESC,id DESC")]
    s["events"] = [{"id": str(r["id"]), "at": r["created_at"].isoformat(), "level": r["level"],
                    "actor": r["actor"], "message": r["message"]}
                   for r in con.execute("SELECT * FROM audit_events ORDER BY created_at DESC,id DESC")]
    s["printJobs"] = [{"id": str(r["id"]), "aggregate": r["aggregate_id"], "status": r["status"], "created": r["created_at"].isoformat()}
                      for r in con.execute("SELECT * FROM print_jobs ORDER BY created_at DESC,id DESC")]
    return s


def save(con, s, previous=None):
    # Closed records are append-only except explicit status/print counters. No JSON snapshot is stored.
    bid = batch_id(con, s["line"], s["batch"])
    con.execute("INSERT INTO station(id,revision,sequence,product_id,line_id,batch_id,operator_id,pallet_target,online) VALUES(1,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET "
                "revision=excluded.revision,sequence=excluded.sequence,product_id=excluded.product_id,line_id=excluded.line_id,"
                "batch_id=excluded.batch_id,operator_id=excluded.operator_id,pallet_target=excluded.pallet_target,online=excluded.online",
                (s["revision"],s["sequence"],s["product"],s["line"],bid,s["operator"],s["palletTarget"],s["online"]))
    for p, q in s["settings"]["recipes"].items():
        con.execute("UPDATE packing_recipes SET quantity=%s WHERE product_id=%s", (q,p))
    con.execute("UPDATE station_settings SET pallet_size=%s WHERE id=1", (s["settings"]["palletSize"],))
    con.execute("DELETE FROM draft_codes")
    con.execute("DELETE FROM draft_pallet_boxes")
    old_aggregates = {a["id"]:a for a in (previous["boxes"]+previous["pallets"])} if previous else {}
    known = set(old_aggregates) if previous else {r["id"] for r in con.execute("SELECT id FROM aggregates")}
    for kind, entries in (("box",s["boxes"]),("pallet",s["pallets"])):
        for a in entries:
            if a["id"] not in known:
                batch = batch_id(con, a["line"], a["batch"])
                con.execute("INSERT INTO aggregates(id,kind,product_id,line_id,batch_id,created_at,print_opens,unit_count) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                            (a["id"],kind,a["product"],a["line"],batch,a["created"],a["prints"],len(a.get("codes",a.get("boxes",[])))))
                children = a["codes"] if kind == "box" else a["boxes"]
                for i, child in enumerate(children):
                    if kind == "box":
                        con.execute("INSERT INTO marking_codes(raw_code,product_id,source) VALUES(%s,%s,'demo') ON CONFLICT DO NOTHING", (child,a["product"]))
                        con.execute("INSERT INTO box_members VALUES(%s,%s,%s)", (child,a["id"],i))
                    else:
                        con.execute("INSERT INTO pallet_members VALUES(%s,%s,%s)", (child,a["id"],i))
            elif a["prints"] != old_aggregates.get(a["id"],{}).get("prints"):
                con.execute("UPDATE aggregates SET print_opens=%s WHERE id=%s AND print_opens<>%s", (a["prints"],a["id"],a["prints"]))
    for i, code in enumerate(s["draft"]):
        con.execute("INSERT INTO marking_codes(raw_code,product_id,source) VALUES(%s,%s,'demo') ON CONFLICT DO NOTHING", (code,s["product"]))
        con.execute("INSERT INTO draft_codes VALUES(%s,%s)", (code,i))
    for i, box in enumerate(s["palletDraft"]):
        con.execute("INSERT INTO draft_pallet_boxes VALUES(%s,%s)", (box,i))
    old_docs = {d["id"]:d for d in previous["documents"]} if previous else {}
    for d in s["documents"]:
        if d == old_docs.get(d["id"]):
            continue
        old = con.execute("SELECT attempts FROM exchange_documents WHERE id=%s", (d["id"],)).fetchone()
        con.execute("INSERT INTO exchange_documents(id,aggregate_id,kind,status,attempts,created_at,message,provider,external_id) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET status=excluded.status,attempts=excluded.attempts,message=excluded.message",
                    (d["id"],d["aggregate"],d["kind"],d["status"],d["attempts"],d["created"],d["message"],d.get("provider","simulator"),d.get("externalId")))
        if d["attempts"] > (old["attempts"] if old else 0):
            con.execute("INSERT INTO exchange_attempts(document_id,number,outcome) VALUES(%s,%s,%s)", (d["id"],d["attempts"],d["status"]))
    old_jobs = {j["id"]:j for j in previous["printJobs"]} if previous else {}
    for job in s["printJobs"]:
        if job == old_jobs.get(job["id"]):
            continue
        con.execute("INSERT INTO print_jobs VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET status=excluded.status",
                    (job["id"],job["aggregate"],job["status"],job["created"]))
    existing_events = {e["id"] for e in previous["events"]} if previous else {str(r["id"]) for r in con.execute("SELECT id FROM audit_events")}
    for event in s["events"]:
        if event["id"] not in existing_events:
            con.execute("INSERT INTO audit_events VALUES(%s,%s,%s,%s,%s)",
                        (event["id"],event["at"],event["level"],event["actor"],event["message"]))


def read_state():
    with connect() as con:
        con.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return load(con)


def command(action, data, request_id, expected_revision):
    from domain import RuleError
    UUID(request_id)
    fingerprint = hashlib.sha256(json.dumps([action,data], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with connect() as con:
        con.execute("SELECT id FROM station WHERE id=1 FOR UPDATE")
        receipt = con.execute("SELECT * FROM command_receipts WHERE id=%s", (request_id,)).fetchone()
        s = load(con)
        if receipt:
            if receipt["fingerprint"] != fingerprint:
                raise Conflict("Этот идентификатор запроса уже использован другой операцией.")
            return s, receipt["error"]
        if expected_revision != s["revision"]:
            raise Conflict("Данные изменились в другом окне. Обновите состояние и повторите действие.")
        error = None
        previous = deepcopy(s)
        try:
            s = apply(s, action, data)
        except RuleError as exc:
            error = str(exc)
            log(s, error, "error")
        s["revision"] += 1
        save(con, s, previous)
        con.execute("INSERT INTO command_receipts(id,fingerprint,error) VALUES(%s,%s,%s)", (request_id,fingerprint,error))
        return s, error


def audit(message, level="info"):
    with connect() as con:
        con.execute("INSERT INTO audit_events VALUES(%s,now(),%s,'demo-operator',%s)", (uuid4(),level,message))
