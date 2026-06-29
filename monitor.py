import urllib.request
import json
import os
from datetime import date

COMPETITOR_STORES = [
    "https://kaged.com",
    "https://gymreapers.com",
]

DATA_FOLDER = os.path.expanduser("~/shopify-competitor-alerts/data")

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
    """One entry per product title — use the lowest variant price."""
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

def save_snapshot(store_name, price_map):
    os.makedirs(DATA_FOLDER, exist_ok=True)
    filename = os.path.join(DATA_FOLDER, f"{store_name}_{date.today()}.json")
    with open(filename, "w") as f:
        json.dump(price_map, f, indent=2)
    print(f"Saved {len(price_map)} products for {store_name}")

def load_last_snapshot(store_name):
    files = sorted([
        f for f in os.listdir(DATA_FOLDER)
        if f.startswith(store_name) and f.endswith(".json")
    ])
    if not files:
        return {}
    with open(os.path.join(DATA_FOLDER, files[-1])) as f:
        return json.load(f)

def detect_changes(old_prices, new_prices):
    changes = []
    for product, new_price in new_prices.items():
        old_price = old_prices.get(product)
        if old_price is None:
            changes.append(f"NEW: {product} — starting at ${new_price:.2f}")
        elif abs(new_price - old_price) > 0.01:
            direction = "DOWN" if new_price < old_price else "UP"
            pct = abs(new_price - old_price) / old_price * 100
            changes.append(
                f"PRICE {direction}: {product} | ${old_price:.2f} → ${new_price:.2f} ({pct:.1f}%)"
            )
    return changes

def run():
    os.makedirs(DATA_FOLDER, exist_ok=True)
    for store_url in COMPETITOR_STORES:
        store_name = store_url.replace("https://", "").replace("http://", "").replace("/", "_").replace(".", "_")
        print(f"\nChecking {store_url}...")
        products = fetch_products(store_url)
        if not products:
            print(f"  Skipping — could not reach store.")
            continue
        new_prices = extract_prices(products)
        old_prices = load_last_snapshot(store_name)
        changes = detect_changes(old_prices, new_prices)
        if changes:
            print(f"  {len(changes)} change(s) detected:")
            for c in changes:
                print(f"    • {c}")
        else:
            print(f"  No changes. {len(new_prices)} products monitored.")
        save_snapshot(store_name, new_prices)

if __name__ == "__main__":
    run()
