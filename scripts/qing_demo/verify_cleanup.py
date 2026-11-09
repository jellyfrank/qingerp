"""Expose fixtures/qing_demo_test_broken in isolated addons before this check."""
import os
import requests
from odoo.sql_db import db_connect

assert env.cr.dbname.startswith("qing_demo_")
response = requests.post("http://127.0.0.1:8069/web/database/create", data={
    "name": "qing_demo_expected_failure", "demo_profile": "test_broken",
    "master_pwd": "qing-demo-test-only", "lang": "en_US", "phone": "",
    "login": "admin@example.com", "password": os.environ["QING_TEST_PASSWORD"],
}, timeout=600)
assert response.status_code == 422
assert "Intentional test dataset failure" in response.text
assert "已清理本次新建数据库" in response.text
with db_connect("postgres").cursor() as cr:
    cr.execute("SELECT 1 FROM pg_database WHERE datname=%s", ["qing_demo_expected_failure"])
    assert not cr.fetchone()
print("PASS actual profile installation failure: database automatically removed")
