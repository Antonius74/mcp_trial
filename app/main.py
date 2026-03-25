from typing import List

import psycopg
from fastapi import FastAPI, HTTPException

from app.database import get_connection
from app.db_bootstrap import bootstrap_database
from app.schemas import CustomerCreate, CustomerOut, OrderCreate, OrderOut


app = FastAPI(
    title="Mock Misc Service API",
    description="REST API mock con backend PostgreSQL (customers + orders).",
    version="1.0.0",
)


@app.on_event("startup")
def on_startup() -> None:
    bootstrap_database()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/customers", response_model=List[CustomerOut])
def list_customers() -> List[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, email, created_at
                FROM customers
                ORDER BY id ASC
                """
            )
            return cur.fetchall()


@app.post("/customers", response_model=CustomerOut, status_code=201)
def create_customer(payload: CustomerCreate) -> dict:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO customers (name, email)
                    VALUES (%s, %s)
                    RETURNING id, name, email, created_at
                    """,
                    (payload.name, payload.email),
                )
                created = cur.fetchone()
            conn.commit()
            return created
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="Email già presente")


@app.get("/orders", response_model=List[OrderOut])
def list_orders() -> List[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, customer_id, item, amount, status, created_at
                FROM orders
                ORDER BY id ASC
                """
            )
            return cur.fetchall()


@app.post("/orders", response_model=OrderOut, status_code=201)
def create_order(payload: OrderCreate) -> dict:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders (customer_id, item, amount, status)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, customer_id, item, amount, status, created_at
                    """,
                    (payload.customer_id, payload.item, payload.amount, payload.status),
                )
                created = cur.fetchone()
            conn.commit()
            return created
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(
            status_code=404,
            detail=f"Customer con id={payload.customer_id} non trovato",
        )
