"""Jalankan aplikasi lewat HTTPS di jaringan kantor (LAN).

Browser hanya mengizinkan kamera dan GPS di https:// atau localhost, jadi absensi dari HP
atau laptop lain perlu mode ini. Sertifikat self-signed dibuat otomatis di folder sertifikat/.
Saat pertama kali dibuka, browser menampilkan peringatan; pilih "Lanjutkan / Advanced -> Proceed".
"""
import ipaddress
import socket
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management.base import BaseCommand
from django.core.servers.basehttp import ThreadedWSGIServer, WSGIRequestHandler, get_internal_wsgi_application


def local_ips():
    ips = {"127.0.0.1"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


def ensure_certificate(folder: Path, ips):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    folder.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = folder / "seanav-lan.crt", folder / "seanav-lan.key"
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            known = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
        except x509.ExtensionNotFound:
            known = set()
        if set(ips) <= known and cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=30):
            return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Seanav Operations LAN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Oseana & Seanav"),
    ])
    now = datetime.now(timezone.utc)
    alt = [x509.DNSName("localhost"), x509.DNSName(socket.gethostname())]
    alt += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


class HttpsHandler(WSGIRequestHandler):
    def get_environ(self):
        env = super().get_environ()
        env["HTTPS"] = "on"
        env["wsgi.url_scheme"] = "https"
        return env


class HttpsServer(ThreadedWSGIServer):
    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError, TimeoutError)):
            return  # browser menolak sertifikat / koneksi terputus: abaikan
        super().handle_error(request, client_address)


class Command(BaseCommand):
    help = "Jalankan server HTTPS untuk jaringan kantor (kamera & GPS absensi di HP)."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=8443)

    def handle(self, *args, **opts):
        port = opts["port"]
        ips = local_ips()
        cert, key = ensure_certificate(Path(settings.BASE_DIR) / "sertifikat", ips)
        for ip in ips:
            if ip not in settings.ALLOWED_HOSTS and "*" not in settings.ALLOWED_HOSTS:
                settings.ALLOWED_HOSTS.append(ip)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        httpd = HttpsServer(("0.0.0.0", port), HttpsHandler, ipv6=False)
        httpd.daemon_threads = True
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
        httpd.set_app(StaticFilesHandler(get_internal_wsgi_application()))

        self.stdout.write("\nServer HTTPS berjalan. Buka salah satu alamat ini:")
        for ip in ips:
            self.stdout.write(f"   https://{ip}:{port}/")
        self.stdout.write("\nPeringatan keamanan saat pertama dibuka itu normal (sertifikat kantor sendiri).")
        self.stdout.write('Pilih "Advanced / Lanjutan" lalu "Proceed / Lanjutkan ke situs".')
        self.stdout.write("Tekan Ctrl+C untuk berhenti.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
