from pathlib import Path
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.forms import BaseFormSet, formset_factory


EQUIPMENT_CATEGORIES = ("Geodetic", "Geophysic", "Metocean")


class EquipmentForm(forms.Form):
    category = forms.CharField(
        label="Kategori", max_length=120,
    )
    brand = forms.CharField(label="Merek", max_length=120, required=False)
    model_name = forms.CharField(label="Nama alat / model", max_length=160)
    serial_number = forms.CharField(label="Serial number", max_length=120, required=False)
    om_sn = forms.CharField(label="OMSN", max_length=120, required=False)
    location = forms.CharField(label="Lokasi", max_length=160, required=False)
    condition = forms.ChoiceField(
        label="Kondisi",
        choices=[("BAIK", "Baik"), ("PERLU_PEMERIKSAAN", "Perlu pemeriksaan"), ("RUSAK", "Rusak")],
    )
    status = forms.ChoiceField(
        label="Status",
        choices=[("TERSEDIA", "Tersedia"), ("DISEWA", "Disewa"), ("DI_PROYEK", "Di proyek"), ("PERAWATAN", "Perawatan")],
    )
    photo = forms.FileField(label="Foto alat", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"] = forms.ChoiceField(
            label="Kategori",
            choices=[("", "Pilih kategori...")] + [(item, item) for item in EQUIPMENT_CATEGORIES],
        )

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if not photo:
            return photo
        allowed_extensions = {".jpg", ".jpeg", ".png", ".webp"}
        if Path(photo.name).suffix.lower() not in allowed_extensions:
            raise forms.ValidationError("Gunakan foto JPG, PNG, atau WEBP.")
        if photo.size > 8 * 1024 * 1024:
            raise forms.ValidationError("Ukuran foto maksimal 8 MB.")
        return photo


class AccessoryForm(forms.Form):
    name = forms.CharField(label="Jenis aksesoris", max_length=180, required=False)
    serial_number = forms.CharField(label="Serial number (opsional)", max_length=120, required=False)
    quantity = forms.DecimalField(
        label="Jumlah", required=False, min_value=Decimal("0.01"),
        max_digits=12, decimal_places=2, initial=1,
    )
    photo = forms.FileField(
        label="Foto aksesori", required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/*", "capture": "environment"}),
    )
    existing_photo = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if not photo:
            return photo
        if Path(photo.name).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise forms.ValidationError("Gunakan foto JPG, PNG, atau WEBP.")
        if photo.size > 8 * 1024 * 1024:
            raise forms.ValidationError("Ukuran foto maksimal 8 MB.")
        return photo

    def clean_existing_photo(self):
        path = (self.cleaned_data.get("existing_photo") or "").strip()
        if path and (not path.startswith("inventory/") or ".." in path or path.startswith("/")):
            return ""
        return path

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get("name") or "").strip()
        serial_number = (cleaned.get("serial_number") or "").strip()
        quantity = cleaned.get("quantity")
        if name and quantity is None:
            cleaned["quantity"] = Decimal("1")
        elif not name and quantity not in (None, Decimal("1")):
            self.add_error("name", "Isi jenis aksesoris jika jumlah diisi.")
        if serial_number and not name:
            self.add_error("name", "Isi jenis aksesoris jika nomor seri diisi.")
        cleaned["name"] = name
        cleaned["serial_number"] = serial_number
        return cleaned


class AccessoryFormSetBase(BaseFormSet):
    def clean(self):
        super().clean()
        count = sum(
            1 for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("name")
        )
        if count > 50:
            raise forms.ValidationError("Maksimal 50 jenis aksesoris untuk satu unit.")


AccessoryFormSet = formset_factory(
    AccessoryForm, formset=AccessoryFormSetBase, extra=1,
    can_delete=True, max_num=50, validate_max=True,
)


class RentalHandoverForm(forms.Form):
    kind = forms.ChoiceField(
        label="Jenis serah-terima",
        choices=[("KELUAR", "Alat keluar ke klien"), ("KEMBALI", "Alat kembali ke kantor")],
    )
    rental_item_id = forms.ChoiceField(
        label="Transaksi / unit rental",
        choices=(),
        help_text="Pilihan menampilkan status unit saat ini. Untuk pengembalian, pilih unit yang sedang disewa.",
    )
    internal_user_id = forms.ChoiceField(label="PIC internal", choices=())
    recipient_name = forms.CharField(label="Nama penerima / pengembali", max_length=180, required=False)
    condition = forms.ChoiceField(
        label="Kondisi alat",
        choices=[("BAIK", "Baik"), ("PERLU_PEMERIKSAAN", "Perlu pemeriksaan"), ("RUSAK", "Rusak")],
    )
    notes = forms.CharField(label="Catatan", widget=forms.Textarea, required=False)

    def __init__(self, *args, **kwargs):
        rental_id = kwargs.pop("rental_id", None)
        return_only = kwargs.pop("return_only", False)
        super().__init__(*args, **kwargs)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ri.id, r.id, r.nomor_rental, kl.nama_perusahaan,
                       concat_ws(' ', m.merek, m.nama_model), u.nomor_seri,
                       CASE WHEN EXISTS (
                           SELECT 1
                           FROM serah_terima_rental_item out_item
                           JOIN serah_terima_rental out_doc
                             ON out_doc.id = out_item.serah_terima_id
                           WHERE out_item.rental_item_id = ri.id
                             AND COALESCE(out_item.unit_alat_id, u.id) = u.id
                             AND out_doc.jenis = 'KELUAR'
                             AND NOT EXISTS (
                                 SELECT 1
                                 FROM serah_terima_rental_item in_item
                                 JOIN serah_terima_rental in_doc
                                   ON in_doc.id = in_item.serah_terima_id
                                 WHERE in_item.rental_item_id = ri.id
                                   AND COALESCE(in_item.unit_alat_id, u.id) = u.id
                                   AND in_doc.jenis = 'KEMBALI'
                                   AND in_doc.tanggal > out_doc.tanggal
                             )
                       ) THEN 'SEDANG DISEWA' ELSE u.status END AS status_tampil
                FROM rental_item ri
                JOIN rental r ON r.id = ri.rental_id
                JOIN klien kl ON kl.id = r.klien_id
                JOIN unit_alat u ON u.id = ri.unit_alat_id
                JOIN model_alat m ON m.id = u.model_alat_id
                ORDER BY r.nomor_rental, m.nama_model
                """
            )
            item_rows = cursor.fetchall()
            if rental_id:
                item_rows = [row for row in item_rows if str(row[1]) == str(rental_id)]
            if return_only:
                item_rows = [row for row in item_rows if row[6] == "SEDANG DISEWA"]
            self.fields["rental_item_id"].choices = [
                (row[0], f"{row[2]} · {row[3]} · {row[4]} · S/N {row[5] or '-'} · {row[6]}")
                for row in item_rows
            ]
            if item_rows:
                self.fields["rental_item_id"].initial = str(item_rows[0][0])
            if return_only:
                self.fields["kind"].initial = "KEMBALI"
            cursor.execute("SELECT id, nama FROM pengguna WHERE aktif ORDER BY nama")
            self.fields["internal_user_id"].choices = [
                (row[0], row[1]) for row in cursor.fetchall()
            ]


class RentalCreateForm(forms.Form):
    quotation_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    company_id = forms.ChoiceField(label="Perusahaan transaksi", choices=())
    rental_number = forms.CharField(label="Nomor rental", max_length=80)
    client_name = forms.CharField(
        label="Nama perusahaan klien", max_length=200,
        help_text="Isi nama resmi perusahaan klien; nama PIC diisi pada kolom terpisah.",
    )
    quotation_reference = forms.CharField(
        label="Referensi quotation (opsional)", max_length=80, required=False
    )
    location_name = forms.CharField(
        label="Lokasi penggunaan alat", max_length=500, required=False,
        help_text="Cari nama perusahaan, gedung, kota, atau area lalu pilih hasilnya di peta.",
    )
    latitude = forms.DecimalField(
        required=False, max_digits=10, decimal_places=7,
        min_value=Decimal("-90"), max_value=Decimal("90"),
        widget=forms.HiddenInput(attrs={"step": "0.0000001"}),
    )
    longitude = forms.DecimalField(
        required=False, max_digits=10, decimal_places=7,
        min_value=Decimal("-180"), max_value=Decimal("180"),
        widget=forms.HiddenInput(attrs={"step": "0.0000001"}),
    )
    rental_start = forms.DateField(label="Tanggal mulai", widget=forms.DateInput(attrs={"type": "date"}))
    rental_end = forms.DateField(label="Tanggal selesai rencana", widget=forms.DateInput(attrs={"type": "date"}))
    unit_ids = forms.MultipleChoiceField(
        label="Unit alat (pilih satu atau lebih)",
        required=False,
        choices=(),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "unit-checkboxes"}),
        help_text="Daftar ini menampilkan unit tersedia dari pool bersama kedua perusahaan.",
    )
    pic_name = forms.CharField(label="Nama PIC internal", max_length=160)
    pic_email = forms.EmailField(label="Email PIC internal", max_length=254)
    recipient_name = forms.CharField(label="Nama penerima alat", max_length=180, required=False)
    delivery_order = forms.CharField(label="Nomor delivery order (opsional)", max_length=100, required=False)
    condition = forms.ChoiceField(
        label="Kondisi saat keluar",
        choices=[("BAIK", "Baik"), ("PERLU_PEMERIKSAAN", "Perlu pemeriksaan"), ("RUSAK", "Rusak")],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, kode, nama_resmi FROM perusahaan WHERE aktif ORDER BY kode")
            self.fields["company_id"].choices = [
                (row[0], f"{row[1]} · {row[2]}") for row in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT u.id, k.nama, m.merek, m.nama_model, u.nomor_seri,
                       u.kode_inventaris, l.nama
                FROM unit_alat u
                JOIN model_alat m ON m.id = u.model_alat_id
                JOIN kategori_alat k ON k.id = m.kategori_alat_id
                LEFT JOIN lokasi l ON l.id = u.lokasi_id
                WHERE u.status = 'TERSEDIA'
                ORDER BY k.nama, m.merek, m.nama_model
                """
            )
            self.fields["unit_ids"].choices = [
                (row[0], f"{row[1]} / {row[2] or ''} {row[3]} / S/N {row[4] or '-'} / {row[5] or 'Tanpa kode'} / {row[6] or 'Lokasi belum diatur'}")
                for row in cursor.fetchall()
            ]

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("rental_start")
        end = cleaned.get("rental_end")
        if (cleaned.get("latitude") is None) != (cleaned.get("longitude") is None):
            self.add_error("location_name", "Pilih titik lokasi di peta atau kosongkan koordinatnya.")
        if start and end and end < start:
            self.add_error("rental_end", "Tanggal selesai harus sama atau setelah tanggal mulai.")
        return cleaned


class RentalLocationForm(forms.Form):
    location_name = forms.CharField(
        label="Lokasi penggunaan alat", max_length=500, required=False,
        help_text="Cari nama perusahaan, gedung, kota, atau area lalu pilih hasilnya di peta.",
    )
    latitude = forms.DecimalField(
        required=False, max_digits=10, decimal_places=7,
        min_value=Decimal("-90"), max_value=Decimal("90"),
        widget=forms.HiddenInput(attrs={"step": "0.0000001"}),
    )
    longitude = forms.DecimalField(
        required=False, max_digits=10, decimal_places=7,
        min_value=Decimal("-180"), max_value=Decimal("180"),
        widget=forms.HiddenInput(attrs={"step": "0.0000001"}),
    )

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("latitude") is None) != (cleaned.get("longitude") is None):
            self.add_error("location_name", "Pilih titik lokasi di peta atau kosongkan koordinatnya.")
        return cleaned


class RoleAssignmentForm(forms.Form):
    staff_user_id = forms.ChoiceField(label="Akun staf Django", choices=())
    role_code = forms.ChoiceField(label="Peran", choices=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        staff_users = user_model.objects.filter(is_active=True, is_staff=True).exclude(email="").order_by("email")
        self.fields["staff_user_id"].choices = [
            (user.pk, f"{user.get_full_name() or user.username} · {user.email}")
            for user in staff_users
        ]
        with connection.cursor() as cursor:
            cursor.execute("SELECT kode, nama FROM peran ORDER BY nama")
            self.fields["role_code"].choices = [(row[0], row[1]) for row in cursor.fetchall()]


QUOTATION_KINDS = (
    ("RENTAL", "Rental alat"),
    ("PENJUALAN", "Penjualan produk"),
    ("JASA_SURVEY", "Jasa survey"),
)


class QuotationForm(forms.Form):
    kind = forms.ChoiceField(
        label="Jenis penawaran", choices=QUOTATION_KINDS, initial="RENTAL", required=False,
        widget=forms.RadioSelect(attrs={"class": "kind-radio"}),
    )
    company_id = forms.ChoiceField(label="Perusahaan penawar", choices=())
    number = forms.CharField(label="Nomor quotation", max_length=80)
    representative_id = forms.ChoiceField(
        label="Prepared by (karyawan)", choices=(),
        help_text="Nama dan email karyawan ini akan dicantumkan pada quotation.",
    )
    client_name = forms.CharField(label="Nama klien", max_length=200)
    client_address = forms.CharField(label="Alamat klien", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    client_contact = forms.CharField(label="Nama PIC klien", max_length=160, required=False)
    client_email = forms.EmailField(label="Email klien", max_length=254, required=False)
    client_phone = forms.CharField(label="Telepon klien", max_length=50, required=False)
    apply_tax = forms.BooleanField(
        label="Kenakan PPN 11%", required=False,
        help_text="Jika dipilih, nominal PPN dan total setelah pajak dihitung otomatis.",
        widget=forms.CheckboxInput(attrs={"class": "tax-checkbox"}),
    )
    date = forms.DateField(label="Tanggal quotation", widget=forms.DateInput(attrs={"type": "date"}))
    valid_until = forms.DateField(
        label="Berlaku sampai", required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    rental_start = forms.DateField(
        label="Rencana mulai (rental / survey)", required=False,
        help_text="Opsional. Bila kosong, jadwal rental dihitung dari tanggal quotation.",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    notes = forms.CharField(label="Catatan", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        user_email = kwargs.pop("user_email", "")
        super().__init__(*args, **kwargs)
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, kode, nama_resmi FROM perusahaan WHERE aktif ORDER BY kode")
            self.fields["company_id"].choices = [
                (row[0], f"{row[1]} · {row[2]}") for row in cursor.fetchall()
            ]
            cursor.execute("SELECT id, nama, email FROM pengguna WHERE aktif ORDER BY nama")
            representatives = cursor.fetchall()
            self.fields["representative_id"].choices = [
                (row[0], f"{row[1]} · {row[2]}") for row in representatives
            ]
            matching_user = next(
                (str(row[0]) for row in representatives
                 if user_email and row[2].strip().lower() == user_email.strip().lower()),
                None,
            )
            if matching_user:
                self.fields["representative_id"].initial = matching_user

    def clean(self):
        cleaned = super().clean()
        valid_until = cleaned.get("valid_until")
        quote_date = cleaned.get("date")
        start = cleaned.get("rental_start")
        if quote_date and valid_until and valid_until < quote_date:
            self.add_error("valid_until", "Tanggal berlaku tidak boleh sebelum tanggal quotation.")
        if not cleaned.get("kind"):
            cleaned["kind"] = "RENTAL"
        return cleaned


class InvoiceForm(forms.Form):
    number = forms.CharField(label="Nomor invoice", max_length=100)
    date = forms.DateField(label="Tanggal invoice", widget=forms.DateInput(attrs={"type": "date"}))
    due_date = forms.DateField(
        label="Jatuh tempo", required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    po_number = forms.CharField(label="Nomor PO klien", max_length=100, required=False)
    payment_terms = forms.CharField(
        label="Syarat pembayaran", required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Syarat akan mengikuti template perusahaan jika kolom ini dikosongkan.",
    )
    notes = forms.CharField(label="Catatan invoice", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    termin_label = forms.CharField(label="Keterangan termin", max_length=160, required=False,
                                   help_text="Contoh: Down payment, Termin 2 – progres 50%, Pelunasan setelah BAST.")
    termin_percent = forms.DecimalField(label="Persentase termin (%)", required=False, min_value=Decimal("0.01"),
                                        max_value=Decimal("100"), max_digits=6, decimal_places=2)

    def __init__(self, *args, termin=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.termin = termin
        if termin:
            self.fields["termin_percent"].required = True
        else:
            del self.fields["termin_label"]
            del self.fields["termin_percent"]

    def clean(self):
        cleaned = super().clean()
        invoice_date = cleaned.get("date")
        due_date = cleaned.get("due_date")
        if invoice_date and due_date and due_date < invoice_date:
            self.add_error("due_date", "Jatuh tempo tidak boleh sebelum tanggal invoice.")
        return cleaned


class ProjectForm(forms.Form):
    company_id = forms.ChoiceField(label="Perusahaan", choices=())
    code = forms.CharField(label="Kode proyek", max_length=80)
    name = forms.CharField(label="Nama proyek", max_length=200)
    client_name = forms.CharField(label="Nama klien", max_length=200)
    service = forms.CharField(label="Jenis layanan", max_length=100)
    location_text = forms.CharField(
        label="Lokasi / area proyek", max_length=500,
        help_text="Tulis nama lokasi atau alamat supaya titik di peta mudah dikenali.",
    )
    latitude = forms.DecimalField(
        label="Latitude", required=False, max_digits=10, decimal_places=7,
        min_value=Decimal("-90"), max_value=Decimal("90"),
        widget=forms.NumberInput(attrs={"step": "0.0000001", "inputmode": "decimal"}),
    )
    longitude = forms.DecimalField(
        label="Longitude", required=False, max_digits=10, decimal_places=7,
        min_value=Decimal("-180"), max_value=Decimal("180"),
        widget=forms.NumberInput(attrs={"step": "0.0000001", "inputmode": "decimal"}),
    )
    start = forms.DateField(
        label="Rencana mulai", required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end = forms.DateField(
        label="Rencana selesai", required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    status = forms.ChoiceField(
        label="Status", choices=[
            ("DIRENCANAKAN", "Direncanakan"), ("MOBILISASI", "Mobilisasi"), ("BERJALAN", "Berjalan"),
            ("PENGOLAHAN_DATA", "Pengolahan data"), ("SELESAI", "Selesai"), ("DIBATALKAN", "Dibatalkan"),
            ("DIARSIPKAN", "Diarsipkan"),
        ],
    )
    rented_unit_ids = forms.MultipleChoiceField(
        label="Alat yang dipakai di proyek (dari Daftar Alat Rental)", required=False, choices=(),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "rented-unit-options"}),
        help_text="Alat terpilih berstatus 'Di proyek' sehingga tidak bisa disewakan sampai proyek selesai.",
    )
    notes = forms.CharField(
        label="Catatan", required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        project_id = kwargs.pop("project_id", None)
        super().__init__(*args, **kwargs)
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, kode, nama_resmi FROM perusahaan WHERE aktif ORDER BY kode")
            self.fields["company_id"].choices = [
                (str(row[0]), f"{row[1]} - {row[2]}") for row in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT u.id, u.nomor_seri, m.merek, m.nama_model, k.nama, u.status
                FROM unit_alat u
                JOIN model_alat m ON m.id = u.model_alat_id
                JOIN kategori_alat k ON k.id = m.kategori_alat_id
                WHERE u.modul = 'RENTAL'
                  AND (u.status = 'TERSEDIA'
                       OR (%s IS NOT NULL AND EXISTS (
                           SELECT 1 FROM proyek_unit pu WHERE pu.unit_alat_id = u.id AND pu.proyek_id = %s)))
                ORDER BY k.nama, m.merek, m.nama_model, u.nomor_seri
                """,
                [project_id, project_id],
            )
            self.fields["rented_unit_ids"].choices = [
                (str(row[0]), " · ".join(part for part in (
                    " ".join(part for part in (row[2], row[3]) if part), f"S/N {row[1] or '-'}", row[4],
                    "Tersedia" if row[5] == "TERSEDIA" else "Dipakai proyek ini",
                ) if part))
                for row in cursor.fetchall()
            ]

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("latitude") is None) != (cleaned.get("longitude") is None):
            self.add_error("latitude", "Isi latitude dan longitude bersama-sama, atau tandai lokasinya pada peta.")
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            self.add_error("end", "Tanggal selesai harus sama atau setelah tanggal mulai.")
        return cleaned


class QuotationItemForm(forms.Form):
    equipment_id = forms.ChoiceField(label="Pilih alat inventaris", required=False, choices=())
    description = forms.CharField(label="Deskripsi item", max_length=500, required=False)
    quantity = forms.DecimalField(
        label="Jumlah unit", required=False, min_value=Decimal("0.01"),
        max_digits=12, decimal_places=2,
    )
    days = forms.IntegerField(
        label="Jumlah hari", required=False, min_value=1, max_value=3650,
        help_text="Wajib untuk alat rental dari daftar alat; untuk item manual boleh dikosongkan.",
    )
    unit = forms.CharField(label="Satuan", max_length=40, required=False, initial="unit")
    unit_price = forms.DecimalField(
        label="Harga satuan (Rp)", required=False,
        min_value=Decimal("0"), max_digits=18, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )

    def clean(self):
        cleaned = super().clean()
        equipment_id = cleaned.get("equipment_id")
        description = (cleaned.get("description") or "").strip()
        if equipment_id:
            if cleaned.get("quantity") is None:
                self.add_error("quantity", "Isi jumlah unit alat.")
            if cleaned.get("unit_price") is None:
                self.add_error("unit_price", "Isi harga khusus untuk quotation ini.")
            cleaned["unit"] = "unit"
        elif description:
            # Item manual: cukup deskripsi. Jumlah kosong = 1, harga kosong = 0 (mis. termasuk / menyusul),
            # jumlah hari boleh kosong.
            if cleaned.get("quantity") is None:
                cleaned["quantity"] = Decimal("1")
            if cleaned.get("unit_price") is None:
                cleaned["unit_price"] = Decimal("0")
            if not cleaned.get("unit"):
                cleaned["unit"] = "unit"
            cleaned["manual"] = True
        elif any(cleaned.get(name) not in (None, "") for name in ("quantity", "days", "unit_price")):
            self.add_error("description", "Isi deskripsi item.")
        cleaned["description"] = description
        return cleaned


class QuotationItemFormSetBase(BaseFormSet):
    def __init__(self, *args, catalog_kind="RENTAL", **kwargs):
        super().__init__(*args, **kwargs)
        self.catalog_kind = catalog_kind
        self.equipment_catalog = []
        self.catalog_error = False
        try:
            with connection.cursor() as cursor:
                if catalog_kind == "PENJUALAN":
                    cursor.execute(
                        """
                        SELECT p.id, k.nama, concat_ws(' ', p.merek, p.nama), p.harga_jual, p.satuan
                        FROM produk_jual p
                        JOIN kategori_alat k ON k.id = p.kategori_alat_id
                        WHERE p.aktif
                        ORDER BY k.nama, p.merek, p.nama
                        """
                    )
                    self.equipment_catalog = [
                        {"id": row[0], "category": row[1], "name": row[2],
                         "price": str(row[3] or 0), "unit": row[4] or "unit"}
                        for row in cursor.fetchall()
                    ]
                elif catalog_kind == "RENTAL":
                    cursor.execute(
                        """
                        SELECT m.id, k.nama, concat_ws(' ', m.merek, m.nama_model)
                        FROM model_alat m
                        JOIN kategori_alat k ON k.id = m.kategori_alat_id
                        WHERE m.aktif AND EXISTS (
                            SELECT 1 FROM unit_alat u WHERE u.model_alat_id = m.id
                              AND u.modul = 'RENTAL' AND u.status NOT IN ('DIARSIPKAN', 'TERJUAL'))
                        ORDER BY k.nama, m.merek, m.nama_model
                        """
                    )
                    self.equipment_catalog = [
                        {"id": row[0], "category": row[1], "name": row[2]}
                        for row in cursor.fetchall()
                    ]
        except DatabaseError:
            self.equipment_catalog = []
            self.catalog_error = True
        placeholder = {"PENJUALAN": "Pilih produk dari katalog atau isi item manual...",
                       "JASA_SURVEY": "Item jasa survey (isi manual)..."}.get(catalog_kind, "Pilih alat atau isi item manual...")
        choices = [("", placeholder)] + [
            (item["id"], f'{item["category"]} · {item["name"]}')
            for item in self.equipment_catalog
        ]
        for form in self.forms:
            form.fields["equipment_id"].choices = choices

    def clean(self):
        super().clean()
        if any(form.errors for form in self.forms):
            return
        if not any(
            (form.cleaned_data.get("description") or form.cleaned_data.get("equipment_id"))
            and not form.cleaned_data.get("DELETE")
            for form in self.forms
        ):
            raise forms.ValidationError("Tambahkan setidaknya satu item quotation.")
        if len(self.forms) > 30:
            raise forms.ValidationError("Maksimal 30 item untuk satu quotation.")


QuotationItemFormSet = formset_factory(
    QuotationItemForm, formset=QuotationItemFormSetBase,
    extra=1, can_delete=True, max_num=30, validate_max=True,
)
