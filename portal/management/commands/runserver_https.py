"""Jalankan aplikasi lewat HTTPS di jaringan kantor (LAN).

Browser hanya mengizinkan kamera dan GPS di https:// atau localhost, jadi absensi dari HP
atau laptop lain perlu mode ini. Sertifikat self-signed dibuat otomatis di folder sertifikat/.
Saat pertama kali dibuka, browser menampilkan peringatan; pilih "Lanjutkan / Advanced -> Proceed".
"""
import ipaddress
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    # Di Windows SO_REUSEADDR membuat dua server bisa "berebut" port yang sama; matikan supaya bentrok terdeteksi.
    allow_reuse_address = os.name != "nt"

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError, TimeoutError)):
            return  # browser menolak sertifikat / koneksi terputus: abaikan
        import traceback
        log = Path(settings.BASE_DIR) / "sertifikat" / "server_error.log"
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} {client_address}\n")
            traceback.print_exc(file=fh)
        super().handle_error(request, client_address)


def already_running(ports):
    """Port tempat Seanav sudah berjalan (mencegah dua jendela server sekaligus)."""
    import urllib.request
    ctx = ssl._create_unverified_context()
    for port in (443, 8443) if not ports else ports:
        try:
            with urllib.request.urlopen(f"https://127.0.0.1:{port}/login/", context=ctx, timeout=2) as resp:
                if b"Oseana" in resp.read(200000) or b"Seanav" in resp.read():
                    return port
        except Exception:
            continue
    return None


def start_redirect(ports, https_port):
    """Server http:// kecil yang mengalihkan semua alamat lama ke https://."""
    suffix = "" if https_port == 443 else f":{https_port}"

    class Redirect(BaseHTTPRequestHandler):
        def _go(self):
            host = (self.headers.get("Host") or "localhost").split(":")[0]
            self.send_response(301)
            self.send_header("Location", f"https://{host}{suffix}{self.path}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_HEAD = do_POST = _go

        def log_message(self, *args):
            pass

    started = []
    for port in ports:
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", port), Redirect)
        except OSError:
            continue
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(port)
    return started


class Command(BaseCommand):
    help = "Jalankan server HTTPS untuk jaringan kantor (kamera & GPS absensi di HP)."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=0, help="0 = otomatis: 443, atau 8443 bila 443 terpakai")
        parser.add_argument("--no-redirect", action="store_true")
        parser.add_argument("--open", action="store_true", help="Buka browser setelah server siap")

    def handle(self, *args, **opts):
        ports = [opts["port"]] if opts["port"] else [443, 8443]
        ips = local_ips()
        cert, key = ensure_certificate(Path(settings.BASE_DIR) / "sertifikat", ips)
        for ip in ips:
            if ip not in settings.ALLOWED_HOSTS and "*" not in settings.ALLOWED_HOSTS:
                settings.ALLOWED_HOSTS.append(ip)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        running = already_running(ports)
        if running:
            suffix = "" if running == 443 else f":{running}"
            self.stdout.write(f"\nSeanav sudah berjalan di jendela lain. Membuka https://localhost{suffix}")
            if opts["open"]:
                import webbrowser
                webbrowser.open(f"https://localhost{suffix}/")
            raise SystemExit(3)
        httpd, port, last_error = None, None, None
        for candidate in ports:
            try:
                httpd = HttpsServer(("0.0.0.0", candidate), HttpsHandler, ipv6=False)
                port = candidate
                break
            except OSError as exc:
                last_error = exc
        if httpd is None:
            self.stderr.write(f"\n[!] Port {', '.join(map(str, ports))} tidak bisa dipakai ({last_error}).")
            self.stderr.write("    Kemungkinan Seanav sudah berjalan di jendela lain. Tutup jendela itu lalu coba lagi.")
            raise SystemExit(2)
        redirects = [] if opts["no_redirect"] else start_redirect([80, 8000], port)
        httpd.daemon_threads = True
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
        httpd.set_app(StaticFilesHandler(get_internal_wsgi_application()))

        suffix = "" if port == 443 else f":{port}"
        lan = [ip for ip in ips if ip != "127.0.0.1"]
        self.stdout.write("\n  ============================================================")
        self.stdout.write("    SEANAV OPERATIONS SUDAH BERJALAN")
        self.stdout.write("  ============================================================")
        for ip in lan[:1] or ["localhost"]:
            self.stdout.write(f"    Alamat untuk semua perangkat :  https://{ip}{suffix}")
        self.stdout.write(f"    Di laptop ini juga bisa      :  https://localhost{suffix}")
        if len(lan) > 1:
            self.stdout.write("    Alamat lain                  :  " + ", ".join(f"https://{ip}{suffix}" for ip in lan[1:]))
        if redirects:
            self.stdout.write("    Alamat lama http:// otomatis dialihkan ke https://")
        self.stdout.write("  ============================================================")
        self.stdout.write("\nPeringatan keamanan saat pertama dibuka itu normal (sertifikat kantor sendiri).")
        self.stdout.write('Pilih "Advanced / Lanjutan" lalu "Proceed / Lanjutkan ke situs".')
        self.stdout.write("Tekan Ctrl+C untuk berhenti.\n")
        if opts["open"]:
            import webbrowser
            threading.Timer(1.5, webbrowser.open, [f"https://localhost{suffix}/"]).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        except Exception:
            import traceback
            with open(Path(settings.BASE_DIR) / "sertifikat" / "server_error.log", "a", encoding="utf-8") as fh:
                fh.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} server berhenti\n")
                traceback.print_exc(file=fh)
            raise
        finally:
            httpd.server_close()
