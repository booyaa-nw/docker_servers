# NW Log View

Python 3.15+ / Docker Composeで動かすsyslog・SNMP Trap・Mailpitメールの統合ビューです。初期実装は `nw_servers` から独立しています。

## 起動

ホストまたはRaspberry Pi 4上で、このディレクトリを作業ディレクトリにします。

```sh
cp .env.example .env
```

`.env` の `LOG_VIEW_ADMIN_PASSWORD` と `LOG_VIEW_SECRET_KEY` を変更してください。秘密鍵は例えば `openssl rand -hex 32` で生成します。データディレクトリをコンテナのUID 10001から書き込めるように準備し、起動します。

```sh
mkdir -p data mibs
sudo chown 10001:10001 data
docker compose up -d --build
docker compose logs -f log-view
```

UI: `http://<PiのIP>:8088/`

Composeには独立したMailpitサービスも含めています。SMTPはホストの1025/tcpへ公開し、MailpitのWeb UIはPi上のlocalhost 8025/tcpだけにbindします（一覧確認にはlog-viewを利用）。既存Mailpitがある環境では `docker compose up log-view` のみで起動し、両Composeを共通Dockerネットワークへ接続して `MAILPIT_API_URL` を合わせます。MailpitにBasic Authを設定した場合は読み取り用のユーザー名・パスワードも設定します。

## 受信ポート

| ホスト | プロトコル | 用途 |
|---|---|---|
| 514 | UDP/TCP | syslog |
| 162 | UDP/TCP | SNMP Trap |
| 1025 | TCP | Mailpit SMTP |
| 127.0.0.1:8025 | TCP | Mailpit Web UI（localhost限定） |
| 8088 | TCP | Web UI / API |

UDP Trapを標準経路として使います。TCP 162も開き、RFC 3430 BER framingを受け付けます。SNMPv1/v2cはOIDとvarbindを解析し、raw BERも保存します。SNMPv3は初期実装ではraw BERを保存し、認証・復号を伴うPDU解析は行いません。Informへの応答も行いません。コミュニティ値はDBのparsed fieldsには含めませんが、raw BERには含まれる可能性があるため、DBとraw exportをアクセス制御してください。

MIBファイルを `mibs/` に配置すると、PySNMP/PySMIがローカルソースからコンパイルし、イベント詳細表示時に数値OIDの解決を試みます。MIBがない・依存MIBが不足・コンパイル失敗の場合は数値OIDを表示します。コンパイルは初回のOID解決時に発生し、compiled filesは `data/mibs-compiled/` に保存されます。

## 取込仕様

- syslog: UDP/TCP 514。RFC5424、RFC3164、FortiGate key=value、その他は本文として扱います。受信バイト列をrawとして保存します。
- SNMP Trap: UDP 162。送信元IP、受信時刻、OID、型付き値、raw BERを保存します。
- Mailpit: APIを2秒間隔でpollし、IDで重複取り込みを防ぎます。メール一覧は最新10件で、クリックするとテキスト本文を表示します。HTML本文はタグとscript/styleを除去します。
- 期間はUTC保存・受信日時ベースで検索し、画面では端末のローカル時刻を表示します。
- 未登録送信元も検索・一覧に残ります。ノードには複数IPとグループを割り当てられます。

## Export・保存・復元

Export APIはJSONまたはCSVを出力します。画面のボタンはJSONを出力し、期間・キーワード・種類・ノード/グループの条件、件数、生成時刻をmanifestに含みます。元データを含むため、SNMP raw BERにコミュニティが入る可能性を考慮してください。SHA-256はレスポンスヘッダー `X-Content-SHA256` に返し、画面でも表示します。

保守画面からSQLite Online Backupをダウンロードできます。復元前に現行DBの安全コピーを `data/pre-restore-*.sqlite3` として作成します。ログ削除は既定保持30日を対象に件数を確認してから実行します。Compose停止中に `data/` を別媒体へコピーする方法も利用できます。

## 制限

- 初回版は管理者1アカウントです。`LOG_VIEW_ADMIN_PASSWORD` は起動時にscryptでハッシュ化してメモリ上で比較します。Cookie署名鍵は `LOG_VIEW_SECRET_KEY` で設定します。
- LAN内の管理端末で使い、インターネットへ直接公開しないでください。TLSが必要な場合はHTTPS reverse proxyを前段に置いてください。
- UDP syslog/Trapには到達保証がありません。受信漏れの調査では、機器側の宛先、VLAN/Firewall、Piの時刻同期を確認します。
- Pi 4のARM64 build、実機受信、Mailpit接続、負荷・ディスク使用量は対象Pi上で確認してください。

## 構成

- `compose.yaml`: 独立Compose
- `app/`: Python API、受信、パーサー、DB、Mailpit連携
- `static/`: 3ペイン画面
- `DESIGN.md`: 実装設計と残る検証事項
