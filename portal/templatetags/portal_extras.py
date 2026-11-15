from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def label(value):
    """DITERIMA_KLIEN -> Diterima Klien"""
    if value is None:
        return ""
    return str(value).replace("_", " ").title()


@register.filter
def rupiah(value, digits=0):
    """1234567.5 -> Rp 1.234.568"""
    if value in (None, ""):
        return "Rp 0"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    text = f"{number:,.{int(digits)}f}"
    return "Rp " + text.replace(",", "#").replace(".", ",").replace("#", ".")


@register.filter
def initial(value):
    return (str(value or "?").strip()[:1] or "?").upper()
