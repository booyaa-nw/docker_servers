"""Interactive LDAP administrator recovery for Docker host operators."""
import argparse
from getpass import getpass
import sys

from . import config, domain
from .directory import DirectoryError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain", help="例: asakusa.lab")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("パスワード入力には対話端末が必要です")
    try:
        name = domain.check_domain(args.domain)
        if name not in {item["name"] for item in domain.list_domains()}:
            raise DirectoryError("指定ドメインがありません")
        password = getpass(f"{name} の新しい管理者パスワード: ")
        if password != getpass("確認入力: "):
            raise DirectoryError("パスワードが一致しません")
        active, rdn = domain.reset_admin_password(name, password)
    except (DirectoryError, config.SettingsMissing, OSError, EOFError, KeyboardInterrupt) as exc:
        parser.exit(1, f"リセット失敗: {exc}\n")
    print(f"{name}: {rdn} のパスワードを更新しました。")
    print("LDAPが設定を再読み込みします。" if active else "次回のドメイン切り替え時に反映されます。")


if __name__ == "__main__":
    main()
