# nwlab LDAP / RADIUS / ユーザ管理Web UI

| コンテナ | 役割 | ポート |
|---|---|---|
| nw-openldap | LDAP（ユーザ・グループ・検索用アカウント） | 389/tcp |
| nw-freeradius | RADIUS（PAP / MS-CHAPv2 / PEAP / EAP-TTLS、Dynamic VLAN） | 1812,1813/udp |
| nw-ldapadmin | ユーザ管理Web UI（Python/FastAPI） | 8080/tcp |

3つのコンテナは `./ldap-shared/` を共有します。

- `settings.json` … ベースDN、admin管理者、FreeRADIUS検索用アカウント（Web UIの「管理設定」で変更）
- `freeradius/nwlab_vlan` … Dynamic VLAN ポリシー（Web UIが自動生成）
- `backup/` … ドメイン変更前のLDIFバックアップ
- `status-*.json` … 各コンテナの反映状況

設定が変わると openldap / freeradius が自動で再起動します（FreeRADIUSは `-C` で検証してから再起動）。

## 既存環境（LAM構成）からの移行

```sh
cd ~/.local/docker/nw_servers
# 念のためLDAPのバックアップ
docker exec nw-openldap slapcat -f /etc/openldap/slapd.conf > ldap-backup-$(date +%Y%m%d).ldif

# LAMを停止・削除
docker compose rm -sf lam
docker volume rm nw_servers_nw-lam-config 2>/dev/null

# zipの中身で上書き（compose.yml / openldap / freeradius / ldapadmin）
# ※ freeradius/ldap（旧ファイル）は不要になります。ldap.tmpl に置き換わりました
rm -f freeradius/ldap
mkdir -p ldap-shared
docker compose up -d --build openldap freeradius ldapadmin
docker compose logs -f openldap freeradius ldapadmin
```

- 既存の `openldap/data` はそのまま使えます（`.env` のパスワードから `ldap-shared/settings.json` が初回だけ作られます）。
- 既存のグループ・ユーザは、Web UIで一度保存すると Dynamic VLAN 用の objectClass が自動で付きます。
- compose.yml の他サービス（influxdb など）は、お手元の内容に合わせてください。

## 使い方

`http://<ホスト>:8080/` に admin管理者（初期値: `admin` / `.env` の `LDAP_ADMIN_PASSWORD`）でログイン。

- **ユーザ** … 追加・編集・名前変更・パスワード変更・削除。所属グループと個別VLAN
- **グループ** … 追加・編集・名前変更・削除。VLAN ID（空欄可）と優先度
- **Dynamic VLAN** … 優先度の一括編集、ユーザごとの実効VLAN、生成ポリシーの確認
- **CSV一括登録** … UTF-8 / Shift_JIS。「確認のみ」で事前チェック可能。テンプレートと書き出しあり
- **管理設定** … ドメイン変更、admin管理者の変更、検索用アカウント（ou=ServiceAccounts）の追加・パスワード変更・削除・FreeRADIUS用の切り替え

### Dynamic VLAN の判定順

1. ユーザ個別のVLAN（LDAP属性 `nwVlanId`）
2. VLAN付きグループのうち、優先度（`nwVlanPriority`）が最も小さいもの
3. どれにも該当しなければ VLAN属性なしで Access-Accept

返却属性: `Tunnel-Type=VLAN(13)` / `Tunnel-Medium-Type=IEEE-802(6)` / `Tunnel-Private-Group-Id=<VLAN ID>`。
PEAP / EAP-TTLS では内側の認証で判定し、外側の Access-Accept に載せます。

## 注意

- ユーザのパスワードは MS-CHAPv2 / PEAP のため LDAP に平文で保存します（検索用アカウントだけが読み取り可）。
- `ldap-shared/settings.json` には admin と検索用アカウントのパスワードが平文で入ります（ラボ用途）。
- ドメイン変更・admin変更・検索用アカウント切替の間（数秒〜十数秒）は認証が止まります。
- NW機器側のLDAP設定（ベースDN、バインドDN/パスワード）は、ドメインや検索用アカウントを変えたら合わせて変更してください。
- RADIUSのデバッグ: `.env` に `RADIUS_DEBUG=1` を書いて `docker compose up -d freeradius`。
