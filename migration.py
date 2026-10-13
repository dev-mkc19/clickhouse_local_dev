#!/usr/bin/env python3
"""Migrate dbt sources from remote ClickHouse into local sandbox.

Reads tables.yaml for per-table load mode (full / incremental / sample / skip).
Caps incremental/sample loads by max_rows and max_bytes (default 1M / 400 MiB).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "PyYAML is required (comes with dbt). Install: pip install pyyaml"
    ) from exc

SCRIPT_DIR = Path(__file__).resolve().parent
TABLES_YAML = Path(os.environ.get("TABLES_YAML", SCRIPT_DIR / "tables.yaml"))

SRC_HOST = os.environ.get("CLICKHOUSE_SCR_HOST", "")
SRC_PORT = os.environ.get("CLICKHOUSE_SCR_PORT", "8200")
SRC_USER = os.environ.get("CLICKHOUSE_SCR_USER", "default")
SRC_PASSWORD = os.environ.get("CLICKHOUSE_SCR_PASSWORD", "")

DEST_HOST = os.environ.get("CLICKHOUSE_DEST_HOST", "localhost")
DEST_PORT = os.environ.get("CLICKHOUSE_DEST_PORT", "9000")
DEST_USER = os.environ.get("CLICKHOUSE_DEST_USER", "admin")
DEST_PASSWORD = os.environ.get("CLICKHOUSE_DEST_PASSWORD", "123")

CLUSTER = os.environ.get("CLICKHOUSE_CLUSTER", "{cluster_name}")
DBT_DIR = Path(os.environ.get("DBT_DIR", "/opt/dbt"))
SKIP_SYSTEM_DBS = {
    "default",
    "system",
    "INFORMATION_SCHEMA",
    "information_schema",
    "_temporary_and_external_tables",
}


@dataclass
class TableConfig:
    mode: str
    date_col: Optional[str] = None
    days: Optional[int] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    max_rows: int = 1_000_000
    max_bytes: int = 400 * 1024 * 1024
    sample_limit: int = 10_000


def run_ch(
    host: str,
    port: str,
    user: str,
    password: str,
    query: str,
    *,
    check: bool = True,
    fmt: Optional[str] = None,
) -> str:
    cmd = [
        "clickhouse-client",
        f"--host={host}",
        f"--port={port}",
        f"--user={user}",
        f"--password={password}",
        f"--query={query}",
    ]
    if fmt:
        cmd.append(f"--format={fmt}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"clickhouse-client failed ({result.returncode}):\n"
            f"stderr: {result.stderr}\nquery: {query[:500]}"
        )
    return result.stdout.strip()


def src_query(query: str, *, check: bool = True, fmt: Optional[str] = None) -> str:
    return run_ch(SRC_HOST, SRC_PORT, SRC_USER, SRC_PASSWORD, query, check=check, fmt=fmt)


def dest_query(query: str, *, check: bool = True, fmt: Optional[str] = None) -> str:
    return run_ch(
        DEST_HOST, DEST_PORT, DEST_USER, DEST_PASSWORD, query, check=check, fmt=fmt
    )


def remote_expr(database: str, table: str) -> str:
    return (
        f"remote('{SRC_HOST}:{SRC_PORT}', '{database}.{table}', "
        f"'{SRC_USER}', '{SRC_PASSWORD}')"
    )


def remote_system_parts() -> str:
    return (
        f"remote('{SRC_HOST}:{SRC_PORT}', 'system.parts', "
        f"'{SRC_USER}', '{SRC_PASSWORD}')"
    )


def remote_system_tables() -> str:
    return (
        f"remote('{SRC_HOST}:{SRC_PORT}', 'system.tables', "
        f"'{SRC_USER}', '{SRC_PASSWORD}')"
    )


def load_config() -> dict[str, Any]:
    if not TABLES_YAML.exists():
        print(f"WARN: {TABLES_YAML} not found, using built-in defaults only")
        return {"defaults": {}, "tables": {}, "dbt_select": "source:*"}
    with TABLES_YAML.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("defaults", {})
    data.setdefault("tables", {})
    return data


def parse_date(value: Optional[str]) -> Optional[date]:
    if value is None or value == "":
        return None
    value = str(value).strip()
    if value.startswith("today-"):
        return date.today() - timedelta(days=int(value.split("-", 1)[1]))
    if value == "today":
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def resolve_table_config(
    schema: str, table: str, cfg_root: dict[str, Any]
) -> TableConfig:
    defaults = cfg_root.get("defaults") or {}
    tables = cfg_root.get("tables") or {}
    key = f"{schema}.{table}"
    entry = tables.get(key) or tables.get(table) or {}

    max_bytes_mb = entry.get("max_bytes_mb", defaults.get("max_bytes_mb", 400))
    if "max_bytes" in entry:
        max_bytes = int(entry["max_bytes"])
    elif "max_bytes" in defaults:
        max_bytes = int(defaults["max_bytes"])
    else:
        max_bytes = int(max_bytes_mb) * 1024 * 1024

    env_from = os.environ.get("LOAD_FROM")
    from_raw = entry.get("from", defaults.get("from", env_from))
    to_raw = entry.get("to", defaults.get("to"))

    days = entry.get("days", defaults.get("days"))
    if days is not None:
        days = int(days)

    max_rows = int(
        os.environ.get(
            "MIGRATION_MAX_ROWS",
            entry.get("max_rows", defaults.get("max_rows", 1_000_000)),
        )
    )
    env_mb = os.environ.get("MIGRATION_MAX_BYTES_MB")
    if env_mb:
        max_bytes = int(env_mb) * 1024 * 1024

    return TableConfig(
        mode=str(entry.get("mode", defaults.get("mode", "sample"))),
        date_col=entry.get("date_col") or defaults.get("date_col"),
        days=days,
        from_date=str(from_raw) if from_raw else None,
        to_date=str(to_raw) if to_raw else None,
        max_rows=max_rows,
        max_bytes=max_bytes,
        sample_limit=int(entry.get("sample_limit", defaults.get("sample_limit", 10_000))),
    )


def estimate_bytes_per_row(schema: str, table: str) -> float:
    query = f"""
    SELECT if(sum(rows) = 0, 0, sum(bytes_on_disk) / sum(rows))
    FROM {remote_system_parts()}
    WHERE active AND database = '{schema}' AND table = '{table}'
    """
    out = src_query(query, check=False)
    try:
        return float(out) if out else 0.0
    except ValueError:
        return 0.0


def estimate_table_bytes(schema: str, table: str) -> int:
    query = f"""
    SELECT ifNull(sum(bytes_on_disk), 0)
    FROM {remote_system_parts()}
    WHERE active AND database = '{schema}' AND table = '{table}'
    """
    out = src_query(query, check=False)
    try:
        return int(float(out)) if out else 0
    except ValueError:
        return 0


def effective_row_limit(cfg: TableConfig, bytes_per_row: float) -> int:
    """min(max_rows, floor(max_bytes / bytes_per_row))."""
    limit = cfg.max_rows
    if bytes_per_row > 0:
        by_bytes = max(1, int(cfg.max_bytes / bytes_per_row))
        limit = min(limit, by_bytes)
    return max(1, limit)


def build_date_predicate(cfg: TableConfig) -> Optional[str]:
    if not cfg.date_col:
        return None

    from_d = parse_date(cfg.from_date)
    to_d = parse_date(cfg.to_date)
    parts: list[str] = []

    lower: Optional[date] = None
    if from_d and cfg.days is not None:
        lower = max(from_d, date.today() - timedelta(days=cfg.days))
    elif from_d:
        lower = from_d
    elif cfg.days is not None:
        lower = date.today() - timedelta(days=cfg.days)

    col = cfg.date_col
    if lower is not None:
        # Works for Date / DateTime / String-ish dates via toDate
        parts.append(f"toDate({col}) >= toDate('{lower.isoformat()}')")
    if to_d is not None:
        parts.append(f"toDate({col}) <= toDate('{to_d.isoformat()}')")

    if not parts:
        return None
    return " AND ".join(parts)


def build_insert_sql(schema: str, table: str, cfg: TableConfig) -> tuple[str, str]:
    """Return (sql, human description)."""
    remote = remote_expr(schema, table)
    bpr = estimate_bytes_per_row(schema, table)
    mode = cfg.mode

    if mode == "full":
        total_bytes = estimate_table_bytes(schema, table)
        if total_bytes and total_bytes > cfg.max_bytes:
            limit = effective_row_limit(cfg, bpr)
            sql = (
                f"INSERT INTO {schema}.{table} "
                f"SELECT * FROM {remote} LIMIT {limit}"
            )
            desc = (
                f"full→capped rows={limit} "
                f"(table≈{total_bytes / (1024**2):.1f} MiB > max "
                f"{cfg.max_bytes / (1024**2):.0f} MiB, bpr={bpr:.1f})"
            )
            return sql, desc
        sql = f"INSERT INTO {schema}.{table} SELECT * FROM {remote}"
        return sql, f"full (no limit, ≈{total_bytes / (1024**2):.1f} MiB)"

    if mode == "incremental":
        if not cfg.date_col:
            raise ValueError(
                f"{schema}.{table}: mode=incremental requires date_col"
            )
        pred = build_date_predicate(cfg)
        if not pred:
            raise ValueError(
                f"{schema}.{table}: mode=incremental needs from and/or days"
            )
        limit = effective_row_limit(cfg, bpr)
        sql = (
            f"INSERT INTO {schema}.{table} "
            f"SELECT * FROM {remote} "
            f"WHERE {pred} "
            f"ORDER BY {cfg.date_col} DESC "
            f"LIMIT {limit}"
        )
        desc = (
            f"incremental where {pred}; "
            f"limit={limit} (max_rows={cfg.max_rows}, "
            f"max_bytes={cfg.max_bytes / (1024**2):.0f}MiB, bpr={bpr:.1f})"
        )
        return sql, desc

    if mode == "sample":
        # sample_limit is intentional small draw; still respect byte cap
        limit = min(cfg.sample_limit, effective_row_limit(cfg, bpr))
        sql = (
            f"INSERT INTO {schema}.{table} "
            f"SELECT * FROM {remote} LIMIT {limit}"
        )
        desc = (
            f"sample limit={limit} "
            f"(sample_limit={cfg.sample_limit}, "
            f"byte_cap≈{cfg.max_bytes / (1024**2):.0f}MiB, bpr={bpr:.1f})"
        )
        return sql, desc

    raise ValueError(f"Unknown mode: {mode}")


def create_databases() -> None:
    schemas = src_query("SHOW DATABASES").splitlines()
    for schema in schemas:
        schema = schema.strip()
        if not schema or schema in SKIP_SYSTEM_DBS:
            continue
        print(f"Creating schema: {schema}")
        dest_query(
            f"CREATE DATABASE IF NOT EXISTS `{schema}` ON CLUSTER '{CLUSTER}'"
        )
    print("Schema migration completed!")


def list_dbt_sources(dbt_select: str) -> list[tuple[str, str]]:
    select = os.environ.get("DBT_SELECT", dbt_select)
    cmd = [
        "dbt",
        "ls",
        "--resource-type",
        "source",
        "--profiles-dir",
        str(DBT_DIR),
    ]
    # * / all / пусто — все sources (--resource-type source без --select)
    # иначе — явный селектор, например source:* или +detail_orders_sensitive
    if select and select not in {"*", "all"}:
        cmd.extend(["--select", select])
    print(f"Listing dbt sources: {' '.join(cmd)}")
    result = subprocess.run(
        cmd, cwd=str(DBT_DIR), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"dbt ls failed:\n{result.stderr or result.stdout}"
        )
    sources: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("source:"):
            continue
        # source:bi_etl.raw_indriver.tbl_order
        rest = line.split(":", 1)[1]
        parts = rest.split(".")
        if len(parts) < 3:
            continue
        # project.schema.table
        schema, table = parts[-2], parts[-1]
        sources.append((schema, table))
    # unique preserve order
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for item in sources:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def fetch_create_table_ddl(schema: str, table: str) -> Optional[str]:
    query = (
        f"SELECT create_table_query FROM {remote_system_tables()} "
        f"WHERE name = '{table}' AND database = '{schema}' "
        f"LIMIT 1"
    )
    ddl = src_query(query, check=False, fmt="LineAsString")
    if not ddl:
        return None
    # Inject ON CLUSTER after table name
    ddl = re.sub(
        r"(CREATE TABLE\s+(?:IF NOT EXISTS\s+)?[`\"']?[\w.]+[`\"']?)",
        rf"\1 ON CLUSTER '{CLUSTER}'",
        ddl,
        count=1,
        flags=re.IGNORECASE,
    )
    return ddl


def process_table(schema: str, table: str, cfg_root: dict[str, Any]) -> None:
    cfg = resolve_table_config(schema, table, cfg_root)
    print(f"\n=== {schema}.{table} mode={cfg.mode} ===")

    if cfg.mode == "skip":
        print("Skipped by tables.yaml")
        return

    ddl = fetch_create_table_ddl(schema, table)
    if not ddl:
        print(f"No table info found for {schema}.{table}")
        return

    print("Creating table on target...")
    dest_query(ddl)

    try:
        sql, desc = build_insert_sql(schema, table, cfg)
    except ValueError as exc:
        print(f"WARN: {exc}; falling back to sample")
        cfg = TableConfig(
            mode="sample",
            max_rows=cfg.max_rows,
            max_bytes=cfg.max_bytes,
            sample_limit=cfg.sample_limit,
        )
        sql, desc = build_insert_sql(schema, table, cfg)

    print(f"Inserting: {desc}")
    print(f"SQL: {sql[:300]}{'...' if len(sql) > 300 else ''}")
    dest_query(sql)
    print(f"OK: {schema}.{table}")


def main() -> int:
    if not SRC_HOST:
        print("CLICKHOUSE_SCR_HOST is required", file=sys.stderr)
        return 1

    cfg_root = load_config()
    print(f"Config: {TABLES_YAML}")
    print(
        f"Defaults: max_rows={cfg_root.get('defaults', {}).get('max_rows', 1_000_000)}, "
        f"max_bytes_mb={cfg_root.get('defaults', {}).get('max_bytes_mb', 400)}"
    )

    create_databases()

    dbt_select = cfg_root.get("dbt_select") or "source:*"
    try:
        sources = list_dbt_sources(dbt_select)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Found {len(sources)} source tables")
    failures: list[str] = []
    for schema, table in sources:
        try:
            process_table(schema, table, cfg_root)
        except Exception as exc:  # noqa: BLE001 — keep migrating other tables
            print(f"FAILED {schema}.{table}: {exc}", file=sys.stderr)
            failures.append(f"{schema}.{table}")

    print("\nDBT sources processed!")
    if failures:
        print(f"Failed ({len(failures)}): {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
