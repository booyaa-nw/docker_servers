# NW Lab 統合環境

Raspberry Pi 4上のDocker Composeで、LDAP/RADIUS、LdapMgtTooz、NW Log View、Mailpit、Caddyポータルをまとめて起動します。検証環境ではCaddyをHTTPのみで動かし、`http://<PiのIP>/` のポータルから各ツールへ移動します。

## サービスとポート

| サービス | 用途 | ホスト公開ポート |
|---|---|---|
| caddy | HTTPポータル、LDAP UIとログ画面のリバースプロキシ | 80/tcp |
| openldap | LDAP | 389/tcp |
| freeradius | RADIUS認証・アカウンティング | 1812/udp, 1813/udp |
| log-view | Syslog/SNMP Trap収集、統合表示、Mailpit API同期 | 514/tcp+udp, 162/tcp+udp |
| mailpit | SMTP受信（STARTTLS） | 1025/tcp |
| mailpit-smtps | SMTP暗黙TLS | 465/tcp |

LdapMgtTooz（`/ldap/`）、Log View（`/log-view/`）、Mailpitの管理Web/APIはホストへ直接公開せず、Caddyまたは内部Dockerネットワークから利用します。

## 初回準備

検証用Web UIはIPアドレスでHTTPアクセスします。`PI_IP` をPiのLANアドレスに合わせてください。この構成ではWeb UIの認証CookieもHTTP用に設定されるため、信頼できる検証LAN内で使ってください。Mailpit SMTPのSTARTTLS/暗黙TLSは引き続き有効で、その証明書名に`PUBLIC_DOMAIN`を使います。

```sh
cp .env.example .env
```

`.env` の全ての `ChangeMe` と `replace-with` を強い秘密値に変更し、`PI_IP` をPiのLANアドレスに合わせます。Log Viewの署名鍵は `openssl rand -hex 32` で生成できます。

Mailpitは1025/tcpでSTARTTLSを提供し、465/tcpでは暗黙TLSを提供します。初回起動前に証明書を配置します（自己署名例。クライアントにCA/証明書を信頼させるか、ラボのCA発行証明書へ置き換えてください）。

```sh
mkdir -p secrets/mailpit ../log_view/mailpit-data ../log_view/data ../log_view/mibs
openssl req -x509 -newkey rsa:3072 -nodes \
  -keyout secrets/mailpit/tls.key -out secrets/mailpit/tls.crt -days 825 \
  -subj "/CN=nwlab.example.invalid" \
  -addext "subjectAltName=DNS:nwlab.example.invalid,IP:192.0.2.10"
sudo chown -R 10001:10001 ../log_view/data ../log_view/mibs
chmod 600 secrets/mailpit/tls.key
```

証明書コマンドのCN、DNS名、IPアドレスは `.env` に設定した実値へ置き換えます。PowerShellでは `New-Item -ItemType Directory -Force secrets/mailpit,../log_view/mailpit-data,../log_view/data,../log_view/mibs` を使えます。OpenSSLはWindowsまたはPiにインストールされたものを使ってください。

証明書生成サービスは証明書がない場合だけ自己署名証明書を作成します。上記手順で証明書を置く場合は、そのファイルを維持して使います。鍵はGitへ追加されません。

## 起動・更新

```sh
docker compose up -d --build
docker compose ps
docker compose logs -f caddy ldapmgttooz log-view mailpit
```

アクセス先は `http://<PiのIP>/`（ポータル）、`http://<PiのIP>/ldap/`（LDAP管理）、`http://<PiのIP>/log-view/`（ログ画面）です。Mailpitへメールを投入するSMTPクライアントはSTARTTLSで1025、または暗黙TLSで465を指定します。1025はSTARTTLSを広告しますが必須にはしていません。465のTLS終端からMailpitまでは内部Dockerネットワーク上のSMTPです。

## データ、バックアップ、復元

- LDAP: `openldap/data/` と `ldap-shared/` をセットで保全します。`ldap-shared/settings.json` と `domains.json` は認証情報を含みます。
- ログ: `../log_view/data/` にSQLite DBとMIBコンパイルキャッシュ、`../log_view/mibs/` に登録MIBを保存します。Log Viewの保守画面からDBバックアップ/復元と保持期間に基づくログ削除ができます。
- Mailpit: `../log_view/mailpit-data/` に受信メールを保存します（従来のLog View単独Composeと同じ場所です）。
- Mailpit SMTP用TLS鍵: `secrets/mailpit/` に保管し、別の安全な場所にもバックアップします。Web UIのHTTPS証明書ではありません。

一貫性のあるファイルバックアップは、対象サービスを停止してからコピーしてください。復元は同じパスへ戻し、所有者（Log View UID 10001）を確認して起動します。ログ保持期間は `.env` の `LOG_RETENTION_DAYS` で設定します。

## 統合後の単独起動

LDAP/RADIUSだけを更新する場合もこのComposeプロジェクトを使います。

```sh
docker compose up -d --build openldap freeradius ldapmgttooz
```

## Log View単独Composeからの移行

従来の `docker/log_view/compose.yaml` を使っていた場合、先にそのディレクトリで `docker compose down` を実行して旧log-view/Mailpitコンテナを停止・削除します。`data/`、`mibs/`、`mailpit-data/` のバインドマウントデータはそのまま残ります。`down -v` は実行しないでください。既存のLog View管理者パスワードと署名鍵を新しい `docker/nw_servers/.env` に引き継いでから、上記初回準備と起動を行います。

初期ドメインは `asakusa.lab`（`dc=asakusa,dc=lab`）です。既存の `ldap-shared/settings.json` があればそのドメイン設定を引き継ぎます。ドメイン作成・切り替え・削除はLdapMgtToozの「ドメイン」画面で操作します。一度に稼働するドメインは1つです。削除は台帳からの除外で、MDBと設定は退避扱いとして保持します。

## 注意事項

- LdapMgtTooz、Log View、暗黙TLSプロキシのPythonイメージはPython 3.15+です。Pi 4ではARM64イメージをビルドします。
- SyslogはRFC3164/RFC5424/FortiGate形式を個別に解析し、判定できない形式は本文をメッセージにします。パース済み項目と受信元データの両方を保持します。
- SNMP TrapはOID、型付き値、送信元、raw BERを保存します。MIB管理画面からインポート・削除でき、MIBがない場合は数値OIDを表示します。raw BERにはSNMP communityが含まれる可能性があります。
- Mailpit APIとWeb UIはDockerネットワーク内に限定し、Log ViewがAPIを2秒周期で同期します。
- UDP syslog/Trapに到達保証はありません。機器側宛先、Firewall、Piの時刻同期も確認してください。
- Web UIはHTTPのみです。HTTPS証明書は設定していません。認証情報を送るため、信頼できる検証LAN内でのみ使用してください。
- Mailpitの自己署名証明書を使うSMTPクライアントでは、クライアント側で証明書を信頼させる必要があります。

## 主なディレクトリ

- `compose.yaml`: 統合Compose
- `Caddyfile`, `portal/`: 公開入口とポータル
- `ldapmgttooz/`, `openldap/`, `freeradius/`: LDAP/RADIUSツール群
- `../log_view/`: Syslog/SNMP/Mailpit統合ビュー
- `mailpit-certs/`, `mailpit-smtps/`: Mailpit証明書初期化と暗黙TLS入口



