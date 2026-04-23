from __future__ import annotations

import os
from urllib.parse import quote_plus

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


load_dotenv()


def get_env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def build_connection_string(database: str) -> str:
    server = os.getenv("DB_SERVER")
    driver = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")

    if not server:
        raise ValueError("Missing DB_SERVER in .env")

    odbc_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Trusted_Connection=yes;"
        "Encrypt=no;"
    )

    return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc_str)}"


def get_engine(database: str):
    conn_str = build_connection_string(database)
    return create_engine(conn_str, fast_executemany=True)


def list_user_tables(database: str, schema_filter: str | None = None) -> list[tuple[str, str]]:
    engine = get_engine(database)

    query = """
    SELECT
        s.name AS schema_name,
        t.name AS table_name
    FROM sys.tables t
    INNER JOIN sys.schemas s
        ON t.schema_id = s.schema_id
    WHERE t.is_ms_shipped = 0
    """

    params = {}
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
    sample_rows: int = 50000,
) -> pd.DataFrame:
    engine = get_engine(database)

    query = f"""
    SELECT TOP ({sample_rows}) *
    FROM [{schema_name}].[{table_name}]
    """

    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    return df


def get_database_list() -> list[str]:
    dbs = get_env_list("DB_DATABASES")
    if not dbs:
        raise ValueError("No DB_DATABASES found in .env")
    return dbs