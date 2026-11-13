"""Dynamic VLAN 用の FreeRADIUS ポリシー（unlang）を生成する。"""
from . import config
from .directory import by_priority


def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _reply(value_expr: str) -> list:
    return [
        "\t\tupdate reply {",
        "\t\t\t&Tunnel-Type := VLAN",
        "\t\t\t&Tunnel-Medium-Type := IEEE-802",
        f"\t\t\t&Tunnel-Private-Group-Id := {value_expr}",
        "\t\t}",
    ]


def render(groups: list) -> str:
    lines = [
        "# このファイルは nw-ldapadmin が自動生成します。直接編集しないでください。",
        "# 評価順: 1) ユーザー個別VLAN  2) グループ（優先度の小さい順）  3) 該当なし→VLAN属性なし",
        "nwlab_vlan {",
        "\tif (&control:Tmp-String-0 && (&control:Tmp-String-0 =~ /^[0-9]+$/)) {",
        *_reply('"%{control:Tmp-String-0}"'),
        "\t}",
    ]
    for g in by_priority([g for g in groups if g["vlan"]]):
        lines.append(f"\t# 優先度 {g['priority']}")
        lines.append(f"\telsif (LDAP-Group == {_q(g['cn'])}) {{")
        lines += _reply(_q(g["vlan"]))
        lines.append("\t}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def sync(groups: list) -> bool:
    """内容が変わったときだけ書き出す（FreeRADIUS が検知して再起動）。"""
    text = render(groups).encode("utf-8")
    try:
        if config.POLICY.read_bytes() == text:
            return False
    except FileNotFoundError:
        pass
    config.atomic_write(config.POLICY, text, mode=0o644)
    return True
