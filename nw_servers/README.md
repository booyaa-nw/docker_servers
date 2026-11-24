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
| ftp | ユーザー認証FTP（読み取り専用） | 21/tcp, 21100-21110/tcp |
| tftp | TFTP（PUT対応） | 69/udp |

## 初回セットアップと更新

Rootless Dockerと通常のDocker daemonのどちらでも、セットアップスクリプトがモードを検出し、`.env`の`BIND_UID/GID`と全bind mount元の所有者を調整します。スクリプトはComposeを起動する前にディレクトリとファイルを作り、Dockerの暗黙のroot所有ディレクトリ作成を防ぎます。`sudo`は使わず、ローカルユーザーで実行してください。

Rootless Dockerでは80、21、25、69番のような1024未満のホストポート公開に、ホスト側の許可設定が必要です。Docker公式手順に従って`net.ipv4.ip_unprivileged_port_start=0`を設定してください。RootlessKitに`CAP_NET_BIND_SERVICE`を付与して対応する場合は、セットアップ時に`ROOTLESS_PRIVILEGED_PORTS=1`を環境変数として渡します。スクリプトはsysctl値を確認し、未設定ならポート公開が失敗する前に案内して停止します。

```sh
cd ~/.local/docker/nw_servers
bash scripts/setup.sh
```

初回は`.env.example`から`.env`を作ります。表示された案内に従い、例のパスワードを強い値に置き換え、`NETRIG_SECRET`を`openssl rand -hex 32`の出力にして、`FTP_PASSWORD`にも`openssl rand -hex 24`などで生成したパスワードを設定してから、もう一度`bash scripts/setup.sh`を実行してください。`PI_IP`はPiのLANアドレスに設定します。`FTP_PASV_ADDRESS`は空欄なら`PI_IP`を使います。別のネットワークから接続させる場合だけ、FTPクライアントに通知する外部アドレスを設定してください。FTPログインには`.env`の`FTP_USERNAME`と`FTP_PASSWORD`を使います。Mailpit TLS証明書とPKIの初期パスワードもセットアップ時に生成されます。

更新時は次を実行します。スクリプトはFast-forward可能な場合にGitを更新し、bind mount所有者を整えてからサービスとPKI CLIイメージを再ビルドします。

```sh
bash scripts/update.sh
```

Web UIは検証LAN向けにHTTPで動作します。ログイン情報を送信するため、信頼できるLAN内で利用してください。ブラウザーで `http://<PiのIP>/` を開いてNetRigにサインインし、ポータルからLDAP管理またはログビューを選択します。MailpitのSMTPクライアントはSTARTTLSで25、または暗黙TLSで465を指定します。

## FTP / TFTP

両サービスは同じ`./public/`を読み書きします。FTPはユーザー名・パスワード認証で21/tcpとパッシブポート21100-21110/tcp、TFTPは認証なしで69/udpを使い、どちらもPUTでアップロードできます。アップロードされたファイルはホスト側のローカルユーザー所有に揃えます。TFTPは認証なしで書き込めるため信頼できるLANに限定し、FTPの認証情報と通信内容も暗号化されないため信頼できるLAN内で利用してください。

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
ftp/{Dockerfile,vsftpd.conf,pam-vsftpd,entrypoint.sh}
tftp/{Dockerfile,entrypoint.sh}
public/
scripts/{setup,update,prepare-bind-mounts}.sh
shared/ldap/
```

Composeファイルと各コンテナのDockerfileは、それぞれの実行環境に近い場所にあります。ログビューはイメージビルド時にDebianの`snmp-mibs-downloader`で標準MIBを取得し、初回起動時に不足分を `plugins/log_view/config/mibs/` へコピーします。手動MIBも同じ場所に保存されます。SQLite DBとコンパイル済みMIBは `plugins/log_view/data/` に保存されます。

FTP/TFTPで共有するファイルは`public/`へ配置します。公開・書き込みさせたくないファイルは置かないでください。セットアップ・更新スクリプトは`public/`も含めbind mount元を実行ユーザー所有に整えます。

## バックアップ

- LDAP: `openldap/data/` と `shared/ldap/` をセットで保存します。共有設定にはLDAP管理者情報が含まれます。
- RADIUS: `shared/ldap/freeradius/` に動的VLANポリシーが保存されます。
- ログ: `plugins/log_view/data/` と `plugins/log_view/config/mibs/` を保存します。
- Mailpit: `mailpit/data/` を保存します。
- SMTP証明書: `mailpit/secrets/` を安全に保管します。秘密鍵はGitに含めないでください。
- FTP/TFTP公開ファイル: `public/` をバックアップします。
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
