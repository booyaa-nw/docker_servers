import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('pki', Path(__file__).resolve().parents[1] / 'pki.py')
pki = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pki)


def test_directory():
    # Docker uses its writable tmpfs; host runs can keep a workspace-local directory.
    return tempfile.TemporaryDirectory(dir=os.environ.get('PKI_TEST_TMP', str(Path.cwd())))


class GuardTests(unittest.TestCase):
    def test_names_and_sans(self):
        for name in ['../lab', '/data', '', 'a/b', 'x' * 65]:
            with self.assertRaises(ValueError):
                pki.slug(name)
        self.assertEqual(pki.hosts(['openldap', '*.example.test', '172.16.201.173', '::1']),
                         ['openldap', '*.example.test', '172.16.201.173', '::1'])
        for san in ['a,b', 'a..b', '../key', 'name\nother']:
            with self.assertRaises(ValueError):
                pki.hosts([san])

    def test_profiles_separate_eku_and_forbid_ca_chaining(self):
        ca = pki.policy('ca', 365)['signing']['default']
        self.assertEqual(ca['ca_constraint'], {'is_ca': True, 'max_path_len': 0, 'max_path_len_zero': True})
        self.assertFalse(ca['copy_extensions'])
        for kind, other in [('server', 'client'), ('client', 'server')]:
            policy = pki.policy(kind, 30)['signing']['default']
            self.assertIn(kind + ' auth', policy['usages'])
            self.assertNotIn(other + ' auth', policy['usages'])
            self.assertFalse(policy['ca_constraint']['is_ca'])

    def test_lock_and_parent_revocation(self):
        with test_directory() as temp:
            store = pki.PKI(temp, Path(temp) / 'offline', 'lab')
            with store.locked():
                with self.assertRaises(ValueError):
                    with store.locked():
                        pass
            self.assertFalse((store.home / '.lock').exists())
            store.save({'root': {'issuer': 'root', 'revoked_at': None},
                        'ca/server': {'issuer': 'root', 'revoked_at': 'now'},
                        'server/test': {'issuer': 'ca/server', 'revoked_at': None}})
            with self.assertRaisesRegex(ValueError, 'revoked'):
                store.record('server/test')
            with self.assertRaises(ValueError):
                store.revoke('root')

    def test_no_overwrite(self):
        with test_directory() as temp:
            key = Path(temp) / 'key.pem'
            pki.write(key, 'first')
            with self.assertRaises(FileExistsError):
                pki.write(key, 'second')
            self.assertEqual(key.read_text(), 'first')


@unittest.skipUnless(shutil.which('cfssl') and shutil.which('openssl'), 'CFSSL/OpenSSL integration requires the built PKI container')
class RealCFSSLTests(unittest.TestCase):
    def test_lifecycle(self):
        with test_directory() as temp:
            folder = Path(temp)
            password = folder / 'password'
            password.write_text('Integration-Only-Password-2026')
            target = folder / 'ldap-certs'
            target.mkdir()
            store = pki.PKI(folder / 'data', folder / 'offline', 'lab')
            with patch.dict(os.environ, {'PKI_ROOT_PASSWORD_FILE': str(password), 'PKI_LDAP_TARGET': str(target)}):
                with store.locked():
                    store.init_root('Integration Root', 40)
                    self.assertIn('ENCRYPTED PRIVATE KEY', (store.offline / 'root.key').read_text())
                    store.init_ca('server', '', 'Server CA', 30)
                    store.init_ca('client', '', 'Client CA', 30)
                    store.init_ca('inspection', 'fw01', 'FW01 Inspection CA', 30)
                    ca = store.folder('ca/server') / 'cert.pem'
                    text = pki.run('openssl', 'x509', '-in', ca, '-text', '-noout').decode()
                    self.assertIn('CA:TRUE, pathlen:0', text)
                    store.issue('server', 'ldap', 'openldap', ['openldap', '172.16.201.173'], 7)
                    store.issue('client', 'alice', 'alice', [], 7)
                    # Untrusted CSR requests a CA; the server profile must reject it.
                    external_key, external_csr = folder / 'external.key', folder / 'external.csr'
                    pki.run('openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes',
                        '-subj', '/CN=untrusted', '-addext', 'basicConstraints=critical,CA:TRUE',
                        '-keyout', external_key, '-out', external_csr)
                    with self.assertRaises(RuntimeError):
                        store.issue('server', 'external', 'external.example.test', ['external.example.test'],
                                    7, str(external_csr))
                    self.assertFalse((store.home / 'issued/server/external').exists())
                    pki.run('openssl', 'req', '-new', '-key', external_key,
                        '-subj', '/CN=external.example.test', '-out', external_csr)
                    store.issue('server', 'external', 'external.example.test', ['external.example.test'],
                                7, str(external_csr))
                    external = store.folder('server/external')
                    self.assertFalse((external / 'key.pem').exists())
                    text = pki.run('openssl', 'x509', '-in', external / 'cert.pem', '-text', '-noout').decode()
                    self.assertNotIn('CA:TRUE', text)
                    with self.assertRaises(ValueError):
                        store.export('server/external', 'p12', password)
                    with self.assertRaises(RuntimeError):
                        store.deploy_ldap('server/external')
                    with self.assertRaises(RuntimeError):
                        store.issue('server', 'too-long', 'example.test', ['example.test'], 35)
                    store.verify('server/ldap')
                    store.verify('client/alice')
                    for identifier, wrong in [('server/ldap', 'sslclient'), ('client/alice', 'sslserver')]:
                        cert = store.folder(identifier)
                        with self.assertRaises(RuntimeError):
                            pki.run('openssl', 'verify', '-CAfile', store.home / 'root/cert.pem',
                                '-untrusted', cert / 'chain.pem', '-purpose', wrong, cert / 'cert.pem')
                    store.deploy_ldap('server/ldap')
                    with self.assertRaises(ValueError):
                        store.deploy_ldap('server/ldap')
                    store.export('client/alice', 'p12', password)
                    store.export('ca/inspection-fw01', 'p12', password)
                    inspection_bundle = store.home / 'exports/ca-inspection-fw01/p12/certificate.p12'
                    pki.run('openssl', 'pkcs12', '-in', inspection_bundle, '-passin', 'file:' + str(password), '-noout')
                    bundle = store.home / 'exports/client-alice/p12/certificate.p12'
                    pki.run('openssl', 'pkcs12', '-in', bundle, '-passin', 'file:' + str(password), '-noout')
                    with self.assertRaises(ValueError):
                        store.issue('server', 'ldap', 'openldap', ['openldap'], 7)
                    store.revoke('client/alice')
                    store.crl('ca/client', 1)
                    client = store.home / 'issued/client/alice'
                    with self.assertRaises(RuntimeError):
                        pki.run('openssl', 'verify', '-CAfile', store.home / 'root/cert.pem',
                            '-untrusted', client / 'chain.pem', '-CRLfile', store.home / 'crls/ca-client.pem',
                            '-crl_check', client / 'cert.pem')
                    store.revoke('ca/server')
                    store.crl('root', 1)
                    with self.assertRaises(ValueError):
                        store.verify('server/ldap')


if __name__ == '__main__':
    unittest.main()

