import os
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import BinaryBytes


@tagged("post_install", "-at_install")
class TestWeChatPayCertificate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("qing_payment_wechatpay.payment_provider_wechatpay")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test merchant")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        cls.cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        cls.key_pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def _upload_pair(self):
        self.provider.write({
            "wechatpay_mch_cert_file": BinaryBytes(self.cert_pem),
            "wechatpay_mch_key_file": BinaryBytes(self.key_pem),
        })

    def test_uploaded_files_are_private_and_cleaned_after_failure(self):
        self._upload_pair()
        paths = []
        with self.assertRaisesRegex(RuntimeError, "request failed"):
            with self.provider._wechatpay_refund_client() as client:
                paths = [client.mch_cert, client.mch_key]
                for path, expected in zip(paths, [self.cert_pem, self.key_pem]):
                    self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                    with open(path, "rb") as pem:
                        self.assertEqual(pem.read(), expected)
                raise RuntimeError("request failed")
        self.assertTrue(paths)
        self.assertTrue(all(not os.path.exists(path) for path in paths))

    def test_incomplete_upload_is_rejected(self):
        self.provider.wechatpay_mch_cert_file = BinaryBytes(self.cert_pem)
        with self.assertRaises(UserError):
            with self.provider._wechatpay_refund_client():
                self.fail("Incomplete credentials must not reach the request.")

    def test_invalid_key_is_rejected(self):
        self._upload_pair()
        self.provider.wechatpay_mch_key_file = BinaryBytes(b"not a PEM key")
        with self.assertRaises(UserError):
            with self.provider._wechatpay_refund_client():
                self.fail("Invalid credentials must not reach the request.")

    def test_existing_path_configuration_is_preserved(self):
        client = Mock()
        with patch.object(type(self.provider), "_get_wechatpay", return_value=client) as factory:
            with self.provider._wechatpay_refund_client() as actual:
                self.assertIs(actual, client)
        factory.assert_called_once_with(with_cert=True)
