from uuid import uuid4
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
import repository
from config import settings


@pytest.fixture
def db(monkeypatch):
    """An isolated random schema; never delete or overwrite application tables."""
    dsn=settings().database_url
    schema="test_arm_"+uuid4().hex
    with psycopg.connect(dsn) as con:
        con.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    monkeypatch.setattr(repository,"connect",lambda: psycopg.connect(dsn,options="-c search_path="+schema,row_factory=dict_row))
    try:
        repository.migrate()
        yield repository
    finally:
        assert schema.startswith("test_arm_") and len(schema)==41
        with psycopg.connect(dsn) as con:
            con.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient
    from server import app
    with TestClient(app) as c:
        yield c
