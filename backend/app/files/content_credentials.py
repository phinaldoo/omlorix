"""C2PA Content Credentials for media generated inside Omlorix."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import threading
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier

from app.paths import DATA_DIR
from app.version import APP_VERSION


CONTENT_CREDENTIALS_DIR = DATA_DIR / "content_credentials"
LOCAL_SIGNER_BUNDLE = CONTENT_CREDENTIALS_DIR / "local_signer.pem"
DIGITAL_SOURCE_TYPE = (
    "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
)

_C2PA_FORMATS = frozenset(
    {
        "image/avif",
        "image/gif",
        "image/heic",
        "image/heif",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/webp",
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
        "video/avi",
        "video/mp4",
        "video/quicktime",
    }
)
_AI_GENERATION_META_KEYS = frozenset(
    {
        "audio_generation",
        "code_execution",
        "image_generation",
        "music_generation",
        "read_aloud_cache",
        "video_generation",
    }
)
_SIGNER_LOCK = threading.Lock()


def is_supported_ai_generated_media(file_type: str, meta: dict | None) -> bool:
    """Return whether a file is eligible for Omlorix's C2PA marking."""

    normalized_meta = meta if isinstance(meta, dict) else {}
    return (
        str(file_type or "").strip().lower() in _C2PA_FORMATS
        and normalized_meta.get("origin") == "assistant"
        and any(normalized_meta.get(key) is True for key in _AI_GENERATION_META_KEYS)
    )


def _certificate_name(common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Omlorix"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def _generate_signer_bundle() -> bytes:
    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(days=3650)

    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = _certificate_name("Omlorix Local Content Credentials Root")
    root_certificate = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(valid_until)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )

    signer_key = ec.generate_private_key(ec.SECP256R1())
    signer_certificate = (
        x509.CertificateBuilder()
        .subject_name(_certificate_name("Omlorix Local Content Credentials Signer"))
        .issuer_name(root_certificate.subject)
        .public_key(signer_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(valid_until)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ObjectIdentifier("1.3.6.1.5.5.7.3.36")]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(signer_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )

    return b"".join(
        (
            signer_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            signer_certificate.public_bytes(serialization.Encoding.PEM),
            root_certificate.public_bytes(serialization.Encoding.PEM),
        )
    )


def _load_signer_material() -> tuple[bytes, bytes]:
    with _SIGNER_LOCK:
        CONTENT_CREDENTIALS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(CONTENT_CREDENTIALS_DIR, 0o700)
        if not LOCAL_SIGNER_BUNDLE.exists():
            bundle = _generate_signer_bundle()
            temporary = CONTENT_CREDENTIALS_DIR / f".{uuid.uuid4().hex}.pem"
            try:
                temporary.write_bytes(bundle)
                os.chmod(temporary, 0o600)
                os.replace(temporary, LOCAL_SIGNER_BUNDLE)
            finally:
                temporary.unlink(missing_ok=True)
        os.chmod(LOCAL_SIGNER_BUNDLE, 0o600)
        bundle = LOCAL_SIGNER_BUNDLE.read_bytes()

    key = serialization.load_pem_private_key(bundle, password=None)
    certificates = x509.load_pem_x509_certificates(bundle)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or len(certificates) != 2:
        raise ValueError("Invalid local Content Credentials signer")
    private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    certificate_chain = b"".join(
        certificate.public_bytes(serialization.Encoding.PEM)
        for certificate in certificates
    )
    return private_key, certificate_chain


def apply_content_credentials(
    *,
    file_bytes: bytes,
    file_type: str,
    original_filename: str,
) -> tuple[bytes, str]:
    """Preserve an existing C2PA manifest or add a local Omlorix claim."""

    import c2pa

    normalized_type = str(file_type or "").strip().lower()
    context = c2pa.Context.from_dict(
        {
            "verify": {"remote_manifest_fetch": False, "ocsp_fetch": False},
            "builder": {"thumbnail": {"enabled": False}},
        }
    )
    try:
        existing = json.loads(
            c2pa.Reader(
                normalized_type,
                BytesIO(file_bytes),
                context=context,
            ).json()
        )
    except c2pa.C2paError as exc:
        if not str(exc).startswith("ManifestNotFound:"):
            raise
    else:
        if existing.get("active_manifest"):
            return file_bytes, "preserved"

    private_key, certificate_chain = _load_signer_material()
    signer = c2pa.Signer.from_info(
        c2pa.C2paSignerInfo(
            c2pa.C2paSigningAlg.ES256,
            certificate_chain,
            private_key,
            None,
        )
    )
    software_agent = {"name": "Omlorix", "version": APP_VERSION}
    manifest = {
        "claim_generator_info": [software_agent],
        "title": Path(original_filename or "generated").name,
        "format": normalized_type,
        "ingredients": [],
        "assertions": [
            {
                "label": "c2pa.actions",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "digitalSourceType": DIGITAL_SOURCE_TYPE,
                            "softwareAgent": software_agent,
                        }
                    ]
                },
            }
        ],
    }
    source = BytesIO(file_bytes)
    destination = BytesIO()
    c2pa.Builder(manifest, context).sign(
        signer,
        normalized_type,
        source,
        destination,
    )
    signed = destination.getvalue()
    if not signed:
        raise RuntimeError("C2PA signing returned empty media")
    return signed, "embedded"


def content_credentials_meta(status: str) -> dict[str, str]:
    metadata = {
        "standard": "C2PA",
        "status": status,
        "digital_source_type": "trainedAlgorithmicMedia",
    }
    if status == "embedded":
        metadata.update(
            {
                "claim_generator": "Omlorix",
                "signer": "local_instance",
            }
        )
    return metadata
