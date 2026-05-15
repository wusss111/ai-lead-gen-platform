"""Country/region → UTC offset with DST support for timezone-aware email sending.

Usage:
    from tools.country_timezone import get_utc_offset, local_hour_now

    offset = get_utc_offset("DE")      # → current effective UTC offset
    hour = local_hour_now(offset)      # → current local hour in Germany
"""

from __future__ import annotations

import datetime

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore[no-redef]

# Country code → primary IANA timezone ID (covers DST automatically)
COUNTRY_TO_TZ: dict[str, str] = {
    # North America
    "US": "America/New_York", "CA": "America/Toronto", "MX": "America/Mexico_City",
    # South America
    "BR": "America/Sao_Paulo", "AR": "America/Argentina/Buenos_Aires",
    "CL": "America/Santiago", "CO": "America/Bogota", "PE": "America/Lima",
    # Western Europe
    "GB": "Europe/London", "UK": "Europe/London", "IE": "Europe/Dublin",
    "PT": "Europe/Lisbon", "IS": "Atlantic/Reykjavik",
    "DE": "Europe/Berlin", "FR": "Europe/Paris", "IT": "Europe/Rome",
    "ES": "Europe/Madrid", "NL": "Europe/Amsterdam", "BE": "Europe/Brussels",
    "LU": "Europe/Luxembourg", "AT": "Europe/Vienna", "CH": "Europe/Zurich",
    "DK": "Europe/Copenhagen", "NO": "Europe/Oslo", "SE": "Europe/Stockholm",
    "FI": "Europe/Helsinki",
    # Central / Eastern Europe
    "PL": "Europe/Warsaw", "CZ": "Europe/Prague", "SK": "Europe/Bratislava",
    "HU": "Europe/Budapest", "SI": "Europe/Ljubljana", "HR": "Europe/Zagreb",
    "RO": "Europe/Bucharest", "BG": "Europe/Sofia", "GR": "Europe/Athens",
    "LT": "Europe/Vilnius", "LV": "Europe/Riga", "EE": "Europe/Tallinn",
    "UA": "Europe/Kyiv",
    # Middle East
    "TR": "Europe/Istanbul", "SA": "Asia/Riyadh", "KW": "Asia/Kuwait",
    "QA": "Asia/Qatar", "AE": "Asia/Dubai", "OM": "Asia/Muscat",
    "IL": "Asia/Jerusalem", "IR": "Asia/Tehran",
    # South Asia
    "IN": "Asia/Kolkata", "PK": "Asia/Karachi", "BD": "Asia/Dhaka",
    # East / Southeast Asia
    "CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "TW": "Asia/Taipei",
    "SG": "Asia/Singapore", "MY": "Asia/Kuala_Lumpur", "PH": "Asia/Manila",
    "TH": "Asia/Bangkok", "VN": "Asia/Ho_Chi_Minh", "ID": "Asia/Jakarta",
    "KH": "Asia/Phnom_Penh",
    "JP": "Asia/Tokyo", "KR": "Asia/Seoul",
    # Oceania
    "AU": "Australia/Sydney", "NZ": "Pacific/Auckland",
    # Africa
    "ZA": "Africa/Johannesburg", "EG": "Africa/Cairo", "KE": "Africa/Nairobi",
    "NG": "Africa/Lagos", "MA": "Africa/Casablanca", "ET": "Africa/Addis_Ababa",
    # Russia
    "RU": "Europe/Moscow",
}

# Fallback: country full names → IANA timezone ID
_NAME_TO_TZ: dict[str, str] = {
    "UNITED STATES": "America/New_York", "USA": "America/New_York",
    "CANADA": "America/Toronto", "MEXICO": "America/Mexico_City",
    "BRAZIL": "America/Sao_Paulo", "ARGENTINA": "America/Argentina/Buenos_Aires",
    "CHILE": "America/Santiago",
    "UNITED KINGDOM": "Europe/London", "UK": "Europe/London",
    "IRELAND": "Europe/Dublin", "PORTUGAL": "Europe/Lisbon",
    "GERMANY": "Europe/Berlin", "DEUTSCHLAND": "Europe/Berlin",
    "FRANCE": "Europe/Paris", "ITALY": "Europe/Rome", "SPAIN": "Europe/Madrid",
    "NETHERLANDS": "Europe/Amsterdam", "BELGIUM": "Europe/Brussels",
    "AUSTRIA": "Europe/Vienna", "SWITZERLAND": "Europe/Zurich",
    "SWEDEN": "Europe/Stockholm", "NORWAY": "Europe/Oslo",
    "DENMARK": "Europe/Copenhagen", "FINLAND": "Europe/Helsinki",
    "POLAND": "Europe/Warsaw", "CZECH": "Europe/Prague", "HUNGARY": "Europe/Budapest",
    "RUSSIA": "Europe/Moscow", "TURKEY": "Europe/Istanbul",
    "CHINA": "Asia/Shanghai", "HONG KONG": "Asia/Hong_Kong",
    "TAIWAN": "Asia/Taipei", "SINGAPORE": "Asia/Singapore",
    "MALAYSIA": "Asia/Kuala_Lumpur", "PHILIPPINES": "Asia/Manila",
    "THAILAND": "Asia/Bangkok", "VIETNAM": "Asia/Ho_Chi_Minh",
    "JAPAN": "Asia/Tokyo", "KOREA": "Asia/Seoul", "SOUTH KOREA": "Asia/Seoul",
    "INDIA": "Asia/Kolkata", "PAKISTAN": "Asia/Karachi",
    "UAE": "Asia/Dubai", "SAUDI ARABIA": "Asia/Riyadh", "ISRAEL": "Asia/Jerusalem",
    "AUSTRALIA": "Australia/Sydney", "NEW ZEALAND": "Pacific/Auckland",
    "SOUTH AFRICA": "Africa/Johannesburg", "EGYPT": "Africa/Cairo",
    "NIGERIA": "Africa/Lagos", "KENYA": "Africa/Nairobi",
}


def get_utc_offset(country: str) -> float | None:
    """Map a country code or name to its current UTC offset in hours (DST-aware).

    Returns None if the country cannot be mapped to a timezone.
    """
    if not country:
        return None
    s = str(country).strip().upper()

    # Try country code → IANA timezone
    tz_name = None
    if len(s) == 2:
        tz_name = COUNTRY_TO_TZ.get(s)
    if tz_name is None and len(s) == 3:
        tz_name = COUNTRY_TO_TZ.get(s)
    if tz_name is None:
        for name, tz in _NAME_TO_TZ.items():
            if name in s:
                tz_name = tz
                break

    if tz_name is None:
        return None

    try:
        tz = ZoneInfo(tz_name)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        return now_utc.astimezone(tz).utcoffset().total_seconds() / 3600.0  # type: ignore[union-attr]
    except (ZoneInfoNotFoundError, Exception):
        return None


def local_hour_now(utc_offset: float) -> int:
    """Return the current local hour (0-23) for the given UTC offset."""
    return (datetime.datetime.now(datetime.timezone.utc).hour + int(utc_offset)) % 24
