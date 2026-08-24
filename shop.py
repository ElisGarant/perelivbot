"""
Модуль магазина: товары, подписки, сделки, поставщики.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

SHOP_DATA_FILE = Path("shop_data.json")


def load_shop_data() -> dict:
    try:
        with open(SHOP_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "products": [],
            "subscriptions": [],
            "transactions": [],
            "broadcast_text": "Спасибо за покупку!",
            "subscription_price": 99,
            "subscription_days": 7,
        }


def save_shop_data(data: dict):
    with open(SHOP_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_products() -> list:
    return load_shop_data()["products"]


def get_product(product_id: int) -> dict | None:
    for p in get_products():
        if p["id"] == product_id:
            return p
    return None


def add_product(name: str, price: float, content: str, supplier_id: int = None) -> dict:
    data = load_shop_data()
    product_id = max([p["id"] for p in data["products"]], default=0) + 1
    product = {
        "id": product_id,
        "name": name,
        "price": price,
        "content": content,
        "supplier_id": supplier_id,
    }
    data["products"].append(product)
    save_shop_data(data)
    return product


def remove_product(product_id: int) -> bool:
    data = load_shop_data()
    before = len(data["products"])
    data["products"] = [p for p in data["products"] if p["id"] != product_id]
    save_shop_data(data)
    return len(data["products"]) < before


def add_subscription(user_id: int, days: int) -> dict:
    data = load_shop_data()
    now = datetime.now()
    expires = now + timedelta(days=days)
    data["subscriptions"] = [s for s in data["subscriptions"] if s["user_id"] != user_id]
    sub = {
        "user_id": user_id,
        "started_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "active": True,
    }
    data["subscriptions"].append(sub)
    save_shop_data(data)
    return sub


def check_subscription(user_id: int) -> dict | None:
    data = load_shop_data()
    for sub in data["subscriptions"]:
        if sub["user_id"] == user_id:
            if datetime.fromisoformat(sub["expires_at"]) > datetime.now():
                return sub
            else:
                sub["active"] = False
                save_shop_data(data)
                return None
    return None


def add_transaction(user_id: int, product_id: int, amount: float, supplier_id: int = None):
    data = load_shop_data()
    tx = {
        "id": len(data["transactions"]) + 1,
        "user_id": user_id,
        "product_id": product_id,
        "amount": amount,
        "supplier_id": supplier_id,
        "timestamp": datetime.now().isoformat(),
    }
    data["transactions"].append(tx)
    save_shop_data(data)
    return tx


def get_transactions(supplier_id: int = None) -> list:
    data = load_shop_data()
    if supplier_id is None:
        return data["transactions"]
    return [t for t in data["transactions"] if t.get("supplier_id") == supplier_id]


def get_suppliers() -> list[int]:
    data = load_shop_data()
    suppliers = set()
    for p in data["products"]:
        if p.get("supplier_id"):
            suppliers.add(p["supplier_id"])
    for t in data["transactions"]:
        if t.get("supplier_id"):
            suppliers.add(t["supplier_id"])
    return list(suppliers)


def get_supplier_stats(supplier_id: int) -> dict:
    products = [p for p in get_products() if p.get("supplier_id") == supplier_id]
    txs = [t for t in get_transactions() if t.get("supplier_id") == supplier_id]
    total_amount = sum(t["amount"] for t in txs)
    return {
        "products_count": len(products),
        "transactions_count": len(txs),
        "total_amount": total_amount,
    }
