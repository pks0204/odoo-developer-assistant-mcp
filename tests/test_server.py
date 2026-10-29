import ast
from pathlib import Path

from server import (
    analyze_odoo_error,
    inspect_odoo_module,
    validate_odoo_manifest,
)


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def create_addon(tmp_path: Path, manifest: dict) -> Path:
    """Create a temporary addon with a manifest."""
    addon = tmp_path / "my_test_module"
    addon.mkdir()

    manifest_path = addon / "__manifest__.py"
    manifest_path.write_text(
        repr(manifest),
        encoding="utf-8",
    )

    return addon


# --------------------------------------------------
# Tests for analyze_odoo_error
# --------------------------------------------------

def test_analyze_external_id_error():
    result = analyze_odoo_error(
        "ValueError: External ID not found in the system: "
        "my_module.my_view"
    )

    assert result["success"] is True
    assert result["category"] == "XML ID Error"
    assert result["confirmed_diagnosis"] is False
    assert len(result["troubleshooting_steps"]) > 0


def test_analyze_owl_error():
    result = analyze_odoo_error(
        "OwlError: Invalid props for component Many2One"
    )

    assert result["success"] is True
    assert result["category"] == "Owl Frontend Error"


def test_analyze_empty_error():
    result = analyze_odoo_error("")

    assert result["success"] is False
    assert "error" in result


def test_analyze_unknown_error():
    result = analyze_odoo_error(
        "An unfamiliar error occurred"
    )

    assert result["success"] is True
    assert result["category"] == "Unclassified Odoo Error"


def test_analyze_oversized_error():
    result = analyze_odoo_error("x" * 20001)

    assert result["success"] is False
    assert "too long" in result["error"].lower()


# --------------------------------------------------
# Tests for inspect_odoo_module
# --------------------------------------------------

def test_inspect_module_reads_manifest(tmp_path):
    manifest = {
        "name": "My Test Module",
        "version": "19.0.1.0.0",
        "depends": ["base", "sale"],
        "data": [],
        "installable": True,
        "application": False,
    }

    addon = create_addon(tmp_path, manifest)

    result = inspect_odoo_module(
        addon_path=str(addon),
        addons_directories=[str(tmp_path)],
    )

    assert result["success"] is True
    assert result["module"] == "my_test_module"
    assert result["name"] == "My Test Module"
    assert result["depends"] == ["base", "sale"]


def test_inspect_module_finds_dependency_in_second_directory(
    tmp_path,
):
    manifest = {
        "name": "My Test Module",
        "version": "19.0.1.0.0",
        "depends": ["sale"],
    }

    addon = create_addon(tmp_path, manifest)

    second_addons = tmp_path / "other_addons"
    second_addons.mkdir()
    (second_addons / "sale").mkdir()

    result = inspect_odoo_module(
        addon_path=str(addon),
        addons_directories=[
            str(tmp_path),
            str(second_addons),
        ],
    )

    assert result["success"] is True
    assert result["dependency_status"]["sale"]["status"] == "found"
    assert str(second_addons) in (
        result["dependency_status"]["sale"]["found_in"]
    )


def test_inspect_module_rejects_addon_outside_roots(tmp_path):
    addon = create_addon(
        tmp_path,
        {
            "name": "Outside Module",
            "version": "19.0.1.0.0",
            "depends": [],
        },
    )

    permitted_root = tmp_path / "permitted"
    permitted_root.mkdir()

    result = inspect_odoo_module(
        addon_path=str(addon),
        addons_directories=[str(permitted_root)],
    )

    assert result["success"] is False


# --------------------------------------------------
# Tests for validate_odoo_manifest
# --------------------------------------------------

def test_validate_manifest_with_missing_files(tmp_path):
    addon = create_addon(
        tmp_path,
        {
            "name": "My Test Module",
            "version": "19.0.1.0.0",
            "depends": ["base"],
            "data": ["views/missing_view.xml"],
        },
    )

    result = validate_odoo_manifest(str(addon))

    assert result["success"] is True
    assert result["valid"] is False
    assert result["missing_files"] == [
        {
            "section": "data",
            "path": "views/missing_view.xml",
        }
    ]


def test_validate_manifest_with_existing_file(tmp_path):
    addon = create_addon(
        tmp_path,
        {
            "name": "My Test Module",
            "version": "19.0.1.0.0",
            "depends": ["base"],
            "data": ["views/example.xml"],
        },
    )

    views = addon / "views"
    views.mkdir()
    (views / "example.xml").write_text(
        "<odoo/>",
        encoding="utf-8",
    )

    result = validate_odoo_manifest(str(addon))

    assert result["success"] is True
    assert result["valid"] is True
    assert result["missing_files"] == []
    assert result["checked_files"][0]["status"] == "found"


def test_validate_manifest_rejects_unsafe_path(tmp_path):
    addon = create_addon(
        tmp_path,
        {
            "name": "My Test Module",
            "version": "19.0.1.0.0",
            "depends": [],
            "data": ["../outside.xml"],
        },
    )

    result = validate_odoo_manifest(str(addon))

    assert result["success"] is True
    assert result["valid"] is False
    assert "../outside.xml" in result["unsafe_paths"]


def test_validate_manifest_rejects_malformed_python(tmp_path):
    addon = tmp_path / "broken_module"
    addon.mkdir()

    (addon / "__manifest__.py").write_text(
        "{'name': 'Broken',",
        encoding="utf-8",
    )

    result = validate_odoo_manifest(str(addon))

    assert result["success"] is False
    assert "parse manifest" in result["error"].lower()

# --------------------------------------------------
# Additional Edge-Case Tests
# --------------------------------------------------

def test_validate_manifest_missing_file(tmp_path):
    """Check behavior when the manifest file does not exist."""
    addon = tmp_path / "no_manifest_module"
    addon.mkdir()

    result = validate_odoo_manifest(str(addon))

    assert result["success"] is False
    assert "error" in result


def test_validate_manifest_missing_required_fields(tmp_path):
    """Check whether required manifest fields are detected."""
    addon = create_addon(
        tmp_path,
        {
            "depends": [],
        },
    )

    result = validate_odoo_manifest(str(addon))

    assert result["success"] is True
    assert result["valid"] is False
    assert "name" in result["missing_fields"]
    assert "version" in result["missing_fields"]


def test_inspect_module_missing_manifest(tmp_path):
    """Check behavior when the addon has no manifest."""
    addon = tmp_path / "module_without_manifest"
    addon.mkdir()

    result = inspect_odoo_module(
        addon_path=str(addon),
        addons_directories=[str(tmp_path)],
    )

    assert result["success"] is False
    assert "error" in result


def test_validate_manifest_invalid_depends_type(tmp_path):
    """Check whether an invalid depends field is detected."""
    addon = create_addon(
        tmp_path,
        {
            "name": "Invalid Depends Module",
            "version": "19.0.1.0.0",
            "depends": "base",
            "data": [],
        },
    )

    result = validate_odoo_manifest(str(addon))

    assert result["success"] is True
    assert result["valid"] is False
    assert len(result["field_issues"]) > 0

# --------------------------------------------------
# Error classification priority tests
# --------------------------------------------------

def test_xml_id_error_with_rpc_wrapper():
    """A specific XML ID error should outrank a generic RPC wrapper."""
    result = analyze_odoo_error(
        "RPC_ERROR: Odoo Server Error\n"
        "ValueError: External ID not found in the system: "
        "my_module.my_view"
    )

    assert result["success"] is True
    assert result["category"] == "XML ID Error"


def test_access_error_with_rpc_wrapper():
    """A specific access error should outrank a generic RPC wrapper."""
    result = analyze_odoo_error(
        "RPC_ERROR: Odoo Server Error\n"
        "AccessError: You are not allowed to access this document."
    )

    assert result["success"] is True
    assert result["category"] == "Access Rights Error"


def test_rpc_error_without_specific_cause():
    """A generic RPC error should receive general troubleshooting steps."""
    result = analyze_odoo_error(
        "RPC_ERROR: Odoo Server Error"
    )

    assert result["success"] is True
    assert result["category"] == "RPC Error"
    assert result["confirmed_diagnosis"] is False


# --------------------------------------------------
# Additional Odoo error classification tests
# --------------------------------------------------

def test_analyze_key_error():
    result = analyze_odoo_error(
        "KeyError: 'my_custom_field'"
    )

    assert result["success"] is True
    assert result["category"] == "Key / Lookup Error"
    assert result["confirmed_diagnosis"] is False


def test_analyze_xml_parse_error():
    result = analyze_odoo_error(
        "odoo.tools.convert.ParseError: "
        "while parsing my_module/views/example.xml"
    )

    assert result["success"] is True
    assert result["category"] == "XML Data Error"


def test_analyze_undefined_column_error():
    result = analyze_odoo_error(
        'psycopg2.errors.UndefinedColumn: '
        'column "custom_field" does not exist'
    )

    assert result["success"] is True
    assert result["category"] == "Database Schema Error"


def test_analyze_specific_error_before_generic_rpc():
    result = analyze_odoo_error(
        "RPC_ERROR: Odoo Server Error\n"
        "psycopg2.errors.UndefinedColumn: "
        'column "custom_field" does not exist'
    )

    assert result["success"] is True
    assert result["category"] == "Database Schema Error"

def test_analyze_non_string_input():
    result = analyze_odoo_error(None)

    assert result["success"] is False
    assert "error" in result
