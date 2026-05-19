from __future__ import annotations

import os
from functools import lru_cache

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv()


def get_env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


@lru_cache(maxsize=16)
def get_engine(database: str) -> Engine:
    """Return a cached SQLAlchemy engine for the given database."""
    import pyodbc as _pyodbc

    server   = os.getenv("DB_SERVER")
    driver   = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
    username = os.getenv("DB_USERNAME")
    password = os.getenv("DB_PASSWORD")

    if not server:
        raise ValueError("Missing DB_SERVER in .env")

    if username and password:
        odbc_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};UID={username};PWD={password};Encrypt=no;"
    else:
        odbc_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};Trusted_Connection=yes;Encrypt=no;"

    def creator():
        return _pyodbc.connect(odbc_str)

    return create_engine(
        "mssql+pyodbc://",
        creator=creator,
        fast_executemany=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def list_user_tables(database: str, schema_filter: str | None = None) -> list[tuple[str, str]]:
    engine = get_engine(database)
    query = """
    SELECT
        s.name AS schema_name,
        t.name AS table_name
    FROM sys.tables t
    INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
    WHERE t.is_ms_shipped = 0
    """
    params: dict = {}
    if schema_filter:
        query += " AND s.name = :schema_name"
        params["schema_name"] = schema_filter
    query += " ORDER BY s.name, t.name"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
    return [(row.schema_name, row.table_name) for row in rows]


def read_table_sample(
    database: str,
    schema_name: str,
    table_name: str,
    sample_rows: int = 50_000,
) -> pd.DataFrame:
    engine = get_engine(database)
    query = f"SELECT TOP ({sample_rows}) * FROM [{schema_name}].[{table_name}]"
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
    return df


def get_table_column_metadata(
    database: str,
    schema_name: str,
    table_name: str,
) -> dict[str, dict]:
    """
    Returns per-column SQL metadata keyed by column name.

    Example output::

        {
            "EmploymentKey": {
                "db_data_type": "int",
                "db_is_nullable": False,
                "db_max_length": 4,
                "db_precision": 10,
                "db_scale": 0,
            }
        }
    """
    engine = get_engine(database)
    query = """
    SELECT
        c.name          AS column_name,
        ty.name         AS db_data_type,
        c.is_nullable   AS db_is_nullable,
        c.max_length    AS db_max_length,
        c.precision     AS db_precision,
        c.scale         AS db_scale
    FROM sys.columns c
    INNER JOIN sys.tables  t  ON c.object_id   = t.object_id
    INNER JOIN sys.schemas s  ON t.schema_id   = s.schema_id
    INNER JOIN sys.types   ty ON c.user_type_id = ty.user_type_id
    WHERE s.name = :schema_name
      AND t.name = :table_name
    ORDER BY c.column_id
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(query),
            {"schema_name": schema_name, "table_name": table_name},
        ).fetchall()

    return {
        row.column_name: {
            "db_data_type":  row.db_data_type,
            "db_is_nullable": bool(row.db_is_nullable),
            "db_max_length": int(row.db_max_length) if row.db_max_length is not None else None,
            "db_precision":  int(row.db_precision)  if row.db_precision  is not None else None,
            "db_scale":      int(row.db_scale)      if row.db_scale      is not None else None,
        }
        for row in rows
    }


def get_database_list() -> list[str]:
    dbs = get_env_list("DB_DATABASES")
    if not dbs:
        raise ValueError(
            "No DB_DATABASES found in .env. "
            "Set DB_DATABASES=MyDb1,MyDb2 (comma-separated)."
        )
    return dbs
