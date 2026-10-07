from __future__ import annotations

import hashlib
import ipaddress
import secrets
import ssl
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from vishing.core.config import Settings, private_ipv4_addresses


def ensure_certificate(settings: Settings) -> str:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if not settings.certificate_path.exists() or not settings.private_key_path.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Vishing Detection Desktop")])
        san: list[x509.GeneralName] = [x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]
        san.extend(x509.IPAddress(ipaddress.ip_address(ip)) for ip in private_ipv4_addresses())
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow() - timedelta(minutes=1))
            .not_valid_after(datetime.utcnow() + timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .sign(key, hashes.SHA256())
        )
        settings.private_key_path.write_bytes(
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        )
        settings.certificate_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    certificate = x509.load_pem_x509_certificate(settings.certificate_path.read_bytes())
    return certificate.fingerprint(hashes.SHA256()).hex()


def make_tls_context(settings: Settings) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(settings.certificate_path), str(settings.private_key_path))
    return context


def new_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_secret(secret: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(secret.encode("utf-8"), salt=actual_salt, n=2**14, r=8, p=1)
    return actual_salt, digest


def verify_secret(secret: str, salt: bytes, expected: bytes) -> bool:
    actual_salt, digest = hash_secret(secret, salt)
    return actual_salt == salt and secrets.compare_digest(digest, expected)
