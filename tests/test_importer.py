import csv
import io
import json
import sqlite3
from pathlib import Path

import pytest

from blastio import importer
from blastio.importer import commit_import, normalize_phone, preview_csv, safe_csv


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript((Path(__file__).parents[1] / "migrations" / "001_initial.sql").read_text())
    yield connection
    connection.close()


def csv_file(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def test_phone_normalization_requires_explicit_country():
    with pytest.raises(ValueError, match="Ambiguous phone country"):
        normalize_phone("202-555-0101")
    assert normalize_phone("202-555-0101", "us") == "+12025550101"
    assert normalize_phone("+44 20 7946 0018") == "+442079460018"
    with pytest.raises(ValueError, match="two-letter"):
        normalize_phone("202-555-0101", "1")
    with pytest.raises(ValueError, match="valid"):
        normalize_phone("+123")
    with pytest.raises(ValueError, match="extension"):
        normalize_phone("+12025550101 ext 42")


def test_mapping_reports_rejections_and_retains_multiple_properties():
    content = csv_file(["Mobile", "First Name", "Property Address"], [
        ["(202) 555-0101", "Avery", "10 Oak St"],
        ["+12025550101", "Avery", "10 Oak St"],
        ["+12025550101", "Avery", "20 Pine St"],
        ["not a number", "Rejected", "30 Pine St"],
    ])
    preview = preview_csv(content, "../../owners.csv", "US")
    assert preview["filename"] == "owners.csv"
    assert preview["mapping"] == {"phone": "Mobile", "first_name": "First Name", "street_address": "Property Address"}
    assert preview["accepted"] == 2
    assert preview["duplicates"] == 1
    assert preview["duplicate_phone_rows"] == 1
    assert preview["unique_contacts"] == 1
    assert preview["rejected"] == 1
    assert preview["rejections"][0]["row_number"] == 5
    assert "No documented consent" in preview["rows"][0]["eligibility"]


def test_manual_mapping_is_authoritative():
    content = b"a,b,first_name\n+12025550101,Pat,Ignore\n"
    preview = preview_csv(content, "custom.csv", None, {"phone": "a", "first_name": "b"})
    assert preview["rows"][0]["first_name"] == "Pat"
    with pytest.raises(ValueError, match="multiple"):
        preview_csv(content, "custom.csv", None, {"phone": "a", "first_name": "a"})
    with pytest.raises(ValueError, match="Unsupported"):
        preview_csv(content, "custom.csv", None, {"phone": "a", "verified": "b"})


def test_unknown_phone_header_still_allows_manual_mapping():
    content = b"number_to_review,name\n+12025550101,Pat\n"
    first = preview_csv(content, "unknown.csv", None)
    assert first["headers"] == ["number_to_review", "name"]
    assert first["mapping_required"] == ["phone"]
    assert first["accepted"] == 0
    corrected = preview_csv(content, "unknown.csv", None, {"phone": "number_to_review", "first_name": "name"})
    assert corrected["accepted"] == 1


def test_missing_region_rejects_national_rows_without_guessing():
    preview = preview_csv(b"phone\n2025550101\n+12025550102\n", "countries.csv", None)
    assert preview["accepted"] == 1
    assert "Ambiguous phone country" in preview["rejections"][0]["reasons"][0]


def test_import_deduplicates_contacts_properties_and_evidence(conn):
    content = csv_file(["phone", "first_name", "street_address", "city", "state", "zip", "consent"], [
        ["+12025550101", "Avery", "10 Oak St", "Denver", "CO", "80202", "true"],
        ["+12025550101", "Avery", "20 Pine St", "Denver", "CO", "80202", "true"],
    ])
    preview = preview_csv(content, "two-properties.csv", None)
    result = commit_import(conn, preview, "reviewer@example.invalid")
    assert result["contacts_created"] == 1
    assert result["properties_created"] == 2
    assert result["consents_pending"] == 1
    repeated = commit_import(conn, preview, "reviewer@example.invalid")
    assert repeated["contacts_created"] == repeated["properties_created"] == repeated["consents_pending"] == 0
    assert repeated["contacts_existing"] == 1
    assert conn.execute("SELECT count(*) FROM contacts").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM properties").fetchone()[0] == 2
    assert conn.execute("SELECT status FROM consents").fetchone()[0] == "pending"
    assert conn.execute("SELECT count(*) FROM audit_log WHERE action='import_committed'").fetchone()[0] == 2


def test_csv_consent_never_becomes_verified_even_when_preview_is_modified(conn):
    headers = ["phone", "consent_business", "consent_channel", "consent_purpose", "consent_source", "consent_occurred_at", "consent_disclosure_version", "consent_evidence_ref", "consent_status"]
    content = csv_file(headers, [["+12025550101", "101XVC", "sms", "seller_outreach", "signed form", "2025-03-01T10:00:00-07:00", "v1", "evidence://signed-form-1", "verified"]])
    preview = preview_csv(content, "claimed-consent.csv", None)
    assert preview["rows"][0]["consent"]["status"] == "pending"
    preview["rows"][0]["consent"]["status"] = "verified"
    commit_import(conn, preview, "operator@example.invalid")
    consent = conn.execute("SELECT * FROM consents").fetchone()
    assert consent["status"] == "pending"
    assert consent["verified_by"] is None
    assert consent["verified_at"] is None
    assert consent["occurred_at"] == "2025-03-01T17:00:00+00:00"
    assert consent["evidence_ref"] == "evidence://signed-form-1"


def test_reimport_or_contact_recreation_does_not_clear_phone_suppression(conn):
    stamp = "2025-01-01T00:00:00+00:00"
    conn.execute("INSERT INTO suppressions VALUES(?,?,?,?,?)", ("+12025550101", "STOP", 1, stamp, stamp))
    preview = preview_csv(b"phone,consent\n+12025550101,true\n", "repeat.csv", None)
    first = commit_import(conn, preview, "operator")
    assert first["suppressed_contacts"] == 1
    contact_id = conn.execute("SELECT id FROM contacts").fetchone()[0]
    conn.execute("DELETE FROM consents WHERE contact_id=?", (contact_id,))
    conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    again = commit_import(conn, preview, "operator")
    assert again["suppressed_contacts"] == 1
    assert conn.execute("SELECT reason,active FROM suppressions").fetchone()["reason"] == "STOP"
    assert conn.execute("SELECT active FROM suppressions").fetchone()[0] == 1


def test_csv_optout_suppresses_number_and_is_not_ignored(conn):
    preview = preview_csv(b"phone,opted_out,consent_status\n+12025550101,true,revoked\n", "opt-out.csv", None)
    assert preview["rows"][0]["opted_out"] is True
    result = commit_import(conn, preview, "operator")
    assert result["opt_outs_recorded"] == 1
    assert result["suppressed_contacts"] == 1
    assert conn.execute("SELECT active FROM suppressions").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM suppression_events").fetchone()[0] == 1


def test_csv_consent_timestamps_need_timezone_and_cannot_be_future():
    for timestamp in ["2025-01-01", "2025-01-01T12:00:00", "2099-01-01T12:00:00Z"]:
        preview = preview_csv(csv_file(["phone", "consent_occurred_at"], [["+12025550101", timestamp]]), "invalid-evidence.csv", None)
        assert preview["accepted"] == 0
        assert preview["rejected"] == 1


@pytest.mark.parametrize("source", ["", "property_address", "area_code", "guessed"])
def test_timezone_is_never_inferred_from_imported_property_or_area_code(source):
    content = csv_file(["phone", "timezone", "timezone_source", "city", "street_address"], [["+12025550101", "America/Denver", source, "Denver", "10 Oak St"]])
    candidate = preview_csv(content, "timezone.csv", None)["rows"][0]
    assert candidate["timezone"] is None
    assert candidate["timezone_source"] is None
    assert "Recipient timezone is unverified" in candidate["eligibility"]


def test_claimed_timezone_evidence_is_retained_but_still_needs_review(conn):
    content = csv_file(["phone", "timezone", "timezone_source", "timezone_evidence_ref"], [["+12025550101", "America/Denver", "recipient_confirmed", "evidence://timezone/form-1"]])
    preview = preview_csv(content, "claimed-zone.csv", None)
    candidate = preview["rows"][0]
    assert candidate["timezone"] == "America/Denver"
    assert candidate["timezone_source"] == "unverified"
    assert candidate["custom_fields"]["claimed_timezone_source"] == "recipient_confirmed"
    assert candidate["custom_fields"]["timezone_evidence_ref"] == "evidence://timezone/form-1"
    assert "Recipient timezone is unverified" in candidate["eligibility"]
    commit_import(conn, preview, "operator")
    contact = conn.execute("SELECT * FROM contacts").fetchone()
    assert contact["timezone_source"] == "unverified"
    assert json.loads(contact["custom_fields"])["timezone_evidence_ref"] == "evidence://timezone/form-1"


def test_import_metadata_does_not_make_valid_custom_fields_fail_on_commit(conn):
    custom = {f"field_{number}": "value" for number in range(importer.MAX_CUSTOM_FIELDS)}
    content = csv_file(["phone", "timezone", "timezone_source", "consent", "consent_status", "custom_fields"], [["+12025550101", "America/Denver", "recipient_confirmed", "false", "pending", json.dumps(custom)]])
    preview = preview_csv(content, "many-fields.csv", None)
    assert preview["accepted"] == 1
    assert preview["rows"][0]["custom_fields"]["csv_consent_claim"] == "false"
    result = commit_import(conn, preview, "operator")
    assert result["contacts_created"] == 1
    saved = json.loads(conn.execute("SELECT custom_fields FROM contacts").fetchone()[0])
    assert saved["csv_consent_claim"] == "false"
    assert len(set(saved) - importer.IMPORT_METADATA_FIELDS) == importer.MAX_CUSTOM_FIELDS


def test_existing_profile_fields_and_reply_hold_are_preserved(conn):
    first = preview_csv(csv_file(["phone", "first_name", "custom_fields"], [["+12025550101", "Reviewed", '{"tier":"reviewed"}']]), "first.csv", None)
    commit_import(conn, first, "operator")
    conn.execute("UPDATE contacts SET reply_hold=1")
    next_preview = preview_csv(csv_file(["phone", "first_name", "last_name", "custom_fields"], [["+12025550101", "Unreviewed", "Fill missing", '{"tier":"unreviewed","extra":"new"}']]), "next.csv", None)
    commit_import(conn, next_preview, "operator")
    contact = conn.execute("SELECT * FROM contacts").fetchone()
    assert contact["first_name"] == "Reviewed"
    assert contact["last_name"] == "Fill missing"
    assert contact["reply_hold"] == 1
    assert json.loads(contact["custom_fields"]) == {"tier": "reviewed", "extra": "new"}


@pytest.mark.parametrize("newline", ["\r\n", "\n"])
def test_rfc_quoted_json_does_not_depend_on_sniffer_quote_heuristics(conn, monkeypatch, newline):
    class MisdetectedDialect(csv.excel):
        doublequote = False

    monkeypatch.setattr(csv.Sniffer, "sniff", lambda self, sample, delimiters=None: MisdetectedDialect)
    content = csv_file(["phone", "first_name", "custom_fields"], [["+12025550101", "Reviewed", '{"tier":"reviewed"}']])
    content = content.replace(b"\r\n", newline.encode())
    preview = preview_csv(content, "quoted-json.csv", None)
    assert preview["accepted"] == 1
    assert preview["rejected"] == 0
    assert preview["rows"][0]["custom_fields"] == {"tier": "reviewed"}
    commit_import(conn, preview, "operator")
    saved = conn.execute("SELECT custom_fields FROM contacts").fetchone()[0]
    assert json.loads(saved) == {"tier": "reviewed"}


def test_property_ownership_collision_requires_review(conn):
    content = b"phone,street_address,city,state\n+12025550101,10 Oak St,Denver,CO\n+12025550102,10 Oak St,Denver,CO\n"
    result = commit_import(conn, preview_csv(content, "collision.csv", None), "operator")
    assert result["contacts_created"] == 2
    assert result["properties_created"] == 1
    assert "ownership" in result["warnings"][0]["reason"]


def test_malformed_row_report_and_fatal_quotes_are_safe():
    preview = preview_csv(b"phone,name\n+12025550101,Pat,extra\n+12025550102,Good\n", "ragged.csv", None)
    assert preview["accepted"] == 1
    assert "Malformed row" in preview["rejections"][0]["reasons"][0]
    fatal = preview_csv(b'phone,first_name\n+12025550101,Valid\n+12025550102,"unterminated\n', "quotes.csv", None)
    assert fatal["fatal_error"]
    assert fatal["accepted"] == 0
    assert fatal["rows"] == []
    tabbed = preview_csv(b"phone\tfirst_name\n+12025550101\tPat\textra\n+12025550102\tGood\n", "ragged.tsv", None)
    assert tabbed["accepted"] == 1
    assert tabbed["rejected"] == 1


def test_rejected_optout_prevents_partial_file_commit(conn):
    content = b"phone,opted_out,consent_occurred_at\n+12025550101,false,\n+12025550102,true,not-a-date\n"
    preview = preview_csv(content, "rejected-optout.csv", None)
    assert preview["fatal_error"].startswith("Opt-out at line 3")
    assert preview["accepted"] == 0
    assert preview["rejected"] == 1
    with pytest.raises(ValueError, match="corrected"):
        commit_import(conn, preview, "operator")
    assert conn.execute("SELECT count(*) FROM contacts").fetchone()[0] == 0


def test_file_and_row_limits_are_enforced(monkeypatch):
    monkeypatch.setattr(importer, "MAX_FILE_BYTES", 32)
    with pytest.raises(ValueError, match="limit"):
        preview_csv(b"phone\n" + b"1" * 33, "too-large.csv", "US")
    monkeypatch.setattr(importer, "MAX_FILE_BYTES", 1024)
    monkeypatch.setattr(importer, "MAX_ROWS", 1)
    with pytest.raises(ValueError, match="row limit|row-limit|-row limit"):
        preview_csv(b"phone\n+12025550101\n+12025550102\n", "too-many.csv", None)


def test_duplicate_headers_and_invalid_custom_fields_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        preview_csv(b"phone,Phone\n+12025550101,+12025550101\n", "duplicate-headers.csv", None)
    for custom in ['[1,2]', '{"key":{"nested":1}}', '{"bad-key":"text"}', '{"number":NaN}']:
        result = preview_csv(csv_file(["phone", "custom_fields"], [["+12025550101", custom]]), "bad-custom.csv", None)
        assert result["accepted"] == 0
        assert result["rejected"] == 1


def test_formula_safe_exports_preserve_structure_and_neutralize_headers():
    data = [{"=bad_header": "=HYPERLINK(\"http://bad\")", "phone": "+12025550101", "text": "  @SUM(1+1)"},
            {"=bad_header": "-2+3", "phone": "\t=1+1", "text": "Normal, quoted\nline"}]
    exported = safe_csv(data)
    parsed = list(csv.reader(io.StringIO(exported)))
    assert parsed[0][0] == "'=bad_header"
    assert parsed[1][0].startswith("'=")
    assert parsed[1][1] == "'+12025550101"
    assert parsed[1][2] == "'  @SUM(1+1)"
    assert parsed[2][0] == "'-2+3"
    assert parsed[2][1] == "'\t=1+1"
    assert parsed[2][2] == "Normal, quoted\nline"


def test_utf8_bom_and_tab_separated_utf16_are_supported():
    assert preview_csv(b"\xef\xbb\xbfphone\n+12025550101\n", "bom.csv", None)["accepted"] == 1
    content = "phone\tfirst_name\n+12025550101\tPat\n".encode("utf-16")
    assert preview_csv(content, "excel.tsv", None)["rows"][0]["first_name"] == "Pat"


def test_commit_validates_all_rows_before_mutating_database(conn):
    preview = preview_csv(b"phone\n+12025550101\n+12025550102\n", "valid.csv", None)
    preview["rows"][1]["phone"] = "malformed"
    with pytest.raises(ValueError):
        commit_import(conn, preview, "operator")
    assert conn.execute("SELECT count(*) FROM contacts").fetchone()[0] == 0


def test_database_error_rolls_back_entire_import(conn, monkeypatch):
    import blastio.db

    def fail_audit(*args, **kwargs):
        raise RuntimeError("simulated durable-store failure")

    monkeypatch.setattr(blastio.db, "audit", fail_audit)
    preview = preview_csv(b"phone,street_address\n+12025550101,10 Oak St\n", "valid.csv", None)
    with pytest.raises(RuntimeError):
        commit_import(conn, preview, "operator")
    assert conn.execute("SELECT count(*) FROM contacts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM properties").fetchone()[0] == 0
