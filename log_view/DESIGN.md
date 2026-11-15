# NW機器ログ・Trap・メール確認ツール 設計書

状態: 実装着手前の確定案  
対象: Raspberry Pi 4 / Docker Compose / `docker/log_view/`  
作成日: 2026-11-14

## 1. 目的と前提

syslog、SNMP Trap、Mailpitで受信したメールを単一画面でリアルタイムに確認し、期間・キーワード・ノードで検索、試験証跡としてエクスポートする。最初は `nw_servers` から独立して起動し、Pi 4で運用確認後にComposeへ統合する。

決定事項:

- パーサーが解釈した値だけでなく、受信時の生データを保持する。
- 未知形式はエラー扱いにせず、本文全体をメッセージとして記録する。
- メール一覧は1件1行。行を選択すると詳細ポップアップを開く。HTML本文はタグを除去したテキストで表示する。
- 機器は複数選択でき、任意のグループに分類できる。機器未登録の送信元も一覧と検索対象に残す。
- エクスポートには期間・適用フィルター・作成時刻を記録し、元データを含める。
- ローカル保存データには保存期間、バックアップ、復元、削除の運用手段を設ける。
- SNMP OIDと型付き値を常に保存し、MIBが使えるときに限り表示名を補足する。

## 2. アーキテクチャ

### 初期構成

`log_view` は当初、次の単一アプリコンテナとMailpit接続で構成する。受信、パース、保存、API、静的Web UIを一つのPythonサービスにまとめ、Pi上での常駐サービスとメモリ使用量を抑える。Logstash、Elasticsearch、独立解析ノードは初期構成に含めない。

| 要素 | 役割 |
|---|---|
| `log-view` | syslog/Trap受信、形式判定、Mailpit同期、SQLite保存、検索API、Web UI、Export、バックアップ操作 |
| SQLite | イベント・機器・グループ・設定の永続化。WALモード、トランザクション使用 |
| Mailpit | メール受信と原本のSMTP試験用保管。`log-view` はMailpit APIから取得し、ローカルDBにもスナップショットを保存 |
| MIBディレクトリ | 利用者が配置するMIBファイル。読取専用マウントし、表示時のOID解決に使用 |

```text
NW機器 -- syslog UDP/TCP 514 --> log-view --+
NW機器 -- SNMP Trap UDP 162 -------> log-view --+--> SQLite --> Web UI / API / Export
Mailpit -- HTTP API ----------------> log-view --+
```

Web UIは同一オリジンのAPIを利用する。ライブ表示は初期実装では1秒間隔のポーリングとし、最後に取得したイベントID以降を取得する。ブラウザ側での重複描画を避け、接続中の新着と検索結果のページングを分ける。

### 初期技術選定

- Python 3.15+、FastAPI、Uvicorn。Dockerは公式 `python:3.15-slim` 系を使い、ARM64対応をPi 4で確認する。
- SQLite（Python標準 `sqlite3`）。別DBコンテナは持たない。
- syslogはホストUDP/TCP 514からコンテナUDP/TCP 1514へ転送して受信する。コンテナは非rootのまま動かす。TCPはRFC 6587のoctet-countingおよび改行区切りを扱う。解析器はRFC 3164、RFC 5424、FortiGate形式、fallbackの順で実装する。
- SNMPはUDP/TCP受信バイト列を保持してから解析する。初期実装はSNMPv1/v2c Trapを構造解析し、TCPはBER長さでメッセージ境界を検出する。SNMPv3はraw BERのみ保存する。SNMPv3認証・復号、Inform応答は初期範囲外。
- MIB解決はPySNMP MIB compiler/readerとし、MIBが未導入・不完全でも数値OIDと値を表示する。MIB変換結果は生OIDを置換せず、表示用の補足情報として扱う。
- HTMLメール本文はサーバ側でHTMLパーサーを使ってタグ・スクリプト・スタイルを除去する。HTMLをブラウザで実行しない。

採用する受信／パーサー依存の具体的バージョン、PySNMPでの生BER保持方法、FortiGateログの実機サンプル対応は実装着手時に固定・確認する。

## 3. ネットワークとCompose

初期Composeは `docker/log_view/compose.yaml`。データは `./data/`、MIBは `./mibs/` をホストへ永続化する。コンテナは非rootで動作させ、SQLiteディレクトリのみ書込可能とする。

| ホスト | コンテナ | 用途 | 初期状態 |
|---|---:|---|---|
| 8088/tcp | 8080/tcp | Web UI/API | 公開。LAN内の管理端末から利用 |
| 514/udp | 1514/udp | Syslog | 公開 |
| 514/tcp | 1514/tcp | Syslog | 公開 |
| 162/udp | 1162/udp | SNMP Trap | 公開。コンテナは高位ポートを使用 |
| 162/tcp | 1162/tcp | SNMP over TCP | 公開。RFC 3430 BER framing受信 |
| 1025/tcp | 1025/tcp | Mailpit SMTP | 公開。試験メール受信用 |
| 127.0.0.1:8025/tcp | 8025/tcp | Mailpit Web UI/API | ホストlocalhost限定。 log-viewからは内部ネットワークで接続 |

SNMP over TCPはオプションのtransport mappingで、UDPを標準経路とする。TCP受信口は要件に合わせて初期Composeから公開するが、機器側でSNMP over TCP送信に設定しなければ利用されない。ホストの162番はDockerが公開し、コンテナには特権ポートをbindさせない。

ファイアウォールは、管理LANから必要な宛先ポートだけ許可する。SNMP Trapは送信元機器のIPに絞る。Dockerのポート公開がホストのファイアウォール規則とどう相互作用するかはPi上で確認する。ホストネットワークモードは使わない。

Mailpitは同一Composeネットワークにある場合はサービス名とコンテナポートで参照し、ホストへMailpit管理APIを追加公開しない。`log-view` が別ネットワークの場合は管理API接続先を設定値にする。API資格情報は`.env`等で渡し、Gitへ登録しない。

## 4. 保存モデル

時刻はUTC ISO-8601で保存し、UIでAsia/Tokyoに変換する。イベントの種類で共通イベント表に格納し、必要な型固有フィールドはJSONで保持する。

### `events`

| カラム | 説明 |
|---|---|
| `id` | 単調増加整数。ライブ取得カーソルと主キー |
| `kind` | `syslog` / `snmp` / `mail` |
| `received_at` | 受信サービスが受けたUTC時刻（検索・並び替えの基本時刻） |
| `event_at` | メッセージに含まれる発生時刻。解釈不能ならNULL |
| `source_ip` | ネットワーク送信元IP。MailpitメールはNULL |
| `node_id` | 登録済みノードとの対応。未登録ならNULL |
| `summary` | 一覧表示用の要約 |
| `message` | 検索対象の本文テキスト。mailはHTMLを除去したテキスト本文 |
| `parsed` | severity、facility、hostname、app、FortiGate KV、SNMP varbind等の正規化JSON |
| `raw_bytes` | 受信syslog/Trapのオリジナルpayload。BLOB |
| `raw_text` | Mailpitから取得した原本フィールドと本文の正規化JSON。メール原文全体の完全なRFC 5322 byte保持は初期対象外 |
| `source_key` | Mailpit message ID等、再取り込み防止に使う一意キー |

検索用に `received_at`、`event_at`、`kind`、`source_ip`、`node_id`、`source_key` のインデックスを作る。キーワード検索はSQLite FTS5を使い、FTS5がないSQLiteビルドではLIKEによる部分一致へフォールバックする。

### `nodes` / `groups` / 関連

- `nodes`: `id`, `name`, `primary_ip`, `description`, `enabled`, `created_at`, `updated_at`
- `node_addresses`: `node_id`, `address`。IPアドレスを一意にし複数アドレスを許可
- `groups`: `id`, `name`, `description`
- `node_groups`: `node_id`, `group_id`
- 受信元IPで自動対応付けし、ノード名は表示上の別名とする。hostnameだけで自動結合しない。
- 未登録IPは `未登録ノード` として残し、画面からノード登録へ進める。
- メールはノードに紐付けない。From/Toはメール固有データで表示する。

## 5. 取込とパース仕様

### Syslog

1. 受信時に送信元IP、受信時刻、UDP/TCP、raw bytesを確定する。
2. RFC 5424、RFC 3164、FortiGate形式の順に形式判定・解析を試みる。
3. FortiGateは確認済みの代表的なkey=value形式を抽出し、未知キーも `parsed.vendor_fields` に残す。FortiGate版・ログタイプの揺れを前提にし、分解に失敗した場合もfallbackメッセージを保存する。
4. すべて失敗した場合は`format=unknown`、`message=受信本文`とする。取り込み自体を失敗扱いにしない。
5. RFC3164には年・タイムゾーンがないため、表示上の推定時刻と元の時刻文字列を分ける。検索・既定ソートには受信時刻を使う。

RFC5424は構造化データ・タイムスタンプ等を個別に保持する。抽出できなかったフィールドがあってもraw bytesは保持する。

### SNMP Trap

- 保存する: 受信時刻、送信元IP/port、SNMP version、PDU種別、request-id、enterprise/sysUpTime/trap OID、各varbindの数値OID・ASN.1型・安全な値表現、raw BER payload。
- UIはMIB名が解決できれば `IF-MIB::linkDown` と `1.3...` を併記する。解決できなければ数値OIDを表示する。
- 既定でSNMPコミュニティを一覧・Exportへ出さない。raw BERにはv1/v2c communityが含まれ得るため、raw exportはオプション扱いにし、画面上で明示する。生データ原本は制限されたローカルDBに保持する。
- 最大データグラムサイズを設定可能にし、超過・不正BERは受信元とraw payload（サイズ上限内）をエラーイベントとして残す。

### Mailpit

- REST APIを2秒間隔でpollする。WebSocketは非公式APIのため初期同期手段にしない。
- Mailpitの一覧APIで新着IDを取り、詳細APIで本文を取得する。Mailpit APIのエンドポイント・ページング仕様に合わせて実装時に固定する。
- `source_key = mailpit:<message-id>` の一意制約で重複を防ぐ。同期カーソルも設定表に永続化し、再起動後に再開する。
- メールの日時、From、To、CC（詳細のみ）、Subject、テキスト本文、HTMLから抽出したテキスト、Mailpit IDを保存する。本文一覧は短縮せず1行内に折りたたみ／省略表示し、クリックで全文を表示する。
- Mailpit側で消えたメールも既に同期済みのDBからは削除しない。ローカル削除はMailpit APIへ伝播させない。
- Mailpit停止中は最後の同期済みデータを表示し、接続状態と最終成功時刻を明示する。

## 6. Web画面

### メイン画面

```text
+----------------------------------------------+
| 期間 From/To | キーワード | 種別 | ノード/グループ | Export |
+------------------------+---------------------+
| Syslog (上左)          | SNMP Trap (上右)    |
| 最新順・スクロール     | 最新順・スクロール  |
+----------------------------------------------+
| Mail (下段、1行1件、初期10件)                 |
+----------------------------------------------+
| 取込状態 / Mailpit最終同期 / 保存DB使用量       |
+----------------------------------------------+
```

- 初期表示は直近1時間、最新順。syslogとSNMPは独立スクロール、メールは直近10件。
- 画面上部の期間・キーワード・種別・ノード・グループ条件は適用先を明確にし、必要なら「全ペインへ適用」を用意する。
- 複数ノードと複数グループを選択可能。未登録ノードも選択肢に出す。
- 各ペインに件数、ロード中、検索条件、クリア操作を持たせる。
- 一覧は受信時刻を表示。イベント時刻が異なる場合は両方見える詳細画面にする。
- syslog/Trap行クリックでイベント詳細ポップアップを開き、解析フィールドとraw dataを確認できる。
- mail行クリックで通常のメール閲覧に近い詳細ポップアップを開き、From/To/日時/Subject/全文テキストを表示する。
- 画面から取得できるイベントはページングし、ブラウザへ全期間データを一括送信しない。

### ノード・グループ管理

専用設定画面でノード、複数IP、説明、グループの作成・編集・削除を行う。削除済みノードに関連する過去イベントは残し、node_idをNULL化して送信元IP表示を保つ。

## 7. API契約（初期）

| Method / path | 内容 |
|---|---|
| `GET /api/events` | `from`, `to`, `q`, `kind`, `node_ids`, `group_ids`, `source_ip`, `after_id`, `limit`, `cursor`検索 |
| `GET /api/events/{id}` | 解析済みデータ・raw dataを含む詳細 |
| `GET /api/nodes` | ノードとグループ、未登録IP候補 |
| `POST/PATCH/DELETE /api/nodes` | 機器管理 |
| `GET/POST/PATCH/DELETE /api/groups` | グループ管理 |
| `GET /api/status` | 受信稼働状態、Mailpit最終同期、DBサイズ、最終イベント時刻 |
| `GET /api/export` | 検索条件と形式（CSV/JSON）を指定し、条件記録付きエクスポートを返す |
| `POST /api/admin/backup` | SQLite整合性を保ったバックアップを生成 |
| `POST /api/admin/restore` | 検証済みバックアップを復元。サービス停止を含む管理操作 |
| `POST /api/admin/retention` | 期間・種類を指定した削除を実行 |

APIは日付境界をUTCで受け付け、`from`含む/`to`含まないの半開区間を採用する。検索条件、形式、出力時刻、件数、ツールのバージョンをエクスポートのmanifestに含める。

## 8. エクスポート・保守

- CSVとJSON Linesを提供する。JSONはmanifestとevents配列を持つ単一JSONファイルも選べる。
- manifest: `generated_at`, `tool_version`, `from`, `to`, `query`, `kinds`, `node_ids`, `group_ids`, `record_count`, `include_raw`。
- raw dataを含める場合はCSVでbase64列、JSONでbase64文字列として出力し、メールは受信したMailpitフィールドのJSONを含める。
- UIのExport既定値はrawを含む。SNMP rawはcommunityを含む可能性がある旨を確認画面に表示する。証跡の改変検知用にファイルSHA-256を同時表示／manifest記録する。
- 既定保持期間: 30日。設定で変更可能。保存容量上限も設定し、上限到達時は自動で古いイベントを削除するのでなく、状態警告を出す。保持期間削除は日次実行し、UIからdry-run件数を確認後に実行する。
- DB削除はイベント種類・期間で行う。バックアップ・復元前に明示的な確認と、復元前DBの退避を行う。
- SQLite Online Backup APIで一貫性のあるバックアップを作成。復元はCompose停止後に行い、起動してDB検査と件数確認を行う手順をREADMEに記載する。
- データ保存先をバックアップ対象として明示し、MIB/設定ファイルも必要に応じて別途保存する。

## 9. セキュリティ・制限

- 初回版から簡易ログインを実装する。`LOG_VIEW_ADMIN_PASSWORD` を環境設定し、アプリ起動時にパスワードハッシュ化して検証する。ログイン後は署名済みHttpOnly/SameSite=Lax Cookieを使い、更新APIはCSRFトークンを要求する。パスワード平文をDB・ログへ出さない。初期の独立検証中も管理LAN限定で公開し、外部公開しない。Compose統合時にLDAPログインへ差し替えるかは別途判断する。
- Mailpitの読み取りAPI資格情報をブラウザに渡さない。
- 原文には機器名、メールアドレス、本文、SNMP community等が含まれ得る。データディレクトリを限定し、UI/APIアクセスを認証で保護する。
- syslog/UDP Trapは到達保証がない。ログが来ない場合の送信元設定、経路、Firewall、時刻同期の確認を運用手順に含める。
- syslog送信元IPは詐称可能な場合がある。IPはノード対応付けの手掛かりであり、真正性の保証ではない。
- TLS syslogは初期対象外。必要な機器があれば別途証明書・相互認証を設計する。

## 10. 段階と実装着手条件

1. **土台**: Compose、DB schema、syslog UDP/TCP受信、生データ保持、RFC3164/5424/fallback、イベントAPI。
2. **Trap**: UDP 162→1162転送、SNMPデコード、生BER保持、数値OID表示、MIB差し替え。
3. **メール**: Mailpit REST API poll、重複防止、本文抽出、詳細ポップアップ。
4. **UI**: 3ペイン、ライブ更新、期間/キーワード/ノード/グループ検索、設定UI。
5. **証跡・運用**: CSV/JSON Export、manifest、ハッシュ、保持/削除、バックアップ/復元。
6. **Pi検証・統合**: ARM64 build、実機送受信、再起動/復元、ディスク・メモリ計測後、`nw_servers` Composeへ統合。

実装開始時の確認事項（コード着手と並行してサンプルで確定し、全体設計を止めない）:

- FortiGateの実ログサンプルを匿名化して取得し、主要ログ型とkey=valueの引用・エスケープを確認。
- 対象機器のSNMP Trapバージョン、送信元ポート、SNMPv3有無、TCP Trap要否を確認。
- Mailpit稼働サービス名/API認証/実API versionを決める。
- Pi 4のRAM容量、OS/CPUアーキテクチャ、空きディスクを確認し、初期DB上限・ポーリング負荷を調整。
- ローカル検証とPi実機検証を分け、ARM64を最終合格条件とする。

## 参考資料

- [Docker Compose ports](https://docs.docker.com/reference/compose-file/services/)
- [Mailpit API v1](https://mailpit.axllent.org/docs/api-v1/)
- [RFC 3164](https://www.rfc-editor.org/rfc/rfc3164)
- [RFC 5424](https://www.rfc-editor.org/rfc/rfc5424)
- [RFC 6587: Transmission of Syslog Messages over TCP](https://www.rfc-editor.org/rfc/rfc6587)
- [RFC 3430: SNMP over TCP](https://www.rfc-editor.org/rfc/rfc3430)
- [PySNMP documentation](https://docs.lextudio.com/pysnmp/v7.1/docs/)
