"""共有設定（settings.json）と各コンテナの反映状況の読み書き。"""
import hashlib
import json
import os
import secrets
from pathlib import Path

SHARED = Path(os.environ.get("SHARED_DIR", "/shared"))
SETTINGS = SHARED / "settings.json"
STATUS_LDAP = SHARED / "status-ldap.json"
STATUS_RADIUS = SHARED / "status-radius.json"
IMPORT_LDIF = SHARED / "import.ldif"
POLICY = SHARED / "freeradius" / "nwlab_vlan"
SECRET_FILE = SHARED / "ldapmgttooz-secret"

LDAP_URI = os.environ.get("LDAP_URI", "ldap://nw-openldap:389")

UID_MIN, UID_MAX = 10000, 19999
GID_MIN, GID_MAX = 20001, 29999
DEFAULT_GID = 20000          # 所属グループなしのユーザーに付ける gidNumber
DEFAULT_PRIORITY = 100


class SettingsMissing(Exception):
    pass


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_settings() -> dict:
    cfg = read_json(SETTINGS)
    if not cfg:
        raise SettingsMissing("settings.json がありません（nw-openldap が起動していません）")
    return cfg


def save_settings(cfg: dict) -> dict:
    """generation を1つ進めて保存する（openldap / freeradius が検知して再起動）。"""
    current = read_json(SETTINGS) or {}
    cfg = dict(cfg)
    cfg["generation"] = int(current.get("generation", cfg.get("generation", 0))) + 1
    atomic_write(SETTINGS, json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8"))
    return cfg


def session_secret() -> str:
    env = os.environ.get("LDAPMGTTOOZ_SECRET") or os.environ.get("LDAPADMIN_SECRET")
    if env:
        return env
    try:
        return SECRET_FILE.read_text().strip()
    except FileNotFoundError:
        legacy = SHARED / "ldapadmin-secret"
        s = legacy.read_text().strip() if legacy.is_file() else secrets.token_urlsafe(48)
        atomic_write(SECRET_FILE, s.encode())
        return s


def policy_path(cfg):
    slot = cfg.get("data_slot")
    return POLICY.parent / slot / POLICY.name if slot else POLICY


def radius_signature() -> str:
    cfg = read_json(SETTINGS) or {}
    try:
        p = hashlib.md5(policy_path(cfg).read_bytes()).hexdigest()
    except FileNotFoundError:
        p = "none"
    return f"{cfg.get('generation', 0)}-{p}"


def apply_state() -> dict:
    """画面上部に表示する反映状況。"""
    cfg = read_json(SETTINGS) or {}
    sl = read_json(STATUS_LDAP) or {}
    sr = read_json(STATUS_RADIUS) or {}
    ldap_current = bool(sl) and sl.get("applied_generation") == cfg.get("generation")
    radius_current = sr.get("applied_signature") == radius_signature()
    initializing = ldap_current and sl.get("phase") == "pending"
    ldap_pending = not ldap_current or not sl.get("ok", True) or initializing
    radius_pending = not radius_current or not sr.get("ok", True)
    errors = []
    if ldap_current and not initializing and not sl.get("ok", True):
        errors.append("LDAP: " + sl.get("message", ""))
    if radius_current and not sr.get("ok", True):
        errors.append("RADIUS: " + sr.get("message", ""))
    return {
        "ldap_pending": ldap_pending,
        "radius_pending": radius_pending,
        "pending": ldap_pending or radius_pending,
        "errors": errors,
        "ldap": sl,
        "radius": sr,
    }


def base_to_domain(base_dn: str) -> str:
    parts = []
    for rdn in base_dn.split(","):
        k, _, v = rdn.strip().partition("=")
        if k.strip().lower() == "dc":
            parts.append(v.strip())
    return ".".join(parts)


def domain_to_base(domain: str) -> str:
    return ",".join(f"dc={label}" for label in domain.split("."))
