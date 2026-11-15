"""Buat (atau reset) akun super admin Seanav Operations dengan password acak.

Jalankan lewat "Buat Super Admin.bat" di folder Database.
"""
import os
import secrets
import string
import sys

import django

USERNAME = "admin"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "seanav_web.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402


def generate_password(length=12):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    while True:
        body = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.isupper() for c in body) and any(c.islower() for c in body)
                and any(c.isdigit() for c in body)):
            return "Snv@" + body


User = get_user_model()
password = generate_password()
user, created = User.objects.get_or_create(username=USERNAME)
user.is_staff = True
user.is_superuser = True
user.is_active = True
user.set_password(password)
user.save()

print()
print("  ==============================================================")
print("   AKUN SUPER ADMIN " + ("DIBUAT" if created else "DIPERBARUI (password baru)"))
print("  ==============================================================")
print()
print("   Username : " + USERNAME)
print("   Password : " + password)
print()
print("   Login di : http://127.0.0.1:8000/login/")
print()
print("   Catat password ini sekarang. Password tidak disimpan di file mana pun.")
print("   Menjalankan file ini lagi akan membuat password baru.")
print("  ==============================================================")
