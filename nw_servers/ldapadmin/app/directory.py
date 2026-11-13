"""LDAP 操作（ユーザー / グループ / 検索用アカウント）。"""
import re
from contextlib import contextmanager

from ldap3 import (BASE, HASHED_SALTED_SHA, LEVEL, MODIFY_ADD, MODIFY_DELETE,
                   MODIFY_REPLACE, NONE, SUBTREE, Connection, Server)
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn
from ldap3.utils.hashed import hashed

from . import config

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


class DirectoryError(Exception):
    pass


# ---------------------------------------------------------------- validation
def check_name(value: str, label: str) -> str:
    value = (value or "").strip()
    if not NAME_RE.match(value):
        raise DirectoryError(f"{label}「{value}」は使用できません（英数字と . _ - 、先頭は英数字、64文字まで）")
    return value


def check_vlan(value) -> str:
    """'' → 指定なし。1〜4094 の整数文字列を返す。"""
    value = ("" if value is None else str(value)).strip()
    if value == "":
        return ""
    if not value.isdigit() or not 1 <= int(value) <= 4094:
        raise DirectoryError(f"VLAN ID「{value}」が不正です（1〜4094）")
    return str(int(value))


def check_priority(value) -> int:
    value = ("" if value is None else str(value)).strip()
    if value == "":
        return config.DEFAULT_PRIORITY
    if not value.lstrip("-").isdigit():
        raise DirectoryError(f"優先度「{value}」は整数で指定してください")
    return int(value)


def check_mail(value: str) -> str:
    value = (value or "").strip()
    if value and not MAIL_RE.match(value):
        raise DirectoryError(f"メールアドレス「{value}」が不正です")
    return value


def display_name(sn: str, given: str, uid: str) -> str:
    return " ".join(x for x in (sn, given) if x) or uid


# ---------------------------------------------------------------- helpers
def _attrs(raw: dict) -> dict:
    out = {}
    for k, vals in raw.items():
        out[k.lower()] = [v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v) for v in vals]
    return out


def _first(a: dict, key: str, default: str = "") -> str:
    v = a.get(key.lower())
    return v[0] if v else default


def _int(a: dict, key: str, default=None):
    try:
        return int(_first(a, key))
    except ValueError:
        return default


def effective_vlan(user: dict, groups_by_priority: list) -> tuple:
    """(VLAN ID, 決定理由) を返す。FreeRADIUS のポリシーと同じ順序で評価する。"""
    if user.get("vlan"):
        return user["vlan"], "個別指定"
    for g in groups_by_priority:
        if g["vlan"] and user["uid"] in g["members"]:
            return g["vlan"], f"グループ {g['cn']}"
    return "", ""


def by_priority(groups: list) -> list:
    return sorted(groups, key=lambda g: (g["priority"], g["cn"].lower()))


class Directory:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or config.load_settings()
        self.base = self.cfg["base_dn"]
        self.users_dn = f"ou=Users,{self.base}"
        self.groups_dn = f"ou=Groups,{self.base}"
        self.svc_dn = f"ou=ServiceAccounts,{self.base}"
        self.admin_dn = f"{self.cfg['admin_rdn']},{self.base}"
        self.bind_dn = f"{self.cfg['bind_rdn']},{self.base}"

    # ------------------------------------------------------------ connection
    @contextmanager
    def conn(self, user: str | None = None, password: str | None = None):
        server = Server(config.LDAP_URI, get_info=NONE, connect_timeout=5)
        try:
            c = Connection(server, user=user or self.admin_dn,
                           password=self.cfg["admin_password"] if password is None else password,
                           auto_bind=True, receive_timeout=15, raise_exceptions=False)
        except LDAPBindError as e:
            raise DirectoryError(f"LDAPへのログインに失敗しました: {e}") from e
        except LDAPException as e:
            raise DirectoryError(f"LDAPサーバに接続できません（反映中の可能性があります）: {e}") from e
        try:
            yield c
        finally:
            c.unbind()

    def verify_bind(self, dn: str, password: str):
        """True=成功 / False=認証失敗 / None=接続不可"""
        if not password:
            return False
        server = Server(config.LDAP_URI, get_info=NONE, connect_timeout=5)
        try:
            Connection(server, user=dn, password=password, auto_bind=True, receive_timeout=10).unbind()
            return True
        except LDAPBindError:
            return False
        except LDAPException:
            return None

    @staticmethod
    def _ok(c, result, what: str):
        if not result:
            r = c.result or {}
            raise DirectoryError(f"{what}に失敗しました: {r.get('description', '')} {r.get('message', '')}".strip())

    @staticmethod
    def _search(c, base, flt, attrs, scope=SUBTREE):
        c.search(base, flt, scope, attributes=attrs)
        rc = (c.result or {}).get("result")
        if rc not in (0, 32):   # 32 = noSuchObject（OUがまだない）
            raise DirectoryError(f"検索に失敗しました: {c.result.get('description')} {c.result.get('message', '')}")
        return [{"dn": e["dn"], "a": _attrs(e["raw_attributes"])}
                for e in c.response or [] if e.get("type") == "searchResEntry"]

    def _exists(self, c, dn) -> bool:
        return bool(self._search(c, dn, "(objectClass=*)", ["1.1"], BASE))

    def ensure_structure(self, c):
        for ou, dn in (("Users", self.users_dn), ("Groups", self.groups_dn), ("ServiceAccounts", self.svc_dn)):
            if not self._exists(c, dn):
                self._ok(c, c.add(dn, ["organizationalUnit"], {"ou": ou}), f"ou={ou} の作成")

    def _next_number(self, c, attr, objclass, lo, hi) -> int:
        rows = self._search(c, self.base, f"(objectClass={objclass})", [attr])
        nums = [n for n in (_int(r["a"], attr) for r in rows) if n is not None and lo <= n <= hi]
        n = max(nums) + 1 if nums else lo
        if n > hi:
            raise DirectoryError(f"{attr} の割り当て範囲（{lo}〜{hi}）を使い切りました")
        return n

    # ------------------------------------------------------------ groups
    GROUP_ATTRS = ["cn", "gidNumber", "description", "memberUid", "nwVlanId", "nwVlanPriority", "objectClass"]

    def _load_groups(self, c) -> list:
        groups = []
        for r in self._search(c, self.groups_dn, "(objectClass=posixGroup)", self.GROUP_ATTRS):
            a = r["a"]
            groups.append({
                "dn": r["dn"],
                "cn": _first(a, "cn"),
                "gid": _int(a, "gidNumber"),
                "description": _first(a, "description"),
                "members": sorted(set(a.get("memberuid", [])), key=str.lower),
                "vlan": _first(a, "nwVlanId"),
                "priority": _int(a, "nwVlanPriority", config.DEFAULT_PRIORITY),
                "aux": "nwradiusgroup" in [o.lower() for o in a.get("objectclass", [])],
            })
        return sorted(groups, key=lambda g: g["cn"].lower())

    def list_groups(self) -> list:
        with self.conn() as c:
            return self._load_groups(c)

    def get_group(self, cn: str) -> dict:
        for g in self.list_groups():
            if g["cn"] == cn:
                return g
        raise DirectoryError(f"グループ「{cn}」が見つかりません")

    def create_group(self, cn, description="", members=(), vlan="", priority=None, c=None):
        cn = check_name(cn, "グループ名")
        vlan = check_vlan(vlan)
        priority = check_priority(priority)
        if c is None:
            with self.conn() as c2:
                return self.create_group(cn, description, members, vlan, priority, c2)
        self.ensure_structure(c)
        if any(g["cn"].lower() == cn.lower() for g in self._load_groups(c)):
            raise DirectoryError(f"グループ「{cn}」は既に存在します")
        known = {u["uid"] for u in self._load_users(c)}
        unknown = [m for m in members if m not in known]
        if unknown:
            raise DirectoryError(f"存在しないユーザーが指定されています: {', '.join(unknown)}")
        gid = self._next_number(c, "gidNumber", "posixGroup", config.GID_MIN, config.GID_MAX)
        attrs = {"cn": cn, "gidNumber": str(gid), "nwVlanPriority": str(priority)}
        if description:
            attrs["description"] = description
        if members:
            attrs["memberUid"] = list(members)
        if vlan:
            attrs["nwVlanId"] = vlan
        dn = f"cn={escape_rdn(cn)},{self.groups_dn}"
        self._ok(c, c.add(dn, ["posixGroup", "nwRadiusGroup"], attrs), "グループの作成")
        return gid

    def update_group(self, cn, description=None, members=None, vlan=None, priority=None, new_cn=None):
        with self.conn() as c:
            groups = self._load_groups(c)
            g = next((x for x in groups if x["cn"] == cn), None)
            if not g:
                raise DirectoryError(f"グループ「{cn}」が見つかりません")
            changes = {}
            if not g["aux"]:
                changes["objectClass"] = [(MODIFY_ADD, ["nwRadiusGroup"])]
            if description is not None:
                changes["description"] = [(MODIFY_REPLACE, [description] if description else [])]
            if members is not None:
                known = {u["uid"] for u in self._load_users(c)}
                unknown = [m for m in members if m not in known]
                if unknown:
                    raise DirectoryError(f"存在しないユーザーが指定されています: {', '.join(unknown)}")
                changes["memberUid"] = [(MODIFY_REPLACE, sorted(set(members)))]
            if vlan is not None:
                vlan = check_vlan(vlan)
                changes["nwVlanId"] = [(MODIFY_REPLACE, [vlan] if vlan else [])]
            if priority is not None:
                changes["nwVlanPriority"] = [(MODIFY_REPLACE, [str(check_priority(priority))])]
            if changes:
                self._ok(c, c.modify(g["dn"], changes), "グループの更新")
            if new_cn and new_cn != cn:
                new_cn = check_name(new_cn, "グループ名")
                if any(x["cn"].lower() == new_cn.lower() for x in groups):
                    raise DirectoryError(f"グループ「{new_cn}」は既に存在します")
                self._ok(c, c.modify_dn(g["dn"], f"cn={escape_rdn(new_cn)}"), "グループ名の変更")

    def delete_group(self, cn) -> int:
        """削除し、このグループを主グループにしていたユーザー数を返す。"""
        with self.conn() as c:
            g = next((x for x in self._load_groups(c) if x["cn"] == cn), None)
            if not g:
                raise DirectoryError(f"グループ「{cn}」が見つかりません")
            primary = [u for u in self._load_users(c) if g["gid"] is not None and u["gidNumber"] == g["gid"]]
            self._ok(c, c.delete(g["dn"]), "グループの削除")
            return len(primary)

    # ------------------------------------------------------------ users
    USER_ATTRS = ["uid", "sn", "givenName", "cn", "displayName", "mail", "uidNumber", "gidNumber",
                  "homeDirectory", "nwVlanId", "objectClass"]

    def _load_users(self, c) -> list:
        users = []
        for r in self._search(c, self.users_dn, "(|(objectClass=inetOrgPerson)(objectClass=posixAccount))",
                              self.USER_ATTRS):
            a = r["a"]
            ocs = [o.lower() for o in a.get("objectclass", [])]
            uid = _first(a, "uid")
            if not uid:
                continue
            users.append({
                "dn": r["dn"], "uid": uid, "sn": _first(a, "sn"), "given": _first(a, "givenName"),
                "cn": _first(a, "cn"), "display": _first(a, "displayName") or _first(a, "cn"),
                "mail": _first(a, "mail"), "uidNumber": _int(a, "uidNumber"), "gidNumber": _int(a, "gidNumber"),
                "home": _first(a, "homeDirectory"), "vlan": _first(a, "nwVlanId"),
                "posix": "posixaccount" in ocs, "aux": "nwradiususer" in ocs,
            })
        return sorted(users, key=lambda u: u["uid"].lower())

    def overview(self) -> tuple:
        """(users, groups) を返す。users には所属グループと実効VLANを付与。"""
        with self.conn() as c:
            users, groups = self._load_users(c), self._load_groups(c)
        prio = by_priority(groups)
        for u in users:
            u["groups"] = [g["cn"] for g in groups if u["uid"] in g["members"]]
            u["eff_vlan"], u["eff_reason"] = effective_vlan(u, prio)
        return users, groups

    def get_user(self, uid: str) -> dict:
        users, _ = self.overview()
        for u in users:
            if u["uid"] == uid:
                return u
        raise DirectoryError(f"ユーザー「{uid}」が見つかりません")

    def user_exists(self, uid: str) -> bool:
        with self.conn() as c:
            return any(u["uid"].lower() == uid.lower() for u in self._load_users(c))

    def create_user(self, uid, sn="", given="", mail="", password="", groups=(), vlan="", c=None):
        uid = check_name(uid, "ユーザー名")
        sn, given = (sn or "").strip(), (given or "").strip()
        mail, vlan = check_mail(mail), check_vlan(vlan)
        if not password:
            raise DirectoryError("パスワードを入力してください")
        if c is None:
            with self.conn() as c2:
                return self.create_user(uid, sn, given, mail, password, groups, vlan, c2)
        self.ensure_structure(c)
        if any(u["uid"].lower() == uid.lower() for u in self._load_users(c)):
            raise DirectoryError(f"ユーザー「{uid}」は既に存在します")
        gmap = {g["cn"]: g for g in self._load_groups(c)}
        missing = [g for g in groups if g not in gmap]
        if missing:
            raise DirectoryError(f"存在しないグループが指定されています: {', '.join(missing)}")
        uid_number = self._next_number(c, "uidNumber", "posixAccount", config.UID_MIN, config.UID_MAX)
        gid = gmap[groups[0]]["gid"] if groups and gmap[groups[0]]["gid"] is not None else config.DEFAULT_GID
        name = display_name(sn, given, uid)
        attrs = {"uid": uid, "sn": sn or uid, "cn": name, "displayName": name,
                 "uidNumber": str(uid_number), "gidNumber": str(gid),
                 "homeDirectory": f"/home/{uid}", "loginShell": "/bin/bash", "userPassword": password}
        if given:
            attrs["givenName"] = given
        if mail:
            attrs["mail"] = mail
        if vlan:
            attrs["nwVlanId"] = vlan
        dn = f"uid={escape_rdn(uid)},{self.users_dn}"
        self._ok(c, c.add(dn, ["inetOrgPerson", "posixAccount", "nwRadiusUser"], attrs), "ユーザーの作成")
        for g in groups:
            self._ok(c, c.modify(gmap[g]["dn"], {"memberUid": [(MODIFY_ADD, [uid])]}), f"グループ {g} への追加")

    def update_user(self, uid, sn=None, given=None, mail=None, groups=None, vlan=None, new_uid=None,
                    password=None):
        """None の項目は変更しない。"""
        with self.conn() as c:
            users = self._load_users(c)
            u = next((x for x in users if x["uid"] == uid), None)
            if not u:
                raise DirectoryError(f"ユーザー「{uid}」が見つかりません")
            all_groups = self._load_groups(c)
            gmap = {g["cn"]: g for g in all_groups}
            new_sn = u["sn"] if sn is None else (sn.strip() or uid)
            new_given = u["given"] if given is None else given.strip()

            changes = {}
            add_oc = []
            if not u["posix"]:
                add_oc.append("posixAccount")
            if not u["aux"]:
                add_oc.append("nwRadiusUser")
            if add_oc:
                changes["objectClass"] = [(MODIFY_ADD, add_oc)]
            if not u["posix"]:
                changes["uidNumber"] = [(MODIFY_REPLACE, [str(self._next_number(
                    c, "uidNumber", "posixAccount", config.UID_MIN, config.UID_MAX))])]
                changes["gidNumber"] = [(MODIFY_REPLACE, [str(config.DEFAULT_GID)])]
                changes["homeDirectory"] = [(MODIFY_REPLACE, [f"/home/{uid}"])]
            if sn is not None or given is not None:
                name = display_name(new_sn, new_given, uid)
                changes["sn"] = [(MODIFY_REPLACE, [new_sn])]
                changes["givenName"] = [(MODIFY_REPLACE, [new_given] if new_given else [])]
                changes["cn"] = [(MODIFY_REPLACE, [name])]
                changes["displayName"] = [(MODIFY_REPLACE, [name])]
            if mail is not None:
                mail = check_mail(mail)
                changes["mail"] = [(MODIFY_REPLACE, [mail] if mail else [])]
            if vlan is not None:
                vlan = check_vlan(vlan)
                changes["nwVlanId"] = [(MODIFY_REPLACE, [vlan] if vlan else [])]
            if password:
                changes["userPassword"] = [(MODIFY_REPLACE, [password])]
            if groups is not None:
                missing = [g for g in groups if g not in gmap]
                if missing:
                    raise DirectoryError(f"存在しないグループが指定されています: {', '.join(missing)}")
                gids = [gmap[g]["gid"] for g in groups if gmap[g]["gid"] is not None]
                if gids and u["gidNumber"] not in gids:
                    changes["gidNumber"] = [(MODIFY_REPLACE, [str(gids[0])])]
            if changes:
                self._ok(c, c.modify(u["dn"], changes), "ユーザーの更新")

            if groups is not None:
                current = {g["cn"] for g in all_groups if uid in g["members"]}
                for g in sorted(set(groups) - current):
                    self._ok(c, c.modify(gmap[g]["dn"], {"memberUid": [(MODIFY_ADD, [uid])]}), f"グループ {g} への追加")
                for g in sorted(current - set(groups)):
                    self._ok(c, c.modify(gmap[g]["dn"], {"memberUid": [(MODIFY_DELETE, [uid])]}), f"グループ {g} からの削除")

            if new_uid and new_uid != uid:
                new_uid = check_name(new_uid, "ユーザー名")
                if any(x["uid"].lower() == new_uid.lower() for x in users):
                    raise DirectoryError(f"ユーザー「{new_uid}」は既に存在します")
                self._ok(c, c.modify_dn(u["dn"], f"uid={escape_rdn(new_uid)}"), "ユーザー名の変更")
                new_dn = f"uid={escape_rdn(new_uid)},{u['dn'].split(',', 1)[1]}"
                if u["home"] in ("", f"/home/{uid}"):
                    self._ok(c, c.modify(new_dn, {"homeDirectory": [(MODIFY_REPLACE, [f"/home/{new_uid}"])]}),
                             "ホームディレクトリの更新")
                for g in self._load_groups(c):
                    if uid in g["members"]:
                        self._ok(c, c.modify(g["dn"], {"memberUid": [(MODIFY_DELETE, [uid]), (MODIFY_ADD, [new_uid])]}),
                                 f"グループ {g['cn']} のメンバー名変更")

    def set_user_password(self, uid, password):
        if not password:
            raise DirectoryError("パスワードを入力してください")
        self.update_user(uid, password=password)

    def delete_user(self, uid):
        with self.conn() as c:
            u = next((x for x in self._load_users(c) if x["uid"] == uid), None)
            if not u:
                raise DirectoryError(f"ユーザー「{uid}」が見つかりません")
            for g in self._load_groups(c):
                if uid in g["members"]:
                    self._ok(c, c.modify(g["dn"], {"memberUid": [(MODIFY_DELETE, [uid])]}), f"グループ {g['cn']} からの削除")
            self._ok(c, c.delete(u["dn"]), "ユーザーの削除")

    # ------------------------------------------------------------ service accounts
    def list_service_accounts(self) -> list:
        with self.conn() as c:
            rows = self._search(c, self.svc_dn, "(objectClass=*)", ["cn", "description"], LEVEL)
        out = []
        for r in rows:
            out.append({"dn": r["dn"], "cn": _first(r["a"], "cn"), "description": _first(r["a"], "description"),
                        "radius": r["dn"].lower() == self.bind_dn.lower()})
        return sorted(out, key=lambda x: x["cn"].lower())

    def _svc(self, c, cn):
        rows = self._search(c, self.svc_dn, f"(cn={escape_filter_chars(cn)})", ["cn"], LEVEL)
        if not rows:
            raise DirectoryError(f"検索用アカウント「{cn}」が見つかりません")
        return rows[0]["dn"]

    def create_service_account(self, cn, password, description=""):
        cn = check_name(cn, "アカウント名")
        if not password:
            raise DirectoryError("パスワードを入力してください")
        with self.conn() as c:
            self.ensure_structure(c)
            dn = f"cn={escape_rdn(cn)},{self.svc_dn}"
            if self._exists(c, dn):
                raise DirectoryError(f"アカウント「{cn}」は既に存在します")
            attrs = {"cn": cn, "userPassword": hashed(HASHED_SALTED_SHA, password)}
            if description:
                attrs["description"] = description
            self._ok(c, c.add(dn, ["organizationalRole", "simpleSecurityObject"], attrs), "アカウントの作成")
        return dn

    def set_service_password(self, cn, password) -> str:
        if not password:
            raise DirectoryError("パスワードを入力してください")
        with self.conn() as c:
            dn = self._svc(c, cn)
            self._ok(c, c.modify(dn, {"userPassword": [(MODIFY_REPLACE, [hashed(HASHED_SALTED_SHA, password)])]}),
                     "パスワード変更")
        return dn

    def delete_service_account(self, cn):
        with self.conn() as c:
            dn = self._svc(c, cn)
            if dn.lower() == self.bind_dn.lower():
                raise DirectoryError("FreeRADIUS が使用中のアカウントは削除できません（先に別のアカウントへ切り替えてください）")
            self._ok(c, c.delete(dn), "アカウントの削除")

    def service_dn(self, cn) -> str:
        with self.conn() as c:
            return self._svc(c, cn)
