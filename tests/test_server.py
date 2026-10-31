import ast
import os
from pathlib import Path
from unittest.mock import patch

from server import (
    analyze_odoo_error,
    analyze_odoo_owl_component,
    analyze_odoo_python_model,
    analyze_odoo_security,
    analyze_odoo_xml_view,
    check_odoo_addon_health,
    cli_check,
    cli_install_hook,
    cli_main,
    cli_migrate,
    cli_scaffold_tests,
    format_cli_report,
    generate_openupgrade_scripts,
    inspect_odoo_module,
    main,
    migrate_odoo_addon,
    scaffold_odoo_tests,
    validate_odoo_manifest,
    verify_odoo_live_metadata,
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


# --------------------------------------------------
# Tests for analyze_odoo_xml_view
# --------------------------------------------------

def test_analyze_xml_valid_view(tmp_path):
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_partner_form_custom" model="ir.ui.view">
        <field name="name">res.partner.form.custom</field>
        <field name="model">res.partner</field>
        <field name="arch" type="xml">
            <form>
                <field name="name"/>
            </form>
        </field>
    </record>
</odoo>"""
    xml_file = tmp_path / "partner_view.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    result = analyze_odoo_xml_view(str(xml_file))

    assert result["success"] is True
    assert result["valid"] is True
    assert result["summary"]["total_records"] == 1
    assert result["summary"]["view_records"] == 1
    assert result["errors"] == []


def test_analyze_xml_malformed_syntax(tmp_path):
    xml_content = """<odoo>
    <record id="broken_view" model="ir.ui.view">
        <field name="name">Broken
    </record>
</odoo>"""
    xml_file = tmp_path / "broken_view.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    result = analyze_odoo_xml_view(str(xml_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "XML_SYNTAX_ERROR" for e in result["errors"])
    syntax_error = next(e for e in result["errors"] if e["code"] == "XML_SYNTAX_ERROR")
    assert syntax_error["line"] is not None


def test_analyze_xml_missing_file(tmp_path):
    missing_file = tmp_path / "nonexistent.xml"

    result = analyze_odoo_xml_view(str(missing_file))

    assert result["success"] is False
    assert "error" in result
    assert "not exist" in result["error"].lower() or "not found" in result["error"].lower()


def test_analyze_xml_directory_input(tmp_path):
    result = analyze_odoo_xml_view(str(tmp_path))

    assert result["success"] is False
    assert "error" in result
    assert "directory" in result["error"].lower() or "regular file" in result["error"].lower()


def test_analyze_xml_empty_file(tmp_path):
    empty_file = tmp_path / "empty.xml"
    empty_file.write_text("", encoding="utf-8")

    result = analyze_odoo_xml_view(str(empty_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "EMPTY_XML_FILE" for e in result["errors"])


def test_analyze_xml_unexpected_root(tmp_path):
    xml_content = """<custom_root>
    <record id="test_rec" model="res.partner"/>
</custom_root>"""
    xml_file = tmp_path / "custom_root.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    result = analyze_odoo_xml_view(str(xml_file))

    assert result["success"] is True
    assert any(w["code"] == "UNEXPECTED_ROOT_TAG" for w in result["warnings"])


def test_analyze_xml_duplicate_record_id(tmp_path):
    xml_content = """<odoo>
    <record id="duplicate_id" model="res.partner"/>
    <record id="duplicate_id" model="res.partner"/>
</odoo>"""
    xml_file = tmp_path / "duplicate.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    result = analyze_odoo_xml_view(str(xml_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "DUPLICATE_RECORD_ID" for e in result["errors"])
    duplicate_err = next(e for e in result["errors"] if e["code"] == "DUPLICATE_RECORD_ID")
    assert "duplicate_id" in duplicate_err["message"]


def test_analyze_xml_missing_record_attributes(tmp_path):
    xml_content = """<odoo>
    <record>
        <field name="name">No ID or Model</field>
    </record>
</odoo>"""
    xml_file = tmp_path / "missing_attrs.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    result = analyze_odoo_xml_view(str(xml_file))

    assert result["success"] is True
    assert result["valid"] is False
    error_codes = [e["code"] for e in result["errors"]]
    assert "MISSING_RECORD_ID" in error_codes
    assert "MISSING_RECORD_MODEL" in error_codes


def test_analyze_xml_view_missing_arch(tmp_path):
    xml_content = """<odoo>
    <record id="view_no_arch" model="ir.ui.view">
        <field name="name">No Arch View</field>
        <field name="model">res.partner</field>
    </record>
</odoo>"""
    xml_file = tmp_path / "no_arch.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    result = analyze_odoo_xml_view(str(xml_file))

    assert result["success"] is True
    assert any(w["code"] == "MISSING_ARCH" for w in result["warnings"])


def test_analyze_xml_view_empty_arch(tmp_path):
    xml_content = """<odoo>
    <record id="view_empty_arch" model="ir.ui.view">
        <field name="name">Empty Arch View</field>
        <field name="model">res.partner</field>
        <field name="arch" type="xml">
        </field>
    </record>
</odoo>"""
    xml_file = tmp_path / "empty_arch.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    result = analyze_odoo_xml_view(str(xml_file))

    assert result["success"] is True
    assert any(w["code"] == "EMPTY_ARCH" for w in result["warnings"])


def test_analyze_xml_external_id_references(tmp_path):
    xml_content = """<odoo>
    <record id="view_inherited" model="ir.ui.view">
        <field name="name">Inherited View</field>
        <field name="model">res.partner</field>
        <field name="inherit_id" ref="base.view_partner_form"/>
        <field name="arch" type="xml">
            <xpath expr="//field[@name='name']" position="after">
                <field name="email"/>
            </xpath>
        </field>
    </record>
</odoo>"""
    xml_file = tmp_path / "inherited_view.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    result = analyze_odoo_xml_view(str(xml_file))

    assert result["success"] is True
    assert result["valid"] is True
    assert any(
        info["code"] == "EXTERNAL_ID_REFERENCE" and "base.view_partner_form" in info["ref"]
        for info in result["info"]
    )


def test_analyze_xml_file_exceeding_size(tmp_path):
    xml_file = tmp_path / "huge.xml"
    xml_file.write_text("<odoo>" + " " * 500_001 + "</odoo>", encoding="utf-8")

    result = analyze_odoo_xml_view(str(xml_file))

    assert result["success"] is False
    assert "too large" in result["error"].lower()


def test_analyze_xml_addon_root_restriction(tmp_path):
    addon_dir = tmp_path / "my_addon"
    addon_dir.mkdir()
    outside_xml = tmp_path / "outside.xml"
    outside_xml.write_text("<odoo/>", encoding="utf-8")

    result = analyze_odoo_xml_view(
        xml_path=str(outside_xml),
        addon_path=str(addon_dir),
    )

    assert result["success"] is False
    assert "addon" in result["error"].lower()


def test_analyze_xml_non_string_input():
    result = analyze_odoo_xml_view(None)

    assert result["success"] is False
    assert "error" in result


def test_analyze_xml_unicode_decoding_error(tmp_path):
    bad_file = tmp_path / "bad_encoding.xml"
    bad_file.write_bytes(b"<odoo>\xff\xfe\x00</odoo>")

    result = analyze_odoo_xml_view(str(bad_file))

    assert result["success"] is False
    assert "decode" in result["error"].lower() or "read" in result["error"].lower()


# --------------------------------------------------
# Tests for analyze_odoo_python_model
# --------------------------------------------------

def test_analyze_python_valid_model(tmp_path):
    py_content = """# -*- coding: utf-8 -*-
from odoo import models, fields, api

class CustomOrder(models.Model):
    _name = 'custom.order'
    _description = 'Custom Sales Order'

    name = fields.Char(string='Order Reference', required=True)
    partner_id = fields.Many2one('res.partner', string='Customer', ondelete='restrict')
    line_ids = fields.One2many('custom.order.line', 'order_id', string='Order Lines')

    @api.depends('name')
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.name
"""
    py_file = tmp_path / "custom_order.py"
    py_file.write_text(py_content, encoding="utf-8")

    result = analyze_odoo_python_model(str(py_file))

    assert result["success"] is True
    assert result["valid"] is True
    assert result["summary"]["model_classes"] == 1
    assert result["summary"]["total_fields"] == 3
    assert result["errors"] == []
    assert result["models_found"][0]["name"] == "custom.order"
    assert result["models_found"][0]["description"] == "Custom Sales Order"


def test_analyze_python_syntax_error(tmp_path):
    py_content = """class BrokenModel(models.Model:
    pass
"""
    py_file = tmp_path / "broken.py"
    py_file.write_text(py_content, encoding="utf-8")

    result = analyze_odoo_python_model(str(py_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "PYTHON_SYNTAX_ERROR" for e in result["errors"])
    syntax_err = next(e for e in result["errors"] if e["code"] == "PYTHON_SYNTAX_ERROR")
    assert syntax_err["line"] is not None


def test_analyze_python_missing_file(tmp_path):
    missing_file = tmp_path / "missing_model.py"

    result = analyze_odoo_python_model(str(missing_file))

    assert result["success"] is False
    assert "error" in result
    assert "not exist" in result["error"].lower() or "not found" in result["error"].lower()


def test_analyze_python_directory_input(tmp_path):
    result = analyze_odoo_python_model(str(tmp_path))

    assert result["success"] is False
    assert "error" in result
    assert "directory" in result["error"].lower() or "regular file" in result["error"].lower()


def test_analyze_python_empty_file(tmp_path):
    empty_file = tmp_path / "empty.py"
    empty_file.write_text("   \n", encoding="utf-8")

    result = analyze_odoo_python_model(str(empty_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "EMPTY_PYTHON_FILE" for e in result["errors"])


def test_analyze_python_missing_description(tmp_path):
    py_content = """from odoo import models, fields

class NewModel(models.Model):
    _name = 'new.model'

    name = fields.Char(string='Name')
"""
    py_file = tmp_path / "no_desc.py"
    py_file.write_text(py_content, encoding="utf-8")

    result = analyze_odoo_python_model(str(py_file))

    assert result["success"] is True
    assert any(w["code"] == "MISSING_MODEL_DESCRIPTION" for w in result["warnings"])


def test_analyze_python_missing_model_name(tmp_path):
    py_content = """from odoo import models

class NamelessModel(models.Model):
    pass
"""
    py_file = tmp_path / "nameless.py"
    py_file.write_text(py_content, encoding="utf-8")

    result = analyze_odoo_python_model(str(py_file))

    assert result["success"] is True
    assert any(w["code"] == "MISSING_MODEL_NAME" for w in result["warnings"])


def test_analyze_python_many2one_missing_comodel(tmp_path):
    py_content = """from odoo import models, fields

class TestModel(models.Model):
    _name = 'test.model'
    _description = 'Test'

    partner_id = fields.Many2one(string='Partner')
"""
    py_file = tmp_path / "missing_comodel.py"
    py_file.write_text(py_content, encoding="utf-8")

    result = analyze_odoo_python_model(str(py_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "MISSING_COMODEL_NAME" for e in result["errors"])


def test_analyze_python_one2many_missing_inverse(tmp_path):
    py_content = """from odoo import models, fields

class TestModel(models.Model):
    _name = 'test.model'
    _description = 'Test'

    line_ids = fields.One2many('test.line', string='Lines')
"""
    py_file = tmp_path / "missing_inverse.py"
    py_file.write_text(py_content, encoding="utf-8")

    result = analyze_odoo_python_model(str(py_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "MISSING_INVERSE_NAME" for e in result["errors"])


def test_analyze_python_invalid_ondelete(tmp_path):
    py_content = """from odoo import models, fields

class TestModel(models.Model):
    _name = 'test.model'
    _description = 'Test'

    partner_id = fields.Many2one('res.partner', ondelete='remove_all')
"""
    py_file = tmp_path / "invalid_ondelete.py"
    py_file.write_text(py_content, encoding="utf-8")

    result = analyze_odoo_python_model(str(py_file))

    assert result["success"] is True
    assert any(w["code"] == "INVALID_ONDELETE" for w in result["warnings"])


def test_analyze_python_empty_api_depends(tmp_path):
    py_content = """from odoo import models, fields, api

class TestModel(models.Model):
    _name = 'test.model'
    _description = 'Test'

    total = fields.Float(compute='_compute_total')

    @api.depends()
    def _compute_total(self):
        pass
"""
    py_file = tmp_path / "empty_depends.py"
    py_file.write_text(py_content, encoding="utf-8")

    result = analyze_odoo_python_model(str(py_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "EMPTY_DEPENDS_ARGS" for e in result["errors"])


def test_analyze_python_empty_api_onchange(tmp_path):
    py_content = """from odoo import models, fields, api

class TestModel(models.Model):
    _name = 'test.model'
    _description = 'Test'

    @api.onchange()
    def _onchange_something(self):
        pass
"""
    py_file = tmp_path / "empty_onchange.py"
    py_file.write_text(py_content, encoding="utf-8")

    result = analyze_odoo_python_model(str(py_file))

    assert result["success"] is True
    assert any(w["code"] == "EMPTY_ONCHANGE_ARGS" for w in result["warnings"])


def test_analyze_python_file_exceeding_size(tmp_path):
    py_file = tmp_path / "huge.py"
    py_file.write_text("# comment\n" + "x = 1\n" * 100_000, encoding="utf-8")

    result = analyze_odoo_python_model(str(py_file))

    assert result["success"] is False
    assert "too large" in result["error"].lower()


def test_analyze_python_addon_root_restriction(tmp_path):
    addon_dir = tmp_path / "my_addon"
    addon_dir.mkdir()
    outside_py = tmp_path / "outside.py"
    outside_py.write_text("x = 1", encoding="utf-8")

    result = analyze_odoo_python_model(
        python_path=str(outside_py),
        addon_path=str(addon_dir),
    )

    assert result["success"] is False
    assert "addon" in result["error"].lower()


def test_analyze_python_non_string_input():
    result = analyze_odoo_python_model(None)

    assert result["success"] is False
    assert "error" in result


# --------------------------------------------------
# Tests for analyze_odoo_security
# --------------------------------------------------

def test_analyze_security_valid_csv(tmp_path):
    csv_content = (
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n"
        "access_custom_order_user,custom.order.user,model_custom_order,base.group_user,1,1,1,0\n"
        "access_custom_order_manager,custom.order.manager,model_custom_order,base.group_system,1,1,1,1\n"
    )
    csv_file = tmp_path / "ir.model.access.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    result = analyze_odoo_security(str(csv_file))

    assert result["success"] is True
    assert result["valid"] is True
    assert result["file_type"] == "csv"
    assert result["summary"]["total_rules"] == 2
    assert result["errors"] == []
    assert len(result["rules_found"]) == 2


def test_analyze_security_csv_missing_header_columns(tmp_path):
    csv_content = (
        "id,name,model_id:id,group_id:id,perm_read,perm_write\n"
        "access_order,order,model_order,base.group_user,1,1\n"
    )
    csv_file = tmp_path / "broken_header.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    result = analyze_odoo_security(str(csv_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "INVALID_CSV_HEADER" for e in result["errors"])


def test_analyze_security_csv_missing_model_prefix(tmp_path):
    csv_content = (
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n"
        "access_custom_order,custom.order,custom_order,base.group_user,1,1,1,0\n"
    )
    csv_file = tmp_path / "no_model_prefix.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    result = analyze_odoo_security(str(csv_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "MISSING_MODEL_PREFIX" for e in result["errors"])


def test_analyze_security_csv_invalid_permission_flag(tmp_path):
    csv_content = (
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n"
        "access_custom_order,custom.order,model_custom_order,base.group_user,1,yes,1,0\n"
    )
    csv_file = tmp_path / "invalid_perm.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    result = analyze_odoo_security(str(csv_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "INVALID_PERMISSION_FLAG" for e in result["errors"])


def test_analyze_security_csv_duplicate_id(tmp_path):
    csv_content = (
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n"
        "access_duplicate,rule1,model_custom_order,base.group_user,1,1,1,0\n"
        "access_duplicate,rule2,model_custom_order,base.group_system,1,1,1,1\n"
    )
    csv_file = tmp_path / "duplicate_id.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    result = analyze_odoo_security(str(csv_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "DUPLICATE_ACCESS_ID" for e in result["errors"])


def test_analyze_security_csv_row_column_mismatch(tmp_path):
    csv_content = (
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n"
        "access_order,order,model_order,1,1\n"
    )
    csv_file = tmp_path / "mismatch.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    result = analyze_odoo_security(str(csv_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "ROW_COLUMN_MISMATCH" for e in result["errors"])


def test_analyze_security_csv_global_rule_info(tmp_path):
    csv_content = (
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n"
        "access_global_order,global.order,model_custom_order,,1,0,0,0\n"
    )
    csv_file = tmp_path / "global.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    result = analyze_odoo_security(str(csv_file))

    assert result["success"] is True
    assert result["valid"] is True
    assert any(info["code"] == "GLOBAL_ACCESS_NOTICE" for info in result["info"])


def test_analyze_security_valid_xml_rule(tmp_path):
    xml_content = """<odoo>
    <record id="rule_custom_order_user" model="ir.rule">
        <field name="name">Custom Order User Rule</field>
        <field name="model_id" ref="model_custom_order"/>
        <field name="domain_force">[('create_uid', '=', user.id)]</field>
    </record>
</odoo>"""
    xml_file = tmp_path / "security_rules.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    result = analyze_odoo_security(str(xml_file))

    assert result["success"] is True
    assert result["valid"] is True
    assert result["file_type"] == "xml"
    assert result["summary"]["total_rules"] == 1
    assert result["errors"] == []


def test_analyze_security_xml_missing_model(tmp_path):
    xml_content = """<odoo>
    <record id="rule_no_model" model="ir.rule">
        <field name="name">No Model Rule</field>
        <field name="domain_force">[(1, '=', 1)]</field>
    </record>
</odoo>"""
    xml_file = tmp_path / "no_model_rule.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    result = analyze_odoo_security(str(xml_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "MISSING_RULE_MODEL" for e in result["errors"])


def test_analyze_security_xml_missing_domain(tmp_path):
    xml_content = """<odoo>
    <record id="rule_no_domain" model="ir.rule">
        <field name="name">No Domain Rule</field>
        <field name="model_id" ref="model_custom_order"/>
    </record>
</odoo>"""
    xml_file = tmp_path / "no_domain_rule.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    result = analyze_odoo_security(str(xml_file))

    assert result["success"] is True
    assert any(w["code"] == "MISSING_RULE_DOMAIN" for w in result["warnings"])


def test_analyze_security_empty_file(tmp_path):
    empty_file = tmp_path / "empty_sec.csv"
    empty_file.write_text("   \n", encoding="utf-8")

    result = analyze_odoo_security(str(empty_file))

    assert result["success"] is True
    assert result["valid"] is False
    assert any(e["code"] == "EMPTY_SECURITY_FILE" for e in result["errors"])


def test_analyze_security_missing_file(tmp_path):
    missing_file = tmp_path / "nonexistent.csv"

    result = analyze_odoo_security(str(missing_file))

    assert result["success"] is False
    assert "error" in result
    assert "not exist" in result["error"].lower() or "not found" in result["error"].lower()


def test_analyze_security_directory_input(tmp_path):
    result = analyze_odoo_security(str(tmp_path))

    assert result["success"] is False
    assert "error" in result
    assert "directory" in result["error"].lower() or "regular file" in result["error"].lower()


def test_analyze_security_addon_root_restriction(tmp_path):
    addon_dir = tmp_path / "my_addon"
    addon_dir.mkdir()
    outside_file = tmp_path / "outside.csv"
    outside_file.write_text("id,name\n", encoding="utf-8")

    result = analyze_odoo_security(
        file_path=str(outside_file),
        addon_path=str(addon_dir),
    )

    assert result["success"] is False
    assert "addon" in result["error"].lower()


def test_analyze_security_non_string_input():
    result = analyze_odoo_security(None)

    assert result["success"] is False
    assert "error" in result


# --------------------------------------------------
# Tests for check_odoo_addon_health
# --------------------------------------------------

def _create_full_valid_addon(addon_dir: Path) -> None:
    """Helper to create a fully valid Odoo addon."""
    addon_dir.mkdir(parents=True, exist_ok=True)
    (addon_dir / "__init__.py").write_text("# -*- coding: utf-8 -*-", encoding="utf-8")

    manifest = {
        "name": "Full Valid Addon",
        "version": "18.0.1.0.0",
        "depends": ["base"],
        "data": [
            "security/ir.model.access.csv",
            "views/order_views.xml",
        ],
        "installable": True,
    }
    (addon_dir / "__manifest__.py").write_text(repr(manifest), encoding="utf-8")

    models_dir = addon_dir / "models"
    models_dir.mkdir()
    (models_dir / "__init__.py").write_text("from . import order", encoding="utf-8")
    (models_dir / "order.py").write_text(
        "from odoo import models, fields, api\n\n"
        "class CustomOrder(models.Model):\n"
        "    _name = 'custom.order'\n"
        "    _description = 'Custom Order'\n"
        "    name = fields.Char(string='Reference')\n"
        "    partner_id = fields.Many2one('res.partner', string='Partner')\n",
        encoding="utf-8",
    )

    views_dir = addon_dir / "views"
    views_dir.mkdir()
    (views_dir / "order_views.xml").write_text(
        "<odoo>\n"
        "    <record id=\"view_custom_order_form\" model=\"ir.ui.view\">\n"
        "        <field name=\"name\">custom.order.form</field>\n"
        "        <field name=\"model\">custom.order</field>\n"
        "        <field name=\"arch\" type=\"xml\">\n"
        "            <form><field name=\"name\"/></form>\n"
        "        </field>\n"
        "    </record>\n"
        "</odoo>\n",
        encoding="utf-8",
    )

    security_dir = addon_dir / "security"
    security_dir.mkdir()
    (security_dir / "ir.model.access.csv").write_text(
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n"
        "access_custom_order,custom.order,model_custom_order,base.group_user,1,1,1,1\n",
        encoding="utf-8",
    )


def test_addon_health_clean_addon(tmp_path):
    addon_dir = tmp_path / "clean_addon"
    _create_full_valid_addon(addon_dir)

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    assert result["health_status"] == "HEALTHY"
    assert result["summary"]["total_errors"] == 0
    assert result["summary"]["total_warnings"] == 0
    assert result["summary"]["files_scanned"] >= 3


def test_addon_health_missing_manifest(tmp_path):
    no_manifest_dir = tmp_path / "no_manifest"
    no_manifest_dir.mkdir()

    result = check_odoo_addon_health(str(no_manifest_dir))

    assert result["success"] is False
    assert "manifest" in result["error"].lower()


def test_addon_health_nonexistent_directory(tmp_path):
    nonexistent = tmp_path / "nonexistent"

    result = check_odoo_addon_health(str(nonexistent))

    assert result["success"] is False
    assert "not exist" in result["error"].lower()


def test_addon_health_file_as_addon_path(tmp_path):
    dummy_file = tmp_path / "dummy.txt"
    dummy_file.write_text("hello", encoding="utf-8")

    result = check_odoo_addon_health(str(dummy_file))

    assert result["success"] is False
    assert "directory" in result["error"].lower()


def test_addon_health_critical_issues_on_manifest_error(tmp_path):
    addon_dir = tmp_path / "manifest_err_addon"
    addon_dir.mkdir()
    manifest = {
        "name": "Broken Manifest Addon",
        "version": "18.0.1.0.0",
        "depends": ["base"],
        "data": ["views/nonexistent_file.xml"],
    }
    (addon_dir / "__manifest__.py").write_text(repr(manifest), encoding="utf-8")

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    assert result["health_status"] == "CRITICAL_ISSUES"
    assert result["summary"]["total_errors"] > 0
    assert len(result["audit_breakdown"]["manifest"]["missing_files"]) > 0


def test_addon_health_detects_xml_syntax_error(tmp_path):
    addon_dir = tmp_path / "bad_xml_addon"
    _create_full_valid_addon(addon_dir)

    # Break the view XML
    (addon_dir / "views" / "order_views.xml").write_text(
        "<odoo><record id='view1'><field name='name'>Broken</record></odoo>",
        encoding="utf-8",
    )

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    assert result["health_status"] == "CRITICAL_ISSUES"
    assert any(not v["valid"] for v in result["audit_breakdown"]["views"])


def test_addon_health_detects_python_model_error(tmp_path):
    addon_dir = tmp_path / "bad_py_addon"
    _create_full_valid_addon(addon_dir)

    # Break the model
    (addon_dir / "models" / "order.py").write_text(
        "from odoo import models, fields\n"
        "class BrokenModel(models.Model):\n"
        "    _name = 'broken.model'\n"
        "    _description = 'Broken'\n"
        "    partner_id = fields.Many2one()\n",  # Missing comodel
        encoding="utf-8",
    )

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    assert result["health_status"] == "CRITICAL_ISSUES"
    assert any(not m["valid"] for m in result["audit_breakdown"]["models"])


def test_addon_health_detects_security_csv_error(tmp_path):
    addon_dir = tmp_path / "bad_sec_addon"
    _create_full_valid_addon(addon_dir)

    # Break the ACL CSV (missing model_ prefix)
    (addon_dir / "security" / "ir.model.access.csv").write_text(
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n"
        "access_custom_order,custom.order,custom_order,base.group_user,1,1,1,1\n",
        encoding="utf-8",
    )

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    assert result["health_status"] == "CRITICAL_ISSUES"
    assert any(not s["valid"] for s in result["audit_breakdown"]["security"])


def test_addon_health_warning_status(tmp_path):
    addon_dir = tmp_path / "warning_addon"
    _create_full_valid_addon(addon_dir)

    # Remove description from model (triggers warning, but not error)
    (addon_dir / "models" / "order.py").write_text(
        "from odoo import models, fields\n"
        "class WarningModel(models.Model):\n"
        "    _name = 'warning.model'\n"
        "    name = fields.Char(string='Name')\n",
        encoding="utf-8",
    )

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    assert result["health_status"] == "NEEDS_ATTENTION"
    assert result["summary"]["total_errors"] == 0
    assert result["summary"]["total_warnings"] > 0


def test_addon_health_with_dependencies(tmp_path):
    addon_dir = tmp_path / "dep_addon"
    _create_full_valid_addon(addon_dir)

    other_addons = tmp_path / "community_addons"
    other_addons.mkdir()
    (other_addons / "base").mkdir()

    result = check_odoo_addon_health(
        addon_path=str(addon_dir),
        addons_directories=[str(other_addons)],
    )

    assert result["success"] is True
    assert result["audit_breakdown"]["dependencies"]["checked"] is True
    assert result["audit_breakdown"]["dependencies"]["dependency_status"]["base"]["status"] == "found"


def test_addon_health_without_models_or_security(tmp_path):
    addon_dir = tmp_path / "views_only_addon"
    addon_dir.mkdir()
    manifest = {
        "name": "Views Only Addon",
        "version": "18.0.1.0.0",
        "depends": ["base"],
        "data": ["views/simple_view.xml"],
    }
    (addon_dir / "__manifest__.py").write_text(repr(manifest), encoding="utf-8")
    views_dir = addon_dir / "views"
    views_dir.mkdir()
    (views_dir / "simple_view.xml").write_text(
        "<odoo><record id='v1' model='ir.ui.view'><field name='name'>V1</field><field name='model'>res.partner</field><field name='arch' type='xml'><form/></field></record></odoo>",
        encoding="utf-8",
    )

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    assert result["health_status"] == "HEALTHY"
    assert len(result["audit_breakdown"]["models"]) == 0
    assert len(result["audit_breakdown"]["security"]) == 0


def test_addon_health_non_string_input():
    result = check_odoo_addon_health(None)

    assert result["success"] is False
    assert "error" in result


def test_addon_health_invalid_addons_directories_type(tmp_path):
    addon_dir = tmp_path / "test_addon"
    _create_full_valid_addon(addon_dir)

    result = check_odoo_addon_health(
        addon_path=str(addon_dir),
        addons_directories="invalid_string_not_list",
    )

    assert result["success"] is False
    assert "list" in result["error"].lower()


def test_addon_health_sample_addon_fixture():
    sample_addon = Path("sample_addons/my_custom_module")

    result = check_odoo_addon_health(str(sample_addon))

    assert result["success"] is True
    assert result["health_status"] == "CRITICAL_ISSUES"
    assert len(result["audit_breakdown"]["manifest"]["missing_files"]) == 2


def test_addon_health_aggregated_suggestions(tmp_path):
    addon_dir = tmp_path / "suggestions_addon"
    _create_full_valid_addon(addon_dir)

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    assert isinstance(result["suggestions"], list)
    assert len(result["suggestions"]) > 0


def test_main_entry_point_calls_mcp_run():
    with patch("server.mcp.run") as mock_run:
        main()
        mock_run.assert_called_once()


def test_addon_health_detects_missing_model_access_rule(tmp_path):
    addon_dir = tmp_path / "missing_sec_addon"
    _create_full_valid_addon(addon_dir)

    # Empty the CSV file (except header) so custom.order has no access rules
    (addon_dir / "security" / "ir.model.access.csv").write_text(
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n",
        encoding="utf-8",
    )

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    assert result["health_status"] == "NEEDS_ATTENTION"
    assert result["summary"]["cross_file_issues"] >= 1
    issues = result["audit_breakdown"]["cross_file_consistency"]["issues"]
    assert any(i["code"] == "MISSING_MODEL_ACCESS_RULE" and "custom.order" in i["message"] for i in issues)
    assert any("ir.model.access.csv" in s for s in result["suggestions"])


def test_addon_health_detects_undeclared_view_field(tmp_path):
    addon_dir = tmp_path / "bad_view_field_addon"
    _create_full_valid_addon(addon_dir)

    # Add an undeclared field to the view arch
    (addon_dir / "views" / "order_views.xml").write_text(
        "<odoo>\n"
        "    <record id=\"view_custom_order_form\" model=\"ir.ui.view\">\n"
        "        <field name=\"name\">custom.order.form</field>\n"
        "        <field name=\"model\">custom.order</field>\n"
        "        <field name=\"arch\" type=\"xml\">\n"
        "            <form>\n"
        "                <field name=\"name\"/>\n"
        "                <field name=\"nonexistent_field\"/>\n"
        "            </form>\n"
        "        </field>\n"
        "    </record>\n"
        "</odoo>\n",
        encoding="utf-8",
    )

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    assert result["health_status"] == "NEEDS_ATTENTION"
    issues = result["audit_breakdown"]["cross_file_consistency"]["issues"]
    assert any(
        i["code"] == "UNDECLARED_VIEW_FIELD"
        and i["element"] == "nonexistent_field"
        and "custom.order" in i["message"]
        for i in issues
    )
    assert any("view field references" in s.lower() for s in result["suggestions"])


def test_addon_health_allows_standard_odoo_base_fields(tmp_path):
    addon_dir = tmp_path / "base_fields_addon"
    _create_full_valid_addon(addon_dir)

    # Use standard base fields (id, display_name, create_date, active) in view
    (addon_dir / "views" / "order_views.xml").write_text(
        "<odoo>\n"
        "    <record id=\"view_custom_order_tree\" model=\"ir.ui.view\">\n"
        "        <field name=\"name\">custom.order.tree</field>\n"
        "        <field name=\"model\">custom.order</field>\n"
        "        <field name=\"arch\" type=\"xml\">\n"
        "            <tree>\n"
        "                <field name=\"id\"/>\n"
        "                <field name=\"display_name\"/>\n"
        "                <field name=\"name\"/>\n"
        "                <field name=\"create_date\"/>\n"
        "                <field name=\"active\"/>\n"
        "            </tree>\n"
        "        </field>\n"
        "    </record>\n"
        "</odoo>\n",
        encoding="utf-8",
    )

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    assert result["health_status"] == "HEALTHY"
    assert result["summary"]["cross_file_issues"] == 0


def test_addon_health_inherited_models_exempt_from_missing_access_rule(tmp_path):
    addon_dir = tmp_path / "inherit_addon"
    _create_full_valid_addon(addon_dir)

    # Replace model with an inherited model (e.g. res.partner extension) without _name
    (addon_dir / "models" / "order.py").write_text(
        "from odoo import models, fields\n\n"
        "class PartnerExtension(models.Model):\n"
        "    _inherit = 'res.partner'\n"
        "    loyalty_points = fields.Integer(string='Loyalty Points')\n",
        encoding="utf-8",
    )

    # Empty access CSV since res.partner already has security in base
    (addon_dir / "security" / "ir.model.access.csv").write_text(
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n",
        encoding="utf-8",
    )

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    # Should NOT flag MISSING_MODEL_ACCESS_RULE for inherited model
    issues = result["audit_breakdown"]["cross_file_consistency"]["issues"]
    assert not any(i["code"] == "MISSING_MODEL_ACCESS_RULE" for i in issues)


def test_addon_health_cross_file_consistency_breakdown_structure(tmp_path):
    addon_dir = tmp_path / "structure_addon"
    _create_full_valid_addon(addon_dir)

    result = check_odoo_addon_health(str(addon_dir))

    assert result["success"] is True
    cross_sec = result["audit_breakdown"]["cross_file_consistency"]
    assert "issues" in cross_sec
    assert "total_issues" in cross_sec
    assert "new_models_checked" in cross_sec
    assert "views_checked" in cross_sec
    assert cross_sec["new_models_checked"] == 1
    assert cross_sec["views_checked"] == 1


def test_verify_live_metadata_invalid_url():
    result = verify_odoo_live_metadata(
        url="",
        db="odoo",
        username="admin",
        password="secretpassword",
    )
    assert result["success"] is False
    assert "url" in result["error"].lower()

    result_scheme = verify_odoo_live_metadata(
        url="ftp://odoo.local",
        db="odoo",
        username="admin",
        password="secretpassword",
    )
    assert result_scheme["success"] is False
    assert "http" in result_scheme["error"].lower()


def test_verify_live_metadata_invalid_params():
    result = verify_odoo_live_metadata(
        url="http://localhost:8069",
        db="",
        username="admin",
        password="secretpassword",
    )
    assert result["success"] is False
    assert "database" in result["error"].lower()

    result_models = verify_odoo_live_metadata(
        url="http://localhost:8069",
        db="odoo",
        username="admin",
        password="secretpassword",
        models_to_check="not_a_list",
    )
    assert result_models["success"] is False
    assert "models_to_check" in result_models["error"]

    result_fields = verify_odoo_live_metadata(
        url="http://localhost:8069",
        db="odoo",
        username="admin",
        password="secretpassword",
        fields_to_check=["not_a_dict"],
    )
    assert result_fields["success"] is False
    assert "fields_to_check" in result_fields["error"]


def test_verify_live_metadata_auth_failure():
    with patch("server._get_server_proxy") as mock_proxy:
        common_mock = mock_proxy.return_value
        common_mock.authenticate.return_value = False

        result = verify_odoo_live_metadata(
            url="http://localhost:8069",
            db="odoo",
            username="admin",
            password="wrongpassword",
        )

        assert result["success"] is True
        assert result["authenticated"] is False
        assert "Authentication failed" in result["error"]
        assert len(result["suggestions"]) > 0


def test_verify_live_metadata_connection_error():
    with patch("server._get_server_proxy") as mock_proxy:
        common_mock = mock_proxy.return_value
        common_mock.authenticate.side_effect = ConnectionRefusedError("Connection refused")

        result = verify_odoo_live_metadata(
            url="http://localhost:8069",
            db="odoo",
            username="admin",
            password="secretpassword",
        )

        assert result["success"] is False
        assert result["error_type"] == "CONNECTION_ERROR"
        assert "Failed to connect" in result["error"]


def test_verify_live_metadata_models_found_and_missing():
    with patch("server._get_server_proxy") as mock_proxy:
        common_mock = mock_proxy.return_value
        common_mock.authenticate.return_value = 2  # UID

        # Mock object proxy
        object_mock = mock_proxy.return_value
        object_mock.execute_kw.return_value = [
            {"model": "sale.order", "name": "Sales Order", "state": "base"}
        ]

        result = verify_odoo_live_metadata(
            url="http://localhost:8069",
            db="odoo",
            username="admin",
            password="secretpassword",
            models_to_check=["sale.order", "nonexistent.model"],
        )

        assert result["success"] is True
        assert result["authenticated"] is True
        assert result["summary"]["models_checked"] == 2
        assert result["summary"]["missing_models"] == 1
        assert result["models"]["sale.order"]["exists"] is True
        assert result["models"]["sale.order"]["name"] == "Sales Order"
        assert result["models"]["nonexistent.model"]["exists"] is False
        assert any("nonexistent.model" in s for s in result["suggestions"])


def test_verify_live_metadata_fields_found_and_missing():
    with patch("server._get_server_proxy") as mock_proxy:
        common_mock = mock_proxy.return_value
        common_mock.authenticate.return_value = 2

        object_mock = mock_proxy.return_value
        object_mock.execute_kw.return_value = [
            {
                "name": "partner_id",
                "ttype": "many2one",
                "field_description": "Customer",
                "required": True,
                "readonly": False,
            }
        ]

        result = verify_odoo_live_metadata(
            url="http://localhost:8069",
            db="odoo",
            username="admin",
            password="secretpassword",
            fields_to_check={"sale.order": ["partner_id", "missing_field"]},
        )

        assert result["success"] is True
        assert result["summary"]["fields_checked"] == 2
        assert result["summary"]["missing_fields"] == 1
        assert result["fields"]["sale.order"]["partner_id"]["exists"] is True
        assert result["fields"]["sale.order"]["partner_id"]["type"] == "many2one"
        assert result["fields"]["sale.order"]["missing_field"]["exists"] is False
        assert any("missing_field" in s for s in result["suggestions"])


def test_verify_live_metadata_xml_ids_found_and_missing():
    with patch("server._get_server_proxy") as mock_proxy:
        common_mock = mock_proxy.return_value
        common_mock.authenticate.return_value = 2

        object_mock = mock_proxy.return_value
        object_mock.execute_kw.return_value = [
            {
                "module": "base",
                "name": "group_user",
                "model": "res.groups",
                "res_id": 4,
            }
        ]

        result = verify_odoo_live_metadata(
            url="http://localhost:8069",
            db="odoo",
            username="admin",
            password="secretpassword",
            xml_ids_to_check=["base.group_user", "my_module.missing_view"],
        )

        assert result["success"] is True
        assert result["summary"]["xml_ids_checked"] == 2
        assert result["summary"]["missing_xml_ids"] == 1
        assert result["xml_ids"]["base.group_user"]["exists"] is True
        assert result["xml_ids"]["base.group_user"]["model"] == "res.groups"
        assert result["xml_ids"]["my_module.missing_view"]["exists"] is False
        assert any("my_module.missing_view" in s for s in result["suggestions"])


def test_verify_live_metadata_credentials_never_leaked():
    with patch("server._get_server_proxy") as mock_proxy:
        common_mock = mock_proxy.return_value
        common_mock.authenticate.return_value = 2

        secret = "super_secret_token_12345"
        result = verify_odoo_live_metadata(
            url="http://localhost:8069",
            db="odoo",
            username="admin",
            password=secret,
            models_to_check=["res.partner"],
        )

        result_str = str(result)
        assert secret not in result_str


# --------------------------------------------------
# Tests for migrate_odoo_addon
# --------------------------------------------------

def test_migrate_addon_invalid_path(tmp_path: Path):
    # Non-string input
    res_num = migrate_odoo_addon(12345)
    assert res_num["success"] is False
    assert "Please provide an addon directory path" in res_num["error"]

    # Nonexistent path
    res_non = migrate_odoo_addon(str(tmp_path / "does_not_exist"))
    assert res_non["success"] is False
    assert "does not exist" in res_non["error"]

    # Path is a file, not a directory
    test_file = tmp_path / "some_file.txt"
    test_file.write_text("hello", encoding="utf-8")
    res_file = migrate_odoo_addon(str(test_file))
    assert res_file["success"] is False
    assert "is a file, not a directory" in res_file["error"]

    # Directory missing __manifest__.py
    empty_dir = tmp_path / "empty_module"
    empty_dir.mkdir()
    res_empty = migrate_odoo_addon(str(empty_dir))
    assert res_empty["success"] is False
    assert "No __manifest__.py found" in res_empty["error"]


def test_migrate_addon_dry_run_preserves_files(tmp_path: Path):
    addon = tmp_path / "demo_module"
    addon.mkdir()
    manifest_file = addon / "__manifest__.py"
    manifest_content = (
        "{\n"
        "    'name': 'Demo',\n"
        "    'version': '16.0.1.0.0',\n"
        "    'depends': ['base'],\n"
        "}\n"
    )
    manifest_file.write_text(manifest_content, encoding="utf-8")

    views_dir = addon / "views"
    views_dir.mkdir()
    view_file = views_dir / "demo_views.xml"
    view_content = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<odoo>\n'
        '    <record id="view_demo_tree" model="ir.ui.view">\n'
        '        <field name="name">demo.tree</field>\n'
        '        <field name="model">demo.model</field>\n'
        '        <field name="arch" type="xml">\n'
        '            <tree string="Demos" editable="bottom">\n'
        '                <field name="name" attrs="{\'invisible\': [(\'state\', \'=\', \'done\')]}"/>\n'
        '            </tree>\n'
        '        </field>\n'
        '    </record>\n'
        '</odoo>\n'
    )
    view_file.write_text(view_content, encoding="utf-8")

    result = migrate_odoo_addon(str(addon), target_version="19.0", dry_run=True, scaffold_openupgrade=True)

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["backup_path"] is None
    assert result["target_version"] == "19.0"
    assert result["summary"]["manifest_version_bumped"] is True
    assert result["summary"]["xml_tree_tags_migrated"] == 2
    assert result["summary"]["attrs_converted"] == 1
    assert result["summary"]["files_modified"] == 2
    assert result["openupgrade"]["scaffolded"] is True

    # Check that disk contents are strictly untouched in dry_run
    assert manifest_file.read_text(encoding="utf-8") == manifest_content
    assert view_file.read_text(encoding="utf-8") == view_content
    assert not (addon / "_migration_backups").exists()
    assert not (addon / "migrations").exists()


def test_migrate_addon_manifest_version_bump(tmp_path: Path):
    addon = tmp_path / "version_module"
    addon.mkdir()
    manifest_file = addon / "__manifest__.py"
    manifest_file.write_text(
        "{\n    'name': 'Version Test',\n    'version': '17.0.2.3.4',\n}\n",
        encoding="utf-8",
    )

    result = migrate_odoo_addon(str(addon), target_version="19.0", dry_run=False, scaffold_openupgrade=False)
    assert result["success"] is True
    assert result["summary"]["manifest_version_bumped"] is True

    updated_manifest = manifest_file.read_text(encoding="utf-8")
    assert "'version': '19.0.2.3.4'" in updated_manifest


def test_migrate_addon_tree_to_list(tmp_path: Path):
    addon = tmp_path / "tree_module"
    addon.mkdir()
    (addon / "__manifest__.py").write_text("{'name': 'Tree', 'version': '18.0.1.0'}", encoding="utf-8")

    views_dir = addon / "views"
    views_dir.mkdir()
    view_file = views_dir / "tree_view.xml"
    view_file.write_text(
        '<odoo>\n'
        '    <tree string="Items" create="true">\n'
        '        <field name="tree_structure"/>\n'
        '    </tree>\n'
        '</odoo>\n',
        encoding="utf-8",
    )

    result = migrate_odoo_addon(str(addon), dry_run=False, scaffold_openupgrade=False)
    assert result["success"] is True
    assert result["summary"]["xml_tree_tags_migrated"] == 2

    updated = view_file.read_text(encoding="utf-8")
    assert '<list string="Items" create="true">' in updated
    assert '</list>' in updated
    assert '<field name="tree_structure"/>' in updated
    assert '<tree' not in updated


def test_migrate_addon_attrs_to_direct_expressions(tmp_path: Path):
    addon = tmp_path / "attrs_module"
    addon.mkdir()
    (addon / "__manifest__.py").write_text("{'name': 'Attrs', 'version': '16.0.1.0'}", encoding="utf-8")

    view_file = addon / "view.xml"
    view_file.write_text(
        '<odoo>\n'
        '    <form>\n'
        '        <field name="partner_id" attrs="{\'invisible\': [(\'state\', \'=\', \'draft\')], \'readonly\': [(\'is_locked\', \'=\', True)]}"/>\n'
        '        <field name="notes" attrs="{\'invisible\': [\'|\', (\'state\', \'=\', \'cancel\'), (\'active\', \'=\', False)]}"/>\n'
        '    </form>\n'
        '</odoo>\n',
        encoding="utf-8",
    )

    result = migrate_odoo_addon(str(addon), dry_run=False, scaffold_openupgrade=False)
    assert result["success"] is True
    assert result["summary"]["attrs_converted"] == 2

    updated = view_file.read_text(encoding="utf-8")
    assert 'invisible="state == \'draft\'"' in updated
    assert 'readonly="is_locked"' in updated
    assert 'invisible="state == \'cancel\' or not active"' in updated
    assert 'attrs=' not in updated


def test_migrate_addon_states_conversion(tmp_path: Path):
    addon = tmp_path / "states_module"
    addon.mkdir()
    (addon / "__manifest__.py").write_text("{'name': 'States', 'version': '16.0.1.0'}", encoding="utf-8")

    view_file = addon / "view.xml"
    view_file.write_text(
        '<odoo>\n'
        '    <form>\n'
        '        <button name="action_confirm" states="draft"/>\n'
        '        <button name="action_cancel" states="draft,sent,waiting"/>\n'
        '    </form>\n'
        '</odoo>\n',
        encoding="utf-8",
    )

    result = migrate_odoo_addon(str(addon), dry_run=False, scaffold_openupgrade=False)
    assert result["success"] is True
    assert result["summary"]["states_converted"] == 2

    updated = view_file.read_text(encoding="utf-8")
    assert 'invisible="state != \'draft\'"' in updated
    assert "invisible=\"state not in ('draft', 'sent', 'waiting')\"" in updated
    assert 'states=' not in updated


def test_migrate_addon_apply_creates_backup(tmp_path: Path):
    addon = tmp_path / "backup_module"
    addon.mkdir()
    manifest = addon / "__manifest__.py"
    manifest.write_text("{'name': 'Backup', 'version': '16.0.1.0.0'}\n", encoding="utf-8")

    view = addon / "view.xml"
    view.write_text('<odoo><tree string="Test"/></odoo>\n', encoding="utf-8")

    result = migrate_odoo_addon(str(addon), dry_run=False, scaffold_openupgrade=False)
    assert result["success"] is True
    assert result["backup_path"] is not None

    backup_dir = Path(result["backup_path"])
    assert backup_dir.exists()
    assert backup_dir.is_dir()

    # The backup should preserve the original un-migrated files
    backup_manifest = (backup_dir / "__manifest__.py").read_text(encoding="utf-8")
    assert "'16.0.1.0.0'" in backup_manifest

    backup_view = (backup_dir / "view.xml").read_text(encoding="utf-8")
    assert '<tree string="Test"/>' in backup_view

    # While the actual files were updated
    assert '<list string="Test"/>' in view.read_text(encoding="utf-8")
    assert "'19.0.1.0.0'" in manifest.read_text(encoding="utf-8")


def test_migrate_addon_scaffolds_openupgrade_files(tmp_path: Path):
    addon = tmp_path / "openupgrade_module"
    addon.mkdir()
    (addon / "__manifest__.py").write_text("{'name': 'OU', 'version': '17.0.1.0'}", encoding="utf-8")

    result = migrate_odoo_addon(str(addon), target_version="19.0", dry_run=False, scaffold_openupgrade=True)
    assert result["success"] is True
    assert result["openupgrade"]["scaffolded"] is True

    migrations_dir = addon / "migrations" / "19.0.1.0"
    pre_file = migrations_dir / "pre-migration.py"
    post_file = migrations_dir / "post-migration.py"
    readme_file = migrations_dir / "README.md"

    assert pre_file.exists()
    assert post_file.exists()
    assert readme_file.exists()

    pre_content = pre_file.read_text(encoding="utf-8")
    assert "OpenUpgrade pre-migration script for Odoo 19.0" in pre_content
    assert "from openupgradelib import openupgrade" in pre_content
    assert "def migrate(env, version):" in pre_content

    post_content = post_file.read_text(encoding="utf-8")
    assert "OpenUpgrade post-migration script for Odoo 19.0" in post_content
    assert "def migrate(env, version):" in post_content

    readme_content = readme_file.read_text(encoding="utf-8")
    assert "upgrading this addon to Odoo 19.0" in readme_content
    assert "-u openupgrade_module" in readme_content

    # Test idempotency: custom modifications in pre-migration.py must not be overwritten
    pre_file.write_text("# Custom migration logic here\n", encoding="utf-8")
    result_second = migrate_odoo_addon(str(addon), target_version="19.0", dry_run=False, scaffold_openupgrade=True)
    assert pre_file.read_text(encoding="utf-8") == "# Custom migration logic here\n"


def test_migrate_addon_detects_name_get_and_suggests_compute_display_name(tmp_path: Path):
    addon = tmp_path / "name_get_addon"
    addon.mkdir()
    (addon / "__manifest__.py").write_text("{'name': 'NameGet', 'version': '16.0.1.0'}", encoding="utf-8")
    models_dir = addon / "models"
    models_dir.mkdir()
    py_file = models_dir / "custom_model.py"
    py_file.write_text(
        "from odoo import models, fields\n\n"
        "class CustomOrder(models.Model):\n"
        "    _name = 'custom.order'\n"
        "    name = fields.Char('Name')\n\n"
        "    def name_get(self):\n"
        "        result = []\n"
        "        for rec in self:\n"
        "            result.append((rec.id, f'{rec.name}'))\n"
        "        return result\n",
        encoding="utf-8",
    )

    result = migrate_odoo_addon(str(addon), dry_run=True, scaffold_openupgrade=False)
    assert result["success"] is True
    assert result["summary"]["python_files_scanned"] == 1
    assert result["summary"]["name_get_count"] == 1
    assert any("name_get" in s for s in result["suggestions"])

    # Check python_audit details
    name_get_issue = next(i for i in result["python_audit"] if i["issue_type"] == "NAME_GET_DEPRECATED")
    assert name_get_issue["class_name"] == "CustomOrder"
    assert name_get_issue["method_name"] == "name_get"
    assert "_compute_display_name" in name_get_issue["suggested_fix"]


def test_migrate_addon_detects_create_missing_model_create_multi(tmp_path: Path):
    addon = tmp_path / "create_addon"
    addon.mkdir()
    (addon / "__manifest__.py").write_text("{'name': 'CreateTest', 'version': '17.0.1.0'}", encoding="utf-8")
    py_file = addon / "models.py"
    py_file.write_text(
        "from odoo import models, fields, api\n\n"
        "class SingleCreate(models.Model):\n"
        "    _name = 'single.create'\n\n"
        "    def create(self, vals):\n"
        "        return super().create(vals)\n\n"
        "class MultiCreate(models.Model):\n"
        "    _name = 'multi.create'\n\n"
        "    @api.model_create_multi\n"
        "    def create(self, vals_list):\n"
        "        return super().create(vals_list)\n",
        encoding="utf-8",
    )

    result = migrate_odoo_addon(str(addon), dry_run=True, scaffold_openupgrade=False)
    assert result["success"] is True
    assert result["summary"]["create_without_multi_count"] == 1
    assert any("missing @api.model_create_multi" in s for s in result["suggestions"])

    create_issue = next(i for i in result["python_audit"] if i["issue_type"] == "CREATE_MISSING_MODEL_CREATE_MULTI")
    assert create_issue["class_name"] == "SingleCreate"
    assert "vals_list" in create_issue["suggested_fix"]


def test_migrate_addon_detects_deprecated_orm_calls(tmp_path: Path):
    addon = tmp_path / "deprecated_calls_addon"
    addon.mkdir()
    (addon / "__manifest__.py").write_text("{'name': 'DepCalls', 'version': '16.0.1.0'}", encoding="utf-8")
    py_file = addon / "service.py"
    py_file.write_text(
        "from odoo import models, fields, api\n\n"
        "class LegacyService(models.Model):\n"
        "    _name = 'legacy.service'\n\n"
        "    @api.multi\n"
        "    def action_sync(self):\n"
        "        today_str = fields.Date.to_string(fields.Date.today())\n"
        "        self.env.cr.commit()\n"
        "        return today_str\n\n"
        "    def _name_search(self, name='', args=None, operator='ilike', limit=100, name_get_uid=None):\n"
        "        return super()._name_search(name, args, operator, limit, name_get_uid)\n",
        encoding="utf-8",
    )

    result = migrate_odoo_addon(str(addon), dry_run=True, scaffold_openupgrade=False)
    assert result["success"] is True
    assert result["summary"]["deprecated_orm_calls_count"] == 4

    issue_types = {i["issue_type"] for i in result["python_audit"]}
    assert "OBSOLETE_DECORATOR" in issue_types
    assert "DEPRECATED_DATETIME_CONVERSION" in issue_types
    assert "MANUAL_TRANSACTION_COMMIT" in issue_types
    assert "NAME_SEARCH_LEGACY_SIGNATURE" in issue_types


def test_migrate_addon_enterprise_dependency_detection(tmp_path: Path):
    addon = tmp_path / "enterprise_addon"
    addon.mkdir()
    manifest_file = addon / "__manifest__.py"
    manifest_file.write_text(
        "{\n"
        "    'name': 'Enterprise Feature',\n"
        "    'version': '16.0.1.0.0',\n"
        "    'depends': ['sale_management', 'account_reports', 'helpdesk', 'web_studio'],\n"
        "}\n",
        encoding="utf-8",
    )

    result = migrate_odoo_addon(str(addon), dry_run=True, scaffold_openupgrade=False)
    assert result["success"] is True
    assert result["summary"]["edition_requirement"] == "Enterprise"
    assert result["enterprise_audit"]["edition_requirement"] == "Enterprise"
    assert "account_reports" in result["enterprise_audit"]["enterprise_dependencies"]
    assert "helpdesk" in result["enterprise_audit"]["enterprise_dependencies"]
    assert result["enterprise_audit"]["has_studio_customizations"] is True
    assert any("Edition requirement: Enterprise" in s for s in result["suggestions"])


def test_migrate_addon_community_dependency_detection(tmp_path: Path):
    addon = tmp_path / "community_addon"
    addon.mkdir()
    manifest_file = addon / "__manifest__.py"
    manifest_file.write_text(
        "{\n"
        "    'name': 'Community Module',\n"
        "    'version': '17.0.1.0.0',\n"
        "    'depends': ['base', 'sale', 'stock', 'account'],\n"
        "}\n",
        encoding="utf-8",
    )

    result = migrate_odoo_addon(str(addon), dry_run=True, scaffold_openupgrade=False)
    assert result["success"] is True
    assert result["summary"]["edition_requirement"] == "Community"
    assert result["enterprise_audit"]["edition_requirement"] == "Community"
    assert result["enterprise_audit"]["enterprise_dependencies"] == []
    assert result["enterprise_audit"]["has_studio_customizations"] is False
    assert any("compatible with Odoo 19 Community Edition" in note for note in result["enterprise_audit"]["notes"])


def test_migrate_addon_handles_python_syntax_error_gracefully(tmp_path: Path):
    addon = tmp_path / "broken_python_addon"
    addon.mkdir()
    (addon / "__manifest__.py").write_text("{'name': 'Broken', 'version': '16.0.1.0'}", encoding="utf-8")
    (addon / "broken.py").write_text("def invalid_syntax(\n", encoding="utf-8")

    result = migrate_odoo_addon(str(addon), dry_run=True, scaffold_openupgrade=False)
    assert result["success"] is True
    assert result["summary"]["python_files_scanned"] == 1
    syntax_issue = next(i for i in result["python_audit"] if i["issue_type"] == "PYTHON_SYNTAX_ERROR")
    assert "Failed to parse Python syntax" in syntax_issue["message"]


# --------------------------------------------------
# Tests for analyze_odoo_owl_component
# --------------------------------------------------

def test_analyze_owl_invalid_path(tmp_path: Path):
    # Non-string path
    res_num = analyze_odoo_owl_component(12345)
    assert res_num["success"] is False
    assert "Please provide a file or directory path" in res_num["error"]

    # Nonexistent path
    res_non = analyze_odoo_owl_component(str(tmp_path / "does_not_exist"))
    assert res_non["success"] is False
    assert "Path does not exist" in res_non["error"]

    # Unsupported extension
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("notes", encoding="utf-8")
    res_ext = analyze_odoo_owl_component(str(txt_file))
    assert res_ext["success"] is False
    assert "Unsupported file extension" in res_ext["error"]

    # Out of addon sandbox
    addon_dir = tmp_path / "addon"
    addon_dir.mkdir()
    outside_file = tmp_path / "outside.js"
    outside_file.write_text("console.log('hi');", encoding="utf-8")
    res_sandbox = analyze_odoo_owl_component(str(outside_file), addon_path=str(addon_dir))
    assert res_sandbox["success"] is False
    assert "is outside the addon directory" in res_sandbox["error"]


def test_analyze_owl_component_valid_js(tmp_path: Path):
    js_file = tmp_path / "custom_widget.js"
    js_file.write_text(
        "/** @odoo-module **/\n\n"
        "import { Component } from '@odoo/owl';\n"
        "import { registry } from '@web/core/registry';\n\n"
        "export class CustomWidget extends Component {\n"
        "    static template = 'my_module.CustomWidget';\n"
        "}\n\n"
        "registry.category('fields').add('custom_widget', CustomWidget);\n",
        encoding="utf-8",
    )

    res = analyze_odoo_owl_component(str(js_file))
    assert res["success"] is True
    assert res["valid"] is True
    assert res["summary"]["error_count"] == 0
    assert res["summary"]["components_found"] == 1
    assert res["components"][0]["class_name"] == "CustomWidget"
    assert res["components"][0]["template"] == "my_module.CustomWidget"
    assert res["summary"]["registries_found"] == 1
    assert res["registries"][0]["category"] == "fields"
    assert res["registries"][0]["name"] == "custom_widget"


def test_analyze_owl_component_missing_module_header(tmp_path: Path):
    js_file = tmp_path / "no_header.js"
    js_file.write_text(
        "import { Component } from '@odoo/owl';\n\n"
        "export class NoHeader extends Component {\n"
        "    static template = 'my_module.NoHeader';\n"
        "}\n",
        encoding="utf-8",
    )

    res = analyze_odoo_owl_component(str(js_file))
    assert res["success"] is True
    assert any(w["code"] == "MISSING_ODOO_MODULE_HEADER" for w in res["warnings"])
    assert any("/** @odoo-module **/" in s for s in res["suggestions"])


def test_analyze_owl_component_legacy_define_and_widget(tmp_path: Path):
    js_file = tmp_path / "legacy_widget.js"
    js_file.write_text(
        "odoo.define('my_module.LegacyWidget', function (require) {\n"
        "    'use strict';\n"
        "    const Widget = require('web.Widget');\n"
        "    const core = require('web.core');\n\n"
        "    const MyWidget = Widget.extend({\n"
        "        init: function () {}\n"
        "    });\n"
        "    return MyWidget;\n"
        "});\n",
        encoding="utf-8",
    )

    res = analyze_odoo_owl_component(str(js_file))
    assert res["success"] is True
    assert res["valid"] is False
    assert res["summary"]["error_count"] >= 3
    error_codes = {e["code"] for e in res["errors"]}
    assert "LEGACY_ODOO_DEFINE" in error_codes
    assert "LEGACY_WIDGET_EXTEND" in error_codes
    assert "LEGACY_REQUIRE_MODULE" in error_codes


def test_analyze_owl_component_legacy_patch_syntax(tmp_path: Path):
    js_file = tmp_path / "patch_test.js"
    js_file.write_text(
        "/** @odoo-module **/\n"
        "import { patch } from '@web/core/utils/patch';\n"
        "import { Orderline } from '@point_of_sale/app/generic_components/orderline/orderline';\n\n"
        "patch(Orderline.prototype, 'pos_restaurant.Orderline', {\n"
        "    setup() {\n"
        "        super.setup();\n"
        "    }\n"
        "});\n",
        encoding="utf-8",
    )

    res = analyze_odoo_owl_component(str(js_file))
    assert res["success"] is True
    assert any(w["code"] == "LEGACY_PATCH_SYNTAX" for w in res["warnings"])
    assert any("Update patch() calls" in s for s in res["suggestions"])


def test_analyze_owl_component_valid_xml_template(tmp_path: Path):
    xml_file = tmp_path / "template.xml"
    xml_file.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<templates xml:space="preserve">\n'
        '    <t t-name="my_module.CustomWidget">\n'
        '        <div class="custom-widget">\n'
        '            <span t-out="props.title"/>\n'
        '        </div>\n'
        '    </t>\n'
        '</templates>\n',
        encoding="utf-8",
    )

    res = analyze_odoo_owl_component(str(xml_file))
    assert res["success"] is True
    assert res["valid"] is True
    assert res["summary"]["templates_found"] == 1
    assert res["templates"][0]["name"] == "my_module.CustomWidget"
    assert res["summary"]["error_count"] == 0


def test_analyze_owl_component_obsolete_t_raw(tmp_path: Path):
    xml_file = tmp_path / "bad_template.xml"
    xml_file.write_text(
        '<templates xml:space="preserve">\n'
        '    <t t-name="my_module.BadWidget">\n'
        '        <div t-raw="props.htmlContent"/>\n'
        '    </t>\n'
        '</templates>\n',
        encoding="utf-8",
    )

    res = analyze_odoo_owl_component(str(xml_file))
    assert res["success"] is True
    assert res["valid"] is False
    assert any(e["code"] == "OBSOLETE_T_RAW" for e in res["errors"])


def test_analyze_owl_component_duplicate_template_name(tmp_path: Path):
    xml_file = tmp_path / "dup_templates.xml"
    xml_file.write_text(
        '<templates>\n'
        '    <t t-name="my_module.DupWidget"><div>A</div></t>\n'
        '    <t t-name="my_module.DupWidget"><div>B</div></t>\n'
        '</templates>\n',
        encoding="utf-8",
    )

    res = analyze_odoo_owl_component(str(xml_file))
    assert res["success"] is True
    assert any(e["code"] == "DUPLICATE_TEMPLATE_NAME" for e in res["errors"])


def test_analyze_owl_component_directory_cross_reference(tmp_path: Path):
    src_dir = tmp_path / "static" / "src"
    src_dir.mkdir(parents=True)

    js_file = src_dir / "counter.js"
    js_file.write_text(
        "/** @odoo-module **/\n"
        "import { Component } from '@odoo/owl';\n\n"
        "export class Counter extends Component {\n"
        "    static template = 'my_module.Counter';\n"
        "}\n",
        encoding="utf-8",
    )

    xml_file = src_dir / "counter.xml"
    xml_file.write_text(
        '<templates xml:space="preserve">\n'
        '    <t t-name="my_module.Counter">\n'
        '        <button t-on-click="increment">Click</button>\n'
        '    </t>\n'
        '</templates>\n',
        encoding="utf-8",
    )

    res = analyze_odoo_owl_component(str(src_dir))
    assert res["success"] is True
    assert res["valid"] is True
    assert res["summary"]["files_scanned"] == 2
    assert res["summary"]["components_found"] == 1
    assert res["summary"]["templates_found"] == 1
    # Should have no UNRESOLVED_OWL_TEMPLATE warning because counter matches counter.xml
    assert not any(w["code"] == "UNRESOLVED_OWL_TEMPLATE" for w in res["warnings"])


def test_analyze_owl_component_unresolved_template(tmp_path: Path):
    src_dir = tmp_path / "static" / "src"
    src_dir.mkdir(parents=True)

    js_file = src_dir / "unresolved.js"
    js_file.write_text(
        "/** @odoo-module **/\n"
        "import { Component } from '@odoo/owl';\n\n"
        "export class UnresolvedComponent extends Component {\n"
        "    static template = 'my_module.MissingTemplate';\n"
        "}\n",
        encoding="utf-8",
    )

    xml_file = src_dir / "other.xml"
    xml_file.write_text(
        '<templates>\n'
        '    <t t-name="my_module.OtherTemplate"><span/></t>\n'
        '</templates>\n',
        encoding="utf-8",
    )

    res = analyze_odoo_owl_component(str(src_dir))
    assert res["success"] is True
    assert any(w["code"] == "UNRESOLVED_OWL_TEMPLATE" for w in res["warnings"])
    assert any("my_module.MissingTemplate" in w["message"] for w in res["warnings"])


def test_analyze_owl_component_file_size_and_empty(tmp_path: Path):
    empty_js = tmp_path / "empty.js"
    empty_js.write_text("   \n", encoding="utf-8")
    res_empty_js = analyze_odoo_owl_component(str(empty_js))
    assert res_empty_js["success"] is True
    assert any(e["code"] == "EMPTY_JS_FILE" for e in res_empty_js["errors"])

    empty_xml = tmp_path / "empty.xml"
    empty_xml.write_text("   \n", encoding="utf-8")
    res_empty_xml = analyze_odoo_owl_component(str(empty_xml))
    assert res_empty_xml["success"] is True
    assert any(e["code"] == "EMPTY_TEMPLATE_FILE" for e in res_empty_xml["errors"])

    large_js = tmp_path / "large.js"
    large_js.write_text("a" * 500_001, encoding="utf-8")
    res_large = analyze_odoo_owl_component(str(large_js))
    assert res_large["success"] is False
    assert "File is too large to inspect" in res_large["error"]


# --------------------------------------------------
# Tests for generate_openupgrade_scripts
# --------------------------------------------------

def test_generate_openupgrade_scripts_invalid_path(tmp_path: Path):
    res_empty = generate_openupgrade_scripts("")
    assert res_empty["success"] is False
    assert "Please provide an addon directory path" in res_empty["error"]

    res_nonexistent = generate_openupgrade_scripts(str(tmp_path / "does_not_exist"))
    assert res_nonexistent["success"] is False
    assert "does not exist" in res_nonexistent["error"]

    file_path = tmp_path / "some_file.txt"
    file_path.write_text("hello", encoding="utf-8")
    res_file = generate_openupgrade_scripts(str(file_path))
    assert res_file["success"] is False
    assert "Provided path is a file" in res_file["error"]

    addon_no_manifest = tmp_path / "no_manifest"
    addon_no_manifest.mkdir()
    res_no_manifest = generate_openupgrade_scripts(str(addon_no_manifest))
    assert res_no_manifest["success"] is False
    assert "No __manifest__.py found" in res_no_manifest["error"]


def test_generate_openupgrade_scripts_dry_run_previews_code(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Module", "version": "18.0.1.0.0"})
    res = generate_openupgrade_scripts(str(addon), target_version="19.0", dry_run=True)

    assert res["success"] is True
    assert res["dry_run"] is True
    assert res["summary"]["files_planned"] == 3
    assert res["summary"]["files_written"] == 0
    assert not (addon / "migrations").exists()

    file_map = {f["file"]: f["content"] for f in res["files"]}
    assert "migrations/19.0.1.0/pre-migration.py" in file_map
    assert "migrations/19.0.1.0/post-migration.py" in file_map
    assert "migrations/19.0.1.0/README.md" in file_map
    assert "openupgradelib" in file_map["migrations/19.0.1.0/pre-migration.py"]


def test_generate_openupgrade_scripts_creates_column_and_model_renames(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Module", "version": "18.0.1.0.0"})
    col_renames = {
        "sale_order": [("client_order_ref", "customer_reference")],
        "res_partner": [("old_fax", "new_fax")],
    }
    model_renames = [("old.sales.lead", "crm.lead"), ("custom.partner", "res.partner")]

    res = generate_openupgrade_scripts(
        str(addon),
        target_version="19.0",
        column_renames=col_renames,
        model_renames=model_renames,
        dry_run=False,
    )

    assert res["success"] is True
    assert res["summary"]["files_written"] == 3
    assert res["summary"]["column_renames_count"] == 2
    assert res["summary"]["model_renames_count"] == 2

    pre_file = addon / "migrations" / "19.0.1.0" / "pre-migration.py"
    assert pre_file.exists()
    content = pre_file.read_text(encoding="utf-8")

    assert "COLUMN_RENAMES = {" in content
    assert '"sale_order": [' in content
    assert '("client_order_ref", "customer_reference")' in content
    assert "MODEL_RENAMES = [" in content
    assert '("old.sales.lead", "crm.lead")' in content
    assert "openupgrade.rename_columns(env.cr, COLUMN_RENAMES)" in content
    assert "openupgrade.rename_models(env.cr, MODEL_RENAMES)" in content


def test_generate_openupgrade_scripts_creates_xmlid_renames(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Module", "version": "18.0.1.0.0"})
    xmlids = [
        ("my_module.old_view_tree", "my_module.old_view_list"),
        ("my_module.action_window_old", "my_module.action_window_new"),
    ]

    res = generate_openupgrade_scripts(
        str(addon),
        target_version="19.0",
        xmlid_renames=xmlids,
        dry_run=False,
    )

    assert res["success"] is True
    assert res["summary"]["xmlid_renames_count"] == 2

    pre_file = addon / "migrations" / "19.0.1.0" / "pre-migration.py"
    content = pre_file.read_text(encoding="utf-8")
    assert "XMLID_RENAMES = [" in content
    assert '("my_module.old_view_tree", "my_module.old_view_list")' in content
    assert "openupgrade.rename_xmlids(env.cr, XMLID_RENAMES)" in content


def test_generate_openupgrade_scripts_creates_selection_and_compute_backfills(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Module", "version": "18.0.1.0.0"})
    selection_map = {
        "sale_order": {
            "state": {"draft": "quotation", "sent": "quotation_sent"}
        }
    }
    compute_backfills = [
        {"model": "sale.order", "field": "amount_tax"},
        {"model": "res.partner", "field": "display_name"},
    ]

    res = generate_openupgrade_scripts(
        str(addon),
        target_version="19.0",
        selection_renames=selection_map,
        compute_backfills=compute_backfills,
        dry_run=False,
    )

    assert res["success"] is True
    assert res["summary"]["selection_renames_count"] == 2
    assert res["summary"]["compute_backfills_count"] == 2

    post_file = addon / "migrations" / "19.0.1.0" / "post-migration.py"
    assert post_file.exists()
    content = post_file.read_text(encoding="utf-8")

    assert 'UPDATE sale_order SET state = %s WHERE state = %s", (\'quotation\', \'draft\')' in content
    assert 'UPDATE sale_order SET state = %s WHERE state = %s", (\'quotation_sent\', \'sent\')' in content
    assert 'env["sale.order"].search([("amount_tax", "=", False)])' in content
    assert "records._compute_amount_tax()" in content
    assert 'env["res.partner"].search([("display_name", "=", False)])' in content
    assert "records._compute_display_name()" in content


def test_generate_openupgrade_scripts_overwrite_protection(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Module", "version": "18.0.1.0.0"})
    mig_dir = addon / "migrations" / "19.0.1.0"
    mig_dir.mkdir(parents=True)
    custom_pre = mig_dir / "pre-migration.py"
    custom_pre.write_text("# CUSTOM PRE SCRIPT", encoding="utf-8")

    # Dry run with overwrite=False
    res_dry = generate_openupgrade_scripts(str(addon), target_version="19.0", dry_run=True, overwrite=False)
    assert res_dry["success"] is True
    pre_stat = next(f for f in res_dry["files"] if "pre-migration.py" in f["file"])
    assert pre_stat["status"] == "skipped_exists"

    # Actual run with overwrite=False
    res_no_over = generate_openupgrade_scripts(str(addon), target_version="19.0", dry_run=False, overwrite=False)
    assert res_no_over["success"] is True
    assert res_no_over["summary"]["files_skipped"] == 1
    assert custom_pre.read_text(encoding="utf-8") == "# CUSTOM PRE SCRIPT"

    # Actual run with overwrite=True
    res_over = generate_openupgrade_scripts(str(addon), target_version="19.0", dry_run=False, overwrite=True)
    assert res_over["success"] is True
    assert res_over["summary"]["files_written"] == 3
    assert custom_pre.read_text(encoding="utf-8") != "# CUSTOM PRE SCRIPT"


def test_migrate_addon_with_custom_openupgrade_renames(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Module", "version": "18.0.1.0.0"})
    col_renames = {"project_task": [("old_status", "stage_id")]}
    compute_backfills = [{"model": "project.task", "field": "effective_hours"}]

    res = migrate_odoo_addon(
        str(addon),
        target_version="19.0",
        dry_run=False,
        scaffold_openupgrade=True,
        column_renames=col_renames,
        compute_backfills=compute_backfills,
    )

    assert res["success"] is True
    assert res["summary"]["openupgrade_scaffolded"] is True

    pre_file = addon / "migrations" / "19.0.1.0" / "pre-migration.py"
    assert pre_file.exists()
    assert '("old_status", "stage_id")' in pre_file.read_text(encoding="utf-8")

    post_file = addon / "migrations" / "19.0.1.0" / "post-migration.py"
    assert post_file.exists()
    assert 'env["project.task"].search([("effective_hours", "=", False)])' in post_file.read_text(encoding="utf-8")


# --------------------------------------------------
# Tests for scaffold_odoo_tests
# --------------------------------------------------

def test_scaffold_odoo_tests_invalid_path(tmp_path: Path):
    res_empty = scaffold_odoo_tests("")
    assert res_empty["success"] is False
    assert "Please provide an addon directory path" in res_empty["error"]

    res_nonexistent = scaffold_odoo_tests(str(tmp_path / "does_not_exist"))
    assert res_nonexistent["success"] is False
    assert "does not exist" in res_nonexistent["error"]

    file_path = tmp_path / "regular_file.txt"
    file_path.write_text("dummy", encoding="utf-8")
    res_file = scaffold_odoo_tests(str(file_path))
    assert res_file["success"] is False
    assert "Provided path is a file" in res_file["error"]

    no_manifest = tmp_path / "no_manifest_addon"
    no_manifest.mkdir()
    res_no_manifest = scaffold_odoo_tests(str(no_manifest))
    assert res_no_manifest["success"] is False
    assert "No __manifest__.py found" in res_no_manifest["error"]


def test_scaffold_odoo_tests_dry_run_previews_code(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Dry Run", "version": "18.0.1.0.0"})
    models_dir = addon / "models"
    models_dir.mkdir()
    (models_dir / "custom_item.py").write_text(
        "from odoo import models, fields\n\n"
        "class CustomItem(models.Model):\n"
        "    _name = 'custom.item'\n"
        "    _description = 'Custom Item'\n"
        "    name = fields.Char(string='Name', required=True)\n",
        encoding="utf-8",
    )

    res = scaffold_odoo_tests(str(addon), dry_run=True)
    assert res["success"] is True
    assert res["dry_run"] is True
    assert res["summary"]["files_planned"] == 2
    assert res["summary"]["files_written"] == 0
    assert res["summary"]["models_covered"] == 1
    assert not (addon / "tests").exists()

    file_map = {f["file"]: f["content"] for f in res["files"]}
    assert "tests/__init__.py" in file_map
    assert "tests/test_custom_item.py" in file_map
    assert "TestCustomItem(TransactionCase)" in file_map["tests/test_custom_item.py"]


def test_scaffold_odoo_tests_scaffolds_model_crud_and_fields(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test CRUD", "version": "18.0.1.0.0"})
    models_dir = addon / "models"
    models_dir.mkdir()
    (models_dir / "order.py").write_text(
        "from odoo import models, fields\n\n"
        "class SaleCustomOrder(models.Model):\n"
        "    _name = 'sale.custom.order'\n"
        "    _description = 'Sales Custom Order'\n"
        "    name = fields.Char(string='Order Ref', required=True)\n"
        "    priority = fields.Integer(string='Priority', required=True)\n"
        "    is_urgent = fields.Boolean(string='Urgent', required=True)\n",
        encoding="utf-8",
    )

    res = scaffold_odoo_tests(str(addon), dry_run=False)
    assert res["success"] is True
    assert res["summary"]["files_written"] == 2
    assert (addon / "tests" / "__init__.py").exists()
    test_file = addon / "tests" / "test_sale_custom_order.py"
    assert test_file.exists()

    content = test_file.read_text(encoding="utf-8")
    assert "class TestSaleCustomOrder(TransactionCase):" in content
    assert 'cls.record = cls.env["sale.custom.order"].create({' in content
    assert '"priority": 10,' in content
    assert '"is_urgent": True,' in content
    assert "def test_record_creation(self):" in content
    assert "def test_record_write(self):" in content
    assert "def test_record_unlink(self):" in content
    assert 'self.assertTrue(self.record.id' in content


def test_scaffold_odoo_tests_generates_constraints_and_actions(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Logic", "version": "18.0.1.0.0"})
    models_dir = addon / "models"
    models_dir.mkdir()
    (models_dir / "subscription.py").write_text(
        "from odoo import models, fields, api\n"
        "from odoo.exceptions import ValidationError\n\n"
        "class Subscription(models.Model):\n"
        "    _name = 'custom.subscription'\n"
        "    _description = 'Subscription'\n"
        "    name = fields.Char(string='Title')\n"
        "    amount = fields.Float(string='Amount')\n\n"
        "    @api.constrains('amount')\n"
        "    def _check_amount(self):\n"
        "        for record in self:\n"
        "            if record.amount < 0:\n"
        "                raise ValidationError('Amount must be positive.')\n\n"
        "    def action_activate(self):\n"
        "        return True\n\n"
        "    def _compute_total(self):\n"
        "        pass\n",
        encoding="utf-8",
    )

    res = scaffold_odoo_tests(str(addon), dry_run=False)
    assert res["success"] is True
    test_file = addon / "tests" / "test_custom_subscription.py"
    content = test_file.read_text(encoding="utf-8")

    assert "def test_constraint__check_amount(self):" in content
    assert "with self.assertRaises(ValidationError):" in content
    assert "def test_action_activate(self):" in content
    assert "res = self.record.action_activate()" in content
    assert "def test__compute_total(self):" in content


def test_scaffold_odoo_tests_generates_access_rights_from_security(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Sec", "version": "18.0.1.0.0"})
    models_dir = addon / "models"
    models_dir.mkdir()
    (models_dir / "lead.py").write_text(
        "from odoo import models, fields\n\n"
        "class CrmLeadCustom(models.Model):\n"
        "    _name = 'crm.lead.custom'\n"
        "    _description = 'CRM Lead'\n"
        "    name = fields.Char()\n",
        encoding="utf-8",
    )
    sec_dir = addon / "security"
    sec_dir.mkdir()
    (sec_dir / "ir.model.access.csv").write_text(
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n"
        "access_crm_lead_custom_user,crm.lead.custom.user,model_crm_lead_custom,base.group_user,1,1,1,0\n",
        encoding="utf-8",
    )

    res = scaffold_odoo_tests(str(addon), test_type="all", dry_run=False)
    assert res["success"] is True
    test_file = addon / "tests" / "test_crm_lead_custom.py"
    content = test_file.read_text(encoding="utf-8")
    assert "def test_access_rights(self):" in content
    assert 'self.assertTrue(self.env["crm.lead.custom"].check_access_rights("read", raise_exception=False))' in content


def test_scaffold_odoo_tests_maintains_init_file(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Init", "version": "18.0.1.0.0"})
    tests_dir = addon / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("# Custom Init\nfrom . import test_existing\n", encoding="utf-8")

    models_dir = addon / "models"
    models_dir.mkdir()
    (models_dir / "new_model.py").write_text(
        "from odoo import models, fields\n\n"
        "class NewModel(models.Model):\n"
        "    _name = 'new.model'\n"
        "    _description = 'New Model'\n",
        encoding="utf-8",
    )

    res = scaffold_odoo_tests(str(addon), dry_run=False)
    assert res["success"] is True

    init_content = (tests_dir / "__init__.py").read_text(encoding="utf-8")
    assert "from . import test_existing" in init_content
    assert "from . import test_new_model" in init_content


def test_scaffold_odoo_tests_overwrite_protection(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Overwrite", "version": "18.0.1.0.0"})
    models_dir = addon / "models"
    models_dir.mkdir()
    (models_dir / "item.py").write_text(
        "from odoo import models, fields\n\n"
        "class Item(models.Model):\n"
        "    _name = 'test.item'\n"
        "    _description = 'Item'\n",
        encoding="utf-8",
    )

    tests_dir = addon / "tests"
    tests_dir.mkdir()
    custom_test = tests_dir / "test_test_item.py"
    custom_test.write_text("# MY CUSTOM TEST CODE", encoding="utf-8")

    # dry_run=False, overwrite=False
    res_no_over = scaffold_odoo_tests(str(addon), dry_run=False, overwrite=False)
    assert res_no_over["success"] is True
    assert res_no_over["summary"]["files_skipped"] == 1
    assert custom_test.read_text(encoding="utf-8") == "# MY CUSTOM TEST CODE"

    # dry_run=False, overwrite=True
    res_over = scaffold_odoo_tests(str(addon), dry_run=False, overwrite=True)
    assert res_over["success"] is True
    assert res_over["summary"]["files_written"] == 2
    assert custom_test.read_text(encoding="utf-8") != "# MY CUSTOM TEST CODE"


def test_scaffold_odoo_tests_filter_specific_models(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test Filter", "version": "18.0.1.0.0"})
    models_dir = addon / "models"
    models_dir.mkdir()
    (models_dir / "models.py").write_text(
        "from odoo import models, fields\n\n"
        "class ModelA(models.Model):\n"
        "    _name = 'model.a'\n"
        "    _description = 'Model A'\n\n"
        "class ModelB(models.Model):\n"
        "    _name = 'model.b'\n"
        "    _description = 'Model B'\n",
        encoding="utf-8",
    )

    res = scaffold_odoo_tests(str(addon), models=["model.a"], dry_run=False)
    assert res["success"] is True
    assert res["summary"]["models_covered"] == 1
    assert (addon / "tests" / "test_model_a.py").exists()
    assert not (addon / "tests" / "test_model_b.py").exists()


def test_scaffold_odoo_tests_fallback_install_test(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Test No Models", "version": "18.0.1.0.0"})
    res = scaffold_odoo_tests(str(addon), dry_run=False)
    assert res["success"] is True
    assert res["summary"]["models_covered"] == 0
    install_test = addon / "tests" / "test_module_install.py"
    assert install_test.exists()
    content = install_test.read_text(encoding="utf-8")
    assert "class TestModuleInstall(TransactionCase):" in content
    assert 'ir.module.module' in content


# --------------------------------------------------
# Tests for CLI Runner & Git Pre-Commit Hook
# --------------------------------------------------

def test_cli_main_server_mode():
    with patch("server.mcp.run") as mock_run:
        exit_code = cli_main(["server"])
        assert exit_code == 0
        mock_run.assert_called_once()


def test_cli_main_via_main_dispatcher():
    with patch("server.mcp.run") as mock_run:
        main(["--stdio"])
        mock_run.assert_called_once()


def test_cli_check_addon_healthy(tmp_path: Path):
    addon = tmp_path / "healthy_addon"
    _create_full_valid_addon(addon)

    exit_code = cli_main(["check", str(addon)])
    assert exit_code == 0


def test_cli_check_addon_critical_issues(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Broken Addon", "version": "18.0.1.0.0", "depends": ["base"]})
    views_dir = addon / "views"
    views_dir.mkdir()
    (views_dir / "broken.xml").write_text("<odoo><record id='broken'>", encoding="utf-8")

    exit_code = cli_main(["check", str(addon)])
    assert exit_code == 1


def test_cli_check_fail_on_warning_flag(tmp_path: Path):
    addon = tmp_path / "warning_addon"
    _create_full_valid_addon(addon)

    # Introduce a suspicious ondelete value to trigger a warning without critical errors
    order_file = addon / "models" / "order.py"
    order_file.write_text(
        "from odoo import models, fields\n\n"
        "class CustomOrder(models.Model):\n"
        "    _name = 'custom.order'\n"
        "    _description = 'Custom Order'\n"
        "    name = fields.Char(string='Reference')\n"
        "    partner_id = fields.Many2one('res.partner', string='Partner', ondelete='invalid_mode')\n",
        encoding="utf-8",
    )

    exit_code_normal = cli_main(["check", str(addon)])
    assert exit_code_normal == 0

    exit_code_strict = cli_main(["check", str(addon), "--fail-on-warning"])
    assert exit_code_strict == 1


def test_cli_check_single_file_routing(tmp_path: Path):
    # Valid XML view file
    xml_file = tmp_path / "view.xml"
    xml_file.write_text("<odoo><record id='test_view' model='ir.ui.view'><field name='arch' type='xml'><form/></field></record></odoo>", encoding="utf-8")
    assert cli_main(["check", str(xml_file)]) == 0

    # Malformed XML view file
    bad_xml = tmp_path / "bad.xml"
    bad_xml.write_text("<odoo><unclosed>", encoding="utf-8")
    assert cli_main(["check", str(bad_xml)]) == 1

    # Valid Python model file
    py_file = tmp_path / "model.py"
    py_file.write_text("from odoo import models, fields\nclass Item(models.Model):\n    _name = 'item'\n    _description = 'Item'\n", encoding="utf-8")
    assert cli_main(["check", str(py_file)]) == 0

    # Valid Security CSV file
    csv_file = tmp_path / "ir.model.access.csv"
    csv_file.write_text("id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\nacc,acc,model_item,base.group_user,1,1,1,1\n", encoding="utf-8")
    assert cli_main(["check", str(csv_file)]) == 0

    # Valid OWL JS file
    js_file = tmp_path / "comp.js"
    js_file.write_text("/** @odoo-module **/\nimport { Component } from '@odoo/owl';\nexport class MyComp extends Component {}\n", encoding="utf-8")
    assert cli_main(["check", str(js_file)]) == 0


def test_cli_migrate_dry_run_and_apply(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Migrate CLI", "version": "18.0.1.0.0"})
    # dry run
    code_dry = cli_main(["migrate", str(addon), "--target-version", "19.0"])
    assert code_dry == 0
    assert not (addon / "migrations").exists()

    # apply
    code_apply = cli_main(["migrate", str(addon), "--target-version", "19.0", "--apply"])
    assert code_apply == 0
    assert (addon / "migrations" / "19.0.1.0" / "pre-migration.py").exists()


def test_cli_scaffold_tests(tmp_path: Path):
    addon = create_addon(tmp_path, {"name": "Scaffold CLI", "version": "18.0.1.0.0"})
    models_dir = addon / "models"
    models_dir.mkdir()
    (models_dir / "item.py").write_text(
        "from odoo import models, fields\n\n"
        "class Item(models.Model):\n"
        "    _name = 'test.item'\n"
        "    _description = 'Item'\n",
        encoding="utf-8",
    )

    code = cli_main(["scaffold-tests", str(addon), "--apply"])
    assert code == 0
    assert (addon / "tests" / "test_test_item.py").exists()


def test_cli_install_hook_creates_executable(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    code = cli_main(["install-hook", "--git-dir", str(git_dir)])
    assert code == 0

    hook_file = git_dir / "hooks" / "pre-commit"
    assert hook_file.exists()
    content = hook_file.read_text(encoding="utf-8")
    assert "$RUNNER check" in content
    assert os.access(hook_file, os.X_OK)


def test_cli_invalid_arguments_returns_error_code(tmp_path: Path):
    # Nonexistent path
    code_nonexistent = cli_main(["check", str(tmp_path / "does_not_exist")])
    assert code_nonexistent == 2

    # Unsupported file type
    txt_file = tmp_path / "some.txt"
    txt_file.write_text("hello", encoding="utf-8")
    code_unsupported = cli_main(["check", str(txt_file)])
    assert code_unsupported == 2


def test_format_cli_report():
    dummy = {
        "addon_name": "my_module",
        "status": "NEEDS_ATTENTION",
        "summary": {"files_scanned": 5},
        "errors": [{"line": 10, "code": "ERR_1", "message": "Bad error"}],
        "warnings": [{"line": 20, "code": "WARN_1", "message": "Minor warning"}],
        "cross_file_issues": [{"issue_type": "MISSING_RULE", "message": "No security rule"}],
        "suggestions": ["Fix errors"],
    }
    report = format_cli_report(dummy, "Test Report")
    assert "Odoo Developer Assistant: Test Report" in report
    assert "Addon: my_module" in report
    assert "Status: [NEEDS_ATTENTION]" in report
    assert "Files scanned: 5" in report
    assert "[ERROR] [Line 10] ERR_1: Bad error" in report
    assert "[WARN] [Line 20] WARN_1: Minor warning" in report
    assert "[CONSISTENCY] MISSING_RULE: No security rule" in report
    assert "* Fix errors" in report
