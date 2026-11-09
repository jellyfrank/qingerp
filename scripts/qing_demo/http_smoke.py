"""Run inside the isolated test container; never target a tenant database."""
import os
import argparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from lxml import html

parser = argparse.ArgumentParser()
parser.add_argument("profile", choices=["cn_standard", "empty", "odoo_us"])
parser.add_argument("--name")
args = parser.parse_args()
session = requests.Session()
session.mount("http://", HTTPAdapter(max_retries=Retry(connect=5, backoff_factor=0.5)))
base = "http://127.0.0.1:8069"
page = session.get(base + "/web/database/manager", timeout=30)
page.raise_for_status()
assert set(html.fromstring(page.text).xpath('//select[@name="demo_profile"]/option/@value')) == {
    "empty", "odoo_us", "cn_standard",
}
name = args.name or "qing_demo_" + args.profile + "_test"
assert name.startswith("qing_demo_")
response = session.post(base + "/web/database/create", data={
    "master_pwd": "qing-demo-test-only", "name": name, "lang": "en_US",
    "password": os.environ["QING_TEST_PASSWORD"], "login": "admin@example.com", "phone": "",
    "country_code": "CN", "demo_profile": args.profile,
}, allow_redirects=False, timeout=600)
print(response.status_code, response.headers.get("Location"), flush=True)
if response.status_code != 303:
    print(html.fromstring(response.text).xpath('//*[contains(@class,"alert")]/text()'), flush=True)
assert response.status_code == 303
page = session.get(base + "/odoo", timeout=120)
assert page.status_code == 200 and "web.login" not in page.text
assert '"uid": 2' in page.text or '"uid":2' in page.text, page.text[-500:]
print("PASS profile creation and authenticated web client:", args.profile, flush=True)
