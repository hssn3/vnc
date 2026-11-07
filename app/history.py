"""Link history, stored next to the executable (links.json).

Every generated link is kept here so the GUI can list them, re-copy them or
send them to Telegram again.  Everything lives inside the application folder —
no %APPDATA%, no %TEMP%.
"""
import json
import os
import time

from settings import app_dir

MAX_ITEMS = 60


def store_path():
    return os.path.join(app_dir(), "links.json")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load():
    try:
        with open(store_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save(items):
    items = items[:MAX_ITEMS]
    tmp = store_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, store_path())
    return items


def add(url, password):
    """Record a freshly created link and close any previously active one."""
    items = load()
    for item in items:
        if item.get("state") == "active":
            item["state"] = "closed"
            item.setdefault("closed_at", _now())
    items.insert(0, {
        "url": url,
        "password": password or "",
        "created_at": _now(),
        "state": "active",
    })
    return save(items)


def close_active():
    items = load()
    changed = False
    for item in items:
        if item.get("state") == "active":
            item["state"] = "closed"
            item["closed_at"] = _now()
            changed = True
    if changed:
        save(items)
    return items


def clear():
    return save([])
