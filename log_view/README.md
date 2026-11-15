# NW Log View

Python 3.15+ / Docker Composeで動くSyslog・SNMP Trap・Mailpitメールの統合ビューです。現在は `docker/nw_servers/compose.yaml` に統合されています。nw_serversのポータル（Caddy）から開いてください。

## 統合環境での起動

初回は [nw_serversのREADME](../nw_servers/README.md) に従い、統合Composeの `.env`、TLS証明書、データディレクトリを準備してください。その後、`docker/nw_servers/` で実行します。

```sh
docker compose up -d --build log-view
docker compose logs -f log-view
```

Web UIは `http://<PiのIP>/log-view/` です。受信ポートはホストの514/tcp+udp（Syslog）と162/tcp+udp（SNMP Trap）です。Mailpit APIは内部Composeネットワークで利用します。

## 機能

- syslog: RFC5424、RFC3164、FortiGate key=valueを解析し、判定できない形式は元本文をメッセージとして保存します。元データも保持します。
- SNMP Trap: UDP 162とRFC3430形式のTCP 162を受け、OID、型付き値、raw BERを保存します。SNMPv3の認証・復号やInform応答は行いません。
- Mailpit: APIを既定2秒間隔で同期し、IDで重複を防ぎます。HTMLメールはタグを除去したテキストを表示し、一覧行をクリックすると詳細を開きます。
- 期間・キーワード検索、複数ノード/グループ選択、未登録送信元の登録、JSON/CSV証跡Exportを提供します。Exportには条件、出力日時、件数、SHA-256を含められます。
- MIB管理画面でMIBをインポート・削除できます。読み込みに失敗するMIBは登録を戻します。MIBがない場合は数値OIDで表示します。
- 保守画面でSQLiteバックアップ/復元と、保持期間に基づいたログ削除ができます。

データは `data/`（SQLiteとコンパイル済みMIB）、MIBソースは `mibs/` に保存されます。統合Composeではいずれもnw_serversプロジェクト外のホストディレクトリとして永続化されます。raw BERにcommunity値が含まれる可能性があるため、DBとraw付きExportを適切に保護してください。

## 開発用の単独Compose

`docker/log_view/compose.yaml` は従来の単独開発用です。nw_servers統合環境と同時に起動すると同じコンテナ名・ホスト受信ポートが競合するため、どちらか片方だけ起動してください。統合運用にはnw_servers側Composeを使ってください。
