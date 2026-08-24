"""
Управление пользователями и ролями.
"""
import json
from pathlib import Path

USERS_FILE = Path("users.json")


def load_users() -> dict:
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        from config import ADMIN_IDS
        return {
            "users": {str(uid): {"role": "owner"} for uid in ADMIN_IDS}
        }


def save_users(data: dict):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user(user_id: int) -> dict:
    data = load_users()
    return data["users"].get(str(user_id), {"role": "user"})


def set_role(user_id: int, role: str):
    data = load_users()
    if "users" not in data:
        data["users"] = {}
    data["users"][str(user_id)] = {"role": role}
    save_users(data)


def get_role(user_id: int) -> str:
    return get_user(user_id).get("role", "user")


def is_owner(user_id: int) -> bool:
    return user_id in ADMIN_IDS or get_role(user_id) == "owner"


def is_admin(user_id: int) -> bool:
    return get_role(user_id) in ("owner", "admin")


def is_supplier(user_id: int) -> bool:
    return get_role(user_id) in ("owner", "admin", "supplier")


def get_all_users() -> dict:
    return load_users()["users"]


def get_users_by_role(role: str) -> list[int]:
    data = load_users()
    result = []
    for uid, user in data["users"].items():
        if user["role"] == role:
            result.append(int(uid))
    return result


def add_user(user_id: int) -> bool:
    data = load_users()
    if str(user_id) in data["users"]:
        return False
    data["users"][str(user_id)] = {"role": "user"}
    save_users(data)
    return True


def remove_user(user_id: int) -> bool:
    data = load_users()
    if str(user_id) not in data["users"]:
        return False
    del data["users"][str(user_id)]
    save_users(data)
    return True
