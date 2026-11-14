"""Persist inactive LDAP profiles; settings.json remains the active profile."""
import re
import secrets
from threading import RLock

from . import config
from .directory import DirectoryError

CATALOG = config.SHARED / "domains.json"
LOCK = RLock()  # The Docker image runs one Uvicorn worker.
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def check_domain(value):
    name = value.strip().lower().removesuffix(".")
    if len(name) > 253 or not all(LABEL.fullmatch(x) for x in name.split(".")):
        raise DirectoryError("ドメイン名が不正です（例: example.test）")
    return name


def _state():
    active = config.load_settings()
    catalog = config.read_json(CATALOG)
    if catalog is None and CATALOG.exists():
        raise DirectoryError("ドメイン台帳を読み込めません")
    if catalog is None:
        catalog = {"domains": {}, "deleted": []}
    name = config.base_to_domain(active["base_dn"]).lower()
    catalog["domains"][name] = active
    return name, catalog


def _save(catalog):
    import json
    config.atomic_write(CATALOG, json.dumps(catalog, ensure_ascii=False).encode())


def list_domains():
    with LOCK:
        active, catalog = _state()
        return [{"name": name, "base_dn": cfg["base_dn"], "active": name == active}
                for name, cfg in sorted(catalog["domains"].items())]


def create(name, admin_password, bind_password):
    name = check_domain(name)
    if min(len(admin_password), len(bind_password)) < 8:
        raise DirectoryError("各パスワードは8文字以上にしてください")
    with LOCK:
        _, catalog = _state()
        if name in catalog["domains"]:
            raise DirectoryError("同じドメインが登録済みです")
        catalog["domains"][name] = {
            "base_dn": config.domain_to_base(name), "data_slot": secrets.token_hex(16),
            "admin_rdn": "cn=admin", "admin_password": admin_password,
            "bind_rdn": "cn=radius,ou=ServiceAccounts", "bind_password": bind_password,
        }
        _save(catalog)
    return name


def switch(name, current_base):
    name = check_domain(name)
    with LOCK:
        active, catalog = _state()
        if catalog["domains"][active]["base_dn"] != current_base:
            raise DirectoryError("表示中にドメインが変わりました")
        if name not in catalog["domains"]:
            raise DirectoryError("指定ドメインがありません")
        if name == active:
            raise DirectoryError("すでに使用中です")
        # Snapshot the live profile before changing the shared pointer.
        _save(catalog)
        config.save_settings(catalog["domains"][name])
    return name


def delete(name, confirmation):
    name = check_domain(name)
    if confirmation != name:
        raise DirectoryError("確認欄にドメイン名を入力してください")
    with LOCK:
        active, catalog = _state()
        if name == active:
            raise DirectoryError("使用中のドメインは削除できません")
        if name not in catalog["domains"]:
            raise DirectoryError("指定ドメインがありません")
        profile = catalog["domains"].pop(name)
        catalog["deleted"].append({"name": name, "profile": profile})
        _save(catalog)
    return name


def reset_admin_password(name, password):
    """Host operator recovery only. Stop the Web UI before running the CLI."""
    name = check_domain(name)
    if len(password) < 8 or any(c in password for c in "\r\n\x00"):
        raise DirectoryError("パスワードは改行やNULを含まない8文字以上にしてください")
    with LOCK:
        active, catalog = _state()
        if name not in catalog["domains"]:
            raise DirectoryError("指定ドメインがありません")
        profile = catalog["domains"][name]
        profile["admin_password"] = password
        if name == active:
            # _state always uses live settings for the active profile.
            config.save_settings(profile)
        else:
            _save(catalog)
    return name == active, profile["admin_rdn"]
