"""Offline CLI wrapper for CFSSL; private values never printed to stdout."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def slug(value):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', value):
        raise ValueError('Names must be lowercase letters/digits/_/- (1..64 characters)')
    return value


def hosts(values):
    for value in values:
        try:
            ipaddress.ip_address(value)
            continue
        except ValueError:
            pass
        if not re.fullmatch(r'(?:\*\.)?[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', value) or '..' in value:
            raise ValueError('SAN must be a DNS name or IP address: ' + value)
    return values


def run(*args, data=None):
    result = subprocess.run([str(x) for x in args], input=data, capture_output=True)
    if result.returncode:
        # Only stderr is included; cfssl stdout can contain private keys.
        raise RuntimeError(str(args[0]) + ': ' + result.stderr.decode(errors='replace').strip())
    return result.stdout


def write(path, value, public=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(value.encode() if isinstance(value, str) else value)
    path.chmod(0o644 if public else 0o600)


def policy(kind, days, crl_url=None):
    if not 1 <= days <= 3650:
        raise ValueError('Lifetime must be 1..3650 days')
    profile = {'expiry': str(days * 24) + 'h', 'backdate': '5m', 'copy_extensions': False}
    if kind == 'ca':
        profile.update(usages=['cert sign', 'crl sign'],
                       ca_constraint={'is_ca': True, 'max_path_len': 0, 'max_path_len_zero': True})
    else:
        profile['usages'] = ['signing', 'key encipherment', kind + ' auth']
        profile['ca_constraint'] = {'is_ca': False}
    if crl_url:
        if not re.fullmatch(r'https?://[^\s]+', crl_url):
            raise ValueError('CRL URL must be HTTP(S)')
        profile['crl_url'] = crl_url
    return {'signing': {'default': profile, 'profiles': {'selected': profile}}}


class PKI:
    def __init__(self, data, offline, environment):
        self.env = slug(environment)
        self.home = Path(data) / self.env
        self.offline = Path(offline) / self.env

    @contextmanager
    def locked(self):
        self.home.mkdir(parents=True, exist_ok=True)
        lock = self.home / '.lock'
        try:
            lock.mkdir()
        except FileExistsError:
            raise ValueError('PKI is locked. Check running commands before removing stale .lock.')
        try:
            yield
        finally:
            lock.rmdir()

    def ledger(self):
        file = self.home / 'ledger.json'
        return json.loads(file.read_text()) if file.exists() else {}

    def save(self, records):
        target = self.home / 'ledger.json'
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(records, indent=2) + '\n')
        temp.chmod(0o600)
        temp.replace(target)

    def record(self, identifier):
        # Resolve paths only through recorded relative names, never arbitrary CLI paths.
        records = self.ledger()
        if identifier not in records:
            raise ValueError('Unknown certificate ID: ' + identifier)
        item = records[identifier]
        if item.get('revoked_at'):
            raise ValueError('Certificate is revoked: ' + identifier)
        if item['issuer'] != identifier:
            self.record(item['issuer'])
        return item

    def register(self, identifier, issuer, folder, kind):
        records = self.ledger()
        if identifier in records:
            raise ValueError('Certificate ID already exists')
        cert = folder / 'cert.pem'
        serial = run('openssl', 'x509', '-in', cert, '-noout', '-serial').decode().strip().split('=', 1)[1]
        end = run('openssl', 'x509', '-in', cert, '-noout', '-enddate').decode().strip().split('=', 1)[1]
        records[identifier] = {'issuer': issuer, 'folder': str(folder.relative_to(self.home)),
            'kind': kind, 'serial_hex': serial, 'not_after': end,
            'issued_at': datetime.now(timezone.utc).isoformat(), 'revoked_at': None}
        self.save(records)

    def folder(self, identifier):
        item = self.record(identifier)
        return self.home / item['folder']

    def ensure_lifetime(self, issuer, days):
        run('openssl', 'x509', '-in', self.folder(issuer) / 'cert.pem', '-noout',
            '-checkend', str(days * 86400))

    @contextmanager
    def rootkey(self):
        key = self.offline / 'root.key'
        password = Path(os.environ.get('PKI_ROOT_PASSWORD_FILE', '/run/secrets/root.pass'))
        if not key.is_file() or not password.is_file():
            raise ValueError('Root key/password are unavailable. Use pki-root with offline storage attached.')
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'root.key'
            write(output, run('openssl', 'pkey', '-in', key, '-passin', 'file:' + str(password)))
            yield output

    def init_root(self, cn, days):
        if not 1 <= days <= 7300:
            raise ValueError('Root lifetime must be 1..7300 days')
        folder = self.home / 'root'
        if folder.exists() or self.offline.exists():
            raise ValueError('Root data already exists; refusing to overwrite')
        password = Path(os.environ.get('PKI_ROOT_PASSWORD_FILE', '/run/secrets/root.pass'))
        if not password.is_file() or len(password.read_bytes().strip()) < 16:
            raise ValueError('Root password file must contain at least 16 characters')
        request = {'CN': cn, 'key': {'algo': 'rsa', 'size': 4096},
                   'ca': {'expiry': str(days * 24) + 'h', 'pathlen': 1}}
        result = json.loads(run('cfssl', 'gencert', '-initca', '-', data=json.dumps(request).encode()))
        self.offline.mkdir(parents=True)
        encrypted = run('openssl', 'pkey', '-aes-256-cbc', '-passout', 'file:' + str(password),
                        data=result['key'].encode())
        write(self.offline / 'root.key', encrypted)
        write(folder / 'cert.pem', result['cert'], True)
        write(self.home / 'public/root.crt', result['cert'], True)
        self.register('root', 'root', folder, 'root')

    def sign(self, issuer, request, settings, csr=None):
        issuer_cert = self.folder(issuer) / 'cert.pem'
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'policy.json'
            config.write_text(json.dumps(settings))
            if csr:
                key = None
                csr_data = Path(csr).read_bytes()
            else:
                result = json.loads(run('cfssl', 'genkey', '-', data=json.dumps(request).encode()))
                key, csr_data = result['key'], result['csr'].encode()
            def execute(keyfile):
                args = ['cfssl', 'sign', '-ca', 'file:' + str(issuer_cert), '-ca-key', 'file:' + str(keyfile),
                        '-config', config, '-profile', 'selected']
                if request.get('hosts'):
                    args += ['-hostname', ','.join(request['hosts'])]
                # Force CLI subject; do not silently accept a CSR's subject instead of --cn.
                subject = Path(temp) / 'subject.json'
                subject.write_text(json.dumps({'CN': request['CN']}))
                return json.loads(run(*args, '-', subject, data=csr_data))['cert']
            if issuer == 'root':
                with self.rootkey() as rootkey:
                    cert = execute(rootkey)
            else:
                cert = execute(self.folder(issuer) / 'key.pem')
        return cert, key, csr_data

    def init_ca(self, kind, name, cn, days, csr=None, crl_url=None):
        label = 'inspection-' + slug(name) if kind == 'inspection' else kind
        if kind != 'inspection' and name:
            raise ValueError('--name is for inspection CAs only')
        identifier = 'ca/' + label
        folder = self.home / 'issuers' / label
        if folder.exists():
            raise ValueError('CA directory already exists')
        self.ensure_lifetime('root', days)
        request = {'CN': cn, 'key': {'algo': 'rsa', 'size': 3072}}
        cert, key, request_pem = self.sign('root', request, policy('ca', days, crl_url), csr)
        write(folder / 'cert.pem', cert, True)
        if key:
            write(folder / 'key.pem', key)
        write(folder / 'request.csr', request_pem, True)
        write(folder / 'chain.pem', cert + (self.home / 'root/cert.pem').read_text(), True)
        self.register(identifier, 'root', folder, 'ca')

    def issue(self, kind, name, cn, sans, days, csr=None, crl_url=None):
        slug(name)
        hosts(sans)
        if kind == 'server' and not sans:
            raise ValueError('Server certificate requires at least one explicit --san')
        identifier = kind + '/' + name
        folder = self.home / 'issued' / kind / name
        if folder.exists():
            raise ValueError('Certificate directory already exists; use a new name for renewal')
        issuer = 'ca/' + kind
        self.ensure_lifetime(issuer, days)
        request = {'CN': cn, 'hosts': sans, 'key': {'algo': 'rsa', 'size': 3072}}
        cert, key, request_pem = self.sign(issuer, request, policy(kind, days, crl_url), csr)
        write(folder / 'cert.pem', cert, True)
        if key:
            write(folder / 'key.pem', key)
        write(folder / 'request.csr', request_pem, True)
        write(folder / 'chain.pem', (self.folder(issuer) / 'chain.pem').read_text(), True)
        write(folder / 'fullchain.pem', cert + (self.folder(issuer) / 'cert.pem').read_text(), True)
        self.register(identifier, issuer, folder, kind)

    def verify(self, identifier):
        item = self.record(identifier)
        folder = self.folder(identifier)
        args = ['openssl', 'verify', '-CAfile', self.home / 'root/cert.pem']
        if item['kind'] in ('server', 'client'):
            args += ['-untrusted', folder / 'chain.pem', '-purpose', 'ssl' + item['kind']]
        run(*args, folder / 'cert.pem')

    def revoke(self, identifier):
        if identifier == 'root':
            raise ValueError('A root trust anchor is removed from trust stores, not revoked here')
        records = self.ledger()
        if identifier not in records:
            raise ValueError('Unknown certificate ID')
        if records[identifier]['revoked_at']:
            raise ValueError('Already revoked')
        records[identifier]['revoked_at'] = datetime.now(timezone.utc).isoformat()
        self.save(records)

    def crl(self, issuer, days):
        if not 1 <= days <= 30:
            raise ValueError('CRL lifetime must be 1..30 days')
        self.ensure_lifetime(issuer, days)
        records = self.ledger()
        serials = '\n'.join(str(int(x['serial_hex'], 16)) for x in records.values()
            if x['issuer'] == issuer and x['revoked_at']) + '\n'
        def generate(key):
            encoded = run('cfssl', 'gencrl', '-', self.folder(issuer) / 'cert.pem', key,
                          str(days * 86400), data=serials.encode())
            der = base64.b64decode(encoded.strip(), validate=True)
            pem = run('openssl', 'crl', '-inform', 'DER', '-outform', 'PEM', data=der)
            return der, pem
        if issuer == 'root':
            with self.rootkey() as rootkey:
                der, pem = generate(rootkey)
        else:
            der, pem = generate(self.folder(issuer) / 'key.pem')
        label = issuer.replace('/', '-')
        output = self.home / 'crls'
        output.mkdir(exist_ok=True)
        # CRLs are intentionally replaceable; certificates and keys are not.
        for extension, value in [('der', der), ('pem', pem)]:
            temp = output / (label + '.' + extension + '.tmp')
            temp.write_bytes(value)
            temp.chmod(0o644)
            temp.replace(output / (label + '.' + extension))

    def export(self, identifier, form, password_file=None, include_key=False):
        self.verify(identifier)
        folder = self.folder(identifier)
        output = self.home / 'exports' / identifier.replace('/', '-') / form
        if output.exists():
            raise ValueError('Export already exists; archive it before creating another')
        key = folder / 'key.pem'
        if (include_key or form == 'p12') and not key.is_file():
            raise ValueError('No exportable private key (external CSR or root)')
        if form == 'p12':
            if not key.is_file():
                raise ValueError('No private key (external CSR or root); PKCS#12 cannot be exported')
            if not password_file:
                raise ValueError('--password-file is required for PKCS#12; use a file under /input')
            if len(Path(password_file).read_bytes().strip()) < 12:
                raise ValueError('PKCS#12 password must contain at least 12 characters')
            chain = self.home / 'root/cert.pem' if self.record(identifier)['kind'] == 'ca' else folder / 'chain.pem'
            bundle = run('openssl', 'pkcs12', '-export', '-in', folder / 'cert.pem', '-inkey', key,
                         '-certfile', chain, '-passout', 'file:' + str(password_file))
            write(output / 'certificate.p12', bundle)
        else:
            write(output / 'cert.pem', (folder / 'cert.pem').read_bytes(), True)
            for name in ['chain.pem', 'fullchain.pem']:
                if (folder / name).is_file():
                    write(output / name, (folder / name).read_bytes(), True)
            write(output / 'root.crt', (self.home / 'root/cert.pem').read_bytes(), True)
            if include_key:
                if not key.is_file():
                    raise ValueError('No exportable private key')
                write(output / 'key.pem', key.read_bytes())

    def deploy_ldap(self, identifier):
        if self.record(identifier)['kind'] != 'server':
            raise ValueError('LDAP requires a server certificate')
        self.verify(identifier)
        folder = self.folder(identifier)
        # Require the Compose service name and IP used by the current lab.
        for option, value in [('-verify_hostname', 'openldap'), ('-verify_ip', '172.16.201.173')]:
            run('openssl', 'verify', '-CAfile', self.home / 'root/cert.pem', '-untrusted',
                folder / 'chain.pem', '-purpose', 'sslserver', option, value, folder / 'cert.pem')
        cert_public = run('openssl', 'x509', '-in', folder / 'cert.pem', '-pubkey', '-noout')
        key_public = run('openssl', 'pkey', '-in', folder / 'key.pem', '-pubout')
        if cert_public != key_public:
            raise ValueError('Private key does not match the certificate')
        destination = Path(os.environ.get('PKI_LDAP_TARGET', '/deploy-ldap'))
        if not destination.is_dir():
            raise ValueError('Use pki-deploy to mount the LDAP destination')
        if any(destination.iterdir()):
            raise ValueError('LDAP certificate directory is nonempty; archive old files first')
        write(destination / 'ca.crt', (self.home / 'root/cert.pem').read_bytes(), True)
        write(destination / 'server.crt', (folder / 'fullchain.pem').read_bytes(), True)
        write(destination / 'server.key', (folder / 'key.pem').read_bytes())


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument('--env', default='lab', type=slug)
    commands = root.add_subparsers(dest='command', required=True)
    cmd = commands.add_parser('init-root')
    cmd.add_argument('--cn', required=True)
    cmd.add_argument('--days', type=int, default=3650)
    cmd = commands.add_parser('init-ca')
    cmd.add_argument('kind', choices=['server', 'client', 'inspection'])
    cmd.add_argument('--name', default='')
    cmd.add_argument('--cn', required=True)
    cmd.add_argument('--days', type=int, default=1825)
    cmd.add_argument('--csr')
    cmd.add_argument('--crl-url')
    cmd = commands.add_parser('issue')
    cmd.add_argument('kind', choices=['server', 'client'])
    cmd.add_argument('name', type=slug)
    cmd.add_argument('--cn', required=True)
    cmd.add_argument('--san', action='append', default=[])
    cmd.add_argument('--days', type=int, default=365)
    cmd.add_argument('--csr')
    cmd.add_argument('--crl-url')
    for name in ['verify', 'revoke', 'deploy-ldap']:
        commands.add_parser(name).add_argument('identifier')
    cmd = commands.add_parser('crl')
    cmd.add_argument('issuer', help='root, ca/server, or ca/client')
    cmd.add_argument('--days', type=int, default=7)
    cmd = commands.add_parser('export')
    cmd.add_argument('identifier')
    cmd.add_argument('--format', choices=['pem', 'p12'], default='pem')
    cmd.add_argument('--include-key', action='store_true')
    cmd.add_argument('--password-file')
    commands.add_parser('list')
    commands.add_parser('self-test')
    return root


def main():
    os.umask(0o077)
    args = parser().parse_args()
    if args.command == 'self-test':
        import unittest
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / 'tests'))
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    store = PKI(os.environ.get('PKI_DATA', '/data'), os.environ.get('PKI_OFFLINE', '/offline'), args.env)
    with store.locked():
        if args.command == 'init-root':
            store.init_root(args.cn, args.days)
        elif args.command == 'init-ca':
            store.init_ca(args.kind, args.name, args.cn, args.days, args.csr, args.crl_url)
        elif args.command == 'issue':
            store.issue(args.kind, args.name, args.cn, args.san, args.days, args.csr, args.crl_url)
        elif args.command == 'verify':
            store.verify(args.identifier)
        elif args.command == 'revoke':
            store.revoke(args.identifier)
        elif args.command == 'crl':
            store.crl(args.issuer, args.days)
        elif args.command == 'export':
            store.export(args.identifier, args.format, args.password_file, args.include_key)
        elif args.command == 'deploy-ldap':
            store.deploy_ldap(args.identifier)
        elif args.command == 'list':
            print(json.dumps(store.ledger(), indent=2))
    print('OK: ' + args.command + ' (environment: ' + args.env + ')')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        raise SystemExit('ERROR: ' + str(error))

