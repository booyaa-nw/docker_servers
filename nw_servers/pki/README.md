# CLI PKI (CFSSL)

常駐CA・HTTP APIは起動しない。証明書操作時だけComposeのrunで実行する。
Root直下にServer CA、Client CA、FortiGateごとのInspection CAを作成する。
通常のSSLオフロードにはServer CAのサーバ証明書を使う。

## 初回準備（Piのnw_servers）

```bash
bash scripts/prepare-bind-mounts.sh
docker compose -f compose.pki.yaml build pki
docker compose -f compose.pki.yaml run --rm pki self-test
```

通常の初期セットアップでは`bash scripts/setup.sh`が保存先と`root.pass`を初期化します。既存の`root.pass`は上書きしません。
DockerfileはCFSSL v1.7.0のソースをGoでビルドする。初回ビルドにはインターネット接続が必要。
ビルド後のCLIコンテナはnetwork_mode:noneで動き、外部接続・ポート公開はしない。
通常コマンドはホストのローカルユーザー所有でファイルを作成する。

self-testは一時環境で実CFSSLによる発行、CA制約、EKU分離、LDAP配布、PKCS#12、CRLを検証する。
Root秘密鍵を含む本番のlabデータには触れない。CFSSL不在のホストでは実発行試験をskipする。

## CA初期化

### self-testが/dataへ書き込めない場合

旧テストは作業ディレクトリ/dataへ一時ファイルを作成していた。
修正版はPKI_TEST_TMP=/tmpを使用し、永続データの書き込み権限に依存しない。
既存イメージでも以下で実行できる。

```bash
docker compose -f compose.pki.yaml run --rm --workdir /tmp pki self-test
```

通常のCA発行は引き続き/dataや/offlineへの書き込み権限が必要。
ホスト側の所有UID/GIDと、コンテナのBIND_UID/BIND_GIDを確認する。
rootやchmod 777で権限問題を回避しない。

```bash
id
stat -c '%u:%g %a %n' pki/data pki/offline pki/import pki/secrets
```

### CLI読み込み時にPermission deniedになる場合

`python3: can't open file '/app/pki.py': [Errno 13] Permission denied`は
ホストからCOPYしたスクリプトの読み取り権限が不足した旧イメージで発生する。
Dockerfileで/appとtestsのディレクトリを755、Pythonファイルを644へ固定し、
ビルド時にも一般ユーザでCLIを読み込むチェックを追加した。
修正版をPiへ反映後、次を実行する（既存CAデータは変更しない）。

```bash
docker compose -f compose.pki.yaml build pki
docker compose -f compose.pki.yaml run --rm pki self-test
```

`pure-ftpd`のorphan警告はこの権限エラーの原因ではない。
対処のためにそのコンテナを削除したり、PKIをrootユーザで起動したりする必要はない。

環境名は--envで指定する（省略時lab）。別の検証環境には別名を使う。
Root 10年、Server/Client/Inspection CA 5年、末端1年が既定。
既存の証明書・鍵は上書きしない。

```bash
docker compose -f compose.pki.yaml run --rm pki-root --env lab init-root --cn 'Lab Root CA'
docker compose -f compose.pki.yaml run --rm pki-root --env lab init-ca server --cn 'Lab Server CA'
docker compose -f compose.pki.yaml run --rm pki-root --env lab init-ca client --cn 'Lab Client CA'
docker compose -f compose.pki.yaml run --rm pki-root --env lab init-ca inspection --name fw01 --cn 'FW01 Inspection CA'
```

3種類のSub CAはCA:TRUE/pathLen:0、RootはpathLen:1。
ServerはserverAuth、ClientはclientAuthに用途を分ける。Sub CAは末端署名用で、さらにSub CAを発行しない。
Inspection CAの証明書と鍵をFortiGateへインポートし、動的な再署名はFortiGate側で行う。
Inspection CAではこのCLIから通常のサーバ証明書を発行する操作を提供しない。

FortiGateで秘密鍵を生成した場合は、CSRだけをpki/import/fw01.csrへ置き、代わりに以下を実行する。
同じ--nameが既存なら別名を使う。CSR署名では秘密鍵を保存しない。

```bash
docker compose -f compose.pki.yaml run --rm pki-root --env lab init-ca inspection --name fw01 --cn 'FW01 Inspection CA' --csr /input/fw01.csr
```

通常のpki/pki-deployサービスにはRoot秘密鍵とパスワードをマウントしない。
Root鍵はpki/offline/<環境>/root.keyに暗号化して保存する。
署名時だけ/tmp（tmpfs）へ復号し、処理終了時に除去する。
Root処理後はofflineディレクトリとroot.passを別媒体へ保管できる。
同じPi上へ置いたままでは完全なオフライン保管にはならない。
通常発行用CAと末端証明書の秘密鍵は保護ディレクトリ内の平文PEMであり、ホスト側で保護する。

## サーバ・クライアント証明書

サーバにはSANが必須。接続時に使うDNS名/IPを個別の--sanで指定する。
OpenLDAPにはDocker内のopenldapとPiのIPを必ず含める。

```bash
docker compose -f compose.pki.yaml run --rm pki --env lab issue server openldap --cn ldap.lab.example.test --san openldap --san ldap.lab.example.test --san 172.16.201.173
docker compose -f compose.pki.yaml run --rm pki --env lab issue server radius-eap --cn radius.lab.example.test --san radius.lab.example.test
docker compose -f compose.pki.yaml run --rm pki --env lab issue server lam --cn lam.lab.example.test --san lam.lab.example.test
docker compose -f compose.pki.yaml run --rm pki --env lab issue client alice --cn alice
```

クライアントのCNをそのままLDAPユーザと認める設定は自動では追加しない。
認証先で発行元CA・EKU・ユーザ対応を設定する。クライアントSANはこの版ではDNS/IPのみ。
メールSAN、UPN OtherName、任意OIDを使う端末プロファイルは対象外。
外部CSRはissueにも--csr /input/name.csrを指定できる。サーバSANはその場合も明示する。
CSRからCA権限やEKUをそのままコピーせず、発行側プロファイルで決める。

--daysで期間を短縮可能。発行元より有効期限が長くなる指定は拒否する。
更新は新しい名前（openldap-2027等）で発行して切り替える。既存IDを上書きしない。

## 確認と出力

```bash
docker compose -f compose.pki.yaml run --rm pki --env lab list
docker compose -f compose.pki.yaml run --rm pki --env lab verify server/openldap
docker compose -f compose.pki.yaml run --rm pki --env lab export server/openldap --format pem --include-key
```

出力先はpki/data/lab/exports/server-openldap/pem/。
cert.pemは末端だけ、fullchain.pemは末端+発行用CA、chain.pemは発行用CA+Root、root.crtはRootだけ。
--include-key指定時だけkey.pemを出力する。CSRのみの発行では秘密鍵出力不可。
rootの秘密鍵はexport対象外。同じ形式の出力先が既存なら上書きしない。

PKCS#12のパスワードは引数で渡さずファイルに保存する。12文字以上必要。

```bash
umask 077
(umask 077; set -C; openssl rand -base64 32 > pki/import/export.pass)
docker compose -f compose.pki.yaml run --rm pki --env lab export client/alice --format p12 --password-file /input/export.pass
docker compose -f compose.pki.yaml run --rm pki --env lab export ca/inspection-fw01 --format p12 --password-file /input/export.pass
```

PKCS#12は秘密鍵とチェーンを含む。出力はexports/client-alice/p12/certificate.p12等。
FortiGateへ外部CSRを使った場合はInspection CAのPEM証明書とRootを取り込み、装置内の鍵を使う。

## OpenLDAPへの配布

既存ldap/create-certs.pyで証明書を作成済みなら、旧ldap/certsを別の場所へ退避して空ディレクトリを作る。
この操作は旧CAを変更するため、FortiGateの信頼CAも切り替える。CLIは既存ディレクトリを上書きしない。

```bash
docker compose -f compose.pki.yaml run --rm pki-deploy --env lab deploy-ldap server/openldap
```

ldap/certsへca.crt（Root）、server.crt（末端+Server CA）、server.keyを配置する。
配布前にチェーン、serverAuth、openldap名、172.16.201.173、鍵の一致を検証する。
既存LDAP Composeはこのファイル名をそのまま利用できる。
LDAP関連コンテナを再作成し、FortiGateには新しいca.crtを配布する。

```bash
docker compose up -d --force-recreate openldap freeradius lam
docker compose exec -T openldap python3 - < ldap/check-access.py
```

FreeRADIUSのEAP用証明書とLAMのHTTPS終端への配置は別作業。このコマンドはLDAP用だけを配布する。

## 失効とCRL

```bash
docker compose -f compose.pki.yaml run --rm pki --env lab revoke client/alice
docker compose -f compose.pki.yaml run --rm pki --env lab crl ca/client --days 7
```

CRLはpki/data/lab/crls/ca-client.pemとca-client.derへ出力し、再生成時だけ上書きする。
Sub CAの失効はrevoke ca/server等を行い、pki-rootサービスでcrl rootを実行する。
Root自体の失効はこのCLIの対象外。信頼ストアから削除する。
失効した発行元からの追加発行と、子証明書のverify/exportは拒否する。

失効日時はledger.jsonへ保存する。CFSSL gencrlの仕様によりCRL内の各失効日時はCRL生成時刻になる。
この版ではOCSP、CRL番号、増分CRL、発行履歴の改ざん防止は提供しない。
CRLの自動更新・HTTP配布サーバも起動しない。有効期限前に再生成して配布先へ配置する。
発行時に--crl-url http://pki.example.test/crl/ca-client.der等を指定するとCDPを設定する。
CAを発行する時の--crl-urlはRoot CRLのURL、末端発行時はその発行元CAのCRLのURLを指定する。
URLを証明書へ書くだけでは失効確認は有効にならない。認証先でCRLの取得・検証を設定する。
verifyコマンドは手元の失効台帳とチェーンを確認し、外部機器の失効確認設定までは変更しない。

## データ保護・環境切替

環境名が異なればdata/<環境>とoffline/<環境>に分かれる。通常のコマンドで既存環境を削除しない。
台帳・CA鍵・証明書をまとめてバックアップし、秘密鍵とバックアップの参照権限を制限する。
root.passは各環境で共通に参照する既定値なので、Rootのパスワードを環境別にする場合は、
compose.pki.yamlのpki-rootのバインド元を環境別ファイルへ切り替える。
障害で.lockが残った場合は、実行中のPKIコマンドがないことを確認したうえで該当環境の.lockだけを除去する。
途中失敗した証明書ディレクトリは自動削除しない。台帳と照合して復旧する。
CAを分離しても、Rootだけを広く信頼する認証先では全Sub CAを信頼し得る。用途・発行元の制限は利用側でも設定する。

作成時の検証: Windows上でCFSSL v1.7.0を実ビルドし、実発行を含む5テストを実行。Pi/ARM64のDockerビルドとFortiGateへのインポートは別途確認が必要。

