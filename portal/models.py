"""Model Django untuk fitur SDM (absensi & izin).

Modul bisnis lain (inventaris, quotation, invoice, rental, proyek) tetap memakai
tabel SQL di schema_awal.sql. Tabel di file ini dibuat otomatis oleh
`python manage.py migrate`.
"""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q


class Absensi(models.Model):
    HADIR = "HADIR"
    TERLAMBAT = "TERLAMBAT"
    STATUS_CHOICES = [(HADIR, "Hadir tepat waktu"), (TERLAMBAT, "Terlambat")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="absensi", verbose_name="Staf")
    tanggal = models.DateField()
    jam_masuk = models.DateTimeField(null=True, blank=True)
    jam_pulang = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=HADIR)

    lat_masuk = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    lng_masuk = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    akurasi_masuk = models.PositiveIntegerField(null=True, blank=True, help_text="meter")
    lokasi_masuk = models.CharField(max_length=200, blank=True)
    foto_masuk = models.FileField(upload_to="absensi/%Y/%m/", blank=True)
    ip_masuk = models.GenericIPAddressField(null=True, blank=True)

    lat_pulang = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    lng_pulang = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    akurasi_pulang = models.PositiveIntegerField(null=True, blank=True, help_text="meter")
    lokasi_pulang = models.CharField(max_length=200, blank=True)
    foto_pulang = models.FileField(upload_to="absensi/%Y/%m/", blank=True)
    ip_pulang = models.GenericIPAddressField(null=True, blank=True)

    catatan = models.TextField(blank=True)
    dibuat_pada = models.DateTimeField(auto_now_add=True)
    diubah_pada = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "absensi"
        ordering = ["-tanggal", "-jam_masuk"]
        verbose_name = "Absensi"
        verbose_name_plural = "Absensi"
        constraints = [
            models.UniqueConstraint(fields=["user", "tanggal"], name="absensi_satu_per_hari"),
            models.CheckConstraint(
                condition=Q(jam_pulang__isnull=True) | Q(jam_masuk__isnull=False),
                name="absensi_pulang_setelah_masuk",
            ),
        ]
        indexes = [models.Index(fields=["tanggal"], name="absensi_tanggal_idx")]

    def __str__(self):
        return f"{self.user} · {self.tanggal}"

    @property
    def durasi(self):
        if self.jam_masuk and self.jam_pulang:
            return self.jam_pulang - self.jam_masuk
        return None

    @property
    def durasi_teks(self):
        d = self.durasi
        if not d:
            return ""
        total = int(d.total_seconds() // 60)
        return f"{total // 60} j {total % 60:02d} m"


class PengajuanIzin(models.Model):
    IZIN, SAKIT, CUTI, DINAS_LUAR = "IZIN", "SAKIT", "CUTI", "DINAS_LUAR"
    JENIS_CHOICES = [(IZIN, "Izin"), (SAKIT, "Sakit"), (CUTI, "Cuti"), (DINAS_LUAR, "Dinas luar / lapangan")]
    MENUNGGU, DISETUJUI, DITOLAK, DIBATALKAN = "MENUNGGU", "DISETUJUI", "DITOLAK", "DIBATALKAN"
    STATUS_CHOICES = [(MENUNGGU, "Menunggu persetujuan"), (DISETUJUI, "Disetujui"),
                      (DITOLAK, "Ditolak"), (DIBATALKAN, "Dibatalkan")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="pengajuan_izin", verbose_name="Staf")
    jenis = models.CharField(max_length=20, choices=JENIS_CHOICES)
    tanggal_mulai = models.DateField()
    tanggal_selesai = models.DateField()
    alasan = models.TextField()
    lampiran = models.FileField(upload_to="izin/%Y/%m/", blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=MENUNGGU)
    diputuskan_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                        null=True, blank=True, related_name="+")
    diputuskan_pada = models.DateTimeField(null=True, blank=True)
    catatan_keputusan = models.TextField(blank=True)
    dibuat_pada = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pengajuan_izin"
        ordering = ["-dibuat_pada"]
        verbose_name = "Pengajuan izin/cuti"
        verbose_name_plural = "Pengajuan izin/cuti"
        constraints = [
            models.CheckConstraint(condition=Q(tanggal_selesai__gte=models.F("tanggal_mulai")),
                                   name="izin_rentang_tanggal_valid"),
        ]
        indexes = [models.Index(fields=["status"], name="izin_status_idx")]

    def __str__(self):
        return f"{self.user} · {self.get_jenis_display()} {self.tanggal_mulai}"

    @property
    def jumlah_hari(self):
        return (self.tanggal_selesai - self.tanggal_mulai).days + 1

    def hari_kerja(self, mulai=None, selesai=None):
        """Daftar tanggal hari kerja (Senin–Jumat) dalam pengajuan, opsional dipotong rentang."""
        start = max(self.tanggal_mulai, mulai) if mulai else self.tanggal_mulai
        end = min(self.tanggal_selesai, selesai) if selesai else self.tanggal_selesai
        days, d = [], start
        while d <= end:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        return days
