import json
import sqlite3
import pandas as pd
from .config import RAW_DIR, SOURCE_DB


def extract_data():
    """
    Extract raw data from all sources and return a dict of DataFrames:
      {"customers": ..., "orders": ..., "products": ..., "stores": ...}
    """
    # --- customers.csv ---
    customers = pd.read_csv(RAW_DIR / "customers.csv")

    # --- orders.csv ---
    orders = pd.read_csv(RAW_DIR / "orders.csv")

    # --- products.json (nested -> flatten with json_normalize) ---
    with open(RAW_DIR / "products.json", "r", encoding="utf-8") as f:
        products_raw = json.load(f)
    products = pd.json_normalize(products_raw)

    # --- stores table from store.db ---
    with sqlite3.connect(SOURCE_DB) as conn:
        stores = pd.read_sql_query("SELECT * FROM stores", conn)

    raw = {
        "customers": customers,
        "orders": orders,
        "products": products,
        "stores": stores,
    }

    for name, df in raw.items():
        print(f"[extract] {name}: shape={df.shape}, columns={list(df.columns)}")

    return raw
