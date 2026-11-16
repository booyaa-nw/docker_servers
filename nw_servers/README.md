# NetRig ネットワーク検証環境

Raspberry Pi 4上のDocker ComposeでLDAP/RADIUS、MailpitとNetRigポータルを運用します。WebアプリはNetRigにログインしてから、LDAP管理・ログビューのプラグインを開きます。個別アプリのログインはありません。

## サービスとポート

| サービス | 用途 | ホスト公開ポート |
|---|---|---|
| caddy / netrig | NetRigポータル、共通ログイン、プラグイン入口 | 80/tcp |
| openldap | LDAPディレクトリ | 389/tcp |
| freeradius | RADIUS認証・アカウンティング | 1812/udp, 1813/udp |
| log-view | Syslog/SNMP Trap収集、イベント表示 | 514/tcp+udp, 162/tcp+udp |
| mailpit | SMTP受信（STARTTLS） | 25/tcp |
| mailpit-smtps | SMTP暗黙TLS | 465/tcp |

## 初回準備

```sh
cp .env.example .env
```

`.env` の `ChangeMe` と `replace-with` を強い値に変更し、`PI_IP` を実際のアドレスに設定します。`NETRIG_SECRET` は `openssl rand -hex 32` で生成します。NetRigのユーザー名は `netrig/config/settings.json`、共通パスワードは `.env` で設定します。

Web UIは検証LAN向けにHTTPで動作します。認証情報を送信するため、信頼できるLAN内で利用してください。Mailpit SMTPはSTARTTLS/暗黙TLSを使います。初回起動前に自己署名証明書を用意します（CN、DNS名、IPは `.env` の実値に合わせてください）。

```sh
mkdir -p mailpit/secrets mailpit/data plugins/log_view/config/mibs plugins/log_view/data
openssl req -x509 -newkey rsa:3072 -nodes \
  -keyout mailpit/secrets/tls.key -out mailpit/secrets/tls.crt -days 825 \
  -subj "/CN=nwlab.local" \
  -addext "subjectAltName=DNS:nwlab.local,IP:192.0.2.10"
chmod 600 mailpit/secrets/tls.key
```

## 起動

```sh
docker compose up -d --build
docker compose ps
docker compose logs -f netrig caddy ldapmgttooz log-view mailpit
```

ブラウザーで `http://<PiのIP>/` を開いてNetRigにサインインします。ポータルからLDAP管理またはログビューを選択します。SMTPクライアントはSTARTTLSで25、または暗黙TLSで465を指定します。

## 新しいディレクトリ構成への初回切り替え

旧LDAP MDBを引き継がない場合は、Composeを停止して新しい `openldap/data/` を空にしてから起動します。この操作でLDAPディレクトリ内の既存データは削除され、環境変数から初期設定を作り直します。

```sh
docker compose down
sudo find ./openldap/data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
sudo install -d -o 10001 -g 10001 plugins/log_view/data plugins/log_view/config/mibs
sudo chown -R 10001:10001 plugins/log_view/data plugins/log_view/config/mibs
docker compose up -d --build
```

OpenLDAPの初期化完了後にLDAP管理画面を開きます。ログビューのDB起動エラーが出る場合は、上記のUID 10001所有権と書き込み権限を確認してください。

LDAP/RADIUSだけを更新する場合も同じComposeプロジェクトを使います。

```sh
docker compose up -d --build openldap freeradius ldapmgttooz
```

## ディレクトリ規則

各コンテナの設定と永続データは、そのコンテナ名の配下に置きます。`shared/ldap/` はOpenLDAP、FreeRADIUS、LDAP管理プラグインが共有する設定領域です。

```text
caddy/{config,data}/
netrig/{app,config,data}/
openldap/{config,data}/
freeradius/{config,data}/
ldapmgttooz/{app,config,data}/
plugins/log_view/{app,config,data}/
mailpit/{config,data,secrets}/
shared/ldap/
```

Composeファイルと各コンテナのDockerfileは、それぞれの実行環境に近い場所にあります。ログビューのアプリコードは `plugins/log_view/app/`、MIBソースは `plugins/log_view/config/mibs/`、SQLite DBとコンパイル済みMIBは `plugins/log_view/data/` に保存されます。

ログビューの書き込み先はコンテナUID 10001です。Linuxホストでは初回起動前に所有者を合わせます。

```sh
sudo chown -R 10001:10001 plugins/log_view/data plugins/log_view/config/mibs
```

## バックアップ

- LDAP: `openldap/data/` と `shared/ldap/` をセットで保存します。共有設定にはLDAP管理者情報が含まれます。
- RADIUS: `shared/ldap/freeradius/` に動的VLANポリシーが保存されます。
- ログ: `plugins/log_view/data/` と `plugins/log_view/config/mibs/` を保存します。
- Mailpit: `mailpit/data/` を保存します。
- SMTP証明書: `mailpit/secrets/` を安全に保管します。秘密鍵はGitに含めないでください。
- NetRig認証: `.env` の `NETRIG_SECRET` と認証ユーザー情報を安全に保管します。

整合したバックアップを取る場合は、対象サービスを停止してからコピーしてください。ログ保持期間は `.env` の `LOG_RETENTION_DAYS` で設定します。

## プラグイン

- LDAP管理: LDAPユーザー、グループ、Dynamic VLAN、複数ドメインの切り替えを管理します。
- ログビュー: Syslog、SNMP Trap、Mailpitイベントの検索・確認、MIB管理、DB保守を行います。

各アプリはCaddyからのみWebアクセス可能です。NetRigが署名Cookieを検証し、認証済みユーザーをプラグインへ渡します。MailpitのWeb/APIもDocker内部ネットワークに限定されます。

## 注意事項

- Web UIはHTTPのみです。信頼できる検証LAN内で使用してください。
- UDP Syslog/Trapに到達保証はありません。機器側宛先、Firewall、時刻同期も確認してください。
- SNMPv3の認証・復号やInform応答は行いません。raw BERやExportにはcommunity等が含まれる可能性があります。
- FreeRADIUS設定は `docker compose run --rm --no-deps freeradius -XC` で確認できます。前景デバッグは `docker compose run --rm --service-ports freeradius -X` を使います。
