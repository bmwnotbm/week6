import logging
import numpy as np
import pandas as pd

from .config import PROVINCE_MAP


def _clean_customers(customers: pd.DataFrame) -> pd.DataFrame:
    df = customers.copy()

    # remove duplicate customer_id (keep first occurrence)
    before = len(df)
    df = df.drop_duplicates(subset="customer_id", keep="first")
    logging.info(f"[transform] customers: removed {before - len(df)} duplicate customer_id rows")

    # standardize province using PROVINCE_MAP (case-insensitive, trimmed)
    def map_province(val):
        if pd.isna(val) or str(val).strip() == "":
            return "Unknown"
        key = str(val).strip().lower()
        return PROVINCE_MAP.get(key, "Unknown")

    df["province"] = df["province"].apply(map_province)

    # handle missing email
    df["email"] = df["email"].fillna("unknown@example.com")
    df.loc[df["email"].astype(str).str.strip() == "", "email"] = "unknown@example.com"

    return df.reset_index(drop=True)


def _clean_products(products: pd.DataFrame) -> pd.DataFrame:
    df = products.copy()

    # flatten + rename nested fields (json_normalize already produced dotted columns)
    df = df.rename(columns={
        "category.name": "category",
        "pricing.price": "price",
    })
    df = df[["product_id", "product_name", "category", "price"]]

    # fill missing category
    df["category"] = df["category"].fillna("Unknown")
    df.loc[df["category"].astype(str).str.strip() == "", "category"] = "Unknown"

    # convert price to numeric (handles values like "1,299.00")
    def to_number(val):
        if isinstance(val, str):
            val = val.replace(",", "").strip()
        return pd.to_numeric(val, errors="coerce")

    df["price"] = df["price"].apply(to_number)

    return df.reset_index(drop=True)


def _parse_mixed_date(val):
    """Try several known date formats, return pd.Timestamp or NaT."""
    if pd.isna(val):
        return pd.NaT
    val = str(val).strip()
    formats = ["%Y/%m/%d", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y"]
    for fmt in formats:
        try:
            return pd.to_datetime(val, format=fmt)
        except ValueError:
            continue
    return pd.NaT


def transform_data(raw: dict):
    customers_raw = raw["customers"]
    products_raw = raw["products"]
    orders_raw = raw["orders"]

    clean_customers = _clean_customers(customers_raw)
    clean_products = _clean_products(products_raw)

    orders = orders_raw.copy()
    orders["status"] = orders["status"].astype(str).str.strip().str.lower()

    reject_frames = []

    # --- 1. duplicate order_id -> keep first, log the rest as rejects ---
    dup_mask = orders.duplicated(subset="order_id", keep="first")
    if dup_mask.any():
        dups = orders[dup_mask].copy()
        dups["reject_reason"] = "duplicate_order_id"
        reject_frames.append(dups)
        logging.info(f"[transform] orders: found {dup_mask.sum()} duplicate order_id rows")
    orders = orders[~dup_mask].copy()

    # --- 2. parse mixed date formats ---
    orders["order_date_parsed"] = orders["order_date"].apply(_parse_mixed_date)

    # --- 3. validate business rules ---
    def find_reasons(row):
        reasons = []
        qty = pd.to_numeric(row["qty"], errors="coerce")
        unit_price = pd.to_numeric(row["unit_price"], errors="coerce")
        discount_pct = pd.to_numeric(row["discount_pct"], errors="coerce")

        if pd.isna(qty) or qty <= 0:
            reasons.append("invalid_qty")
        if pd.isna(unit_price) or unit_price <= 0:
            reasons.append("invalid_unit_price")
        if pd.isna(discount_pct) or discount_pct < 0 or discount_pct > 100:
            reasons.append("invalid_discount_pct")
        if pd.isna(row["order_date_parsed"]):
            reasons.append("invalid_order_date")
        return reasons

    orders["_reasons"] = orders.apply(find_reasons, axis=1)
    invalid_mask = orders["_reasons"].apply(lambda r: len(r) > 0)

    invalid_orders = orders[invalid_mask].copy()
    if len(invalid_orders):
        invalid_orders["reject_reason"] = invalid_orders["_reasons"].apply(lambda r: ";".join(r))
        invalid_orders = invalid_orders.drop(columns=["_reasons", "order_date_parsed"])
        reject_frames.append(invalid_orders)
        logging.info(f"[transform] orders: {len(invalid_orders)} rows failed validation rules")

    valid_orders = orders[~invalid_mask].drop(columns=["_reasons"]).copy()
    valid_orders["qty"] = pd.to_numeric(valid_orders["qty"], errors="coerce")
    valid_orders["unit_price"] = pd.to_numeric(valid_orders["unit_price"], errors="coerce")
    valid_orders["discount_pct"] = pd.to_numeric(valid_orders["discount_pct"], errors="coerce")

    # --- 4. keep only paid/completed orders for revenue purposes ---
    sales_candidates = valid_orders[valid_orders["status"].isin(["paid", "completed"])].copy()
    excluded_status = valid_orders[~valid_orders["status"].isin(["paid", "completed"])].copy()
    logging.info(
        f"[transform] orders: {len(sales_candidates)} paid/completed, "
        f"{len(excluded_status)} excluded (pending/cancelled/other) - not treated as rejects"
    )

    # --- 5. join with customers + products, reject unknown keys ---
    known_customers = set(clean_customers["customer_id"])
    known_products = set(clean_products["product_id"])

    unknown_cust_mask = ~sales_candidates["customer_id"].isin(known_customers)
    unknown_prod_mask = ~sales_candidates["product_id"].isin(known_products)
    unknown_mask = unknown_cust_mask | unknown_prod_mask

    unknown_orders = sales_candidates[unknown_mask].copy()
    if len(unknown_orders):
        def unk_reason(row):
            reasons = []
            if row["customer_id"] not in known_customers:
                reasons.append("unknown_customer")
            if row["product_id"] not in known_products:
                reasons.append("unknown_product")
            return ";".join(reasons)
        unknown_orders["reject_reason"] = unknown_orders.apply(unk_reason, axis=1)
        unknown_orders = unknown_orders.drop(columns=["order_date_parsed"], errors="ignore")
        reject_frames.append(unknown_orders)
        logging.info(f"[transform] orders: {len(unknown_orders)} rows rejected (unknown customer/product)")

    matched = sales_candidates[~unknown_mask].copy()

    sales = matched.merge(
        clean_customers[["customer_id", "name", "province", "email"]],
        on="customer_id", how="left",
    ).merge(
        clean_products[["product_id", "product_name", "category", "price"]],
        on="product_id", how="left",
    )

    # --- 6. calculate amounts ---
    sales["gross_amount"] = sales["qty"] * sales["unit_price"]
    sales["discount_amount"] = sales["gross_amount"] * sales["discount_pct"] / 100
    sales["sales_amount"] = sales["gross_amount"] - sales["discount_amount"]
    sales["order_date"] = sales["order_date_parsed"]
    sales = sales.drop(columns=["order_date_parsed"])

    sales_cols = [
        "order_id", "customer_id", "product_id", "order_date",
        "qty", "unit_price", "discount_pct",
        "gross_amount", "discount_amount", "sales_amount", "status",
    ]
    sales = sales[sales_cols].reset_index(drop=True)

    # --- 7. combine rejects ---
    if reject_frames:
        rejects = pd.concat(reject_frames, ignore_index=True, sort=False)
        # keep original order columns + reason, drop helper cols if present
        drop_cols = [c for c in ["order_date_parsed", "_reasons"] if c in rejects.columns]
        rejects = rejects.drop(columns=drop_cols)
    else:
        rejects = pd.DataFrame(columns=list(orders_raw.columns) + ["reject_reason"])

    logging.info(f"[transform] TOTAL rejects: {len(rejects)}, TOTAL valid sales rows: {len(sales)}")

    return clean_customers, clean_products, sales, rejects
