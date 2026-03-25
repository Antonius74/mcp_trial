from typing import Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.sql import Identifier, SQL

from app.config import settings


def _admin_conn() -> psycopg.Connection:
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname=settings.postgres_admin_db,
        autocommit=True,
    )


def _app_conn() -> psycopg.Connection:
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname=settings.postgres_db,
        row_factory=dict_row,
    )


def create_database_if_missing() -> bool:
    created = False
    with _admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (settings.postgres_db,),
            )
            if cur.fetchone() is None:
                cur.execute(
                    SQL("CREATE DATABASE {}").format(Identifier(settings.postgres_db))
                )
                created = True
    return created


def _run_many(cur: psycopg.Cursor, statements: Iterable[str]) -> None:
    for stmt in statements:
        cur.execute(stmt)


def create_tables_and_seed() -> None:
    create_tables_sql = [
        """
        CREATE TABLE IF NOT EXISTS customers (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            item TEXT NOT NULL,
            amount NUMERIC(10,2) NOT NULL CHECK (amount >= 0),
            status TEXT NOT NULL DEFAULT 'new',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
    ]

    seed_sql = [
        """
        INSERT INTO customers (name, email)
        VALUES
            ('Mario Rossi', 'mario.rossi@example.com'),
            ('Laura Bianchi', 'laura.bianchi@example.com')
        ON CONFLICT (email) DO NOTHING;
        """,
        """
        INSERT INTO orders (customer_id, item, amount, status)
        SELECT c.id, 'Notebook', 1299.00, 'paid'
        FROM customers c
        WHERE c.email = 'mario.rossi@example.com'
          AND NOT EXISTS (
              SELECT 1 FROM orders o
              WHERE o.customer_id = c.id AND o.item = 'Notebook'
          );
        """,
        """
        INSERT INTO orders (customer_id, item, amount, status)
        SELECT c.id, 'Mouse', 39.90, 'new'
        FROM customers c
        WHERE c.email = 'laura.bianchi@example.com'
          AND NOT EXISTS (
              SELECT 1 FROM orders o
              WHERE o.customer_id = c.id AND o.item = 'Mouse'
          );
        """,
    ]

    with _app_conn() as conn:
        with conn.cursor() as cur:
            _run_many(cur, create_tables_sql)
            _run_many(cur, seed_sql)
        conn.commit()


def bootstrap_database() -> dict:
    created_db = create_database_if_missing()
    create_tables_and_seed()
    return {
        "database": settings.postgres_db,
        "created_database": created_db,
        "tables": ["customers", "orders"],
    }


if __name__ == "__main__":
    result = bootstrap_database()
    print(result)
