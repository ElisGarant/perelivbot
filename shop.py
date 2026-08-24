"""Модуль магазина: товары, подписки, сделки, поставщики."""import jsonfrom datetime import datetime, timedeltafrom pathlib import PathSHOP_DATA_FILE = Path("shop_data.json")def load_shop_data() -> dict:    try:        with open(SHOP_DATA_FILE, "r", encoding="utf-8") as f:            return json.load(f)    except (FileNotFoundError, json.JSONDecodeError):        return {            "products": [],            "subscriptions": [],            "transactions": [],            "broadcast_text": "Спасибо за покупку!",            "subscription_price": 99,            "subscription_days": 7,        }def save_shop_data(data: dict):    with open(SHOP_DATA_FILE, "w", encoding="utf-8") as f:        json.dump(data, f, ensure_ascii=False, indent=2)def get_products() -> list:    return load_shop_data()["products"]def get_product(product_id: int) -> dict | None:    for p in get_products():


def get_subscriptions() -> list:
    """Return all stored subscriptions for userbot startup."""
    return load_shop_data()["subscriptions"]
