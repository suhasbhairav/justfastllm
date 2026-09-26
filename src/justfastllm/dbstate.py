from __future__ import annotations

import json
import time
from typing import Mapping
from urllib.parse import parse_qs, unquote, urlparse


SUPPORTED_DATABASE_FAMILIES = {"postgres", "mysql", "mssql", "mongodb"}
DATABASE_DRIVER_DEPENDENCIES = {
    "postgres": "psycopg[binary]",
    "mysql": "PyMySQL",
    "mssql": "pymssql",
    "mongodb": "pymongo",
}


def is_mongo_url(url: str) -> bool:
    scheme = urlparse(url).scheme.lower()
    return scheme in {"mongodb", "mongodb+srv"}


def database_family(url: str) -> str:
    scheme = urlparse(url).scheme.lower().split("+", 1)[0]
    if scheme in {"postgres", "postgresql"}:
        return "postgres"
    if scheme in {"mysql", "mariadb"}:
        return "mysql"
    if scheme in {"mssql", "sqlserver"}:
        return "mssql"
    if scheme in {"mongodb", "mongodb+srv"}:
        return "mongodb"
    if scheme == "sqlite":
        return "sqlite"
    return "unknown"


def is_supported_database_url(url: str) -> bool:
    return database_family(url) in SUPPORTED_DATABASE_FAMILIES


def database_driver_dependency(url: str) -> str:
    return DATABASE_DRIVER_DEPENDENCIES.get(database_family(url), "")


def database_transport_security(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    family = database_family(url)
    query = _query_params(parsed.query)
    local = _is_local_database_host(parsed.hostname)
    encrypted = False
    indicators: list[str] = []

    if local:
        encrypted = True
        indicators.append("local_endpoint")
    elif family == "postgres":
        sslmode = query.get("sslmode", "").lower()
        if sslmode in {"require", "verify-ca", "verify-full"}:
            encrypted = True
            indicators.append(f"sslmode={sslmode}")
        elif _truthy_query(query, "ssl"):
            encrypted = True
            indicators.append("ssl=true")
    elif family == "mysql":
        ssl_mode = query.get("ssl_mode", query.get("ssl-mode", "")).lower().replace("-", "_")
        if ssl_mode in {"required", "verify_ca", "verify_identity"}:
            encrypted = True
            indicators.append(f"ssl_mode={ssl_mode}")
        elif _truthy_query(query, "ssl") or any(key in query for key in ("ssl_ca", "ssl_cert", "ssl_key")):
            encrypted = True
            indicators.append("ssl_parameters")
    elif family == "mssql":
        encrypt = query.get("encrypt", "").lower()
        if encrypt in {"true", "yes", "mandatory", "strict", "1"}:
            encrypted = True
            indicators.append(f"encrypt={encrypt}")
    elif family == "mongodb":
        if parsed.scheme.lower() == "mongodb+srv" and not _falsey_query(query, "tls") and not _falsey_query(query, "ssl"):
            encrypted = True
            indicators.append("mongodb+srv_tls_default")
        elif _truthy_query(query, "tls") or _truthy_query(query, "ssl"):
            encrypted = True
            indicators.append("tls=true")

    return {
        "database_family": family,
        "encrypted": encrypted,
        "local_endpoint": local,
        "indicators": indicators,
        "requirement": _database_transport_requirement(family),
    }


def state_matches(expected: Mapping[str, object], observed: object) -> bool:
    if not isinstance(observed, dict):
        return False
    return _canonical_json(expected) == _canonical_json(observed)


def load_sql_state(url: str, table_name: str) -> dict[str, object] | None:
    try:
        from sqlalchemy import Column, Float, MetaData, String, Table, Text, create_engine, select
    except ImportError as exc:
        raise OSError("Install SQLAlchemy and a database driver for SQL state storage") from exc

    engine = create_engine(url)
    metadata = MetaData()
    table = Table(
        table_name,
        metadata,
        Column("id", String(64), primary_key=True),
        Column("state_json", _sql_state_json_type(url, Text), nullable=False),
        Column("updated_at", Float, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        state_json = conn.execute(select(table.c.state_json).where(table.c.id == "default")).scalar_one_or_none()
    if not state_json:
        return None
    payload = json.loads(str(state_json))
    return payload if isinstance(payload, dict) else None


def save_sql_state(url: str, table_name: str, state: Mapping[str, object]) -> None:
    try:
        from sqlalchemy import Column, Float, MetaData, String, Table, Text, create_engine, delete, insert
    except ImportError as exc:
        raise OSError("Install SQLAlchemy and a database driver for SQL state storage") from exc

    engine = create_engine(url)
    metadata = MetaData()
    table = Table(
        table_name,
        metadata,
        Column("id", String(64), primary_key=True),
        Column("state_json", _sql_state_json_type(url, Text), nullable=False),
        Column("updated_at", Float, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(delete(table).where(table.c.id == "default"))
        conn.execute(
            insert(table).values(
                id="default",
                state_json=json.dumps(state, sort_keys=True),
                updated_at=time.time(),
            )
        )


def load_mongo_state(url: str, collection_name: str) -> dict[str, object] | None:
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise OSError("Install pymongo for MongoDB state storage") from exc

    client = MongoClient(url)
    try:
        db_name = urlparse(url).path.lstrip("/") or "justfastllm"
        document = client[db_name][collection_name].find_one({"_id": "default"})
        state = document.get("state") if isinstance(document, dict) else None
        return state if isinstance(state, dict) else None
    finally:
        client.close()


def save_mongo_state(url: str, collection_name: str, state: Mapping[str, object]) -> None:
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise OSError("Install pymongo for MongoDB state storage") from exc

    client = MongoClient(url)
    try:
        db_name = urlparse(url).path.lstrip("/") or "justfastllm"
        client[db_name][collection_name].replace_one(
            {"_id": "default"},
            {"_id": "default", "state": dict(state), "updated_at": time.time()},
            upsert=True,
        )
    finally:
        client.close()


def safe_storage_name(value: str, default: str = "justfastllm_state") -> str:
    cleaned = "".join(char if char.isalnum() or char == "_" else "_" for char in (value or default))
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"jfl_{cleaned}"
    return cleaned[:120]


def _sql_state_json_type(url: str, text_type):
    if database_family(url) != "mysql":
        return text_type
    try:
        from sqlalchemy.dialects.mysql import LONGTEXT
    except ImportError:
        return text_type
    return LONGTEXT()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _query_params(query: str) -> dict[str, str]:
    parsed = parse_qs(query, keep_blank_values=True)
    return {unquote(key).lower(): values[-1] if values else "" for key, values in parsed.items()}


def _truthy_query(query: Mapping[str, str], key: str) -> bool:
    return query.get(key, "").lower() in {"1", "true", "yes", "on", "required", "require"}


def _falsey_query(query: Mapping[str, str], key: str) -> bool:
    return query.get(key, "").lower() in {"0", "false", "no", "off", "disable", "disabled"}


def _is_local_database_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    return hostname.lower() in {"localhost", "127.0.0.1", "::1"}


def _database_transport_requirement(family: str) -> str:
    requirements = {
        "postgres": "remote Postgres URLs should use sslmode=require, sslmode=verify-ca, or sslmode=verify-full",
        "mysql": "remote MySQL/MariaDB URLs should use ssl_mode=REQUIRED, VERIFY_CA, VERIFY_IDENTITY, or explicit SSL parameters",
        "mssql": "remote MSSQL URLs should use encrypt=true, encrypt=yes, encrypt=mandatory, or encrypt=strict",
        "mongodb": "remote MongoDB URLs should use tls=true, ssl=true, or mongodb+srv without disabling TLS",
    }
    return requirements.get(family, "unsupported database family")
