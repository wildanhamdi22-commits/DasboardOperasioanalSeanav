"""Konversi koordinat WGS84 (lintang/bujur) <-> UTM (Easting/Northing).

Jakarta berada di UTM zona 48 Selatan (48S, EPSG:32748). Rumus Transverse Mercator standar
(Snyder / USGS), akurasi sub-milimeter di dalam zona.
"""
import math
import re

_A = 6378137.0
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
_K0 = 0.9996
DEFAULT_ZONE = 48
DEFAULT_SOUTH = True


def _meridian(phi):
    e2, e4, e6 = _E2, _E2 ** 2, _E2 ** 3
    return _A * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                 - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
                 + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
                 - (35 * e6 / 3072) * math.sin(6 * phi))


def to_utm(lat, lng, zone=None):
    """(lat, lng) derajat -> (easting, northing, zona, 'S'/'N')."""
    lat, lng = float(lat), float(lng)
    zone = zone or int((lng + 180) // 6) + 1
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi, lam = math.radians(lat), math.radians(lng)
    n = _A / math.sqrt(1 - _E2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = _EP2 * math.cos(phi) ** 2
    a = math.cos(phi) * (lam - lon0)
    m = _meridian(phi)
    easting = _K0 * n * (a + (1 - t + c) * a ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * _EP2) * a ** 5 / 120) + 500000
    northing = _K0 * (m + n * math.tan(phi) * (a * a / 2 + (5 - t + 9 * c + 4 * c * c) * a ** 4 / 24
                                                + (61 - 58 * t + t * t + 600 * c - 330 * _EP2) * a ** 6 / 720))
    hemi = "S" if lat < 0 else "N"
    if lat < 0:
        northing += 10000000
    return easting, northing, zone, hemi


def from_utm(easting, northing, zone=DEFAULT_ZONE, south=DEFAULT_SOUTH):
    """(easting, northing) -> (lat, lng) derajat."""
    x = float(easting) - 500000
    y = float(northing) - (10000000 if south else 0)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    m = y / _K0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    n1 = _A / math.sqrt(1 - _E2 * math.sin(phi1) ** 2)
    t1 = math.tan(phi1) ** 2
    c1 = _EP2 * math.cos(phi1) ** 2
    r1 = _A * (1 - _E2) / (1 - _E2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * _K0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (d * d / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * _EP2) * d ** 4 / 24
                                                + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * _EP2 - 3 * c1 * c1) * d ** 6 / 720)
    lng = lon0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                  + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * _EP2 + 24 * t1 * t1) * d ** 5 / 120) / math.cos(phi1)
    return math.degrees(lat), math.degrees(lng)


def fmt_utm(lat, lng):
    """'E 704.215 · N 9.311.837 (UTM 48S)'"""
    e, n, zone, hemi = to_utm(lat, lng)
    dot = lambda v: f"{v:,.0f}".replace(",", ".")
    return f"E {dot(e)} · N {dot(n)} (UTM {zone}{hemi})"


def parse_utm(text):
    """Baca 'E 704215 N 9311837', '704215, 9311837' atau '48S 704215 9311837' -> (lat, lng) atau None."""
    t = (text or "").upper().replace("·", " ")
    zone, south = DEFAULT_ZONE, DEFAULT_SOUTH
    z = re.search(r"\b(\d{1,2})\s*([C-HJ-NP-X])\b", t)
    if z:
        letter = z.group(2)
        zone, south = int(z.group(1)), letter == "S" or (letter != "N" and letter < "N")
        t = t[:z.start()] + " " + t[z.end():]
    t = re.sub(r"(?<=\d)[.](?=\d{3}\b)", "", t)  # 9.311.837 -> 9311837
    nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", t)]
    e_m = re.search(r"E\w*\s*[:=]?\s*(\d+(?:[.,]\d+)?)", t)
    n_m = re.search(r"N\w*\s*[:=]?\s*(\d+(?:[.,]\d+)?)", t)
    if e_m and n_m:
        e, n = float(e_m.group(1).replace(",", ".")), float(n_m.group(1).replace(",", "."))
    elif len(nums) == 2:
        e, n = nums
    else:
        return None
    if not (100000 <= e <= 900000 and 0 <= n <= 10000000):
        return None
    return from_utm(e, n, zone, south)
