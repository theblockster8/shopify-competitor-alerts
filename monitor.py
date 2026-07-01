import os
import smtplib
from decimal import Decimal, InvalidOperation
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Tuple, Any

import requests
from supabase import create_client, Client


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

USER_ID = os.environ.get("USER_ID", "")

SUPABASE_TABLE = "product_snapshots"
HISTORY_TABLE = "product_snapshot_history"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

DEMO_STORES = [
    {
        "name": "Kaged",
        "store_key": "demo__kaged_com",
        "base_url": "https://kaged.com",
        "products_json": "https://kaged.com/products.json?limit=250",
        "user_id": None,
    },
    {
        "name": "Gymreapers",
        "store_key": "demo__gymreapers_com",
        "base_url": "https://gymreapers.com",
        "products_json": "https://gymreapers.com/products.json?limit=250",
        "user_id": None,
    },
]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def money_str(value: Decimal | None) -> str:
    if value is None:
        return "None"
    return f"{value:.2f}"


def product_identity(row: dict) -> str:
    handle = (row.get("product_handle") or "").strip()
    variant = (row.get("variant_name") or "").strip()
    return f"{handle}::{variant}"


def load_stores_for_user(user_id: str) -> List[dict]:
    response = (
        supabase.table("rivals")
        .select("id, store_url, store_name, user_id")
        .eq("user_id", user_id)
        .eq("active", True)
        .execute()
    )
    rows = response.data or []
    stores = []
    for row in rows:
        raw_url = (row.get("store_url") or "").rstrip("/")
        if not raw_url:
            continue
        if not raw_url.startswith("http"):
            raw_url = "https://" + raw_url
        store_key_raw = raw_url.replace("https://", "").replace("http://", "").replace(".", "_").replace("/", "_")
        store_key = f"{user_id[:8]}__{store_key_raw}"
        stores.append({
            "name": row.get("store_name") or store_key_raw,
            "store_key": store_key,
            "base_url": raw_url,
            "products_json": f"{raw_url}/products.json?limit=250",
            "user_id": user_id,
        })
    print(f"[STORES] user_id={user_id} stores_found={len(stores)}")
    return stores


def fetch_live_products(store: dict) -> List[dict]:
    print(f"Checking {store['base_url']}...")

    rows: List[dict] = []
    seen = set()
    page_url = store["products_json"]

    while page_url:
        response = requests.get(page_url, headers=HEADERS, timeout=60)
        response.raise_for_status()
        data = response.json()

        for product in data.get("products", []):
            handle = product.get("handle", "")
            product_name = product.get("title", "")
            product_url = f"{store['base_url']}/products/{handle}"

            variants = product.get("variants", []) or []
            if not variants:
                row = {
                    "store_key": store["store_key"],
                    "product_handle": handle,
                    "product_name": product_name,
                    "variant_name": "",
                    "price": None,
                    "compare_at_price": None,
                    "product_url": product_url,
                }
                key = product_identity(row)
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
                continue

            for variant in variants:
                variant_name = (variant.get("title") or "").strip()
                if variant_name.lower() == "default title":
                    variant_name = ""

                row = {
                    "store_key": store["store_key"],
                    "product_handle": handle,
                    "product_name": product_name,
                    "variant_name": variant_name,
                    "price": to_decimal(variant.get("price")),
                    "compare_at_price": to_decimal(variant.get("compare_at_price")),
                    "product_url": product_url,
                }
                key = product_identity(row)
                if key not in seen:
                    seen.add(key)
                    rows.append(row)

        link_header = response.headers.get("Link", "")
        next_url = None
        if 'rel="next"' in link_header:
            parts = [p.strip() for p in link_header.split(",")]
            for part in parts:
                if 'rel="next"' in part:
                    start = part.find("<")
                    end = part.find(">")
                    if start != -1 and end != -1:
                        next_url = part[start + 1:end]
                        break
        page_url = next_url

    print(f"[SCRAPE] store={store['store_key']} rows_scraped={len(rows)}")
    return rows


def load_previous_snapshot(store_key: str) -> List[dict]:
    all_rows = []
    page_size = 1000
    start = 0

    while True:
        response = (
            supabase.table(SUPABASE_TABLE)
            .select("store_key, product_handle, product_name, variant_name, price, compare_at_price, product_url")
            .eq("store_key", store_key)
            .order("id")
            .range(start, start + page_size - 1)
            .execute()
        )

        batch = response.data or []
        if not batch:
            break

        for row in batch:
            all_rows.append({
                "store_key": row.get("store_key"),
                "product_handle": row.get("product_handle"),
                "product_name": row.get("product_name"),
                "variant_name": row.get("variant_name") or "",
                "price": to_decimal(row.get("price")),
                "compare_at_price": to_decimal(row.get("compare_at_price")),
                "product_url": row.get("product_url"),
            })

        if len(batch) < page_size:
            break

        start += page_size

    print(f"[LOAD] store={store_key} rows_previous={len(all_rows)}")
    return all_rows


def index_snapshot(rows: List[dict]) -> Dict[str, dict]:
    return {product_identity(row): row for row in rows}


def compare_snapshots(old_rows: List[dict], new_rows: List[dict]) -> dict:
    old_map = index_snapshot(old_rows)
    new_map = index_snapshot(new_rows)

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    new_products = [new_map[k] for k in sorted(new_keys - old_keys)]
    disappeared_products = [old_map[k] for k in sorted(old_keys - new_keys)]

    price_changes = []
    compare_at_changes = []

    for key in sorted(old_keys & new_keys):
        old_row = old_map[key]
        new_row = new_map[key]

        if old_row.get("price") != new_row.get("price"):
            price_changes.append({
                "product_handle": new_row.get("product_handle"),
                "product_name": new_row.get("product_name"),
                "variant_name": new_row.get("variant_name"),
                "product_url": new_row.get("product_url"),
                "old_price": old_row.get("price"),
                "new_price": new_row.get("price"),
            })

        if old_row.get("compare_at_price") != new_row.get("compare_at_price"):
            compare_at_changes.append({
                "product_handle": new_row.get("product_handle"),
                "product_name": new_row.get("product_name"),
                "variant_name": new_row.get("variant_name"),
                "product_url": new_row.get("product_url"),
                "old_compare_at_price": old_row.get("compare_at_price"),
                "new_compare_at_price": new_row.get("compare_at_price"),
            })

    return {
        "new_products": new_products,
        "disappeared_products": disappeared_products,
        "price_changes": price_changes,
        "compare_at_changes": compare_at_changes,
        "has_changes": any([new_products, disappeared_products, price_changes, compare_at_changes]),
        "old_count": len(old_rows),
        "new_count": len(new_rows),
    }


def format_product_label(row: dict) -> str:
    name = row.get("product_name") or row.get("product_handle") or "Unknown product"
    variant = (row.get("variant_name") or "").strip()
    return f"{name} — {variant}" if variant else name


def html_list(items: List[str]) -> str:
    if not items:
        return "<p>None</p>"
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def build_email(store: dict, diff: dict) -> Tuple[str, str, str]:
    subject = (
        f"[RivalRadar] {store['name']} changes detected "
        f"(new: {len(diff['new_products'])}, "
        f"price: {len(diff['price_changes'])}, "
        f"compare-at: {len(diff['compare_at_changes'])}, "
        f"gone: {len(diff['disappeared_products'])})"
    )

    price_change_items = []
    for item in diff["price_changes"]:
        label = format_product_label(item)
        price_change_items.append(
            f'<a href="{item["product_url"]}">{label}</a>: '
            f'{money_str(item["old_price"])} → {money_str(item["new_price"])}'
        )

    compare_at_change_items = []
    for item in diff["compare_at_changes"]:
        label = format_product_label(item)
        compare_at_change_items.append(
            f'<a href="{item["product_url"]}">{label}</a>: '
            f'{money_str(item["old_compare_at_price"])} → {money_str(item["new_compare_at_price"])}'
        )

    new_product_items = []
    for item in diff["new_products"]:
        label = format_product_label(item)
        new_product_items.append(
            f'<a href="{item["product_url"]}">{label}</a> — current price {money_str(item["price"])}'
        )

    disappeared_items = []
    for item in diff["disappeared_products"]:
        label = format_product_label(item)
        disappeared_items.append(f'{label} — last seen price {money_str(item["price"])}')

    email_to = EMAIL_TO
    if store.get("user_id"):
        try:
            profile = supabase.table("profiles").select("email").eq("id", store["user_id"]).single().execute()
            if profile.data and profile.data.get("email"):
                email_to = profile.data["email"]
        except Exception:
            pass

    html = f"""
    <html>
      <body style="font-family: sans-serif; color: #111;">
        <h2>🔔 {store['name']} — changes detected</h2>
        <p><strong>Previous rows:</strong> {diff['old_count']}<br>
           <strong>Current rows:</strong> {diff['new_count']}</p>
        <h3>Price changes ({len(diff['price_changes'])})</h3>
        {html_list(price_change_items)}
        <h3>Compare-at price changes ({len(diff['compare_at_changes'])})</h3>
        {html_list(compare_at_change_items)}
        <h3>New products ({len(diff['new_products'])})</h3>
        {html_list(new_product_items)}
        <h3>Disappeared products ({len(diff['disappeared_products'])})</h3>
        {html_list(disappeared_items)}
        <hr>
        <p style="color:#888;font-size:12px;">Sent by RivalRadar · rival-radar.online</p>
      </body>
    </html>
    """

    return subject, html, email_to


def send_email_alert(store: dict, diff: dict) -> None:
    if not EMAIL_FROM or not GMAIL_APP_PASSWORD:
        print("[EMAIL] Skipped — EMAIL_FROM or GMAIL_APP_PASSWORD missing")
        return

    subject, html_body, email_to = build_email(store, diff)

    if not email_to:
        print("[EMAIL] Skipped — no recipient email found")
        return

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = EMAIL_FROM
    message["To"] = email_to
    message.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.login(EMAIL_FROM, GMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_FROM, [email_to], message.as_string())

    print(f"[EMAIL] store={store['store_key']} sent_to={email_to}")


def save_current_snapshot(store_key: str, rows: List[dict]) -> None:
    print(f"[WRITE] store={store_key} rows_prepared={len(rows)}")

    delete_response = (
        supabase.table(SUPABASE_TABLE)
        .delete()
        .eq("store_key", store_key)
        .execute()
    )
    deleted_rows = len(delete_response.data or [])
    print(f"[DELETE] store={store_key} deleted_rows={deleted_rows}")

    payload = []
    for row in rows:
        payload.append({
            "store_key": row["store_key"],
            "product_handle": row["product_handle"],
            "product_name": row["product_name"],
            "variant_name": row["variant_name"],
            "price": float(row["price"]) if row["price"] is not None else None,
            "compare_at_price": float(row["compare_at_price"]) if row["compare_at_price"] is not None else None,
            "product_url": row["product_url"],
        })

    if payload:
        batch_size = 500
        inserted_rows = 0
        for i in range(0, len(payload), batch_size):
            batch = payload[i:i + batch_size]
            insert_response = supabase.table(SUPABASE_TABLE).insert(batch).execute()
            inserted_rows += len(insert_response.data or [])
    else:
        inserted_rows = 0

    print(f"[INSERT] store={store_key} inserted_rows={inserted_rows}")


def save_snapshot_history(store_key: str, rows: List[dict]) -> None:
    payload = []
    for row in rows:
        payload.append({
            "store_key": row["store_key"],
            "product_handle": row["product_handle"],
            "product_name": row["product_name"],
            "variant_name": row["variant_name"],
            "price": float(row["price"]) if row["price"] is not None else None,
            "compare_at_price": float(row["compare_at_price"]) if row["compare_at_price"] is not None else None,
            "product_url": row["product_url"],
        })

    if not payload:
        print(f"[HISTORY] store={store_key} rows_inserted=0")
        return

    inserted = 0
    batch_size = 500
    for i in range(0, len(payload), batch_size):
        batch = payload[i:i + batch_size]
        response = supabase.table(HISTORY_TABLE).insert(batch).execute()
        inserted += len(response.data or [])

    print(f"[HISTORY] store={store_key} rows_inserted={inserted}")


def run_store(store: dict) -> None:
    current_rows = fetch_live_products(store)
    previous_rows = load_previous_snapshot(store["store_key"])
    diff = compare_snapshots(previous_rows, current_rows)

    print(
        f"[COMPARE] store={store['store_key']} "
        f"new_products={len(diff['new_products'])} "
        f"price_changes={len(diff['price_changes'])} "
        f"compare_at_changes={len(diff['compare_at_changes'])} "
        f"disappeared_products={len(diff['disappeared_products'])}"
    )

    if diff["has_changes"]:
        send_email_alert(store, diff)
    else:
        print(f"[EMAIL] store={store['store_key']} no changes, no email sent")

    save_current_snapshot(store["store_key"], current_rows)
    save_snapshot_history(store["store_key"], current_rows)


def run() -> None:
    print(f"Supabase URL: {SUPABASE_URL}")
    print(f"Supabase key present: {bool(SUPABASE_KEY)}")
    print(f"USER_ID: {USER_ID or '(demo mode)'}")

    if USER_ID:
        stores = load_stores_for_user(USER_ID)
        if not stores:
            print(f"[WARN] No active rivals found for user_id={USER_ID}. Nothing to do.")
            return
    else:
        print("[DEMO] No USER_ID set — running demo stores")
        stores = DEMO_STORES

    errors = []
    for store in stores:
        try:
            run_store(store)
        except Exception as exc:
            print(f"[ERROR] store={store['store_key']} error={exc}")
            errors.append((store['store_key'], str(exc)))
            continue  # keep going for remaining stores

    if errors:
        print(f"\n[SUMMARY] Completed with {len(errors)} error(s):")
        for store_key, err in errors:
            print(f"  - {store_key}: {err}")
        raise SystemExit(1)
    else:
        print(f"\n[SUMMARY] All {len(stores)} stores completed successfully.")


if __name__ == "__main__":
    run()
