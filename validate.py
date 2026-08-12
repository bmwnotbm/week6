import sqlite3

from .config import WAREHOUSE_DB


def validate_data(source_sales):
    source_valid_rows = len(source_sales)
    source_total_sales = round(float(source_sales["sales_amount"].sum()), 2)

    with sqlite3.connect(WAREHOUSE_DB) as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM fact_sales")
        warehouse_rows = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT order_id, COUNT(*) c FROM fact_sales
                GROUP BY order_id HAVING c > 1
            )
        """)
        duplicate_order_ids = cur.fetchone()[0]

        cur.execute("SELECT COALESCE(SUM(sales_amount), 0) FROM fact_sales")
        warehouse_total_sales = round(float(cur.fetchone()[0]), 2)

    status = "PASS" if (
        warehouse_rows == source_valid_rows
        and duplicate_order_ids == 0
        and abs(warehouse_total_sales - source_total_sales) < 0.01
    ) else "FAIL"

    return {
        "source_valid_rows": source_valid_rows,
        "warehouse_rows": warehouse_rows,
        "duplicate_order_ids": duplicate_order_ids,
        "source_total_sales": source_total_sales,
        "warehouse_total_sales": warehouse_total_sales,
        "status": status,
    }
