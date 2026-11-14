"""CSV 一括登録 / 書き出し。"""
import csv
import io

from .directory import Directory, DirectoryError, check_mail, check_name, check_vlan

COLUMNS = ["username", "last_name", "first_name", "password", "groups", "mail", "vlan", "action"]
ALIASES = {
    "username": ["username", "user", "uid", "ユーザ名", "ユーザー名", "ログイン名"],
    "last_name": ["last_name", "lastname", "sn", "姓"],
    "first_name": ["first_name", "firstname", "givenname", "名"],
    "password": ["password", "パスワード"],
    "groups": ["groups", "group", "グループ"],
    "mail": ["mail", "email", "メール", "メールアドレス"],
    "vlan": ["vlan", "vlan_id", "vlanid"],
    "action": ["action", "操作"],
}
LOOKUP = {alias.lower(): key for key, names in ALIASES.items() for alias in names}

TEMPLATE_ROWS = [
    ["username", "last_name", "first_name", "password", "groups", "mail", "vlan", "action"],
    ["hanako.sato", "佐藤", "花子", "P@ssw0rd-01", "NW-Staff", "hanako.sato@example.local", "", "add"],
    ["jiro.suzuki", "鈴木", "次郎", "P@ssw0rd-02", "NW-Staff;NW-Admin", "", "20", "add"],
    ["old.user", "", "", "", "", "", "", "delete"],
]


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise DirectoryError("文字コードを判別できません（UTF-8 または Shift_JIS で保存してください）")


def to_csv(rows: list) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")   # Excel で文字化けしないよう BOM 付き


def export_users(users: list) -> bytes:
    rows = [COLUMNS[:-1]]
    for u in users:
        rows.append([u["uid"], "" if u["sn"] == u["uid"] else u["sn"], u["given"], "", ";".join(u["groups"]), u["mail"], u["vlan"]])
    return to_csv(rows)


def _split_groups(s: str) -> list:
    return [g.strip() for g in s.replace("|", ";").replace("、", ";").split(";") if g.strip()]


def run_import(d: Directory, data: bytes, on_exists: str, create_groups: bool, dry_run: bool) -> list:
    text = decode(data)
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise DirectoryError("CSVが空です")
    keys = [LOOKUP.get(h.strip().lower()) for h in header]
    if "username" not in keys:
        raise DirectoryError("ヘッダー行に username（ユーザー名）列がありません")
    unknown = [h for h, k in zip(header, keys) if k is None and h.strip()]

    users, groups = d.overview()
    existing_users = {u["uid"]: u for u in users}
    existing_groups = {g["cn"] for g in groups}
    planned_groups = set()
    seen = set()
    results = []
    if unknown:
        results.append({"line": 1, "user": "", "result": "注意", "message": f"無視した列: {', '.join(unknown)}"})

    for line_no, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        rec = {k: (row[i].strip() if i < len(row) else "") for i, k in enumerate(keys) if k}
        present = {k for k in keys if k}
        uid = rec.get("username", "")
        res = {"line": line_no, "user": uid, "result": "", "message": ""}
        try:
            uid = check_name(uid, "ユーザー名")
            if uid.lower() in seen:
                raise DirectoryError("CSV内で同じユーザー名が重複しています")
            seen.add(uid.lower())
            action = (rec.get("action") or "add").lower()
            if action not in ("add", "update", "delete"):
                raise DirectoryError(f"action「{action}」は add / update / delete のいずれかにしてください")
            exists = uid in existing_users

            if action == "delete":
                if not exists:
                    raise DirectoryError("削除対象のユーザーが存在しません")
                if not dry_run:
                    d.delete_user(uid)
                res.update(result="削除", message="")
                results.append(res)
                continue

            check_mail(rec.get("mail", ""))
            check_vlan(rec.get("vlan", ""))
            grp = _split_groups(rec.get("groups", "")) if "groups" in present else None
            for g in grp or []:
                check_name(g, "グループ名")
                if g not in existing_groups and g not in planned_groups:
                    if not create_groups:
                        raise DirectoryError(f"グループ「{g}」が存在しません（「存在しないグループを作成する」で自動作成できます）")
                    planned_groups.add(g)
                    if not dry_run:
                        d.create_group(g, description="CSV一括登録で作成")
                        existing_groups.add(g)
                    res["message"] += f"グループ {g} を作成。"

            if exists:
                if action == "add" and on_exists == "skip":
                    res.update(result="スキップ", message=res["message"] + "既に存在します")
                    results.append(res)
                    continue
                # 空欄の項目は変更しない
                kw = {
                    "sn": rec.get("last_name") or None,
                    "given": rec.get("first_name") or None,
                    "mail": rec.get("mail") or None,
                    "vlan": rec.get("vlan") or None,
                    "groups": grp if grp else None,
                    "password": rec.get("password") or None,
                }
                if not dry_run:
                    d.update_user(uid, **kw)
                res.update(result="更新")
            else:
                if action == "update":
                    raise DirectoryError("更新対象のユーザーが存在しません")
                if not rec.get("password"):
                    raise DirectoryError("新規ユーザーにはパスワードが必要です")
                if not dry_run:
                    d.create_user(uid, rec.get("last_name", ""), rec.get("first_name", ""), rec.get("mail", ""),
                                  rec["password"], grp or [], rec.get("vlan", ""))
                existing_users[uid] = {"uid": uid}
                res.update(result="作成")
        except DirectoryError as e:
            res.update(result="エラー", message=res["message"] + str(e))
        results.append(res)
    return results
