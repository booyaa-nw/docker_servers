# NetRig ログビュープラグイン

Syslog、SNMP Trap、Mailpitメールを集約して検索・確認するNetRigプラグインです。単独Composeや個別ログインはなく、`docker/nw_servers/compose.yaml` から起動し、NetRig共通ログインを使います。

## 保存先

- `config/mibs/`: インポートしたMIBソース
- `data/logs.sqlite3`: 受信イベント、ノード、グループ
- `data/mibs-compiled/`: コンパイル済みMIBキャッシュ

Composeのホスト側保存先は`.env`の`LOG_VIEW_DATA_PATH`と`LOG_VIEW_MIB_PATH`で指定します。相対パスはComposeプロジェクトディレクトリを基準に解決されます。

イメージのビルド時にDebianの`snmp-mibs-downloader`でIETF RFCとIANAの標準MIBを取得します。コンテナ起動時に`config/mibs/`へ不足分だけコピーするため、手動で追加したMIBは上書きしません。標準MIBを更新する場合はログビューイメージを再ビルドしてください。Debianパッケージは`non-free`リポジトリのものです。

イベントDBのバックアップ・復元、保持期間に基づく削除は保守画面から行えます。raw BERやExportにはSNMP community等が含まれる場合があるため、保管先を適切に保護してください。

## 機能

- RFC5424、RFC3164、FortiGate形式のSyslog解析
- UDP SyslogとSNMP Trapの受信、OIDと型付き値の表示
- Mailpit APIからのメール同期
- 期間・キーワード・ノード・グループ検索、JSON/CSV Export
- ノード・グループとMIBの管理
- SQLiteバックアップ・復元、ログ保持期間管理

ホスト受信ポートは514/tcp+udp（Syslog）と162/tcp+udp（SNMP Trap）です。Mailpit APIとWeb UIはDocker内部だけで利用します。

初回起動時にMIBファイルのコピーができない場合もWebアプリは起動し、コンテナログにUIDと必要な権限修正を表示します。.envのLOG_VIEW_UID / LOG_VIEW_GIDをホスト側MIBディレクトリの所有者に合わせてください。

FortiGate MIBのインポートでは、同梱のconfig/mibs-bundled/FORTINET-CORE-MIB.mibを依存MIBとして自動配置します。コンパイル時の失敗原因はMIB管理画面に返されます。
