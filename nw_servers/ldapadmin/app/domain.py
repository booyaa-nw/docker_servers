"""ドメイン（ベースDN）変更: 全エントリを書き出し、DNを置換した LDIF を作る。"""
import base64
import re

from ldap3 import SUBTREE
from ldap3.utils.dn import parse_dn

from . import config
from .directory import Directory, DirectoryError

LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
SKIP_REWRITE = {"userpassword"}


def check_domain(domain: str) -> str:
    domain = (domain or "").strip().lower().rstrip(".")
    labels = domain.split(".")
    if not domain or not all(LABEL_RE.match(x) for x in labels):
        raise DirectoryError(f"ドメイン「{domain}」が不正です（例: example.local）")
    return domain


def _ldif_value(attr: str, val: bytes) -> str:
    safe = True
    try:
        s = val.decode("ascii")
    except UnicodeDecodeError:
        safe = False
    else:
        if s and (s[0] in " :<" or s.endswith(" ")) or any(ch in s for ch in "\r\n\0"):
            safe = False
    if safe:
        return f"{attr}: {s}"
    return f"{attr}:: {base64.b64encode(val).decode()}"


def _depth(dn: str) -> int:
    try:
        return len(parse_dn(dn))
    except Exception:
        return dn.count(",") + 1


def build_ldif(d: Directory, new_domain: str, rewrite_mail: bool) -> tuple[bytes, int]:
    old_base = d.base
    new_base = config.domain_to_base(new_domain)
    if new_base.lower() == old_base.lower():
        raise DirectoryError("現在と同じドメインです")
    old_l = old_base.lower()
    old_domain = config.base_to_domain(old_base)

    def rewrite(s: str) -> str:
        sl = s.lower()
        if sl == old_l or sl.endswith("," + old_l):
            return s[: len(s) - len(old_base)] + new_base
        return s

    with d.conn() as c:
        c.search(old_base, "(objectClass=*)", SUBTREE, attributes=["*"])
        if (c.result or {}).get("result") != 0:
            raise DirectoryError(f"エントリの読み出しに失敗しました: {c.result.get('description')}")
        entries = [(e["dn"], e["raw_attributes"]) for e in c.response if e.get("type") == "searchResEntry"]

    out = []
    for dn, raw in entries:
        new_dn = rewrite(dn)
        if new_dn == dn:
            raise DirectoryError(f"ベースDN配下でないエントリがあります: {dn}")
        attrs = {}
        for attr, vals in raw.items():
            new_vals = []
            for v in vals:
                if attr.lower() not in SKIP_REWRITE:
                    try:
                        s = v.decode("utf-8")
                    except UnicodeDecodeError:
                        pass
                    else:
                        s = rewrite(s)
                        if rewrite_mail and attr.lower() == "mail" and s.lower().endswith("@" + old_domain.lower()):
                            s = s[: len(s) - len(old_domain)] + new_domain
                        v = s.encode("utf-8")
                new_vals.append(v)
            attrs[attr] = new_vals
        if dn.lower() == old_l:   # ベースエントリ（dcObject / organization）
            for k in list(attrs):
                if k.lower() in ("dc", "o"):
                    del attrs[k]
            attrs["dc"] = [new_domain.split(".")[0].encode()]
            ocs = [o.decode().lower() for o in raw.get("objectClass", [])]
            if "organization" in ocs:
                attrs["o"] = [new_domain.encode()]
        out.append((new_dn, attrs))

    out.sort(key=lambda x: _depth(x[0]))
    lines = []
    for dn, attrs in out:
        lines.append(_ldif_value("dn", dn.encode("utf-8")))
        for attr, vals in attrs.items():
            for v in vals:
                lines.append(_ldif_value(attr, v))
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8"), len(out)


def change_domain(new_domain: str, rewrite_mail: bool) -> int:
    new_domain = check_domain(new_domain)
    cfg = config.load_settings()
    d = Directory(cfg)
    data, count = build_ldif(d, new_domain, rewrite_mail)
    config.atomic_write(config.IMPORT_LDIF, data)
    cfg["previous_base_dn"] = cfg["base_dn"]
    cfg["base_dn"] = config.domain_to_base(new_domain)
    config.save_settings(cfg)
    return count
