"""Run with ldapmgttooz on PYTHONPATH and httpx installed."""
from pathlib import Path
import hmac
import re
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import config, domain
from app.directory import DirectoryError

with patch.dict("os.environ", {"LDAPMGTTOOZ_SECRET": "test-session-key", "NETRIG_SHARED_SECRET": "test-session-key"}):
    from app import main


class DomainTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory(dir=Path.cwd()))
        root = Path(temp)
        self.enterContext(patch.multiple(config, SETTINGS=root / "settings.json",
            STATUS_LDAP=root / "status-ldap.json", STATUS_RADIUS=root / "status-radius.json",
            POLICY=root / "freeradius" / "nwlab_vlan"))
        self.enterContext(patch.object(domain, "CATALOG", root / "domains.json"))
        self.initial = config.save_settings({"base_dn": "dc=asakusa,dc=lab",
            "admin_rdn": "cn=admin", "admin_password": "lab-initial",
            "bind_rdn": "cn=radius,ou=ServiceAccounts", "bind_password": "lab-bind0"})

    def test_profiles_and_delete_isolation(self):
        self.assertEqual(domain.list_domains()[0]["name"], "asakusa.lab")
        domain.create("Example.Test.", "lab-admin", "lab-bind1")
        self.assertEqual(config.load_settings(), self.initial)
        with self.assertRaises(DirectoryError):
            domain.create("example.test", "lab-admin", "lab-bind1")
        with self.assertRaises(DirectoryError):
            domain.create("../escape", "lab-admin", "lab-bind1")
        domain.switch("example.test", "dc=asakusa,dc=lab")
        other = config.load_settings()
        self.assertEqual(other["base_dn"], "dc=example,dc=test")
        self.assertEqual(other["bind_password"], "lab-bind1")
        self.assertNotEqual(config.policy_path(other), config.policy_path(self.initial))
        with self.assertRaises(DirectoryError):
            domain.delete("example.test", "example.test")
        other["admin_password"] = "updated-password"
        config.save_settings(other)
        domain.switch("asakusa.lab", other["base_dn"])
        self.assertEqual(config.load_settings()["admin_password"], "lab-initial")
        domain.switch("example.test", self.initial["base_dn"])
        self.assertEqual(config.load_settings()["admin_password"], "updated-password")
        domain.switch("asakusa.lab", other["base_dn"])
        with self.assertRaises(DirectoryError):
            domain.delete("example.test", "wrong")
        domain.delete("example.test", "example.test")
        self.assertEqual([d["name"] for d in domain.list_domains()], ["asakusa.lab"])
        self.assertEqual(config.read_json(domain.CATALOG)["deleted"][0]["profile"]["data_slot"], other["data_slot"])
        domain.create("example.test", "lab-admin", "lab-bind1")
        self.assertNotEqual(config.read_json(domain.CATALOG)["domains"]["example.test"]["data_slot"], other["data_slot"])

    def test_web_routes_and_stale_form(self):
        self.enterContext(patch.object(main, "sync_policy"))
        client = self.enterContext(TestClient(main.app, follow_redirects=False))
        self.assertEqual(client.get("/domains").status_code, 303)
        user = "admin"
        secret = b"test-session-key"
        signature = hmac.new(secret, user.encode(), "sha256").hexdigest()
        client.headers.update({"X-NetRig-User": user, "X-NetRig-Signature": signature})
        page = client.get("/domains")
        self.assertEqual(page.status_code, 200)
        self.assertIn("LdapMgtTooz", page.text)
        self.assertIn("asakusa.lab", page.text)
        self.assertNotIn("lab-initial", page.text)
        csrf = re.search(r'name="_csrf" value="([^"]+)"', page.text)[1]
        form = {"_csrf": csrf, "_generation": str(self.initial["generation"]),
                "domain": "example.test", "admin_password": "test-admin", "bind_password": "test-bind"}
        self.assertEqual(client.post("/domains/create", data=form).status_code, 303)
        self.assertEqual(client.post("/domains/switch", data={
            **form, "current_base": self.initial["base_dn"]}).status_code, 303)
        self.assertEqual(client.post("/users/new", data=form).status_code, 409)
        self.assertEqual(client.post("/domains/delete", data={"domain": "asakusa.lab"}).status_code, 403)
        self.assertIn("example.test", client.get("/domains").text)


if __name__ == "__main__":
    unittest.main()
