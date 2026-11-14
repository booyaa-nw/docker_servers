# nwlab LDAP / RADIUS / ユーザ管理Web UI

| コンテナ | 役割 | ポート |
|---|---|---|
| nw-openldap | LDAP（ユーザ・グループ・検索用アカウント） | 389/tcp |
| nw-freeradius | RADIUS（PAP / MS-CHAPv2 / PEAP / EAP-TTLS、Dynamic VLAN） | 1812,1813/udp |
| nw-ldapmgttooz | ユーザ管理Web UI（Python/FastAPI） | 8080/tcp |

3つのコンテナは `./ldap-shared/` を共有します。

- `settings.json` … ベースDN、admin管理者、FreeRADIUS検索用アカウント（Web UIの「管理設定」で変更）
- `freeradius/nwlab_vlan` … Dynamic VLAN ポリシー（Web UIが自動生成）
- `domains.json` … 各ドメインの設定と退避情報
- `status-*.json` … 各コンテナの反映状況

設定が変わると openldap / freeradius が自動で再起動します（FreeRADIUSは `-C` で検証してから再起動）。

## 更新・起動

同じ Compose プロジェクトと既存の `openldap/data`、`ldap-shared` を使ってください。
旧 Web UI のコンテナがある場合は、名前とポートを確認し、そのコンテナだけを停止・削除してから起動します。

```sh
docker compose up -d --build openldap freeradius ldapmgttooz
docker compose logs -f openldap freeradius ldapmgttooz
```

初期ドメインは `asakusa.lab`（`dc=asakusa,dc=lab`）。既存の settings.json があれば、そのドメインを引き継ぎます。
新規ドメインに切り替える場合は UI から作成して切り替えてください。既存データの DN は書き換えません。

## ドメイン管理

LdapMgtTooz の「ドメイン」画面で、作成・一覧・切り替え・削除を操作できます。

- 新規ドメインは、初回の切り替え時に OU と FreeRADIUS 用検索アカウントを作ります。ユーザとグループは空です。
- 一度に稼働するのは 1 ドメインです。LDAP の反映後に FreeRADIUS が切り替わります。
- ドメインごとに管理者・検索アカウント・MDB・Dynamic VLAN を保持します。再ログインには切替先の認証情報を使います。
- 使用中のドメインは削除できません。削除は一覧からの除外で、MDB と設定は退避扱いとして保持します。同名で再作成しても旧データは再利用しません。

バックアップには `ldap-shared/` と `openldap/data/` の両方が必要です。
`domains.json` はドメイン台帳で、各ドメインの認証情報を含みます。

## 使い方

`http://<ホスト>:8080/` に admin管理者（初期値: `admin` / `.env` の `LDAP_ADMIN_PASSWORD`）でログイン。

- **ユーザ** … 追加・編集・名前変更・パスワード変更・削除。所属グループと個別VLAN
- **グループ** … 追加・編集・名前変更・削除。VLAN ID（空欄可）と優先度
- **Dynamic VLAN** … 優先度の一括編集、ユーザごとの実効VLAN、生成ポリシーの確認
- **CSV一括登録** … UTF-8 / Shift_JIS。「確認のみ」で事前チェック可能。テンプレートと書き出しあり
- **管理設定** … admin管理者の変更、検索用アカウント（ou=ServiceAccounts）の追加・パスワード変更・削除・FreeRADIUS用の切り替え

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
