import urllib.request
import json
import os
from supabase import create_client, Client
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
print("Supabase client initialized")

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL")

COMPETITOR_STORES = [
    "https://kaged.com",
    "https://gymreapers.com",
]


def load_previous_snapshot(store_key):
    if not supabase:
        return {}

    response = (
        supabase.table("product_snapshots")
        .select("*")
        .eq("store_key", store_key)
        .execute()
    )

    previous = {}
    for row in response.data:
        product_name = row.get("product_name")
        price = row.get("price")
        if product_name is not None and price is not None:
            previous[product_name] = float(price)
    return previous


def save_current_snapshot(store_key, price_map):
    delete_response = (
        supabase.table("product_snapshots")
        .delete()
        .eq("store_key", store_key)
        .execute()
    )
    print(f"[Supabase] Delete {store_key}: data={delete_response.data}, error={delete_response.error}")

    rows = []
    for product_name, price in price_map.items():
        rows.append({
            "store_key": store_key,
            "product_handle": product_name.lower().replace(" ", "-"),
            "product_name": product_name,
            "variant_name": "",
            "price": price,
            "compare_at_price": None,
            "product_url": "",
        })

    print(f"[Supabase] Prepared {len(rows)} rows for {store_key}")

    insert_response = supabase.table("product_snapshots").insert(rows).execute()
    print(f"[Supabase] Insert {store_key}: data={insert_response.data}, error={insert_response.error}")
    
def fetch_products(store_url):
    url = f"{store_url}/products.json?limit=250"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read())
            return data.get("products", [])
    except Exception as e:
        print(f"Could not fetch {store_url}: {e}")
        return []

def extract_prices(products):
    price_map = {}
    for product in products:
        title = product.get("title", "Unknown")
        prices = []
        for variant in product.get("variants", []):
            try:
                prices.append(float(variant.get("price", "0")))
            except:
                pass
        if prices:
            price_map[title] = min(prices)
    return price_map





def detect_changes(old_prices, new_prices):
    changes = []
    for product, new_price in new_prices.items():
        old_price = old_prices.get(product)
        if old_price is None:
            changes.append(f"NEW PRODUCT: {product} - starting at ${new_price:.2f}")
        elif abs(new_price - old_price) > 0.01:
            direction = "DOWN" if new_price < old_price else "UP"
            pct = abs(new_price - old_price) / old_price * 100
            changes.append(f"PRICE {direction}: {product} | ${old_price:.2f} -> ${new_price:.2f} ({pct:.1f}%)")
    return changes

def send_email_alert(store_url, changes):
    subject = f"Competitor Alert: {len(changes)} change(s) at {store_url}"
    rows = "".join([f"<li style='margin-bottom:8px'>{c}</li>" for c in changes])
    html = f"<div style='font-family:Arial,sans-serif;max-width:600px;margin:auto'><h2 style='color:#e63946'>Competitor Price Alert</h2><p>Changes at <strong>{store_url}</strong> on {date.today()}</p><ul style='line-height:1.8'>{rows}</ul><hr/><p style='color:#999;font-size:12px'>Sent by your Shopify Competitor Monitor</p></div>"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = ALERT_EMAIL
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, ALERT_EMAIL, msg.as_string())
            print(f"  Email alert sent to {ALERT_EMAIL}")
    except Exception as e:
        print(f"  Email failed: {e}")

def run():
    
    for store_url in COMPETITOR_STORES:
        store_name = store_url.replace("https://", "").replace("http://", "").replace("/", "_").replace(".", "_")
        print(f"Checking {store_url}...")
        products = fetch_products(store_url)
        if not products:
            continue
        new_prices = extract_prices(products)
        old_prices = load_previous_snapshot(store_name)
        changes = detect_changes(old_prices, new_prices)
        if changes:
            print(f"  {len(changes)} change(s) detected:")
            for c in changes:
                print(f"    * {c}")
            send_email_alert(store_url, changes)
        else:
            print(f"  No changes. {len(new_prices)} products monitored.")
        save_current_snapshot(store_name, new_prices)

if __name__ == "__main__":
    run()
