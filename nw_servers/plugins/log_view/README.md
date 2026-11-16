# NetRig ログビュープラグイン

Syslog、SNMP Trap、Mailpitメールを集約して検索・確認するNetRigプラグインです。単独Composeや個別ログインはなく、`docker/nw_servers/compose.yaml` から起動し、NetRig共通ログインを使います。

## 保存先

- `config/mibs/`: インポートしたMIBソース
- `data/logs.sqlite3`: 受信イベント、ノード、グループ
- `data/mibs-compiled/`: コンパイル済みMIBキャッシュ

イベントDBのバックアップ・復元、保持期間に基づく削除は保守画面から行えます。raw BERやExportにはSNMP community等が含まれる場合があるため、保管先を適切に保護してください。

## 機能

- RFC5424、RFC3164、FortiGate形式のSyslog解析
- UDP SyslogとSNMP Trapの受信、OIDと型付き値の表示
- Mailpit APIからのメール同期
- 期間・キーワード・ノード・グループ検索、JSON/CSV Export
- ノード・グループとMIBの管理
- SQLiteバックアップ・復元、ログ保持期間管理

ホスト受信ポートは514/tcp+udp（Syslog）と162/tcp+udp（SNMP Trap）です。Mailpit APIとWeb UIはDocker内部だけで利用します。
