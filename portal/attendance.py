"""Fitur SDM: absensi masuk/pulang, pengajuan izin/sakit/cuti, rekap, profil, dan file media."""
import base64
import binascii
import calendar
import csv
import math
import mimetypes
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils._os import safe_join
from django.views.decorators.http import require_POST

from .models import Absensi, PengajuanIzin

BULAN = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
         "Agustus", "September", "Oktober", "November", "Desember"]
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MAX_PHOTO_BYTES = 3 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


# ---------------------------------------------------------------- helpers
def staff_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.get_full_path()}")
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def is_attendance_approver(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pengguna u
                    JOIN pengguna_peran up ON up.pengguna_id = u.id
                    JOIN izin_peran ip ON ip.peran_id = up.peran_id
                    WHERE lower(u.email) = lower(%s) AND u.aktif
                      AND ip.sumber = 'ABSENSI' AND ip.aksi = 'SETUJUI'
                )
                """,
                [user.email],
            )
            return bool(cursor.fetchone()[0])
    except DatabaseError:
        return False


def approver_required(view):
    @wraps(view)
    @staff_required
    def wrapped(request, *args, **kwargs):
        if not is_attendance_approver(request.user):
            return render(request, "portal/permission_denied.html",
                          {"database_error": False, "source": "ABSENSI", "action": "SETUJUI"}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def _setting(name, default):
    return getattr(settings, name, default)


def jam_masuk_kantor():
    raw = str(_setting("ABSENSI_JAM_MASUK", "08:00"))
    try:
        h, m = raw.split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return time(8, 0)


def _haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _office():
    try:
        lat = float(_setting("KANTOR_LAT", ""))
        lng = float(_setting("KANTOR_LNG", ""))
        radius = int(_setting("KANTOR_RADIUS_M", 200))
        return lat, lng, radius
    except (TypeError, ValueError):
        return None


def _decimal(value, lo, hi):
    try:
        d = Decimal(str(value)).quantize(Decimal("0.0000001"))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if lo <= d <= hi else None


def _location_label(lat, lng):
    if lat is None or lng is None:
        return "Lokasi tidak tersedia"
    office = _office()
    if not office:
        return "Lokasi tercatat"
    dist = _haversine_m(float(lat), float(lng), office[0], office[1])
    if dist <= office[2]:
        return f"Di kantor ({int(dist)} m)"
    if dist < 1000:
        return f"Di luar kantor ({int(dist)} m)"
    return f"Di luar kantor ({dist / 1000:.1f} km)"


def _photo_from_data_url(data_url, user_id, kind):
    if not data_url:
        return None
    if not data_url.startswith("data:image/jpeg;base64,") and not data_url.startswith("data:image/png;base64,"):
        raise ValueError("Format foto tidak dikenali.")
    header, encoded = data_url.split(",", 1)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Foto rusak, silakan ambil ulang.")
    if len(raw) > MAX_PHOTO_BYTES:
        raise ValueError("Ukuran foto terlalu besar (maks. 3 MB).")
    ext = "png" if "png" in header else "jpg"
    if ext == "jpg" and not raw.startswith(b"\xff\xd8"):
        raise ValueError("Foto tidak valid.")
    if ext == "png" and not raw.startswith(b"\x89PNG"):
        raise ValueError("Foto tidak valid.")
    return ContentFile(raw, name=f"{user_id}_{kind}_{uuid.uuid4().hex[:10]}.{ext}")


def _client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", "")) or None
    return ip


def _month_from_request(request):
    raw = request.GET.get("bulan", "")
    today = timezone.localdate()
    try:
        y, m = raw.split("-")
        y, m = int(y), int(m)
        if 2000 <= y <= 2100 and 1 <= m <= 12:
            return y, m
    except ValueError:
        pass
    return today.year, today.month


def _month_bounds(y, m):
    first = date(y, m, 1)
    last = date(y, m, calendar.monthrange(y, m)[1])
    return first, last


def _shift_month(y, m, delta):
    m2 = m + delta
    y2 = y + (m2 - 1) // 12
    m2 = (m2 - 1) % 12 + 1
    return y2, m2


# ---------------------------------------------------------------- absensi harian
@staff_required
def attendance(request):
    user = request.user
    today = timezone.localdate()
    record = Absensi.objects.filter(user=user, tanggal=today).first()
    require_photo = bool(_setting("ABSENSI_WAJIB_FOTO", False))

    if request.method == "POST":
        action = request.POST.get("action")
        lat = _decimal(request.POST.get("lat"), Decimal("-90"), Decimal("90"))
        lng = _decimal(request.POST.get("lng"), Decimal("-180"), Decimal("180"))
        if lat is None or lng is None:
            lat = lng = None
        try:
            accuracy = max(0, min(int(float(request.POST.get("accuracy") or 0)), 100000)) or None
        except ValueError:
            accuracy = None
        note = (request.POST.get("note") or "").strip()[:500]
        try:
            photo = _photo_from_data_url(request.POST.get("photo", ""), user.id, action or "x")
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("attendance")
        if require_photo and photo is None:
            messages.error(request, "Foto selfie wajib diambil sebelum absen.")
            return redirect("attendance")
        now = timezone.now()
        local_now = timezone.localtime(now)
        label = _location_label(lat, lng)
        try:
            with transaction.atomic():
                record = Absensi.objects.select_for_update().filter(user=user, tanggal=today).first()
                if action == "masuk":
                    if record and record.jam_masuk:
                        messages.warning(request, "Anda sudah absen masuk hari ini.")
                        return redirect("attendance")
                    late_limit = datetime.combine(today, jam_masuk_kantor()) + timedelta(
                        minutes=int(_setting("ABSENSI_TOLERANSI_MENIT", 15)))
                    status = Absensi.TERLAMBAT if local_now.replace(tzinfo=None) > late_limit else Absensi.HADIR
                    record = record or Absensi(user=user, tanggal=today)
                    record.jam_masuk = now
                    record.status = status
                    record.lat_masuk, record.lng_masuk, record.akurasi_masuk = lat, lng, accuracy
                    record.lokasi_masuk = label
                    record.ip_masuk = _client_ip(request)
                    if note:
                        record.catatan = note
                    if photo:
                        record.foto_masuk.save(photo.name, photo, save=False)
                    record.save()
                    messages.success(request, f"Absen masuk tercatat pukul {local_now:%H:%M}"
                                              f"{' (terlambat)' if status == Absensi.TERLAMBAT else ''}. Selamat bekerja!")
                elif action == "pulang":
                    if not record or not record.jam_masuk:
                        messages.error(request, "Absen masuk dulu sebelum absen pulang.")
                        return redirect("attendance")
                    if record.jam_pulang:
                        messages.warning(request, "Anda sudah absen pulang hari ini.")
                        return redirect("attendance")
                    record.jam_pulang = now
                    record.lat_pulang, record.lng_pulang, record.akurasi_pulang = lat, lng, accuracy
                    record.lokasi_pulang = label
                    record.ip_pulang = _client_ip(request)
                    if note:
                        record.catatan = (record.catatan + "\n" if record.catatan else "") + note
                    if photo:
                        record.foto_pulang.save(photo.name, photo, save=False)
                    record.save()
                    messages.success(request, f"Absen pulang tercatat pukul {local_now:%H:%M}. "
                                              f"Durasi kerja {record.durasi_teks}. Terima kasih!")
                else:
                    messages.error(request, "Aksi absensi tidak dikenali.")
        except IntegrityError:
            messages.warning(request, "Absensi hari ini sudah tercatat.")
        return redirect("attendance")

    # Kalender bulan berjalan
    y, m = today.year, today.month
    first, last = _month_bounds(y, m)
    month_records = {a.tanggal: a for a in Absensi.objects.filter(user=user, tanggal__range=(first, last))}
    leaves = PengajuanIzin.objects.filter(user=user, status=PengajuanIzin.DISETUJUI,
                                          tanggal_mulai__lte=last, tanggal_selesai__gte=first)
    leave_days = {}
    for lv in leaves:
        for d in lv.hari_kerja(first, last):
            leave_days[d] = lv.jenis
    joined = timezone.localtime(user.date_joined).date() if user.date_joined else first
    cal = []
    pad = first.weekday()
    cal.extend([None] * pad)
    d = first
    stats = {"hadir": 0, "terlambat": 0, "izin": 0, "alpa": 0}
    while d <= last:
        rec = month_records.get(d)
        state = ""
        if rec and rec.jam_masuk:
            state = rec.status
            stats["hadir"] += 1
            if rec.status == Absensi.TERLAMBAT:
                stats["terlambat"] += 1
        elif d in leave_days:
            state = leave_days[d]
            stats["izin"] += 1
        elif d.weekday() < 5 and joined <= d < today:
            state = "ALPA"
            stats["alpa"] += 1
        cal.append({"date": d, "rec": rec, "state": state, "weekend": d.weekday() >= 5, "today": d == today})
        d += timedelta(days=1)

    history = Absensi.objects.filter(user=user).order_by("-tanggal")[:10]
    office = _office()
    return render(request, "portal/attendance.html", {
        "record": record, "today": today, "hari": HARI[today.weekday()], "bulan": BULAN[today.month],
        "jam_masuk_kantor": jam_masuk_kantor(), "toleransi": _setting("ABSENSI_TOLERANSI_MENIT", 15),
        "require_photo": require_photo, "office": office, "calendar": cal, "stats": stats,
        "history": history, "day_names": [h[:3] for h in HARI],
        "pending_leave": PengajuanIzin.objects.filter(user=user, status=PengajuanIzin.MENUNGGU).count(),
    })


# ---------------------------------------------------------------- izin / cuti
class LeaveForm(forms.ModelForm):
    class Meta:
        model = PengajuanIzin
        fields = ["jenis", "tanggal_mulai", "tanggal_selesai", "alasan", "lampiran"]
        labels = {"jenis": "Jenis pengajuan", "tanggal_mulai": "Tanggal mulai",
                  "tanggal_selesai": "Tanggal selesai", "alasan": "Alasan / keterangan",
                  "lampiran": "Lampiran (opsional)"}
        help_texts = {"lampiran": "Surat dokter, undangan, dll. PDF/JPG/PNG maks. 5 MB."}
        widgets = {"tanggal_mulai": forms.DateInput(attrs={"type": "date"}),
                   "tanggal_selesai": forms.DateInput(attrs={"type": "date"}),
                   "alasan": forms.Textarea(attrs={"rows": 4})}

    def clean_lampiran(self):
        f = self.cleaned_data.get("lampiran")
        if f:
            if f.size > MAX_ATTACHMENT_BYTES:
                raise forms.ValidationError("Ukuran lampiran maksimal 5 MB.")
            ext = Path(f.name).suffix.lower()
            if ext not in {".pdf", ".jpg", ".jpeg", ".png"}:
                raise forms.ValidationError("Lampiran harus PDF, JPG, atau PNG.")
        return f

    def clean(self):
        data = super().clean()
        start, end = data.get("tanggal_mulai"), data.get("tanggal_selesai")
        if start and end:
            if end < start:
                self.add_error("tanggal_selesai", "Tanggal selesai tidak boleh sebelum tanggal mulai.")
            elif (end - start).days > 60:
                self.add_error("tanggal_selesai", "Rentang pengajuan maksimal 60 hari.")
        return data


@staff_required
def leave_list(request):
    approver = is_attendance_approver(request.user)
    status = request.GET.get("status", "")
    valid = {c[0] for c in PengajuanIzin.STATUS_CHOICES}
    scope = request.GET.get("lingkup", "semua" if approver else "saya")
    qs = PengajuanIzin.objects.select_related("user", "diputuskan_oleh")
    if not approver or scope == "saya":
        qs = qs.filter(user=request.user)
        scope = "saya"
    if status in valid:
        qs = qs.filter(status=status)
    else:
        status = ""
    pending_all = PengajuanIzin.objects.filter(status=PengajuanIzin.MENUNGGU).count() if approver else 0
    return render(request, "portal/leave_list.html", {
        "items": qs[:300], "approver": approver, "status": status, "scope": scope,
        "status_choices": PengajuanIzin.STATUS_CHOICES, "pending_all": pending_all,
    })


@staff_required
def leave_create(request):
    form = LeaveForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.user = request.user
        overlap = PengajuanIzin.objects.filter(
            user=request.user, status__in=[PengajuanIzin.MENUNGGU, PengajuanIzin.DISETUJUI],
            tanggal_mulai__lte=obj.tanggal_selesai, tanggal_selesai__gte=obj.tanggal_mulai).exists()
        if overlap:
            form.add_error(None, "Sudah ada pengajuan lain (menunggu/disetujui) pada rentang tanggal tersebut.")
        else:
            obj.save()
            messages.success(request, "Pengajuan terkirim dan menunggu persetujuan atasan.")
            return redirect("leave_list")
    return render(request, "portal/leave_form.html", {"form": form})


@require_POST
@staff_required
def leave_decide(request, pk, action):
    item = get_object_or_404(PengajuanIzin, pk=pk)
    if action == "batalkan":
        if item.user_id != request.user.id or item.status != PengajuanIzin.MENUNGGU:
            raise PermissionDenied
        item.status = PengajuanIzin.DIBATALKAN
        item.save(update_fields=["status"])
        messages.info(request, "Pengajuan dibatalkan.")
        return redirect("leave_list")
    if action not in {"setujui", "tolak"}:
        raise Http404
    if not is_attendance_approver(request.user):
        raise PermissionDenied
    if item.user_id == request.user.id and not request.user.is_superuser:
        messages.error(request, "Pengajuan sendiri harus disetujui atasan lain.")
        return redirect("leave_list")
    if item.status != PengajuanIzin.MENUNGGU:
        messages.warning(request, "Pengajuan ini sudah diputuskan.")
        return redirect("leave_list")
    item.status = PengajuanIzin.DISETUJUI if action == "setujui" else PengajuanIzin.DITOLAK
    item.diputuskan_oleh = request.user
    item.diputuskan_pada = timezone.now()
    item.catatan_keputusan = (request.POST.get("catatan") or "").strip()[:500]
    item.save()
    who = item.user.get_full_name() or item.user.username
    messages.success(request, f"Pengajuan {who} {'disetujui' if action == 'setujui' else 'ditolak'}.")
    return redirect(request.POST.get("next") or "leave_list")


# ---------------------------------------------------------------- rekap
def _build_report(y, m, user_filter=None):
    first, last = _month_bounds(y, m)
    today = timezone.localdate()
    cutoff = min(last, today)
    workdays = [first + timedelta(days=i) for i in range((last - first).days + 1)
                if (first + timedelta(days=i)).weekday() < 5]
    past_workdays = [d for d in workdays if d <= cutoff]
    User = get_user_model()
    users = User.objects.filter(is_active=True, is_staff=True).order_by("first_name", "username")
    if user_filter:
        users = users.filter(pk=user_filter)
    records = Absensi.objects.filter(tanggal__range=(first, last)).select_related("user")
    if user_filter:
        records = records.filter(user_id=user_filter)
    by_user = {}
    for r in records:
        by_user.setdefault(r.user_id, {})[r.tanggal] = r
    leaves = PengajuanIzin.objects.filter(status=PengajuanIzin.DISETUJUI,
                                          tanggal_mulai__lte=last, tanggal_selesai__gte=first)
    leave_map = {}
    for lv in leaves:
        for d in lv.hari_kerja(first, last):
            leave_map.setdefault(lv.user_id, {})[d] = lv.jenis
    rows = []
    for u in users:
        recs = by_user.get(u.id, {})
        lmap = leave_map.get(u.id, {})
        hadir = sum(1 for r in recs.values() if r.jam_masuk)
        late = sum(1 for r in recs.values() if r.jam_masuk and r.status == Absensi.TERLAMBAT)
        counts = {k: sum(1 for v in lmap.values() if v == k) for k in ("IZIN", "SAKIT", "CUTI", "DINAS_LUAR")}
        joined = timezone.localtime(u.date_joined).date() if u.date_joined else first
        alpa = sum(1 for d in past_workdays
                   if d >= joined and d not in lmap and not (recs.get(d) and recs[d].jam_masuk))
        eligible = [d for d in past_workdays if d >= joined]
        minutes = sum(int(r.durasi.total_seconds() // 60) for r in recs.values() if r.durasi)
        rows.append({"user": u, "name": u.get_full_name() or u.username, "hadir": hadir, "terlambat": late,
                     "izin": counts["IZIN"], "sakit": counts["SAKIT"], "cuti": counts["CUTI"],
                     "dinas": counts["DINAS_LUAR"], "alpa": alpa,
                     "jam_kerja": f"{minutes // 60} j {minutes % 60:02d} m",
                     "kehadiran": min(100, round(hadir * 100 / len(eligible))) if eligible else 0})
    return {"first": first, "last": last, "workdays": len(workdays), "past_workdays": len(past_workdays),
            "rows": rows, "records": records.order_by("-tanggal", "user__username")}


@approver_required
def attendance_report(request):
    y, m = _month_from_request(request)
    try:
        user_filter = int(request.GET.get("staf") or 0) or None
    except ValueError:
        user_filter = None
    report = _build_report(y, m, user_filter)
    py, pm = _shift_month(y, m, -1)
    ny, nm = _shift_month(y, m, 1)
    User = get_user_model()
    today = timezone.localdate()
    today_records = Absensi.objects.filter(tanggal=today).select_related("user")
    staff_total = User.objects.filter(is_active=True, is_staff=True).count()
    on_leave_today = PengajuanIzin.objects.filter(status=PengajuanIzin.DISETUJUI,
                                                  tanggal_mulai__lte=today, tanggal_selesai__gte=today).count()
    return render(request, "portal/attendance_report.html", {
        **report, "year": y, "month": m, "month_label": f"{BULAN[m]} {y}", "month_value": f"{y}-{m:02d}",
        "prev": f"{py}-{pm:02d}", "next": f"{ny}-{nm:02d}", "staff": User.objects.filter(
            is_active=True, is_staff=True).order_by("first_name", "username"),
        "staff_filter": user_filter, "today_records": today_records, "staff_total": staff_total,
        "today_present": sum(1 for r in today_records if r.jam_masuk),
        "today_late": sum(1 for r in today_records if r.status == Absensi.TERLAMBAT),
        "on_leave_today": on_leave_today,
        "pending_leave": PengajuanIzin.objects.filter(status=PengajuanIzin.MENUNGGU).count(),
    })


@approver_required
def attendance_export(request):
    y, m = _month_from_request(request)
    kind = request.GET.get("jenis", "rekap")
    try:
        user_filter = int(request.GET.get("staf") or 0) or None
    except ValueError:
        user_filter = None
    report = _build_report(y, m, user_filter)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"absensi_{kind}_{y}-{m:02d}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")
    writer = csv.writer(response, delimiter=";")
    if kind == "detail":
        writer.writerow(["Tanggal", "Hari", "Nama", "Username", "Jam masuk", "Jam pulang", "Durasi",
                         "Status", "Lokasi masuk", "Lokasi pulang", "Koordinat masuk", "Catatan"])
        for r in report["records"].order_by("tanggal", "user__username"):
            jm = timezone.localtime(r.jam_masuk).strftime("%H:%M") if r.jam_masuk else ""
            jp = timezone.localtime(r.jam_pulang).strftime("%H:%M") if r.jam_pulang else ""
            coord = f"{r.lat_masuk},{r.lng_masuk}" if r.lat_masuk is not None else ""
            writer.writerow([r.tanggal.strftime("%d/%m/%Y"), HARI[r.tanggal.weekday()],
                             r.user.get_full_name() or r.user.username, r.user.username, jm, jp,
                             r.durasi_teks, r.get_status_display(), r.lokasi_masuk, r.lokasi_pulang,
                             coord, r.catatan.replace("\n", " ")])
    else:
        writer.writerow([f"Rekap absensi {BULAN[m]} {y}", f"Hari kerja: {report['workdays']}",
                         f"Hari kerja berjalan: {report['past_workdays']}"])
        writer.writerow(["Nama", "Username", "Hadir", "Terlambat", "Izin", "Sakit", "Cuti",
                         "Dinas luar", "Tanpa keterangan", "Total jam kerja", "Kehadiran (%)"])
        for row in report["rows"]:
            writer.writerow([row["name"], row["user"].username, row["hadir"], row["terlambat"], row["izin"],
                             row["sakit"], row["cuti"], row["dinas"], row["alpa"], row["jam_kerja"],
                             row["kehadiran"]])
    return response


# ---------------------------------------------------------------- profil
@staff_required
def profile(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    for field in form.fields.values():
        field.widget.attrs.setdefault("autocomplete", "new-password")
    form.fields["old_password"].widget.attrs["autocomplete"] = "current-password"
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Kata sandi berhasil diganti.")
        return redirect("profile")
    today = timezone.localdate()
    first, last = _month_bounds(today.year, today.month)
    month_records = Absensi.objects.filter(user=request.user, tanggal__range=(first, last))
    return render(request, "portal/profile.html", {
        "form": form, "is_approver": is_attendance_approver(request.user),
        "month_label": f"{BULAN[today.month]} {today.year}",
        "month_present": sum(1 for r in month_records if r.jam_masuk),
        "month_late": sum(1 for r in month_records if r.status == Absensi.TERLAMBAT),
        "leave_pending": PengajuanIzin.objects.filter(user=request.user, status=PengajuanIzin.MENUNGGU).count(),
    })


# ---------------------------------------------------------------- media (hanya staf login)
@staff_required
def protected_media(request, path):
    from django.core.files.storage import FileSystemStorage, default_storage

    rel = path.replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        raise Http404
    if rel.startswith("absensi/") and not is_attendance_approver(request.user):
        owner = Absensi.objects.filter(user=request.user, foto_masuk=rel).exists() or \
            Absensi.objects.filter(user=request.user, foto_pulang=rel).exists()
        if not owner:
            raise PermissionDenied
    if rel.startswith("izin/") and not is_attendance_approver(request.user):
        if not PengajuanIzin.objects.filter(user=request.user, lampiran=rel).exists():
            raise PermissionDenied

    if not isinstance(default_storage, FileSystemStorage):
        # Penyimpanan cloud: arahkan ke URL bertanda tangan yang berlaku sementara
        if not default_storage.exists(rel):
            raise Http404
        return redirect(default_storage.url(rel))

    try:
        full = Path(safe_join(str(settings.MEDIA_ROOT), rel))
    except Exception:
        raise Http404
    if not full.is_file():
        raise Http404
    content_type, _ = mimetypes.guess_type(str(full))
    response = FileResponse(open(full, "rb"), content_type=content_type or "application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=3600"
    return response
