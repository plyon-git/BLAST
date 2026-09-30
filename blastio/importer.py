"""Bounded CSV review and transactional imports. Uploaded data never proves consent.

Contacts are identified by canonical E.164 telephone numbers. Properties have
separate stable identities, and suppression is always retained by telephone
number even when contact records are recreated.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import phonenumbers

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLUMNS = 100
MAX_CELL_CHARS = 16_384
MAX_CUSTOM_FIELDS = 50
IMPORT_METADATA_FIELDS = frozenset({"claimed_timezone_source", "timezone_evidence_ref", "csv_consent_claim", "csv_consent_status_claim"})

ALIASES: dict[str, tuple[str, ...]] = {
    "phone": ("phone", "phone number", "telephone", "mobile", "cell", "cell phone", "mobile phone"),
    "first_name": ("first_name", "first name", "firstname", "given name"),
    "last_name": ("last_name", "last name", "lastname", "surname"),
    "street_address": ("street_address", "street address", "property address", "address", "street"),
    "city": ("city", "property city"),
    "state": ("state", "province", "property state"),
    "zip": ("zip", "zip code", "zipcode", "postal code", "property zip"),
    "property_id": ("property_id", "property id", "parcel", "parcel id", "apn"),
    "custom_fields": ("custom_fields", "custom fields", "custom json"),
    "timezone": ("timezone", "time zone", "recipient timezone"),
    "timezone_source": ("timezone_source", "timezone source", "recipient timezone source"),
    "timezone_evidence_ref": ("timezone_evidence_ref", "timezone evidence reference", "timezone evidence"),
    "consent": ("consent", "sms consent", "consent provided"),
    "consent_business": ("consent_business", "consent business", "business identity"),
    "consent_channel": ("consent_channel", "consent channel"),
    "consent_purpose": ("consent_purpose", "consent purpose"),
    "consent_source": ("consent_source", "consent source"),
    "consent_occurred_at": ("consent_occurred_at", "consent timestamp", "consent date", "consent_at"),
    "consent_disclosure_version": ("consent_disclosure_version", "disclosure version"),
    "consent_evidence_ref": ("consent_evidence_ref", "consent evidence", "evidence reference", "evidence ref"),
    "consent_status": ("consent_status", "consent status"),
    "opted_out": ("opted_out", "opted out", "opt out", "unsubscribed", "do not contact", "dnc"),
}

# These are evidence claims, not automatically verified facts. Review remains
# necessary for consent; address/area-code inference is explicitly excluded.
TIMEZONE_SOURCES = frozenset({
    "recipient_confirmed", "recipient_reported", "recipient_confirmation",
    "documented_recipient_confirmation", "consent_form", "signed_consent_form",
    "verified_crm", "documented_evidence", "manual_review",
    "recipient", "verified", "manual_verified", "documented", "synthetic",
})


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _clean(value: Any, *, limit: int = MAX_CELL_CHARS) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    if len(value) > limit:
        raise ValueError(f"Value exceeds {limit:,} characters")
    if "\x00" in value or any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError("Value contains unsupported control characters")
    return unicodedata.normalize("NFC", value.strip())


def normalize_phone(value: str, region: str | None = None) -> str:
    """Return E.164, requiring an explicit country for non-international input."""
    value = _clean(value, limit=128)
    if not value:
        raise ValueError("Phone number is missing")
    normalized_region = str(region).upper().strip() if region else None
    if normalized_region and normalized_region not in phonenumbers.SUPPORTED_REGIONS:
        raise ValueError("Choose a supported two-letter country region, such as US or GB")
    if not value.startswith("+") and not normalized_region:
        raise ValueError("Ambiguous phone country: choose a country region or use +country-code E.164")
    if re.search(r"[A-Za-z]", value):
        raise ValueError("Phone must contain a number without letters or an extension")
    try:
        number = phonenumbers.parse(value, normalized_region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError("Phone number could not be parsed") from exc
    if number.extension:
        raise ValueError("Phone extensions are not supported for SMS")
    if not phonenumbers.is_valid_number(number):
        raise ValueError("Phone number is not a valid international telephone number")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def _timestamp(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Consent timestamp must be ISO 8601 with an explicit timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError("Consent timestamp must include an explicit timezone")
    if parsed > datetime.now(timezone.utc):
        raise ValueError("Consent timestamp is in the future")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _truth(value: str) -> bool:
    return value.casefold().strip() in {"true", "yes", "1", "y", "opted_out", "revoked", "unsubscribed"}


def _is_optout(values: dict[str, str]) -> bool:
    return _truth(values.get("opted_out", "")) or values.get("consent_status", "").casefold().strip() in {"revoked", "opted_out", "unsubscribed"}


def _property_identity(prop: dict[str, str]) -> str:
    # Do not merge unrelated owners merely because their name matches. Property
    # keys are independent of contacts; ownership collisions require review.
    normalize = lambda value: " ".join(value.casefold().split())
    if prop.get("property_id"):
        identity = {key: normalize(prop.get(key, "")) for key in ("property_id", "city", "state", "zip")}
    else:
        identity = {key: normalize(prop.get(key, "")) for key in ("street_address", "city", "state", "zip")}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def _candidate(values: dict[str, str], region: str | None, row_number: int) -> dict[str, Any]:
    warnings: list[str] = []
    phone = normalize_phone(values.get("phone", ""), region)
    custom: dict[str, Any] = {}
    if values.get("custom_fields"):
        try:
            custom = json.loads(values["custom_fields"])
        except (ValueError, TypeError) as exc:
            raise ValueError("custom_fields must be a JSON object") from exc
        if not isinstance(custom, dict) or len(set(custom) - IMPORT_METADATA_FIELDS) > MAX_CUSTOM_FIELDS or len(custom) > MAX_CUSTOM_FIELDS + len(IMPORT_METADATA_FIELDS):
            raise ValueError(f"custom_fields must be a JSON object with at most {MAX_CUSTOM_FIELDS} keys")
        if any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) for key in custom):
            raise ValueError("Custom-field names must be simple identifiers up to 64 characters")
        for key, value in custom.items():
            if isinstance(value, (dict, list)):
                raise ValueError("Custom-field values must be text, numbers, booleans or null")
            if isinstance(value, float) and not (-float("inf") < value < float("inf")):
                raise ValueError("Custom-field numeric values must be finite")
            if isinstance(value, str):
                custom[key] = _clean(value)
    timezone_name = values.get("timezone", "")
    timezone_source = values.get("timezone_source", "")
    timezone_evidence = values.get("timezone_evidence_ref", "")
    if timezone_name:
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Timezone must be a valid IANA zone, such as America/Denver") from exc
        source = re.sub(r"[\s-]+", "_", timezone_source.casefold().strip())
        if source == "unverified":
            source = str(custom.get("claimed_timezone_source", ""))
        if source not in TIMEZONE_SOURCES:
            warnings.append("Timezone held: recipient evidence source is missing or unsupported; an address or area code is insufficient")
            timezone_name = timezone_source = ""
        else:
            custom["claimed_timezone_source"] = source
            if timezone_evidence:
                custom["timezone_evidence_ref"] = timezone_evidence
            timezone_source = "unverified"
            warnings.append("Imported recipient timezone claim requires reviewer verification before sending")
    elif timezone_source:
        warnings.append("Timezone source has no corresponding recipient timezone")
        timezone_source = ""
    prop = {key: values.get(key, "") for key in ("street_address", "city", "state", "zip", "property_id")}
    property_record = prop if any(prop.values()) else None
    if property_record and not (prop["property_id"] or prop["street_address"]):
        raise ValueError("A property requires a street_address or property_id")
    consent_fields = {key.removeprefix("consent_"): values.get(key, "") for key in ALIASES if key.startswith("consent_") and key != "consent_status"}
    claimed_status = values.get("consent_status", "").casefold()
    opted_out = _is_optout(values)
    if values.get("consent"):
        custom["csv_consent_claim"] = values["consent"]
        if values["consent"].casefold().strip() in {"false", "no", "0", "n"}:
            warnings.append("CSV declares no consent; review evidence before any eligibility restoration")
    if claimed_status:
        custom["csv_consent_status_claim"] = claimed_status
    consent_record = None
    if any(consent_fields.values()) or values.get("consent") or claimed_status:
        consent_fields["occurred_at"] = _timestamp(consent_fields["occurred_at"])
        consent_fields["channel"] = consent_fields["channel"].casefold()
        consent_record = {**consent_fields, "status": "pending"}
        warnings.append("Imported consent is pending evidence review; CSV declarations never verify consent")
        missing = [key for key, value in consent_fields.items() if not value]
        if missing:
            warnings.append("Consent evidence incomplete: " + ", ".join(missing))
    if opted_out:
        warnings.append("Imported opt-out will suppress this number across all campaigns and senders")
    eligibility = ["Imported consent requires evidence review" if consent_record else "No documented consent"]
    if opted_out:
        eligibility.append("Recorded opt-out: messaging suppressed")
    if not timezone_name or timezone_source == "unverified":
        eligibility.append("Recipient timezone is unverified")
    return {
        "row_number": row_number, "phone": phone,
        "first_name": values.get("first_name", ""), "last_name": values.get("last_name", ""),
        "timezone": timezone_name or None, "timezone_source": timezone_source or None,
        "custom_fields": custom, "property": property_record,
        "consent": consent_record, "opted_out": opted_out,
        "warnings": warnings, "eligibility": eligibility,
    }


def _mapping(headers: list[str], supplied: dict[str, str] | None) -> dict[str, str]:
    if supplied is not None:
        if not isinstance(supplied, dict):
            raise ValueError("Column mapping must be an object mapping field names to headers")
        result = {}
        for field, header in supplied.items():
            if field not in ALIASES:
                raise ValueError(f"Unsupported mapped field: {field}")
            if header in (None, ""):
                continue
            if not isinstance(header, str) or header not in headers:
                raise ValueError(f"Mapped header is missing: {header}")
            if header in result.values():
                raise ValueError(f"One CSV column cannot map to multiple fields: {header}")
            result[field] = header
    else:
        lookup = {_key(alias): field for field, aliases in ALIASES.items() for alias in aliases}
        result = {}
        for header in headers:
            field = lookup.get(_key(header))
            if field and field not in result:
                result[field] = header
    return result


def preview_csv(content: bytes, filename: str, region: str | None, mapping: dict | None = None) -> dict:
    """Normalize a bounded file without writing contacts or trusting consent."""
    if not isinstance(content, bytes):
        raise ValueError("CSV content must be bytes")
    if not content:
        raise ValueError("CSV file is empty")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError(f"CSV file exceeds the {MAX_FILE_BYTES // 1024 // 1024} MiB limit; split it into smaller files")
    if region and str(region).upper().strip() not in phonenumbers.SUPPORTED_REGIONS:
        raise ValueError("Choose a supported two-letter country region, such as US or GB")
    try:
        text = content.decode("utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("CSV must use UTF-8 or BOM-marked UTF-16 encoding") from exc
    if "\x00" in text:
        raise ValueError("CSV contains NUL characters or unsupported encoding")
    try:
        delimiter = csv.Sniffer().sniff(text[:65_536], delimiters=",;\t|").delimiter
    except csv.Error:
        # Ragged records can defeat Sniffer. Recover the delimiter from the
        # correctly parsed header so the operator can see a rejected-row report.
        candidates = []
        for delimiter in ",;\t|":
            try:
                parsed_header = next(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True))
                candidates.append((len(parsed_header), delimiter))
            except (csv.Error, StopIteration):
                pass
        delimiter = max(candidates, key=lambda pair: pair[0])[1] if candidates else ","
    # Infer only the separator. Sniffer's quote/doublequote heuristics differ
    # between Python patch releases and can misread RFC CSV containing quoted
    # JSON. CSV exports from Excel/Python escape a quote by doubling it; keep
    # that contract deterministic across Windows and Linux.
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter,
                        quotechar='"', doublequote=True, escapechar=None, strict=True)
    try:
        raw_headers = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise ValueError("CSV must contain a valid header row") from exc
    headers = [_clean(header, limit=256) for header in raw_headers]
    if not headers or len(headers) > MAX_COLUMNS or any(not header for header in headers):
        raise ValueError(f"CSV requires 1 to {MAX_COLUMNS} nonempty column names")
    if len({_key(header) for header in headers}) != len(headers):
        raise ValueError("CSV contains duplicate or ambiguous column names")
    chosen_mapping = _mapping(headers, mapping)
    if "phone" not in chosen_mapping:
        return {
            "filename": _clean(str(filename).replace("\\", "/").rsplit("/", 1)[-1], limit=256),
            "headers": headers, "mapping": chosen_mapping, "rows": [],
            "accepted": 0, "rejected": 0, "rejections": [], "duplicates": 0,
            "duplicate_phone_rows": 0, "unique_contacts": 0, "total_rows": 0,
            "fatal_error": None, "mapping_required": ["phone"],
            "warnings": ["Map a phone-number column, then preview the same file again."],
        }
    rows, rejected, signatures, phone_set = [], [], set(), set()
    duplicate_rows = duplicate_phone_rows = total = 0
    for_line_error = None
    try:
        for cells in reader:
            if not cells or not any(cell.strip() for cell in cells):
                continue
            total += 1
            if total > MAX_ROWS:
                raise ValueError(f"CSV exceeds the {MAX_ROWS:,}-row limit; split it into smaller files")
            line = reader.line_num
            raw = dict(zip(headers, cells))
            raw_mapped = {field: raw.get(header, "") for field, header in chosen_mapping.items()}
            if len(cells) != len(headers):
                rejected.append({"row_number": line, "reasons": [f"Malformed row: expected {len(headers)} columns, received {len(cells)}"], "values": raw})
                if _is_optout(raw_mapped):
                    for_line_error = f"Opt-out at line {line} could not be safely imported; correct the rejected row before importing this file"
                continue
            try:
                cleaned = {header: _clean(cell) for header, cell in zip(headers, cells)}
                values = {field: cleaned[header] for field, header in chosen_mapping.items()}
                row = _candidate(values, region, line)
            except ValueError as exc:
                rejected.append({"row_number": line, "reasons": [str(exc)], "values": raw})
                if _is_optout(raw_mapped):
                    for_line_error = f"Opt-out at line {line} could not be safely imported; correct the rejected row before importing this file"
                continue
            signature = json.dumps({key: value for key, value in row.items() if key not in {"row_number", "warnings", "eligibility"}}, sort_keys=True, ensure_ascii=False)
            if signature in signatures:
                duplicate_rows += 1
                continue
            signatures.add(signature)
            if row["phone"] in phone_set:
                duplicate_phone_rows += 1
            phone_set.add(row["phone"])
            rows.append(row)
    except csv.Error as exc:
        for_line_error = f"Malformed CSV at line {reader.line_num}: {exc}"
        # A truncated parse is unsafe to commit because unseen rows may carry an
        # opt-out. Fail the entire file while keeping an inspectable report.
        rejected.append({"row_number": reader.line_num, "reasons": [for_line_error], "values": {}})
    if for_line_error:
        rows = []
    return {
        "filename": _clean(str(filename).replace("\\", "/").rsplit("/", 1)[-1], limit=256),
        "headers": headers, "mapping": chosen_mapping, "rows": rows,
        "accepted": len(rows), "rejected": len(rejected), "rejections": rejected,
        "duplicates": duplicate_rows, "duplicate_phone_rows": duplicate_phone_rows,
        "unique_contacts": len({row["phone"] for row in rows}), "total_rows": total,
        "fatal_error": for_line_error,
        "mapping_required": [],
        "warnings": ["Uploaded contact data does not establish permission to send SMS.",
                     "Phone format validation does not verify ownership, deliverability or SMS capability."] + ([for_line_error] if for_line_error else []),
    }


def _validate_saved_candidate(row: dict) -> dict:
    """Validate again at the trust boundary; never accept a verified CSV status."""
    if not isinstance(row, dict):
        raise ValueError("Import rows must be objects")
    values = {key: _clean(row.get(key)) for key in ("phone", "first_name", "last_name", "timezone", "timezone_source")}
    if row.get("property") is not None:
        if not isinstance(row["property"], dict):
            raise ValueError("Invalid property data")
        values.update({key: _clean(row["property"].get(key)) for key in ("street_address", "city", "state", "zip", "property_id")})
    custom = row.get("custom_fields", {})
    values["custom_fields"] = _clean(json.dumps(custom, ensure_ascii=False, allow_nan=False))
    if isinstance(custom, dict):
        values["timezone_evidence_ref"] = _clean(custom.get("timezone_evidence_ref"))
    if row.get("consent") is not None:
        if not isinstance(row["consent"], dict):
            raise ValueError("Invalid consent evidence")
        values.update({"consent_" + key: _clean(row["consent"].get(key)) for key in ("business", "channel", "purpose", "source", "occurred_at", "disclosure_version", "evidence_ref")})
        values["consent"] = _clean(custom.get("csv_consent_claim", "true")) if isinstance(custom, dict) else "true"
        values["consent_status"] = _clean(custom.get("csv_consent_status_claim")) if isinstance(custom, dict) else ""
    values["opted_out"] = "true" if row.get("opted_out") is True else ""
    try:
        line = int(row.get("row_number", 0))
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid import row number") from exc
    return _candidate(values, None, line)


def commit_import(conn, preview: dict, actor: str) -> dict:
    """Commit a reviewed preview atomically; suppression is never removed."""
    from .db import audit, now

    if not isinstance(preview, dict) or not isinstance(preview.get("rows"), list):
        raise ValueError("A saved import preview is required")
    if preview.get("fatal_error"):
        raise ValueError("Malformed CSV must be corrected and previewed again")
    if preview.get("mapping_required") or not preview["rows"]:
        raise ValueError("Map the phone column and preview at least one valid row before committing")
    if len(preview["rows"]) > MAX_ROWS:
        raise ValueError("Import row limit exceeded")
    # Validate every row before touching the database.
    rows = [_validate_saved_candidate(row) for row in preview["rows"]]
    result = {"contacts_created": 0, "contacts_existing": 0, "properties_created": 0,
              "properties_existing": 0, "consents_pending": 0, "consents_existing": 0,
              "consents_created": 0, "suppressed_contacts": 0, "opt_outs_recorded": 0, "warnings": []}
    savepoint = "import_" + uuid.uuid4().hex
    conn.execute("SAVEPOINT " + savepoint)
    seen_contacts, suppressed, seen_optouts, created_consents = set(), set(), set(), []
    try:
        for row in rows:
            stamp = now()
            contact = conn.execute("SELECT * FROM contacts WHERE phone=?", (row["phone"],)).fetchone()
            if contact is None:
                inserted = conn.execute("INSERT INTO contacts(phone,first_name,last_name,timezone,timezone_source,custom_fields,reply_hold,created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,?)",
                             (row["phone"], row["first_name"], row["last_name"], row["timezone"], row["timezone_source"], json.dumps(row["custom_fields"]), stamp, stamp))
                contact_id = inserted.lastrowid
                result["contacts_created"] += 1
            else:
                contact_id = contact["id"]
                if contact_id not in seen_contacts:
                    result["contacts_existing"] += 1
                # Reimports fill missing profile data; they do not overwrite
                # reviewed facts or remove reply holds.
                stored_custom = json.loads(contact["custom_fields"] or "{}")
                merged_custom = {**row["custom_fields"], **stored_custom}
                timezone_name = contact["timezone"] or row["timezone"]
                timezone_source = contact["timezone_source"] or row["timezone_source"]
                if contact["timezone"] and row["timezone"] and contact["timezone"] != row["timezone"]:
                    result["warnings"].append({"row_number": row["row_number"], "reason": "Existing recipient timezone retained; conflicting claim needs review"})
                conn.execute("UPDATE contacts SET first_name=?,last_name=?,timezone=?,timezone_source=?,custom_fields=?,updated_at=? WHERE id=?",
                             (contact["first_name"] or row["first_name"], contact["last_name"] or row["last_name"], timezone_name, timezone_source, json.dumps(merged_custom), stamp, contact_id))
            seen_contacts.add(contact_id)
            prop = row["property"]
            if prop:
                property_key = _property_identity(prop)
                existing = conn.execute("SELECT id,contact_id FROM properties WHERE property_key=?", (property_key,)).fetchone()
                if existing:
                    result["properties_existing"] += 1
                    if existing["contact_id"] != contact_id:
                        result["warnings"].append({"row_number": row["row_number"], "reason": "Property belongs to another contact record; ownership association requires review"})
                else:
                    conn.execute("INSERT INTO properties(contact_id,property_key,street_address,city,state,zip,property_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                                 (contact_id, property_key, prop["street_address"], prop["city"], prop["state"], prop["zip"], prop["property_id"], stamp))
                    result["properties_created"] += 1
            consent = row["consent"]
            if consent:
                fields = ("business", "channel", "purpose", "source", "occurred_at", "disclosure_version", "evidence_ref")
                evidence = tuple(consent[key] for key in fields)
                match = conn.execute("SELECT id FROM consents WHERE contact_id=? AND " + " AND ".join("COALESCE(" + key + ",'')=?" for key in fields) + " LIMIT 1", (contact_id, *evidence)).fetchone()
                if match:
                    result["consents_existing"] += 1
                else:
                    inserted_consent = conn.execute("INSERT INTO consents(contact_id,business,channel,purpose,source,occurred_at,disclosure_version,evidence_ref,status,created_at) VALUES(?,?,?,?,?,?,?,?,'pending',?)",
                                 (contact_id, *evidence, stamp))
                    created_consents.append(inserted_consent.lastrowid)
                    result["consents_created"] += 1
            if row["opted_out"] and row["phone"] not in seen_optouts:
                from .engine import suppress
                suppress(conn, row["phone"], "CSV records an opt-out", actor=actor)
                seen_optouts.add(row["phone"])
                result["opt_outs_recorded"] += 1
            suppression = conn.execute("SELECT active FROM suppressions WHERE phone=?", (row["phone"],)).fetchone()
            if suppression and suppression["active"]:
                suppressed.add(row["phone"])
        result["suppressed_contacts"] = len(suppressed)
        result["consents_pending"] = sum(conn.execute("SELECT status FROM consents WHERE id=?", (consent_id,)).fetchone()["status"] == "pending" for consent_id in created_consents)
        result["accepted_rows"] = len(rows)
        audit(conn, str(actor), "import_committed", "import", str(preview.get("id", "")),
              {key: value for key, value in result.items() if key != "warnings"})
        conn.execute("RELEASE SAVEPOINT " + savepoint)
        return result
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT " + savepoint)
        conn.execute("RELEASE SAVEPOINT " + savepoint)
        raise


def _spreadsheet_safe(value: Any) -> str:
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=False, allow_nan=False)
    else:
        rendered = "" if value is None else str(value)
    # Quote CSV structurally AND neutralize formulas when spreadsheet software
    # interprets a quoted cell. Leading whitespace/control characters can hide
    # a formula, so inspect the stripped value as well as its original prefix.
    if rendered.startswith(("\t", "\r", "\n")) or rendered.lstrip().startswith(("=", "+", "-", "@")):
        rendered = "'" + rendered
    return rendered


def safe_csv(rows: list[dict]) -> str:
    """Export dictionaries as UTF-8-friendly CSV with formula defenses."""
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("CSV export exceeds the row limit")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("CSV export rows must be objects")
    headers = list(dict.fromkeys(key for row in rows for key in row))
    if len(headers) > MAX_COLUMNS:
        raise ValueError("Invalid CSV export rows or column count")
    output = io.StringIO(newline="")
    writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow([_spreadsheet_safe(header) for header in headers])
    for row in rows:
        writer.writerow([_spreadsheet_safe(row.get(header)) for header in headers])
        if output.tell() > MAX_FILE_BYTES * 4:
            raise ValueError("CSV export exceeds the output size limit")
    return output.getvalue()
