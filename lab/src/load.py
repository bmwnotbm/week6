import logging
import sqlite3

from .config import WAREHOUSE_DB


DDL = {
    "dim_customer": """
        CREATE TABLE IF NOT EXISTS dim_customer (
            customer_id TEXT PRIMARY KEY,
            name        TEXT,
            province    TEXT,
            email       TEXT
        )
    """,
    "dim_product": """
        CREATE TABLE IF NOT EXISTS dim_product (
            product_id   TEXT PRIMARY KEY,
            product_name TEXT,
            category     TEXT,
            price        REAL
        )
    """,
    "fact_sales": """
        CREATE TABLE IF NOT EXISTS fact_sales (
            order_id      TEXT PRIMARY KEY,
            customer_id   TEXT,
            product_id    TEXT,
            order_date    TEXT,
            qty           REAL,
            unit_price    REAL,
            discount_pct  REAL,
            sales_amount  REAL
        )
    """,
}


def load_data(customers, products, sales):
    WAREHOUSE_DB.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(WAREHOUSE_DB) as conn:
        cur = conn.cursor()
        for ddl in DDL.values():
            cur.execute(ddl)

        # --- dim_customer: upsert (customer_id is unique business key) ---
        cust_rows = list(
            customers[["customer_id", "name", "province", "email"]].itertuples(index=False, name=None)
        )
        cur.executemany(
            """
            INSERT INTO dim_customer (customer_id, name, province, email)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
                name = excluded.name,
                province = excluded.province,
                email = excluded.email
            """,
            cust_rows,
        )

        # --- dim_product: upsert (product_id is unique business key) ---
        prod_rows = list(
            products[["product_id", "product_name", "category", "price"]].itertuples(index=False, name=None)
        )
        cur.executemany(
            """
            INSERT INTO dim_product (product_id, product_name, category, price)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                product_name = excluded.product_name,
                category = excluded.category,
                price = excluded.price
            """,
            prod_rows,
        )

        # --- fact_sales: INSERT OR IGNORE so rerunning the pipeline never duplicates rows ---
        sales_df = sales.copy()
        sales_df["order_date"] = sales_df["order_date"].astype(str)
        fact_rows = list(
            sales_df[[
                "order_id", "customer_id", "product_id", "order_date",
                "qty", "unit_price", "discount_pct", "sales_amount",
            ]].itertuples(index=False, name=None)
        )
        cur.executemany(
            """
            INSERT OR IGNORE INTO fact_sales
                (order_id, customer_id, product_id, order_date, qty, unit_price, discount_pct, sales_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fact_rows,
        )

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM dim_customer")
        n_cust = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM dim_product")
        n_prod = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM fact_sales")
        n_fact = cur.fetchone()[0]

        logging.info(
            f"[load] dim_customer={n_cust} rows, dim_product={n_prod} rows, fact_sales={n_fact} rows"
        )
