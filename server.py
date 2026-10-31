import argparse
import ast
import csv
import datetime
import difflib
import io
from pathlib import Path
import re
import shutil
import socket
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import xml.sax
import xmlrpc.client

from mcp.server import MCPServer

mcp = MCPServer(
    name="odoo-developer-assistant",
    instructions=(
        "An assistant for investigating Odoo development errors. "
        "Provides initial troubleshooting suggestions, not confirmed diagnoses."
    ),
)


@mcp.tool()
def analyze_odoo_error(error_text: str) -> dict:
    """Analyze an Odoo error and suggest troubleshooting steps.

    Args:
        error_text: The error message or traceback to investigate.
    """
    if not isinstance(error_text, str) or not error_text.strip():
        return {
            "success": False,
            "error": "Please provide an Odoo error message as text.",
        }

    if len(error_text) > 20000:
        return {
            "success": False,
            "error": "Error text is too long. Maximum length is 20000 characters.",
        }

    error = error_text.lower()

    if "external id not found" in error:
        category = "XML ID Error"
        cause = (
            "A referenced external ID may be missing, incorrectly named, "
            "or loaded in the wrong order."
        )
        steps = [
            "Check the external ID spelling and module prefix.",
            "Verify that the referenced record exists.",
            "Check manifest dependencies and XML loading order.",
        ]

    elif (
        "undefinedcolumn" in error
        or "undefined column" in error
        or "column " in error and " does not exist" in error
    ):
        category = "Database Schema Error"
        cause = (
            "A database column may be missing or the database schema "
            "may not match the installed module code."
        )
        steps = [
            "Identify the model and field referenced by the failing query.",
            "Check whether the field exists in the model definition.",
            "Check whether the relevant module upgrade completed successfully.",
            "Review the database error and server traceback before changing data.",
        ]

    elif "accesserror" in error or "access error" in error:
        category = "Access Rights Error"
        cause = (
            "The user may lack the required permissions, record access, "
            "or company access."
        )
        steps = [
            "Identify the affected user and company.",
            "Review model access rights and record rules.",
            "Check the operation that triggered the error.",
        ]

    elif "parseerror" in error or "xmlsyntaxerror" in error:
        category = "XML Data Error"
        cause = (
            "An XML file may contain invalid syntax or an error may have "
            "occurred while Odoo was loading XML data."
        )
        steps = [
            "Inspect the XML file and the line or location reported in the traceback.",
            "Check XML tag nesting, attributes, and closing tags.",
            "Verify referenced external IDs and manifest data loading order.",
            "Read the complete traceback to distinguish syntax errors from data-loading errors.",
        ]

    elif "keyerror" in error or "key error" in error:
        category = "Key / Lookup Error"
        cause = (
            "A requested dictionary key or lookup entry may be missing "
            "from the relevant data structure."
        )
        steps = [
            "Identify the missing key in the traceback.",
            "Check where the dictionary or lookup data is created.",
            "Verify that the key exists before accessing it.",
            "Check whether an expected model, field, configuration entry, or external ID is missing.",
        ]

    elif "owlerror" in error:
        category = "Owl Frontend Error"
        cause = "A frontend component or its JavaScript code may be failing."
        steps = [
            "Read the complete browser console traceback.",
            "Identify the failing component and source file.",
            "Inspect related JavaScript, component props, and XML templates.",
            "Check whether the error started after a custom frontend change.",
        ]

    elif "rpc_error" in error or "rpc error" in error:
        category = "RPC Error"
        cause = (
            "An RPC request failed, but the underlying server-side "
            "cause could not be identified from the supplied text."
        )
        steps = [
            "Read the complete server traceback, not just the RPC_ERROR heading.",
            "Identify the underlying exception and failing model method or controller.",
            "Inspect the request data and relevant server logs.",
        ]

    else:
        category = "Unclassified Odoo Error"
        cause = "There is not enough information to classify this error."
        steps = [
            "Provide the complete error message.",
            "Include the relevant server or browser traceback.",
            "Describe what you were doing when the error occurred.",
        ]

    return {
        "success": True,
        "category": category,
        "possible_cause": cause,
        "troubleshooting_steps": steps,
        "confirmed_diagnosis": False,
    }

@mcp.tool()
def inspect_odoo_module(
    addon_path: str,
    addons_directories: list[str],
) -> dict:
    """Inspect an Odoo addon manifest and check dependencies
    across multiple addons directories.

    Args:
        addon_path: Absolute path to the addon directory.
        addons_directories: Absolute paths to directories containing
            Odoo addons, such as standard, enterprise, and custom addons.
    """
    if not addons_directories:
        return {
            "success": False,
            "error": "Provide at least one addons directory.",
        }

    try:
        addon = Path(addon_path).expanduser().resolve()
        roots = [
            Path(directory).expanduser().resolve()
            for directory in addons_directories
        ]
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "success": False,
            "error": f"Invalid path: {exc}",
        }

    # Validate that all supplied addons directories exist.
    for root in roots:
        if not root.is_dir():
            return {
                "success": False,
                "error": f"Addons directory does not exist: {root}",
            }

    # The addon must be inside at least one supplied addons directory.
    if not any(addon.is_relative_to(root) for root in roots):
        return {
            "success": False,
            "error": (
                "The addon must be inside one of the supplied "
                "addons directories."
            ),
        }

    if not addon.is_dir():
        return {
            "success": False,
            "error": "Addon directory does not exist.",
        }

    manifest_path = addon / "__manifest__.py"

    if not manifest_path.is_file():
        return {
            "success": False,
            "error": "__manifest__.py was not found.",
        }

    try:
        source = manifest_path.read_text(encoding="utf-8")

        if len(source) > 100_000:
            return {
                "success": False,
                "error": "Manifest is too large to inspect.",
            }

        # Parse the manifest without executing its Python code.
        tree = ast.parse(source, filename=str(manifest_path))

        if len(tree.body) != 1 or not isinstance(
            tree.body[0], ast.Expr
        ):
            return {
                "success": False,
                "error": (
                    "Expected a manifest containing a single "
                    "dictionary expression."
                ),
            }

        manifest = ast.literal_eval(tree.body[0].value)

        if not isinstance(manifest, dict):
            return {
                "success": False,
                "error": "The manifest must contain a dictionary.",
            }

    except (SyntaxError, ValueError, TypeError, IndexError) as exc:
        return {
            "success": False,
            "error": f"Unable to parse manifest: {exc}",
        }
    except (OSError, UnicodeError, RuntimeError) as exc:
        return {
            "success": False,
            "error": f"Unable to read manifest: {exc}",
        }

    dependencies = manifest.get("depends", [])

    if not isinstance(dependencies, list) or not all(
        isinstance(dep, str) and dep.strip()
        for dep in dependencies
    ):
        return {
            "success": False,
            "error": (
                "The manifest's depends field must be a "
                "list of non-empty strings."
            ),
        }

    dependency_status = {}

    for dependency in dependencies:
        # Odoo addon names should be directory names, not paths.
        if (
            dependency in {".", ".."}
            or Path(dependency).name != dependency
            or "/" in dependency
            or "\\" in dependency
        ):
            dependency_status[dependency] = {
                "status": "invalid dependency name",
                "found_in": [],
            }
            continue

        found_in = []

        for root in roots:
            try:
                dependency_path = (root / dependency).resolve()

                # Prevent a symlink from escaping the addons directory.
                if not dependency_path.is_relative_to(root):
                    continue

                if dependency_path.is_dir():
                    found_in.append(str(root))

            except (OSError, RuntimeError, ValueError):
                continue

        if found_in:
            dependency_status[dependency] = {
                "status": "found",
                "found_in": found_in,
            }
        else:
            dependency_status[dependency] = {
                "status": "not found in supplied directories",
                "found_in": [],
            }

    return {
        "success": True,
        "module": addon.name,
        "manifest_path": str(manifest_path),
        "name": manifest.get("name"),
        "version": manifest.get("version"),
        "summary": manifest.get("summary"),
        "depends": dependencies,
        "data_files": manifest.get("data", []),
        "installable": manifest.get("installable", True),
        "application": manifest.get("application", False),
        "checked_addons_directories": [str(root) for root in roots],
        "dependency_status": dependency_status,
        "note": (
            "Dependencies are checked across the supplied addons "
            "directories only. A missing dependency does not prove "
            "it is unavailable elsewhere in the Odoo installation."
        ),
    }

@mcp.tool()
def validate_odoo_manifest(addon_path: str) -> dict:
    """Validate an Odoo addon's manifest and declared data/demo files.

    Checks manifest syntax, important fields, and whether files declared
    in 'data' and 'demo' exist inside the addon directory.

    Args:
        addon_path: Absolute path to the Odoo addon directory.
    """
    try:
        addon = Path(addon_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "success": False,
            "error": f"Invalid addon path: {exc}",
        }

    if not addon.is_dir():
        return {
            "success": False,
            "error": "Addon directory does not exist.",
        }

    manifest_path = addon / "__manifest__.py"

    if not manifest_path.is_file():
        return {
            "success": False,
            "error": "__manifest__.py was not found.",
        }

    try:
        source = manifest_path.read_text(encoding="utf-8")

        if len(source) > 100_000:
            return {
                "success": False,
                "error": "Manifest is too large to inspect.",
            }

        # Parse the manifest without executing Python code.
        tree = ast.parse(source, filename=str(manifest_path))

        if len(tree.body) != 1 or not isinstance(
            tree.body[0], ast.Expr
        ):
            return {
                "success": False,
                "error": (
                    "Expected a manifest containing a single "
                    "dictionary expression."
                ),
            }

        manifest = ast.literal_eval(tree.body[0].value)

        if not isinstance(manifest, dict):
            return {
                "success": False,
                "error": "The manifest must contain a dictionary.",
            }

    except (SyntaxError, ValueError, TypeError, IndexError) as exc:
        return {
            "success": False,
            "error": f"Unable to parse manifest: {exc}",
        }
    except (OSError, UnicodeError, RuntimeError) as exc:
        return {
            "success": False,
            "error": f"Unable to read manifest: {exc}",
        }

    # Check important manifest fields.
    required_fields = ["name", "version", "depends"]
    missing_fields = [
        field for field in required_fields
        if field not in manifest
    ]

    field_issues = []

    if not isinstance(manifest.get("name"), str) or not manifest.get("name", "").strip():
        field_issues.append("'name' must be a non-empty string.")

    if not isinstance(manifest.get("version"), str) or not manifest.get("version", "").strip():
        field_issues.append("'version' must be a non-empty string.")

    if not isinstance(manifest.get("depends"), list) or not all(
        isinstance(dep, str) and dep.strip()
        for dep in manifest.get("depends", [])
    ):
        field_issues.append(
            "'depends' must be a list of non-empty strings."
        )

    missing_files = []
    unsafe_paths = []
    checked_files = []

    # Validate files declared in both data and demo.
    for section in ("data", "demo"):
        files = manifest.get(section, [])

        if not isinstance(files, list) or not all(
            isinstance(file_path, str) and file_path.strip()
            for file_path in files
        ):
            field_issues.append(
                f"'{section}' must be a list of non-empty file paths."
            )
            continue

        for relative_path in files:
            file_path = Path(relative_path)

            # Manifest file entries should be relative to the addon.
            if file_path.is_absolute():
                unsafe_paths.append(relative_path)
                continue

            try:
                resolved_path = (addon / file_path).resolve()

                # Prevent paths or symlinks from escaping the addon.
                if not resolved_path.is_relative_to(addon):
                    unsafe_paths.append(relative_path)
                    continue

                if resolved_path.is_file():
                    checked_files.append({
                        "section": section,
                        "path": relative_path,
                        "status": "found",
                    })
                else:
                    missing_files.append({
                        "section": section,
                        "path": relative_path,
                    })

            except (OSError, RuntimeError, ValueError):
                unsafe_paths.append(relative_path)

    valid = not (
        missing_fields
        or field_issues
        or missing_files
        or unsafe_paths
    )

    suggestions = []

    if missing_fields:
        suggestions.append(
            "Add the missing manifest fields: "
            + ", ".join(missing_fields)
        )

    if field_issues:
        suggestions.append(
            "Correct the manifest field types and values."
        )

    if missing_files:
        suggestions.append(
            "Create the missing files or correct their paths "
            "in the manifest."
        )

    if unsafe_paths:
        suggestions.append(
            "Use safe paths relative to the addon directory. "
            "Absolute paths and paths escaping the addon are not allowed."
        )

    if valid:
        suggestions.append(
            "The manifest passed these static checks. "
            "This does not guarantee that the module will install successfully."
        )

    return {
        "success": True,
        "valid": valid,
        "module": addon.name,
        "manifest_path": str(manifest_path),
        "missing_fields": missing_fields,
        "field_issues": field_issues,
        "checked_files": checked_files,
        "missing_files": missing_files,
        "unsafe_paths": unsafe_paths,
        "suggestions": suggestions,
    }


MAX_XML_FILE_SIZE = 500_000  # 500 KB
REF_EVAL_REGEX = re.compile(r"ref\(['\"]([a-zA-Z0-9_\.]+)['\"]\)")


class _OdooXMLContentHandler(xml.sax.ContentHandler):
    """SAX handler to collect Odoo XML records, views, and external references with line numbers."""

    def __init__(self):
        super().__init__()
        self.locator = None
        self.root_tag = None
        self.records = []
        self.refs = []
        self._current_record = None
        self._current_field = None
        self._in_arch = False
        self._arch_children_count = 0

    def setDocumentLocator(self, locator):
        self.locator = locator

    def startElement(self, name, attrs):
        line = self.locator.getLineNumber() if self.locator else None
        if self.root_tag is None:
            self.root_tag = (name, line)

        # Collect ref attributes
        if "ref" in attrs:
            self.refs.append({
                "ref": attrs["ref"],
                "line": line,
                "element": name,
            })

        # Collect references in eval attributes e.g. ref('...')
        if "eval" in attrs:
            eval_val = attrs["eval"]
            for match in REF_EVAL_REGEX.finditer(eval_val):
                self.refs.append({
                    "ref": match.group(1),
                    "line": line,
                    "element": name,
                })

        if name == "record":
            rec_id = attrs.get("id")
            rec_model = attrs.get("model")
            self._current_record = {
                "id": rec_id,
                "model": rec_model,
                "target_model": None,
                "line": line,
                "has_arch": False,
                "arch_children": 0,
                "arch_fields": [],
            }
            self.records.append(self._current_record)
        elif self._current_record:
            if name == "field":
                fname = attrs.get("name")
                self._current_field = fname
                if fname == "arch":
                    self._current_record["has_arch"] = True
                    self._in_arch = True
                    self._arch_children_count = 0
                elif self._in_arch:
                    self._arch_children_count += 1
                    self._current_record["arch_children"] = self._arch_children_count
                    if fname and fname not in self._current_record["arch_fields"]:
                        self._current_record["arch_fields"].append(fname)
            elif self._in_arch:
                self._arch_children_count += 1
                self._current_record["arch_children"] = self._arch_children_count

    def characters(self, content):
        if (
            self._current_record
            and not self._in_arch
            and self._current_field == "model"
        ):
            if self._current_record.get("target_model") is None:
                self._current_record["target_model"] = ""
            self._current_record["target_model"] += content

    def endElement(self, name):
        if name == "record":
            if (
                self._current_record
                and isinstance(self._current_record.get("target_model"), str)
            ):
                self._current_record["target_model"] = self._current_record["target_model"].strip()
            self._current_record = None
        elif name == "field" and self._current_field == "arch":
            self._in_arch = False
            self._current_field = None
        elif name == "field":
            self._current_field = None


@mcp.tool()
def analyze_odoo_xml_view(
    xml_path: str,
    addon_path: str | None = None,
) -> dict:
    """Analyze an Odoo XML data or view file for structural errors,
    suspicious references, and common issues.

    Args:
        xml_path: Absolute or relative path to the XML file.
        addon_path: Optional path to the containing addon directory to enforce
            sandboxed file access.
    """
    if not isinstance(xml_path, str) or not xml_path.strip():
        return {
            "success": False,
            "error": "Please provide an XML file path as text.",
        }

    try:
        resolved_xml = Path(xml_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "success": False,
            "error": f"Invalid XML path: {exc}",
        }

    if addon_path is not None:
        if not isinstance(addon_path, str) or not addon_path.strip():
            return {
                "success": False,
                "error": "addon_path must be a non-empty string when provided.",
            }
        try:
            resolved_addon = Path(addon_path).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            return {
                "success": False,
                "error": f"Invalid addon path: {exc}",
            }

        if not resolved_addon.is_dir():
            return {
                "success": False,
                "error": f"Addon directory does not exist: {resolved_addon}",
            }

        if not resolved_xml.is_relative_to(resolved_addon):
            return {
                "success": False,
                "error": (
                    "The XML file must reside inside the supplied addon directory."
                ),
            }

    if not resolved_xml.exists():
        return {
            "success": False,
            "error": f"XML file does not exist: {resolved_xml}",
        }

    if not resolved_xml.is_file():
        return {
            "success": False,
            "error": f"Path is a directory or not a regular file: {resolved_xml}",
        }

    try:
        file_size = resolved_xml.stat().st_size
    except OSError as exc:
        return {
            "success": False,
            "error": f"Unable to access XML file: {exc}",
        }

    if file_size > MAX_XML_FILE_SIZE:
        return {
            "success": False,
            "error": (
                f"XML file is too large to inspect ({file_size} bytes). "
                f"Maximum allowed size is {MAX_XML_FILE_SIZE} bytes."
            ),
        }

    try:
        content = resolved_xml.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return {
            "success": False,
            "error": f"Unable to decode XML file as UTF-8: {exc}",
        }
    except OSError as exc:
        return {
            "success": False,
            "error": f"Unable to read XML file: {exc}",
        }

    if not content.strip():
        return {
            "success": True,
            "valid": False,
            "file": str(resolved_xml),
            "summary": {
                "total_records": 0,
                "view_records": 0,
                "error_count": 1,
                "warning_count": 0,
                "info_count": 0,
            },
            "errors": [
                {
                    "code": "EMPTY_XML_FILE",
                    "message": "The XML file is empty.",
                    "line": 1,
                    "element": None,
                }
            ],
            "warnings": [],
            "info": [],
            "suggestions": [
                "Provide valid Odoo XML content with an <odoo> root element."
            ],
        }

    handler = _OdooXMLContentHandler()
    parser = xml.sax.make_parser()
    parser.setFeature(xml.sax.handler.feature_external_ges, False)
    parser.setFeature(xml.sax.handler.feature_external_pes, False)
    parser.setContentHandler(handler)

    try:
        parser.parse(io.StringIO(content))
    except xml.sax.SAXParseException as exc:
        return {
            "success": True,
            "valid": False,
            "file": str(resolved_xml),
            "summary": {
                "total_records": 0,
                "view_records": 0,
                "error_count": 1,
                "warning_count": 0,
                "info_count": 0,
            },
            "errors": [
                {
                    "code": "XML_SYNTAX_ERROR",
                    "message": f"XML syntax error: {exc.getMessage()}",
                    "line": exc.getLineNumber(),
                    "column": exc.getColumnNumber(),
                    "element": None,
                }
            ],
            "warnings": [],
            "info": [],
            "suggestions": [
                f"Check XML tag nesting and closing tags around line {exc.getLineNumber()}."
            ],
        }

    errors = []
    warnings = []
    info = []
    suggestions = []

    # Check root tag
    root_tag, root_line = handler.root_tag if handler.root_tag else (None, None)
    if root_tag not in ("odoo", "openerp"):
        warnings.append({
            "code": "UNEXPECTED_ROOT_TAG",
            "message": (
                f"Unexpected root tag '<{root_tag}>'. Expected '<odoo>' (or legacy '<openerp>')."
            ),
            "line": root_line,
            "element": root_tag,
        })

    seen_record_ids = {}
    total_records = len(handler.records)
    view_records = 0

    for rec in handler.records:
        rec_id = rec.get("id")
        rec_model = rec.get("model")
        rec_line = rec.get("line")

        if not rec_id or not rec_id.strip():
            errors.append({
                "code": "MISSING_RECORD_ID",
                "message": "<record> element is missing a required 'id' attribute.",
                "line": rec_line,
                "element": "record",
            })
        else:
            if rec_id in seen_record_ids:
                errors.append({
                    "code": "DUPLICATE_RECORD_ID",
                    "message": (
                        f"Duplicate record ID '{rec_id}' found (first declared at line {seen_record_ids[rec_id]})."
                    ),
                    "line": rec_line,
                    "element": "record",
                })
            else:
                seen_record_ids[rec_id] = rec_line

            if " " in rec_id or "\t" in rec_id:
                warnings.append({
                    "code": "SUSPICIOUS_ID_FORMAT",
                    "message": (
                        f"Record ID '{rec_id}' contains whitespace, which is invalid for an XML ID."
                    ),
                    "line": rec_line,
                    "element": "record",
                })

        if not rec_model or not rec_model.strip():
            errors.append({
                "code": "MISSING_RECORD_MODEL",
                "message": "<record> element is missing a required 'model' attribute.",
                "line": rec_line,
                "element": "record",
            })

        if rec_model == "ir.ui.view":
            view_records += 1
            if not rec.get("has_arch"):
                warnings.append({
                    "code": "MISSING_ARCH",
                    "message": (
                        f"View record '{rec_id or 'unnamed'}' does not define an 'arch' field."
                    ),
                    "line": rec_line,
                    "element": "record",
                })
            elif rec.get("arch_children", 0) == 0:
                warnings.append({
                    "code": "EMPTY_ARCH",
                    "message": (
                        f"View record '{rec_id or 'unnamed'}' has an empty 'arch' field."
                    ),
                    "line": rec_line,
                    "element": "field",
                })

    for ref_item in handler.refs:
        info.append({
            "code": "EXTERNAL_ID_REFERENCE",
            "message": (
                f"External ID reference '{ref_item['ref']}' requires verification in addon dependencies or database."
            ),
            "ref": ref_item["ref"],
            "line": ref_item["line"],
            "element": ref_item["element"],
        })

    valid = len(errors) == 0

    if errors:
        suggestions.append(
            "Resolve the identified XML errors to ensure valid record definitions."
        )
    if warnings:
        suggestions.append(
            "Review warnings to verify view architecture and root element structure."
        )
    if info:
        suggestions.append(
            "Verify that referenced external IDs exist in depends modules."
        )
    if valid and not warnings:
        suggestions.append(
            "The XML file passed static structural checks. "
            "Semantic validation requires an active Odoo instance."
        )

    return {
        "success": True,
        "valid": valid,
        "file": str(resolved_xml),
        "summary": {
            "total_records": total_records,
            "view_records": view_records,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "info_count": len(info),
        },
        "views_found": [
            {
                "id": r.get("id"),
                "target_model": r.get("target_model"),
                "line": r.get("line"),
                "arch_fields": r.get("arch_fields", []),
            }
            for r in handler.records
            if r.get("model") == "ir.ui.view"
        ],
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "suggestions": suggestions,
    }


MAX_PYTHON_FILE_SIZE = 500_000  # 500 KB


def _is_odoo_model_class(node: ast.ClassDef) -> bool:
    """Check if an AST ClassDef represents an Odoo model."""
    for base in node.bases:
        if isinstance(base, ast.Attribute):
            if isinstance(base.value, ast.Name) and base.value.id == "models":
                if base.attr in ("Model", "TransientModel", "AbstractModel"):
                    return True
        elif isinstance(base, ast.Name):
            if base.id in ("Model", "TransientModel", "AbstractModel"):
                return True
    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id in ("_name", "_inherit"):
                    return True
    return False


@mcp.tool()
def analyze_odoo_python_model(
    python_path: str,
    addon_path: str | None = None,
) -> dict:
    """Analyze an Odoo Python model file for structural errors, missing attributes,
    decorator misconfigurations, and common mistakes using AST.

    Args:
        python_path: Absolute or relative path to the Python file.
        addon_path: Optional path to the containing addon directory to enforce
            sandboxed file access.
    """
    if not isinstance(python_path, str) or not python_path.strip():
        return {
            "success": False,
            "error": "Please provide a Python file path as text.",
        }

    try:
        resolved_python = Path(python_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "success": False,
            "error": f"Invalid Python path: {exc}",
        }

    if addon_path is not None:
        if not isinstance(addon_path, str) or not addon_path.strip():
            return {
                "success": False,
                "error": "addon_path must be a non-empty string when provided.",
            }
        try:
            resolved_addon = Path(addon_path).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            return {
                "success": False,
                "error": f"Invalid addon path: {exc}",
            }

        if not resolved_addon.is_dir():
            return {
                "success": False,
                "error": f"Addon directory does not exist: {resolved_addon}",
            }

        if not resolved_python.is_relative_to(resolved_addon):
            return {
                "success": False,
                "error": (
                    "The Python file must reside inside the supplied addon directory."
                ),
            }

    if not resolved_python.exists():
        return {
            "success": False,
            "error": f"Python file does not exist: {resolved_python}",
        }

    if not resolved_python.is_file():
        return {
            "success": False,
            "error": f"Path is a directory or not a regular file: {resolved_python}",
        }

    try:
        file_size = resolved_python.stat().st_size
    except OSError as exc:
        return {
            "success": False,
            "error": f"Unable to access Python file: {exc}",
        }

    if file_size > MAX_PYTHON_FILE_SIZE:
        return {
            "success": False,
            "error": (
                f"Python file is too large to inspect ({file_size} bytes). "
                f"Maximum allowed size is {MAX_PYTHON_FILE_SIZE} bytes."
            ),
        }

    try:
        content = resolved_python.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return {
            "success": False,
            "error": f"Unable to decode Python file as UTF-8: {exc}",
        }
    except OSError as exc:
        return {
            "success": False,
            "error": f"Unable to read Python file: {exc}",
        }

    if not content.strip():
        return {
            "success": True,
            "valid": False,
            "file": str(resolved_python),
            "summary": {
                "total_classes": 0,
                "model_classes": 0,
                "total_fields": 0,
                "error_count": 1,
                "warning_count": 0,
                "info_count": 0,
            },
            "models_found": [],
            "errors": [
                {
                    "code": "EMPTY_PYTHON_FILE",
                    "message": "The Python file is empty.",
                    "line": 1,
                    "element": None,
                }
            ],
            "warnings": [],
            "info": [],
            "suggestions": [
                "Provide Python code with Odoo model definitions."
            ],
        }

    try:
        tree = ast.parse(content, filename=str(resolved_python))
    except SyntaxError as exc:
        return {
            "success": True,
            "valid": False,
            "file": str(resolved_python),
            "summary": {
                "total_classes": 0,
                "model_classes": 0,
                "total_fields": 0,
                "error_count": 1,
                "warning_count": 0,
                "info_count": 0,
            },
            "models_found": [],
            "errors": [
                {
                    "code": "PYTHON_SYNTAX_ERROR",
                    "message": f"Python syntax error: {exc.msg}",
                    "line": exc.lineno,
                    "column": exc.offset,
                    "element": None,
                }
            ],
            "warnings": [],
            "info": [],
            "suggestions": [
                f"Check Python syntax around line {exc.lineno}: {exc.text or ''}".strip()
            ],
        }

    errors = []
    warnings = []
    info = []
    suggestions = []
    models_found = []

    total_classes = 0
    total_fields = 0

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue

        total_classes += 1
        if not _is_odoo_model_class(node):
            continue

        model_name = None
        model_inherit = None
        model_description = None
        class_fields = []

        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        if target.id == "_name":
                            if isinstance(stmt.value, ast.Constant) and isinstance(
                                stmt.value.value, str
                            ):
                                model_name = stmt.value.value
                        elif target.id == "_inherit":
                            if isinstance(stmt.value, ast.Constant) and isinstance(
                                stmt.value.value, str
                            ):
                                model_inherit = stmt.value.value
                            elif isinstance(stmt.value, (ast.List, ast.Tuple)):
                                model_inherit = [
                                    elt.value
                                    for elt in stmt.value.elts
                                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                                ]
                        elif target.id == "_description":
                            if isinstance(stmt.value, ast.Constant) and isinstance(
                                stmt.value.value, str
                            ):
                                model_description = stmt.value.value

                # Inspect field assignments
                if isinstance(stmt.value, ast.Call):
                    func = stmt.value.func
                    is_field = False
                    field_type = None

                    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                        if func.value.id == "fields":
                            is_field = True
                            field_type = func.attr
                    elif isinstance(func, ast.Name):
                        if func.id in (
                            "Char",
                            "Integer",
                            "Float",
                            "Boolean",
                            "Text",
                            "Html",
                            "Date",
                            "Datetime",
                            "Binary",
                            "Selection",
                            "Many2one",
                            "One2many",
                            "Many2many",
                            "Many2oneReference",
                        ):
                            is_field = True
                            field_type = func.id

                    if is_field:
                        total_fields += 1
                        field_name = (
                            stmt.targets[0].id
                            if stmt.targets and isinstance(stmt.targets[0], ast.Name)
                            else "unknown"
                        )
                        class_fields.append({
                            "name": field_name,
                            "type": field_type,
                            "line": stmt.lineno,
                        })
                        args = stmt.value.args
                        keywords = {kw.arg: kw.value for kw in stmt.value.keywords if kw.arg}

                        # Relational field validations
                        if field_type == "Many2one":
                            has_comodel = len(args) >= 1 or "comodel_name" in keywords
                            if not has_comodel:
                                errors.append({
                                    "code": "MISSING_COMODEL_NAME",
                                    "message": (
                                        f"Field '{field_name}' (Many2one) is missing a required comodel_name."
                                    ),
                                    "line": stmt.lineno,
                                    "element": field_name,
                                })
                            if "ondelete" in keywords:
                                ondelete_node = keywords["ondelete"]
                                if isinstance(ondelete_node, ast.Constant) and isinstance(
                                    ondelete_node.value, str
                                ):
                                    val = ondelete_node.value.lower()
                                    if val not in {
                                        "set null",
                                        "restrict",
                                        "cascade",
                                        "set default",
                                    }:
                                        warnings.append({
                                            "code": "INVALID_ONDELETE",
                                            "message": (
                                                f"Field '{field_name}' specifies suspicious ondelete='{val}'. "
                                                "Standard options are 'set null', 'restrict', 'cascade', 'set default'."
                                            ),
                                            "line": stmt.lineno,
                                            "element": field_name,
                                        })

                        elif field_type == "One2many":
                            has_comodel = len(args) >= 1 or "comodel_name" in keywords
                            has_inverse = len(args) >= 2 or "inverse_name" in keywords
                            if not has_comodel:
                                errors.append({
                                    "code": "MISSING_COMODEL_NAME",
                                    "message": (
                                        f"Field '{field_name}' (One2many) is missing a required comodel_name."
                                    ),
                                    "line": stmt.lineno,
                                    "element": field_name,
                                })
                            if not has_inverse:
                                errors.append({
                                    "code": "MISSING_INVERSE_NAME",
                                    "message": (
                                        f"Field '{field_name}' (One2many) is missing a required inverse_name "
                                        "(the Many2one field name on the comodel)."
                                    ),
                                    "line": stmt.lineno,
                                    "element": field_name,
                                })

                        elif field_type == "Many2many":
                            has_comodel = len(args) >= 1 or "comodel_name" in keywords
                            if not has_comodel:
                                errors.append({
                                    "code": "MISSING_COMODEL_NAME",
                                    "message": (
                                        f"Field '{field_name}' (Many2many) is missing a required comodel_name."
                                    ),
                                    "line": stmt.lineno,
                                    "element": field_name,
                                })

            # Inspect method decorators
            elif isinstance(stmt, ast.FunctionDef):
                for dec in stmt.decorator_list:
                    if isinstance(dec, ast.Call):
                        dec_func = dec.func
                        if (
                            isinstance(dec_func, ast.Attribute)
                            and isinstance(dec_func.value, ast.Name)
                            and dec_func.value.id == "api"
                        ):
                            dec_name = dec_func.attr
                            if dec_name == "depends" and len(dec.args) == 0:
                                errors.append({
                                    "code": "EMPTY_DEPENDS_ARGS",
                                    "message": (
                                        f"@api.depends() on method '{stmt.name}' has no dependency arguments."
                                    ),
                                    "line": stmt.lineno,
                                    "element": stmt.name,
                                })
                            elif dec_name == "onchange" and len(dec.args) == 0:
                                warnings.append({
                                    "code": "EMPTY_ONCHANGE_ARGS",
                                    "message": (
                                        f"@api.onchange() on method '{stmt.name}' has no field arguments."
                                    ),
                                    "line": stmt.lineno,
                                    "element": stmt.name,
                                })
                            elif dec_name == "constrains" and len(dec.args) == 0:
                                warnings.append({
                                    "code": "EMPTY_CONSTRAINS_ARGS",
                                    "message": (
                                        f"@api.constrains() on method '{stmt.name}' has no field arguments."
                                    ),
                                    "line": stmt.lineno,
                                    "element": stmt.name,
                                })

        # Class-level model validations
        if not model_name and not model_inherit:
            warnings.append({
                "code": "MISSING_MODEL_NAME",
                "message": (
                    f"Class '{node.name}' inherits from an Odoo model but defines neither '_name' nor '_inherit'."
                ),
                "line": node.lineno,
                "element": node.name,
            })

        if model_name and not model_inherit and not model_description:
            warnings.append({
                "code": "MISSING_MODEL_DESCRIPTION",
                "message": (
                    f"Model '{model_name}' (class '{node.name}') defines a new model without a '_description' attribute."
                ),
                "line": node.lineno,
                "element": node.name,
            })

        models_found.append({
            "class_name": node.name,
            "name": model_name,
            "inherit": model_inherit,
            "description": model_description,
            "fields": [f["name"] for f in class_fields],
            "field_definitions": class_fields,
            "line": node.lineno,
        })

    valid = len(errors) == 0

    if errors:
        suggestions.append(
            "Resolve Python syntax or model field/decorator errors before running the addon."
        )
    if warnings:
        suggestions.append(
            "Review model warnings to ensure proper documentation, naming, and decorator configuration."
        )
    if valid and not warnings:
        suggestions.append(
            "The Python model file passed static AST checks. Runtime Odoo behavior requires testing with an active Odoo instance."
        )

    return {
        "success": True,
        "valid": valid,
        "file": str(resolved_python),
        "summary": {
            "total_classes": total_classes,
            "model_classes": len(models_found),
            "total_fields": total_fields,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "info_count": len(info),
        },
        "models_found": models_found,
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "suggestions": suggestions,
    }


MAX_SECURITY_FILE_SIZE = 500_000  # 500 KB
EXPECTED_ACCESS_CSV_HEADERS = [
    "id",
    "name",
    "model_id:id",
    "group_id:id",
    "perm_read",
    "perm_write",
    "perm_create",
    "perm_unlink",
]


class _SecurityXMLContentHandler(xml.sax.ContentHandler):
    """SAX handler to collect Odoo security records (ir.rule, res.groups)."""

    def __init__(self):
        super().__init__()
        self.locator = None
        self.rules = []
        self.records = []
        self._current_rec = None

    def setDocumentLocator(self, locator):
        self.locator = locator

    def startElement(self, name, attrs):
        line = self.locator.getLineNumber() if self.locator else None
        if name == "record":
            model = attrs.get("model")
            rec_id = attrs.get("id")
            self._current_rec = {
                "id": rec_id,
                "model": model,
                "line": line,
                "has_model_id": False,
                "has_domain_force": False,
            }
            self.records.append(self._current_rec)
            if model == "ir.rule":
                self.rules.append(self._current_rec)
        elif self._current_rec and name == "field":
            fname = attrs.get("name")
            if fname == "model_id":
                self._current_rec["has_model_id"] = True
            elif fname == "domain_force":
                self._current_rec["has_domain_force"] = True

    def endElement(self, name):
        if name == "record":
            self._current_rec = None


@mcp.tool()
def analyze_odoo_security(
    file_path: str,
    addon_path: str | None = None,
) -> dict:
    """Analyze an Odoo security file (ir.model.access.csv or security XML data)
    for syntax mistakes, invalid headers, missing model prefixes, and misconfigured rules.

    Args:
        file_path: Absolute or relative path to the security file (.csv or .xml).
        addon_path: Optional path to the containing addon directory to enforce
            sandboxed file access.
    """
    if not isinstance(file_path, str) or not file_path.strip():
        return {
            "success": False,
            "error": "Please provide a security file path as text.",
        }

    try:
        resolved_file = Path(file_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "success": False,
            "error": f"Invalid file path: {exc}",
        }

    if addon_path is not None:
        if not isinstance(addon_path, str) or not addon_path.strip():
            return {
                "success": False,
                "error": "addon_path must be a non-empty string when provided.",
            }
        try:
            resolved_addon = Path(addon_path).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            return {
                "success": False,
                "error": f"Invalid addon path: {exc}",
            }

        if not resolved_addon.is_dir():
            return {
                "success": False,
                "error": f"Addon directory does not exist: {resolved_addon}",
            }

        if not resolved_file.is_relative_to(resolved_addon):
            return {
                "success": False,
                "error": (
                    "The security file must reside inside the supplied addon directory."
                ),
            }

    if not resolved_file.exists():
        return {
            "success": False,
            "error": f"Security file does not exist: {resolved_file}",
        }

    if not resolved_file.is_file():
        return {
            "success": False,
            "error": f"Path is a directory or not a regular file: {resolved_file}",
        }

    try:
        file_size = resolved_file.stat().st_size
    except OSError as exc:
        return {
            "success": False,
            "error": f"Unable to access security file: {exc}",
        }

    if file_size > MAX_SECURITY_FILE_SIZE:
        return {
            "success": False,
            "error": (
                f"Security file is too large to inspect ({file_size} bytes). "
                f"Maximum allowed size is {MAX_SECURITY_FILE_SIZE} bytes."
            ),
        }

    try:
        content = resolved_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return {
            "success": False,
            "error": f"Unable to decode security file as UTF-8: {exc}",
        }
    except OSError as exc:
        return {
            "success": False,
            "error": f"Unable to read security file: {exc}",
        }

    if not content.strip():
        return {
            "success": True,
            "valid": False,
            "file": str(resolved_file),
            "file_type": "unknown",
            "summary": {
                "total_rules": 0,
                "error_count": 1,
                "warning_count": 0,
                "info_count": 0,
            },
            "rules_found": [],
            "errors": [
                {
                    "code": "EMPTY_SECURITY_FILE",
                    "message": "The security file is empty.",
                    "line": 1,
                    "element": None,
                }
            ],
            "warnings": [],
            "info": [],
            "suggestions": [
                "Provide valid security CSV access rules or XML record rules."
            ],
        }

    is_xml = resolved_file.suffix.lower() == ".xml" or content.lstrip().startswith("<")
    file_type = "xml" if is_xml else "csv"

    errors = []
    warnings = []
    info = []
    suggestions = []
    rules_found = []

    if is_xml:
        handler = _SecurityXMLContentHandler()
        parser = xml.sax.make_parser()
        parser.setFeature(xml.sax.handler.feature_external_ges, False)
        parser.setFeature(xml.sax.handler.feature_external_pes, False)
        parser.setContentHandler(handler)

        try:
            parser.parse(io.StringIO(content))
        except xml.sax.SAXParseException as exc:
            return {
                "success": True,
                "valid": False,
                "file": str(resolved_file),
                "file_type": "xml",
                "summary": {
                    "total_rules": 0,
                    "error_count": 1,
                    "warning_count": 0,
                    "info_count": 0,
                },
                "rules_found": [],
                "errors": [
                    {
                        "code": "XML_SYNTAX_ERROR",
                        "message": f"XML syntax error: {exc.getMessage()}",
                        "line": exc.getLineNumber(),
                        "column": exc.getColumnNumber(),
                        "element": None,
                    }
                ],
                "warnings": [],
                "info": [],
                "suggestions": [
                    f"Check XML tag nesting and closing tags around line {exc.getLineNumber()}."
                ],
            }

        seen_ids = {}
        for rec in handler.records:
            rec_id = rec.get("id")
            rec_model = rec.get("model")
            rec_line = rec.get("line")

            if rec_id:
                if rec_id in seen_ids:
                    errors.append({
                        "code": "DUPLICATE_RECORD_ID",
                        "message": (
                            f"Duplicate record ID '{rec_id}' found (first defined at line {seen_ids[rec_id]})."
                        ),
                        "line": rec_line,
                        "element": "record",
                    })
                else:
                    seen_ids[rec_id] = rec_line

            if rec_model == "ir.rule":
                rules_found.append({
                    "id": rec_id,
                    "name": rec_id,
                    "model": "ir.rule",
                    "line": rec_line,
                })
                if not rec.get("has_model_id"):
                    errors.append({
                        "code": "MISSING_RULE_MODEL",
                        "message": (
                            f"Record rule '{rec_id or 'unnamed'}' is missing a required 'model_id' field."
                        ),
                        "line": rec_line,
                        "element": "record",
                    })
                if not rec.get("has_domain_force"):
                    warnings.append({
                        "code": "MISSING_RULE_DOMAIN",
                        "message": (
                            f"Record rule '{rec_id or 'unnamed'}' does not define a 'domain_force' filter."
                        ),
                        "line": rec_line,
                        "element": "record",
                    })

    else:
        # CSV mode: ir.model.access.csv
        reader = csv.reader(io.StringIO(content))
        headers = None
        seen_ids = {}

        for line_num, row in enumerate(reader, start=1):
            if not row or not any(field.strip() for field in row):
                continue

            if headers is None:
                headers = [c.strip() for c in row]
                if headers != EXPECTED_ACCESS_CSV_HEADERS:
                    errors.append({
                        "code": "INVALID_CSV_HEADER",
                        "message": (
                            f"CSV header mismatch. Expected: {', '.join(EXPECTED_ACCESS_CSV_HEADERS)}. "
                            f"Found: {', '.join(headers)}"
                        ),
                        "line": line_num,
                        "element": "header",
                    })
                continue

            if len(row) != len(EXPECTED_ACCESS_CSV_HEADERS):
                errors.append({
                    "code": "ROW_COLUMN_MISMATCH",
                    "message": (
                        f"Row has {len(row)} columns, expected {len(EXPECTED_ACCESS_CSV_HEADERS)}."
                    ),
                    "line": line_num,
                    "element": "row",
                })
                continue

            row_id = row[0].strip()
            row_name = row[1].strip()
            row_model = row[2].strip()
            row_group = row[3].strip()
            perms = [row[4].strip(), row[5].strip(), row[6].strip(), row[7].strip()]

            if not row_id:
                errors.append({
                    "code": "MISSING_ACCESS_ID",
                    "message": "Access rule row is missing a required 'id'.",
                    "line": line_num,
                    "element": "id",
                })
            elif row_id in seen_ids:
                errors.append({
                    "code": "DUPLICATE_ACCESS_ID",
                    "message": (
                        f"Duplicate access ID '{row_id}' found (first defined at line {seen_ids[row_id]})."
                    ),
                    "line": line_num,
                    "element": "id",
                })
            else:
                seen_ids[row_id] = line_num

            # Validate model_id prefix
            last_model_part = row_model.split(".")[-1]
            if not last_model_part.startswith("model_"):
                errors.append({
                    "code": "MISSING_MODEL_PREFIX",
                    "message": (
                        f"model_id:id '{row_model}' is missing the required 'model_' prefix. "
                        f"Example: 'model_{row_model.replace('.', '_')}'."
                    ),
                    "line": line_num,
                    "element": "model_id:id",
                })

            # Validate permission flags
            perm_names = ["perm_read", "perm_write", "perm_create", "perm_unlink"]
            for p_name, p_val in zip(perm_names, perms):
                if p_val not in ("0", "1"):
                    errors.append({
                        "code": "INVALID_PERMISSION_FLAG",
                        "message": (
                            f"Column '{p_name}' has invalid value '{p_val}'. Permissions must strictly be '0' or '1'."
                        ),
                        "line": line_num,
                        "element": p_name,
                    })

            if not row_group:
                info.append({
                    "code": "GLOBAL_ACCESS_NOTICE",
                    "message": (
                        f"Access rule '{row_id or 'unnamed'}' has an empty group_id:id and applies globally to all users."
                    ),
                    "line": line_num,
                    "rule": row_id,
                })

            rules_found.append({
                "id": row_id,
                "name": row_name,
                "model": row_model,
                "group": row_group,
                "perm": ",".join(perms),
                "line": line_num,
            })

    valid = len(errors) == 0

    if errors:
        suggestions.append(
            "Resolve security access and rule errors to prevent AccessError or module installation failures."
        )
    if warnings:
        suggestions.append(
            "Review security warnings to verify record-level domain filters."
        )
    if valid and not warnings:
        suggestions.append(
            "The security file passed static checks. Actual access rights depend on installed groups in Odoo."
        )

    return {
        "success": True,
        "valid": valid,
        "file": str(resolved_file),
        "file_type": file_type,
        "summary": {
            "total_rules": len(rules_found),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "info_count": len(info),
        },
        "rules_found": rules_found,
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "suggestions": suggestions,
    }


ODOO_BASE_FIELDS = {
    "id",
    "display_name",
    "create_uid",
    "create_date",
    "write_uid",
    "write_date",
    "active",
    "__last_update",
}


@mcp.tool()
def check_odoo_addon_health(
    addon_path: str,
    addons_directories: list[str] | None = None,
) -> dict:
    """Run a comprehensive static health audit for an entire Odoo addon directory,
    evaluating the manifest, dependencies, XML views, Python models, and security rules.

    Args:
        addon_path: Absolute or relative path to the Odoo addon directory.
        addons_directories: Optional list of directory paths containing other addons
            to check module dependencies against.
    """
    if not isinstance(addon_path, str) or not addon_path.strip():
        return {
            "success": False,
            "error": "Please provide an addon directory path as text.",
        }

    if addons_directories is not None and (
        not isinstance(addons_directories, list)
        or not all(isinstance(d, str) for d in addons_directories)
    ):
        return {
            "success": False,
            "error": "addons_directories must be a list of directory path strings.",
        }

    try:
        resolved_addon = Path(addon_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "success": False,
            "error": f"Invalid addon path: {exc}",
        }

    if not resolved_addon.exists():
        return {
            "success": False,
            "error": f"Addon directory does not exist: {resolved_addon}",
        }

    if not resolved_addon.is_dir():
        return {
            "success": False,
            "error": f"Path is not a directory: {resolved_addon}",
        }

    manifest_path = resolved_addon / "__manifest__.py"
    if not manifest_path.is_file():
        return {
            "success": False,
            "error": f"__manifest__.py not found in addon directory: {resolved_addon}",
        }

    # 1. Manifest audit
    manifest_res = validate_odoo_manifest(str(resolved_addon))
    if not manifest_res.get("success", False):
        return {
            "success": False,
            "error": f"Manifest validation error: {manifest_res.get('error')}",
        }

    # 2. Dependency audit (if addons_directories provided)
    dep_info = {"checked": False}
    if addons_directories:
        try:
            source = manifest_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            manifest_dict = ast.literal_eval(tree.body[0].value)
            deps = manifest_dict.get("depends", [])
            dep_status = {}
            roots = [Path(d).expanduser().resolve() for d in addons_directories]

            for dep in deps:
                found_in = [str(r) for r in roots if (r / dep).is_dir()]
                dep_status[dep] = {
                    "status": "found" if found_in else "not found in supplied directories",
                    "found_in": found_in,
                }
            dep_info = {
                "checked": True,
                "dependency_status": dep_status,
            }
        except Exception as exc:
            dep_info = {"checked": False, "error": str(exc)}

    # 3. XML Views & Data Audit
    xml_files_to_check = set()
    for section in ("data", "demo"):
        for item in manifest_res.get("checked_files", []):
            p = item.get("path")
            if p and p.endswith(".xml"):
                resolved_p = (resolved_addon / p).resolve()
                if resolved_p.is_file():
                    xml_files_to_check.add(resolved_p)

    for subdir_name in ("views", "data"):
        subdir = resolved_addon / subdir_name
        if subdir.is_dir():
            for xml_file in subdir.glob("**/*.xml"):
                if xml_file.is_file():
                    xml_files_to_check.add(xml_file.resolve())

    views_audit = []
    all_views = []
    for xml_path in sorted(xml_files_to_check):
        rel_path = (
            str(xml_path.relative_to(resolved_addon))
            if xml_path.is_relative_to(resolved_addon)
            else str(xml_path)
        )
        xml_analysis = analyze_odoo_xml_view(
            xml_path=str(xml_path), addon_path=str(resolved_addon)
        )
        views_audit.append({
            "file": rel_path,
            "valid": xml_analysis.get("valid", False),
            "errors": xml_analysis.get("errors", []),
            "warnings": xml_analysis.get("warnings", []),
            "info": xml_analysis.get("info", []),
        })
        for v in xml_analysis.get("views_found", []):
            all_views.append({
                "file": rel_path,
                "view_id": v.get("id"),
                "target_model": v.get("target_model"),
                "line": v.get("line"),
                "arch_fields": v.get("arch_fields", []),
            })

    # 4. Python Models Audit
    models_audit = []
    all_models = []
    models_dir = resolved_addon / "models"
    if models_dir.is_dir():
        for py_file in sorted(models_dir.glob("**/*.py")):
            if py_file.name == "__init__.py":
                continue
            rel_path = str(py_file.relative_to(resolved_addon))
            py_analysis = analyze_odoo_python_model(
                python_path=str(py_file), addon_path=str(resolved_addon)
            )
            models_found_in_file = py_analysis.get("models_found", [])
            models_audit.append({
                "file": rel_path,
                "valid": py_analysis.get("valid", False),
                "models_found": len(models_found_in_file),
                "errors": py_analysis.get("errors", []),
                "warnings": py_analysis.get("warnings", []),
            })
            for m in models_found_in_file:
                all_models.append({
                    "file": rel_path,
                    "class_name": m.get("class_name"),
                    "name": m.get("name"),
                    "inherit": m.get("inherit"),
                    "fields": m.get("fields", []),
                })

    # 5. Security Audit
    security_audit = []
    all_rules = []
    security_dir = resolved_addon / "security"
    if security_dir.is_dir():
        for sec_file in sorted(security_dir.iterdir()):
            if (
                sec_file.is_file()
                and sec_file.suffix.lower() in (".csv", ".xml")
                and not sec_file.name.startswith(".")
            ):
                rel_path = str(sec_file.relative_to(resolved_addon))
                sec_analysis = analyze_odoo_security(
                    file_path=str(sec_file), addon_path=str(resolved_addon)
                )
                security_audit.append({
                    "file": rel_path,
                    "valid": sec_analysis.get("valid", False),
                    "file_type": sec_analysis.get("file_type", "csv"),
                    "total_rules": sec_analysis.get("summary", {}).get(
                        "total_rules", 0
                    ),
                    "errors": sec_analysis.get("errors", []),
                    "warnings": sec_analysis.get("warnings", []),
                })
                all_rules.extend(sec_analysis.get("rules_found", []))

    # 6. Cross-File Consistency Audit
    addon_models_by_name = {}
    for mod in all_models:
        m_name = mod.get("name")
        if m_name:
            if m_name not in addon_models_by_name:
                addon_models_by_name[m_name] = {
                    "file": mod.get("file"),
                    "class_name": mod.get("class_name"),
                    "inherit": mod.get("inherit"),
                    "fields": set(mod.get("fields", [])),
                }
            else:
                addon_models_by_name[m_name]["fields"].update(mod.get("fields", []))

    secured_model_names = set()
    for rule in all_rules:
        r_model = rule.get("model")
        if r_model and isinstance(r_model, str):
            clean_model = r_model.split(".")[-1]
            if clean_model.startswith("model_"):
                clean_model = clean_model[6:]
            secured_model_names.add(clean_model.lower())

    cross_file_issues = []

    # Check A: Custom models have security access rules
    for m_name, m_info in addon_models_by_name.items():
        inherit = m_info.get("inherit")
        is_new_model = not inherit or (isinstance(inherit, str) and inherit != m_name)
        if is_new_model:
            normalized_name = m_name.replace(".", "_").lower()
            if normalized_name not in secured_model_names:
                cross_file_issues.append({
                    "code": "MISSING_MODEL_ACCESS_RULE",
                    "severity": "warning",
                    "message": (
                        f"Model '{m_name}' defined in '{m_info['file']}' has no access rules defined in security CSV files. "
                        "In Odoo 14+, models without access rules are inaccessible to users."
                    ),
                    "element": m_name,
                    "file": m_info["file"],
                })

    # Check B: View fields exist on target models defined in addon
    for view in all_views:
        target_model = view.get("target_model")
        if target_model and target_model in addon_models_by_name:
            m_info = addon_models_by_name[target_model]
            valid_fields = m_info["fields"] | ODOO_BASE_FIELDS
            for field_name in view.get("arch_fields", []):
                if field_name not in valid_fields:
                    cross_file_issues.append({
                        "code": "UNDECLARED_VIEW_FIELD",
                        "severity": "warning",
                        "message": (
                            f"View '{view.get('view_id') or 'unnamed'}' in '{view['file']}' references field '{field_name}' "
                            f"on model '{target_model}', but it is not declared in addon model definitions."
                        ),
                        "element": field_name,
                        "file": view["file"],
                    })

    cross_file_errors = [i for i in cross_file_issues if i.get("severity") == "error"]
    cross_file_warnings = [i for i in cross_file_issues if i.get("severity") == "warning"]

    # Aggregates
    manifest_valid = manifest_res.get("valid", True)
    manifest_errors_count = len(manifest_res.get("missing_fields", [])) + len(
        manifest_res.get("missing_files", [])
    )

    views_errors_count = sum(len(v["errors"]) for v in views_audit)
    views_warnings_count = sum(len(v["warnings"]) for v in views_audit)

    models_errors_count = sum(len(m["errors"]) for m in models_audit)
    models_warnings_count = sum(len(m["warnings"]) for m in models_audit)

    sec_errors_count = sum(len(s["errors"]) for s in security_audit)
    sec_warnings_count = sum(len(s["warnings"]) for s in security_audit)

    total_errors = (
        manifest_errors_count
        + views_errors_count
        + models_errors_count
        + sec_errors_count
        + len(cross_file_errors)
    )
    total_warnings = (
        views_warnings_count
        + models_warnings_count
        + sec_warnings_count
        + len(cross_file_warnings)
    )

    files_scanned = (
        1 + len(views_audit) + len(models_audit) + len(security_audit)
    )

    if total_errors > 0 or not manifest_valid:
        health_status = "CRITICAL_ISSUES"
    elif total_warnings > 0:
        health_status = "NEEDS_ATTENTION"
    else:
        health_status = "HEALTHY"

    suggestions = []
    if not manifest_valid:
        suggestions.extend(manifest_res.get("suggestions", []))
    if views_errors_count > 0:
        suggestions.append(f"Fix {views_errors_count} error(s) in view XML files.")
    if models_errors_count > 0:
        suggestions.append(f"Fix {models_errors_count} error(s) in Python models.")
    if sec_errors_count > 0:
        suggestions.append(f"Fix {sec_errors_count} error(s) in security files.")
    if any(i["code"] == "MISSING_MODEL_ACCESS_RULE" for i in cross_file_issues):
        suggestions.append(
            "Define access rules in security/ir.model.access.csv for all custom models."
        )
    if any(i["code"] == "UNDECLARED_VIEW_FIELD" for i in cross_file_issues):
        suggestions.append(
            "Verify view field references against declared Python model fields."
        )
    if total_warnings > 0:
        suggestions.append(f"Review {total_warnings} warning(s) across the addon.")
    if health_status == "HEALTHY":
        suggestions.append(
            "All static checks passed. The addon is ready for testing in an active Odoo instance."
        )

    return {
        "success": True,
        "addon_name": resolved_addon.name,
        "addon_path": str(resolved_addon),
        "health_status": health_status,
        "summary": {
            "files_scanned": files_scanned,
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "cross_file_issues": len(cross_file_issues),
        },
        "audit_breakdown": {
            "manifest": {
                "valid": manifest_valid,
                "missing_files": manifest_res.get("missing_files", []),
                "missing_fields": manifest_res.get("missing_fields", []),
                "field_issues": manifest_res.get("field_issues", []),
            },
            "dependencies": dep_info,
            "views": views_audit,
            "models": models_audit,
            "security": security_audit,
            "cross_file_consistency": {
                "issues": cross_file_issues,
                "total_issues": len(cross_file_issues),
                "new_models_checked": len([
                    m
                    for m, inf in addon_models_by_name.items()
                    if not inf.get("inherit") or inf.get("inherit") != m
                ]),
                "views_checked": len([
                    v
                    for v in all_views
                    if v.get("target_model") in addon_models_by_name
                ]),
            },
        },
        "suggestions": suggestions,
    }

def _get_server_proxy(endpoint: str, timeout: int = 10) -> xmlrpc.client.ServerProxy:
    """Create an XML-RPC ServerProxy with custom socket timeout."""
    if endpoint.startswith("https://"):
        class SafeTimeoutTransport(xmlrpc.client.SafeTransport):
            def make_connection(self, host):
                conn = super().make_connection(host)
                conn.timeout = timeout
                return conn
        transport = SafeTimeoutTransport()
    else:
        class TimeoutTransport(xmlrpc.client.Transport):
            def make_connection(self, host):
                conn = super().make_connection(host)
                conn.timeout = timeout
                return conn
        transport = TimeoutTransport()
    return xmlrpc.client.ServerProxy(endpoint, transport=transport, allow_none=True)


@mcp.tool()
def verify_odoo_live_metadata(
    url: str,
    db: str,
    username: str,
    password: str,
    models_to_check: list[str] | None = None,
    fields_to_check: dict[str, list[str]] | None = None,
    xml_ids_to_check: list[str] | None = None,
    timeout: int = 10,
) -> dict:
    """Connect to a running Odoo instance via read-only XML-RPC to dynamically
    verify whether models, model fields, and external XML IDs exist in the active database.

    Args:
        url: URL of the Odoo instance (e.g. 'http://localhost:8069' or 'https://myodoo.example.com').
        db: Name of the Odoo database.
        username: User login name / email.
        password: User password or API key.
        models_to_check: Optional list of model names to verify (e.g. ['sale.order', 'res.partner']).
        fields_to_check: Optional mapping of model names to lists of field names to verify (e.g. {'sale.order': ['partner_id', 'custom_ref']}).
        xml_ids_to_check: Optional list of external XML IDs to verify (e.g. ['base.group_user', 'sale.action_orders']).
        timeout: Network timeout in seconds (default: 10).
    """
    if not isinstance(url, str) or not url.strip():
        return {
            "success": False,
            "error": "Please provide a valid Odoo URL as text.",
        }

    if not isinstance(db, str) or not db.strip():
        return {
            "success": False,
            "error": "Please provide a non-empty database name.",
        }

    if not isinstance(username, str) or not username.strip():
        return {
            "success": False,
            "error": "Please provide a non-empty username.",
        }

    if not isinstance(password, str) or not password:
        return {
            "success": False,
            "error": "Please provide a non-empty password or API key.",
        }

    if models_to_check is not None and (
        not isinstance(models_to_check, list)
        or not all(isinstance(m, str) for m in models_to_check)
    ):
        return {
            "success": False,
            "error": "models_to_check must be a list of model name strings.",
        }

    if fields_to_check is not None and (
        not isinstance(fields_to_check, dict)
        or not all(
            isinstance(k, str)
            and isinstance(v, list)
            and all(isinstance(f, str) for f in v)
            for k, v in fields_to_check.items()
        )
    ):
        return {
            "success": False,
            "error": "fields_to_check must be a dictionary mapping model names to lists of field name strings.",
        }

    if xml_ids_to_check is not None and (
        not isinstance(xml_ids_to_check, list)
        or not all(isinstance(x, str) for x in xml_ids_to_check)
    ):
        return {
            "success": False,
            "error": "xml_ids_to_check must be a list of XML ID strings.",
        }

    if not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 60:
        return {
            "success": False,
            "error": "timeout must be a positive number up to 60 seconds.",
        }

    parsed_url = urllib.parse.urlparse(url.strip())
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        return {
            "success": False,
            "error": "Invalid Odoo URL. Please provide an HTTP or HTTPS URL (e.g. 'http://localhost:8069').",
        }

    clean_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    if parsed_url.path and parsed_url.path != "/":
        clean_url += parsed_url.path.rstrip("/")

    # 1. Authenticate against /xmlrpc/2/common
    try:
        common = _get_server_proxy(f"{clean_url}/xmlrpc/2/common", timeout=int(timeout))
        uid = common.authenticate(db.strip(), username.strip(), password, {})
    except (OSError, socket.timeout, ConnectionRefusedError) as exc:
        return {
            "success": False,
            "error_type": "CONNECTION_ERROR",
            "error": f"Failed to connect to Odoo instance at {clean_url}: {exc}",
        }
    except xmlrpc.client.Fault as exc:
        return {
            "success": False,
            "error_type": "XMLRPC_FAULT",
            "error": f"Odoo server returned an error: {exc.faultString}",
        }
    except Exception as exc:
        return {
            "success": False,
            "error_type": "UNKNOWN_ERROR",
            "error": f"Unexpected error connecting to Odoo: {exc}",
        }

    if not uid:
        return {
            "success": True,
            "authenticated": False,
            "error": f"Authentication failed for user '{username}' on database '{db}'.",
            "suggestions": [
                "Verify that the database name, username, and password / API key are correct.",
                "Ensure the user has access to database and XML-RPC web services are enabled.",
            ],
        }

    # 2. Connect to /xmlrpc/2/object
    try:
        models_proxy = _get_server_proxy(f"{clean_url}/xmlrpc/2/object", timeout=int(timeout))
    except Exception as exc:
        return {
            "success": False,
            "error_type": "CONNECTION_ERROR",
            "error": f"Failed to initialize object endpoint: {exc}",
        }

    # 3. Query Models
    models_status = {}
    if models_to_check:
        try:
            records = models_proxy.execute_kw(
                db.strip(), uid, password,
                "ir.model", "search_read",
                [[["model", "in", models_to_check]]],
                {"fields": ["model", "name", "state"]},
            )
            found_dict = {r["model"]: r for r in records}
            for m in models_to_check:
                if m in found_dict:
                    models_status[m] = {
                        "exists": True,
                        "name": found_dict[m].get("name"),
                        "state": found_dict[m].get("state"),
                    }
                else:
                    models_status[m] = {"exists": False}
        except Exception as exc:
            models_status = {"error": f"Failed to query ir.model: {exc}"}

    # 4. Query Fields
    fields_status = {}
    if fields_to_check:
        for m_name, f_list in fields_to_check.items():
            try:
                records = models_proxy.execute_kw(
                    db.strip(), uid, password,
                    "ir.model.fields", "search_read",
                    [[["model", "=", m_name], ["name", "in", f_list]]],
                    {"fields": ["name", "ttype", "field_description", "required", "readonly"]},
                )
                found_fields = {r["name"]: r for r in records}
                field_res = {}
                for fn in f_list:
                    if fn in found_fields:
                        rec = found_fields[fn]
                        field_res[fn] = {
                            "exists": True,
                            "type": rec.get("ttype"),
                            "description": rec.get("field_description"),
                            "required": rec.get("required"),
                            "readonly": rec.get("readonly"),
                        }
                    else:
                        field_res[fn] = {"exists": False}
                fields_status[m_name] = field_res
            except Exception as exc:
                fields_status[m_name] = {"error": f"Failed to query fields for {m_name}: {exc}"}

    # 5. Query External XML IDs
    xml_ids_status = {}
    if xml_ids_to_check:
        parsed_xml_ids = []
        domain_clauses = []
        for xid in xml_ids_to_check:
            if "." in xid:
                mod_name, rec_name = xid.split(".", 1)
            else:
                mod_name, rec_name = "", xid
            parsed_xml_ids.append((xid, mod_name, rec_name))
            domain_clauses.append(["&", ["module", "=", mod_name], ["name", "=", rec_name]])

        try:
            if len(domain_clauses) == 1:
                combined_domain = domain_clauses[0]
            else:
                combined_domain = []
                for _ in range(len(domain_clauses) - 1):
                    combined_domain.append("|")
                for clause in domain_clauses:
                    combined_domain.extend(clause)

            records = models_proxy.execute_kw(
                db.strip(), uid, password,
                "ir.model.data", "search_read",
                [combined_domain],
                {"fields": ["module", "name", "model", "res_id"]},
            )
            found_xids = {}
            for r in records:
                full_key = f"{r['module']}.{r['name']}" if r["module"] else r["name"]
                found_xids[full_key] = r

            for xid, mod_name, rec_name in parsed_xml_ids:
                full_key = f"{mod_name}.{rec_name}" if mod_name else rec_name
                if full_key in found_xids:
                    rec = found_xids[full_key]
                    xml_ids_status[xid] = {
                        "exists": True,
                        "model": rec.get("model"),
                        "res_id": rec.get("res_id"),
                    }
                else:
                    xml_ids_status[xid] = {"exists": False}
        except Exception as exc:
            xml_ids_status = {"error": f"Failed to query ir.model.data: {exc}"}

    # 6. Suggestions and Summary
    suggestions = []
    missing_models = [
        m for m, stat in models_status.items()
        if isinstance(stat, dict) and not stat.get("exists") and "error" not in stat
    ]
    if missing_models:
        suggestions.append(
            f"Models not found in database: {', '.join(missing_models)}. Ensure the required module is installed."
        )

    missing_fields = []
    for m, f_dict in fields_status.items():
        if isinstance(f_dict, dict) and "error" not in f_dict:
            for fn, f_stat in f_dict.items():
                if isinstance(f_stat, dict) and not f_stat.get("exists"):
                    missing_fields.append(f"{m}.{fn}")
    if missing_fields:
        suggestions.append(
            f"Fields not found in database: {', '.join(missing_fields)}. Check spelling or upgrade the module in Odoo."
        )

    missing_xml_ids = [
        x for x, stat in xml_ids_status.items()
        if isinstance(stat, dict) and not stat.get("exists") and "error" not in stat
    ]
    if missing_xml_ids:
        suggestions.append(
            f"External XML IDs not found: {', '.join(missing_xml_ids)}. Verify dependent modules are installed."
        )

    if not suggestions:
        suggestions.append("All queried metadata verified successfully in the live Odoo database.")

    total_models_checked = len(models_to_check) if models_to_check else 0
    total_fields_checked = sum(len(fl) for fl in fields_to_check.values()) if fields_to_check else 0
    total_xml_ids_checked = len(xml_ids_to_check) if xml_ids_to_check else 0

    return {
        "success": True,
        "authenticated": True,
        "database": db.strip(),
        "user_id": uid,
        "url": clean_url,
        "summary": {
            "models_checked": total_models_checked,
            "fields_checked": total_fields_checked,
            "xml_ids_checked": total_xml_ids_checked,
            "missing_models": len(missing_models),
            "missing_fields": len(missing_fields),
            "missing_xml_ids": len(missing_xml_ids),
        },
        "models": models_status,
        "fields": fields_status,
        "xml_ids": xml_ids_status,
        "suggestions": suggestions,
    }


def _eval_domain_ast(domain: list) -> str:
    """Convert an Odoo domain list into an Odoo 17/18/19 Python boolean expression."""
    if not isinstance(domain, list):
        return ""

    stack = []
    for token in reversed(domain):
        if token == "|":
            if len(stack) >= 2:
                left = stack.pop()
                right = stack.pop()
                stack.append(f"({left} or {right})")
        elif token == "&":
            if len(stack) >= 2:
                left = stack.pop()
                right = stack.pop()
                stack.append(f"({left} and {right})")
        elif isinstance(token, (list, tuple)) and len(token) == 3:
            f, op, val = token
            if op == "=" and val is True:
                expr = str(f)
            elif op == "=" and val is False:
                expr = f"not {f}"
            elif op == "!=" and val is True:
                expr = f"not {f}"
            elif op == "!=" and val is False:
                expr = str(f)
            elif op == "=":
                expr = f"{f} == {repr(val)}"
            elif op == "!=":
                expr = f"{f} != {repr(val)}"
            elif op == "in":
                if isinstance(val, (list, tuple)):
                    val_str = f"({repr(val[0])},)" if len(val) == 1 else f"{tuple(val)}"
                else:
                    val_str = repr(val)
                expr = f"{f} in {val_str}"
            elif op == "not in":
                if isinstance(val, (list, tuple)):
                    val_str = f"({repr(val[0])},)" if len(val) == 1 else f"{tuple(val)}"
                else:
                    val_str = repr(val)
                expr = f"{f} not in {val_str}"
            elif op in ("<", ">", "<=", ">="):
                expr = f"{f} {op} {repr(val)}"
            else:
                expr = f"{f} {op} {repr(val)}"
            stack.append(expr)

    if not stack:
        return ""
    if len(stack) == 1:
        res = stack[0]
        if res.startswith("(") and res.endswith(")"):
            res = res[1:-1]
        return res
    stack.reverse()
    return " and ".join(stack)


ATTRS_PATTERN = re.compile(r'attrs=([\"\'])(.*?)\1', re.DOTALL)
STATES_PATTERN = re.compile(r'states=([\"\'])(.*?)\1', re.DOTALL)
TREE_OPEN_PATTERN = re.compile(r'<tree(\s|>)')
TREE_CLOSE_PATTERN = re.compile(r'</tree>')
MANIFEST_VERSION_PATTERN = re.compile(r'([\'\"]version[\'\"]\s*:\s*[\'\"])(?:\d+\.\d+)(\.[^\'\"]+)?([\'\"])')

def _build_pre_migration_script(
    target_version: str,
    column_renames: dict | None = None,
    model_renames: list | None = None,
    xmlid_renames: list | None = None,
) -> str:
    """Build a pre-migration.py script for OpenUpgrade with concrete or scaffolded renames."""
    clean_target = str(target_version).strip() or "19.0"

    col_str = "COLUMN_RENAMES = {\n"
    if column_renames and isinstance(column_renames, dict):
        for table, pairs in column_renames.items():
            table_name = str(table).replace(".", "_")
            col_str += f'    "{table_name}": [\n'
            for pair in pairs:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    col_str += f'        ("{pair[0]}", "{pair[1]}"),\n'
            col_str += "    ],\n"
    else:
        col_str += (
            '    # "sale_order": [\n'
            '    #     ("old_column_name", "new_column_name"),\n'
            '    # ],\n'
        )
    col_str += "}\n"

    mod_str = "MODEL_RENAMES = [\n"
    if model_renames and isinstance(model_renames, (list, tuple)):
        for pair in model_renames:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                mod_str += f'    ("{pair[0]}", "{pair[1]}"),\n'
    else:
        mod_str += '    # ("custom.old.model", "custom.new.model"),\n'
    mod_str += "]\n"

    xml_str = "XMLID_RENAMES = [\n"
    if xmlid_renames and isinstance(xmlid_renames, (list, tuple)):
        for pair in xmlid_renames:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                xml_str += f'    ("{pair[0]}", "{pair[1]}"),\n'
    else:
        xml_str += '    # ("my_module.old_view_id", "my_module.new_view_id"),\n'
    xml_str += "]\n"

    has_concrete_cols = bool(column_renames)
    has_concrete_mods = bool(model_renames)
    has_concrete_xml = bool(xmlid_renames)

    lines = [
        f'"""OpenUpgrade pre-migration script for Odoo {clean_target}.',
        "",
        "Executed in PostgreSQL before Odoo models load.",
        "Use openupgradelib to rename tables, columns, and convert data structures without data loss.",
        '"""',
        "import logging",
        "from openupgradelib import openupgrade",
        "",
        "_logger = logging.getLogger(__name__)",
        "",
        col_str.strip(),
        "",
        mod_str.strip(),
        "",
        xml_str.strip(),
        "",
        "",
        "@openupgrade.migrate()",
        "def migrate(env, version):",
        '    _logger.info("Starting pre-migration for database %s to %s", env.cr.dbname, version)',
        "",
    ]

    if has_concrete_cols:
        lines.extend([
            "    if COLUMN_RENAMES:",
            '        _logger.info("Renaming columns: %s", COLUMN_RENAMES)',
            "        openupgrade.rename_columns(env.cr, COLUMN_RENAMES)",
            "",
        ])
    else:
        lines.extend([
            "    # Example: openupgrade.rename_columns(env.cr, COLUMN_RENAMES)",
            "",
        ])

    if has_concrete_mods:
        lines.extend([
            "    if MODEL_RENAMES:",
            '        _logger.info("Renaming models: %s", MODEL_RENAMES)',
            "        openupgrade.rename_models(env.cr, MODEL_RENAMES)",
            "",
        ])
    else:
        lines.extend([
            "    # Example: openupgrade.rename_models(env.cr, MODEL_RENAMES)",
            "",
        ])

    if has_concrete_xml:
        lines.extend([
            "    if XMLID_RENAMES:",
            '        _logger.info("Renaming XML IDs: %s", XMLID_RENAMES)',
            "        openupgrade.rename_xmlids(env.cr, XMLID_RENAMES)",
            "",
        ])
    else:
        lines.extend([
            "    # Example: openupgrade.rename_xmlids(env.cr, XMLID_RENAMES)",
            "",
        ])

    return "\n".join(lines).strip() + "\n"


def _build_post_migration_script(
    target_version: str,
    selection_renames: dict | None = None,
    compute_backfills: list | None = None,
) -> str:
    """Build a post-migration.py script for OpenUpgrade with concrete or scaffolded data migration."""
    clean_target = str(target_version).strip() or "19.0"

    lines = [
        f'"""OpenUpgrade post-migration script for Odoo {clean_target}.',
        "",
        "Executed after Odoo models have been loaded and updated in the database.",
        "Use Python ORM code to compute new fields, re-link records, and clean up.",
        '"""',
        "import logging",
        "from openupgradelib import openupgrade",
        "",
        "_logger = logging.getLogger(__name__)",
        "",
        "",
        "@openupgrade.migrate()",
        "def migrate(env, version):",
        '    _logger.info("Starting post-migration for database %s to %s", env.cr.dbname, version)',
        "",
    ]

    if selection_renames and isinstance(selection_renames, dict):
        lines.append("    # 1. Selection key migrations (SQL updates)")
        for table, col_map in selection_renames.items():
            table_name = str(table).replace(".", "_")
            if isinstance(col_map, dict):
                for column, val_map in col_map.items():
                    if isinstance(val_map, dict):
                        for old_val, new_val in val_map.items():
                            lines.append(
                                f'    env.cr.execute("UPDATE {table_name} SET {column} = %s WHERE {column} = %s", ({repr(new_val)}, {repr(old_val)}))'
                            )
        lines.append("")
    else:
        lines.extend([
            "    # 1. Selection key migrations (SQL updates)",
            '    # env.cr.execute("UPDATE sale_order SET state = %s WHERE state = %s", ("quotation", "draft"))',
            "",
        ])

    if compute_backfills and isinstance(compute_backfills, (list, tuple)):
        lines.append("    # 2. Recompute newly added stored fields")
        for item in compute_backfills:
            if isinstance(item, dict) and "model" in item:
                model_name = item["model"]
                field_name = item.get("field")
                if field_name:
                    lines.extend([
                        f'    records = env["{model_name}"].search([("{field_name}", "=", False)])',
                        '    if records:',
                        f'        _logger.info("Recomputing %d {model_name} records for {field_name}", len(records))',
                        f'        records._compute_{field_name}() if hasattr(records, "_compute_{field_name}") else None',
                    ])
                else:
                    lines.extend([
                        f'    records = env["{model_name}"].search([])',
                        f'    _logger.info("Found %d {model_name} records", len(records))',
                    ])
        lines.append("")
    else:
        lines.extend([
            "    # 2. Recompute newly added stored fields",
            '    # records = env["custom.order"].search([("computed_total", "=", False)])',
            '    # if records:',
            '    #     records._compute_total()',
            "",
        ])

    return "\n".join(lines).strip() + "\n"


def _build_migrations_readme(
    target_version: str,
    addon_name: str,
) -> str:
    """Build README.md for an addon migrations directory."""
    clean_target = str(target_version).strip() or "19.0"
    return f"""# OpenUpgrade Migration Directory

This directory contains database migration scripts for upgrading this addon to Odoo {clean_target}.

## Structure
- `pre-migration.py`: Runs before model schema updates. Ideal for table and column renames via `openupgradelib`.
- `post-migration.py`: Runs after models update. Ideal for ORM data transformations and compute field backfills.

## Execution
Run with OpenUpgrade or invoke via standard Odoo CLI:
```bash
odoo-bin -c odoo.conf -u {addon_name} -d <database_name> --stop-after-init
```
"""


PRE_MIGRATION_TEMPLATE = _build_pre_migration_script("{target_version}")
POST_MIGRATION_TEMPLATE = _build_post_migration_script("{target_version}")
MIGRATIONS_README_TEMPLATE = _build_migrations_readme("{target_version}", "{addon_name}")


@mcp.tool()
def generate_openupgrade_scripts(
    addon_path: str,
    target_version: str = "19.0",
    column_renames: dict | None = None,
    model_renames: list | None = None,
    xmlid_renames: list | None = None,
    selection_renames: dict | None = None,
    compute_backfills: list | None = None,
    dry_run: bool = True,
    overwrite: bool = False,
) -> dict:
    """Generate production-ready OpenUpgrade database migration scripts (pre-migration.py,
    post-migration.py, README.md) with column, model, XML ID renames, selection value updates,
    and compute field backfills.

    Args:
        addon_path: Path to the Odoo addon directory.
        target_version: Target Odoo version (default: '19.0').
        column_renames: Optional dict of table to list of (old_col, new_col) tuples,
            e.g. {"sale_order": [("old_ref", "new_ref")]}.
        model_renames: Optional list of (old_model, new_model) tuples,
            e.g. [("old.model", "new.model")].
        xmlid_renames: Optional list of (old_xml_id, new_xml_id) tuples,
            e.g. [("my_module.old_view", "my_module.new_view")].
        selection_renames: Optional dict of table to {column: {old_val: new_val}},
            e.g. {"sale_order": {"state": {"draft": "quotation"}}}.
        compute_backfills: Optional list of dicts with model and field,
            e.g. [{"model": "sale.order", "field": "amount_tax"}].
        dry_run: If True, previews generated scripts without writing to disk.
        overwrite: If True, overwrites existing migration files in migrations/<version>.1.0/.
    """
    if not isinstance(addon_path, str) or not addon_path.strip():
        return {
            "success": False,
            "error": "Please provide an addon directory path as text.",
        }

    try:
        resolved_addon = Path(addon_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "success": False,
            "error": f"Invalid addon path: {exc}",
        }

    if not resolved_addon.exists():
        return {
            "success": False,
            "error": f"Addon directory does not exist: {resolved_addon}",
        }

    if not resolved_addon.is_dir():
        return {
            "success": False,
            "error": f"Provided path is a file, not a directory: {resolved_addon}",
        }

    manifest_path = resolved_addon / "__manifest__.py"
    if not manifest_path.is_file():
        return {
            "success": False,
            "error": f"No __manifest__.py found in addon directory: {resolved_addon}",
        }

    clean_target = str(target_version).strip() or "19.0"
    migrations_dir = resolved_addon / "migrations" / f"{clean_target}.1.0"

    pre_content = _build_pre_migration_script(clean_target, column_renames, model_renames, xmlid_renames)
    post_content = _build_post_migration_script(clean_target, selection_renames, compute_backfills)
    readme_content = _build_migrations_readme(clean_target, resolved_addon.name)

    files_plan = {
        migrations_dir / "pre-migration.py": pre_content,
        migrations_dir / "post-migration.py": post_content,
        migrations_dir / "README.md": readme_content,
    }

    files_written = []
    files_skipped = []
    file_results = []

    if not dry_run:
        migrations_dir.mkdir(parents=True, exist_ok=True)
        for path_obj, content in files_plan.items():
            rel = str(path_obj.relative_to(resolved_addon))
            if path_obj.exists() and not overwrite:
                files_skipped.append(path_obj)
                file_results.append({
                    "file": rel,
                    "status": "skipped_exists",
                    "content": path_obj.read_text(encoding="utf-8"),
                })
            else:
                path_obj.write_text(content, encoding="utf-8")
                files_written.append(path_obj)
                file_results.append({
                    "file": rel,
                    "status": "written",
                    "content": content,
                })
    else:
        for path_obj, content in files_plan.items():
            rel = str(path_obj.relative_to(resolved_addon))
            file_results.append({
                "file": rel,
                "status": "planned" if not path_obj.exists() else ("planned_overwrite" if overwrite else "skipped_exists"),
                "content": content,
            })

    col_count = sum(len(pairs) for pairs in column_renames.values()) if (column_renames and isinstance(column_renames, dict)) else 0
    mod_count = len(model_renames) if (model_renames and isinstance(model_renames, (list, tuple))) else 0
    xml_count = len(xmlid_renames) if (xmlid_renames and isinstance(xmlid_renames, (list, tuple))) else 0
    sel_count = sum(len(v) for col_map in selection_renames.values() for v in col_map.values() if isinstance(v, dict)) if (selection_renames and isinstance(selection_renames, dict)) else 0
    comp_count = len(compute_backfills) if (compute_backfills and isinstance(compute_backfills, (list, tuple))) else 0

    suggestions = []
    if dry_run:
        suggestions.append(
            "Dry run complete. Review generated OpenUpgrade scripts above. To write them to disk, re-run with dry_run=False."
        )
    else:
        suggestions.append(
            f"OpenUpgrade migration scripts generated in 'migrations/{clean_target}.1.0/'."
        )

    if col_count or mod_count or xml_count:
        suggestions.append(
            f"Pre-migration script configured with {col_count} column, {mod_count} model, and {xml_count} XML ID rename(s)."
        )

    if sel_count or comp_count:
        suggestions.append(
            f"Post-migration script configured with {sel_count} selection value mapping(s) and {comp_count} compute backfill(s)."
        )

    return {
        "success": True,
        "addon_name": resolved_addon.name,
        "addon_path": str(resolved_addon),
        "target_version": clean_target,
        "dry_run": dry_run,
        "migrations_directory": str(migrations_dir),
        "summary": {
            "files_planned": len(files_plan),
            "files_written": len(files_written),
            "files_skipped": len(files_skipped),
            "column_renames_count": col_count,
            "model_renames_count": mod_count,
            "xmlid_renames_count": xml_count,
            "selection_renames_count": sel_count,
            "compute_backfills_count": comp_count,
        },
        "files": file_results,
        "suggestions": suggestions,
    }


KNOWN_ENTERPRISE_MODULES = {
    "account_accountant",
    "account_asset",
    "account_budget",
    "account_consolidation",
    "account_reports",
    "appointment",
    "approvals",
    "data_recycle",
    "documents",
    "documents_hr",
    "helpdesk",
    "industry_fsm",
    "industry_fsm_report",
    "knowledge",
    "marketing_automation",
    "mrp_mps",
    "mrp_subcontracting_account",
    "mrp_workorder",
    "planning",
    "quality_control",
    "quality_mrp",
    "sale_renting",
    "sale_subscription",
    "sign",
    "social",
    "timesheet_grid",
    "voip",
    "web_cohort",
    "web_gantt",
    "web_grid",
    "web_map",
    "web_studio",
}


def _audit_python_migration_file(file_path: Path, rel_path: str) -> list[dict]:
    """Inspect a Python file for Odoo 16/17/18 -> 19 migration deprecations and patterns."""
    issues = []
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
    except Exception as exc:
        return [
            {
                "file": rel_path,
                "line": 1,
                "class_name": None,
                "method_name": None,
                "issue_type": "PYTHON_SYNTAX_ERROR",
                "message": f"Failed to parse Python syntax: {exc}",
                "suggested_fix": "Fix Python syntax error.",
            }
        ]

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue

        is_model = _is_odoo_model_class(node)
        class_name = node.name

        for stmt in node.body:
            if isinstance(stmt, ast.FunctionDef):
                method_name = stmt.name

                # Check decorators on this method
                has_model_create_multi = False
                for dec in stmt.decorator_list:
                    dec_name = None
                    if isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name) and dec.value.id == "api":
                        dec_name = dec.attr
                    elif isinstance(dec, ast.Name):
                        dec_name = dec.id
                    elif isinstance(dec, ast.Call):
                        if (
                            isinstance(dec.func, ast.Attribute)
                            and isinstance(dec.func.value, ast.Name)
                            and dec.func.value.id == "api"
                        ):
                            dec_name = dec.func.attr
                        elif isinstance(dec.func, ast.Name):
                            dec_name = dec.func.id

                    if dec_name == "model_create_multi":
                        has_model_create_multi = True
                    elif dec_name in ("multi", "one", "returns"):
                        issues.append({
                            "file": rel_path,
                            "line": dec.lineno if hasattr(dec, "lineno") else stmt.lineno,
                            "class_name": class_name,
                            "method_name": method_name,
                            "issue_type": "OBSOLETE_DECORATOR",
                            "message": f"Legacy decorator @api.{dec_name} is obsolete and removed in modern Odoo.",
                            "suggested_fix": f"Remove @api.{dec_name} decorator.",
                        })

                # 1. Check name_get
                if is_model and method_name == "name_get":
                    issues.append({
                        "file": rel_path,
                        "line": stmt.lineno,
                        "class_name": class_name,
                        "method_name": "name_get",
                        "issue_type": "NAME_GET_DEPRECATED",
                        "message": (
                            "name_get() was deprecated in Odoo 17 and removed. "
                            "In Odoo 18/19, display_name is a computed field. Implement _compute_display_name() instead."
                        ),
                        "suggested_fix": (
                            "def _compute_display_name(self):\n"
                            "    for record in self:\n"
                            "        record.display_name = f\"{record.name}\""
                        ),
                    })

                # 2. Check create override
                if is_model and method_name == "create":
                    if not has_model_create_multi:
                        issues.append({
                            "file": rel_path,
                            "line": stmt.lineno,
                            "class_name": class_name,
                            "method_name": "create",
                            "issue_type": "CREATE_MISSING_MODEL_CREATE_MULTI",
                            "message": (
                                "create() override does not use @api.model_create_multi. "
                                "In Odoo 17/18/19, create() must accept vals_list for high-performance batch creation."
                            ),
                            "suggested_fix": (
                                "@api.model_create_multi\n"
                                "def create(self, vals_list):\n"
                                "    return super().create(vals_list)"
                            ),
                        })

                # 3. Check _name_search legacy signature
                if is_model and method_name == "_name_search":
                    arg_names = [arg.arg for arg in stmt.args.args]
                    if "name_get_uid" in arg_names:
                        issues.append({
                            "file": rel_path,
                            "line": stmt.lineno,
                            "class_name": class_name,
                            "method_name": "_name_search",
                            "issue_type": "NAME_SEARCH_LEGACY_SIGNATURE",
                            "message": (
                                "_name_search() signature with 'name_get_uid' is obsolete in Odoo 17/18/19."
                            ),
                            "suggested_fix": (
                                "Update signature to: def _name_search(self, name='', domain=None, operator='ilike', limit=None, order=None):"
                            ),
                        })

    # Traverse all call expressions across the AST for deprecated ORM calls and manual commits
    for subnode in ast.walk(tree):
        if isinstance(subnode, ast.Call):
            func = subnode.func
            # Check fields.Date.to_string, etc.
            if isinstance(func, ast.Attribute) and func.attr in ("to_string", "from_string"):
                if isinstance(func.value, ast.Attribute) and isinstance(func.value.value, ast.Name):
                    if func.value.value.id == "fields" and func.value.attr in ("Date", "Datetime"):
                        issues.append({
                            "file": rel_path,
                            "line": subnode.lineno,
                            "class_name": None,
                            "method_name": None,
                            "issue_type": "DEPRECATED_DATETIME_CONVERSION",
                            "message": (
                                f"fields.{func.value.attr}.{func.attr}() is deprecated. "
                                "Use native Python datetime / date methods (.isoformat(), .strftime(), or str())."
                            ),
                            "suggested_fix": "Replace with native Python date/datetime operations.",
                        })

            # Check cr.commit() or _cr.commit()
            if isinstance(func, ast.Attribute) and func.attr == "commit":
                is_commit = False
                if isinstance(func.value, ast.Name) and func.value.id in ("cr", "_cr"):
                    is_commit = True
                elif isinstance(func.value, ast.Attribute) and func.value.attr in ("cr", "_cr"):
                    is_commit = True
                if is_commit:
                    issues.append({
                        "file": rel_path,
                        "line": subnode.lineno,
                        "class_name": None,
                        "method_name": None,
                        "issue_type": "MANUAL_TRANSACTION_COMMIT",
                        "message": (
                            "Direct transaction commit (cr.commit()) detected. Manual commits break atomic test "
                            "suites and rollback safety in modern Odoo."
                        ),
                        "suggested_fix": "Remove manual cr.commit() calls and rely on Odoo's transaction management.",
                    })

    return issues


@mcp.tool()
def migrate_odoo_addon(
    addon_path: str,
    target_version: str = "19.0",
    dry_run: bool = True,
    scaffold_openupgrade: bool = True,
    column_renames: dict | None = None,
    model_renames: list | None = None,
    xmlid_renames: list | None = None,
    selection_renames: dict | None = None,
    compute_backfills: list | None = None,
) -> dict:
    """Migrate a custom Odoo addon from Odoo 16/17/18 to Odoo 19 (Community & Enterprise).

    Refactors XML view tags (<tree> -> <list>), converts legacy attrs="..." and states="..."
    dynamic expressions to modern invisible/readonly/required attributes, bumps manifest
    version, audits Python AST for deprecated ORM patterns (name_get removal, batch create overrides,
    fields.Date.to_string, cr.commit()), evaluates Enterprise vs Community edition compatibility,
    and scaffolds OCA OpenUpgrade database migration templates.

    Args:
        addon_path: Absolute or relative path to the Odoo addon directory.
        target_version: Target Odoo version (default: '19.0').
        dry_run: If True, previews unified diffs without modifying files on disk.
            If False, saves an automatic timestamped backup and applies all changes.
        scaffold_openupgrade: If True, scaffolds standard migrations/19.0.1.0/
            OpenUpgrade pre-migration and post-migration templates.
        column_renames: Optional dict of table to list of (old_col, new_col) tuples.
        model_renames: Optional list of (old_model, new_model) tuples.
        xmlid_renames: Optional list of (old_xml_id, new_xml_id) tuples.
        selection_renames: Optional dict of table to {column: {old_val: new_val}}.
        compute_backfills: Optional list of dicts with model and field.
    """
    if not isinstance(addon_path, str) or not addon_path.strip():
        return {
            "success": False,
            "error": "Please provide an addon directory path as text.",
        }

    try:
        resolved_addon = Path(addon_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "success": False,
            "error": f"Invalid addon path: {exc}",
        }

    if not resolved_addon.exists():
        return {
            "success": False,
            "error": f"Addon directory does not exist: {resolved_addon}",
        }

    if not resolved_addon.is_dir():
        return {
            "success": False,
            "error": f"Provided path is a file, not a directory: {resolved_addon}",
        }

    manifest_path = resolved_addon / "__manifest__.py"
    if not manifest_path.is_file():
        return {
            "success": False,
            "error": f"No __manifest__.py found in addon directory: {resolved_addon}",
        }

    clean_target = str(target_version).strip()
    if not clean_target:
        clean_target = "19.0"

    modified_files = []
    total_files_scanned = 0
    tree_converted_count = 0
    attrs_converted_count = 0
    states_converted_count = 0
    manifest_bumped = False

    files_to_write = {}

    # 1. Inspect and bump Manifest
    total_files_scanned += 1
    manifest_depends = []
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
        new_manifest, count = MANIFEST_VERSION_PATTERN.subn(
            lambda m: f"{m.group(1)}{clean_target}{m.group(2) or ''}{m.group(3)}",
            manifest_text,
        )
        if count > 0 and new_manifest != manifest_text:
            manifest_bumped = True
            diff_lines = list(difflib.unified_diff(
                manifest_text.splitlines(keepends=True),
                new_manifest.splitlines(keepends=True),
                fromfile=f"a/{manifest_path.name}",
                tofile=f"b/{manifest_path.name}",
            ))
            files_to_write[manifest_path] = new_manifest
            modified_files.append({
                "file": "__manifest__.py",
                "type": "manifest",
                "changes_count": count,
                "diff": "".join(diff_lines),
            })

        # Parse dependencies for Enterprise / Community edition audit
        try:
            m_ast = ast.parse(manifest_text, filename=str(manifest_path))
            for m_node in m_ast.body:
                if isinstance(m_node, ast.Expr) and isinstance(m_node.value, ast.Dict):
                    for k, v in zip(m_node.value.keys, m_node.value.values):
                        if isinstance(k, ast.Constant) and k.value == "depends":
                            if isinstance(v, (ast.List, ast.Tuple)):
                                for item in v.elts:
                                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                                        manifest_depends.append(item.value)
        except Exception:
            pass
    except Exception as exc:
        return {
            "success": False,
            "error": f"Failed to read manifest file: {exc}",
        }

    enterprise_deps = sorted(set(d for d in manifest_depends if d in KNOWN_ENTERPRISE_MODULES))
    has_studio = "web_studio" in manifest_depends or "studio_customization" in manifest_depends
    edition_requirement = "Enterprise" if (enterprise_deps or has_studio) else "Community"

    enterprise_notes = []
    if enterprise_deps:
        enterprise_notes.append(
            f"Addon depends on Odoo Enterprise module(s): {', '.join(enterprise_deps)}. "
            "An Odoo Enterprise license and repository access are required for Odoo 19."
        )
    if has_studio:
        enterprise_notes.append(
            "Addon references Odoo Studio customization components. Ensure Studio is available in target Odoo 19."
        )
    if not enterprise_deps and not has_studio:
        enterprise_notes.append(
            "Addon has no direct Enterprise dependencies and is compatible with Odoo 19 Community Edition."
        )

    # 2. Inspect and refactor XML view files
    xml_files = sorted(resolved_addon.glob("**/*.xml"))
    for xml_file in xml_files:
        if xml_file.name.startswith(".") or "_migration_backups" in xml_file.parts:
            continue
        total_files_scanned += 1
        try:
            content = xml_file.read_text(encoding="utf-8")
            file_changes = 0

            # Tree -> List
            new_content, t_open_c = TREE_OPEN_PATTERN.subn(r'<list\1', content)
            new_content, t_close_c = TREE_CLOSE_PATTERN.subn(r'</list>', new_content)
            tree_changes = t_open_c + t_close_c
            tree_converted_count += tree_changes
            file_changes += tree_changes

            # Attrs -> direct attributes
            def replace_attrs(match):
                raw_dict_str = match.group(2)
                try:
                    attrs_dict = ast.literal_eval(raw_dict_str)
                    if not isinstance(attrs_dict, dict):
                        return match.group(0)
                except Exception:
                    return match.group(0)

                replacements = []
                for attr_name in ("invisible", "readonly", "required"):
                    if attr_name in attrs_dict:
                        domain = attrs_dict[attr_name]
                        expr = _eval_domain_ast(domain)
                        if expr:
                            replacements.append(f'{attr_name}="{expr}"')
                return " ".join(replacements) if replacements else match.group(0)

            new_content, a_c = ATTRS_PATTERN.subn(replace_attrs, new_content)
            attrs_converted_count += a_c
            file_changes += a_c

            # States -> invisible
            def replace_states(match):
                raw_states = match.group(2)
                vals = [s.strip() for s in raw_states.split(",") if s.strip()]
                if not vals:
                    return match.group(0)
                if len(vals) == 1:
                    return f'invisible="state != \'{vals[0]}\'"'
                tuple_str = f"({', '.join(repr(v) for v in vals)})"
                return f'invisible="state not in {tuple_str}"'

            new_content, s_c = STATES_PATTERN.subn(replace_states, new_content)
            states_converted_count += s_c
            file_changes += s_c

            if file_changes > 0 and new_content != content:
                rel_path = str(xml_file.relative_to(resolved_addon))
                diff_lines = list(difflib.unified_diff(
                    content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=f"a/{rel_path}",
                    tofile=f"b/{rel_path}",
                ))
                files_to_write[xml_file] = new_content
                modified_files.append({
                    "file": rel_path,
                    "type": "xml_view",
                    "changes_count": file_changes,
                    "diff": "".join(diff_lines),
                })
        except Exception:
            continue

    # 3. Inspect Python files for AST deprecations
    py_files = sorted(resolved_addon.glob("**/*.py"))
    python_files_scanned = 0
    python_issues = []
    name_get_count = 0
    create_without_multi_count = 0
    deprecated_orm_calls_count = 0

    for py_file in py_files:
        if (
            py_file.name.startswith(".")
            or "_migration_backups" in py_file.parts
            or "migrations" in py_file.parts
            or "tests" in py_file.parts
            or py_file.name == "__manifest__.py"
        ):
            continue
        python_files_scanned += 1
        total_files_scanned += 1
        rel_py_path = str(py_file.relative_to(resolved_addon))
        f_issues = _audit_python_migration_file(py_file, rel_py_path)
        for issue in f_issues:
            python_issues.append(issue)
            if issue["issue_type"] == "NAME_GET_DEPRECATED":
                name_get_count += 1
            elif issue["issue_type"] == "CREATE_MISSING_MODEL_CREATE_MULTI":
                create_without_multi_count += 1
            elif issue["issue_type"] in (
                "DEPRECATED_DATETIME_CONVERSION",
                "NAME_SEARCH_LEGACY_SIGNATURE",
                "MANUAL_TRANSACTION_COMMIT",
                "OBSOLETE_DECORATOR",
            ):
                deprecated_orm_calls_count += 1

    # 4. OpenUpgrade Scaffolding
    migrations_dir = resolved_addon / "migrations" / f"{clean_target}.1.0"
    scaffold_created = False
    openupgrade_files_planned = []

    if scaffold_openupgrade:
        pre_file = migrations_dir / "pre-migration.py"
        post_file = migrations_dir / "post-migration.py"
        readme_file = migrations_dir / "README.md"

        if not pre_file.exists():
            openupgrade_files_planned.append(str(pre_file.relative_to(resolved_addon)))
        if not post_file.exists():
            openupgrade_files_planned.append(str(post_file.relative_to(resolved_addon)))
        if not readme_file.exists():
            openupgrade_files_planned.append(str(readme_file.relative_to(resolved_addon)))

    # 5. Apply changes or prepare dry-run
    backup_path = None
    if not dry_run:
        # Create timestamped backup
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = resolved_addon / "_migration_backups" / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = str(backup_dir)

        # Backup & write modified files
        for target_file, new_text in files_to_write.items():
            rel = target_file.relative_to(resolved_addon)
            dest_backup = backup_dir / rel
            dest_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target_file, dest_backup)
            target_file.write_text(new_text, encoding="utf-8")

        # Create OpenUpgrade templates
        if scaffold_openupgrade:
            migrations_dir.mkdir(parents=True, exist_ok=True)
            pre_file = migrations_dir / "pre-migration.py"
            if not pre_file.exists():
                pre_content = _build_pre_migration_script(clean_target, column_renames, model_renames, xmlid_renames)
                pre_file.write_text(pre_content, encoding="utf-8")
                scaffold_created = True

            post_file = migrations_dir / "post-migration.py"
            if not post_file.exists():
                post_content = _build_post_migration_script(clean_target, selection_renames, compute_backfills)
                post_file.write_text(post_content, encoding="utf-8")
                scaffold_created = True

            readme_file = migrations_dir / "README.md"
            if not readme_file.exists():
                readme_content = _build_migrations_readme(clean_target, resolved_addon.name)
                readme_file.write_text(readme_content, encoding="utf-8")
                scaffold_created = True
    else:
        scaffold_created = len(openupgrade_files_planned) > 0

    suggestions = []
    if dry_run:
        suggestions.append(
            "Dry run complete. Review the planned diffs above. To apply changes to disk, re-run with dry_run=False."
        )
    else:
        suggestions.append(
            f"Migration changes applied successfully. Full backup saved to '{backup_path}'."
        )

    if enterprise_deps:
        suggestions.append(
            f"Edition requirement: Enterprise. Addon depends on: {', '.join(enterprise_deps)}."
        )

    if name_get_count > 0:
        suggestions.append(
            f"Found {name_get_count} legacy name_get() method(s). Odoo 17+ removed name_get(); "
            "replace with _compute_display_name() to avoid runtime attribute errors."
        )

    if create_without_multi_count > 0:
        suggestions.append(
            f"Found {create_without_multi_count} create() method(s) missing @api.model_create_multi. "
            "Update to accept vals_list for batch compatibility in Odoo 19."
        )

    if deprecated_orm_calls_count > 0:
        suggestions.append(
            f"Found {deprecated_orm_calls_count} deprecated ORM or transaction call(s) (e.g., cr.commit() or fields.Date.to_string())."
        )

    if scaffold_openupgrade:
        suggestions.append(
            f"OpenUpgrade migration templates available in 'migrations/{clean_target}.1.0/'. "
            "Customize pre-migration.py if any database columns or models were renamed."
        )

    return {
        "success": True,
        "addon_name": resolved_addon.name,
        "addon_path": str(resolved_addon),
        "target_version": clean_target,
        "dry_run": dry_run,
        "backup_path": backup_path,
        "summary": {
            "files_scanned": total_files_scanned,
            "files_modified": len(modified_files),
            "xml_tree_tags_migrated": tree_converted_count,
            "attrs_converted": attrs_converted_count,
            "states_converted": states_converted_count,
            "manifest_version_bumped": manifest_bumped,
            "openupgrade_scaffolded": scaffold_created,
            "python_files_scanned": python_files_scanned,
            "name_get_count": name_get_count,
            "create_without_multi_count": create_without_multi_count,
            "deprecated_orm_calls_count": deprecated_orm_calls_count,
            "edition_requirement": edition_requirement,
        },
        "modified_files": modified_files,
        "openupgrade": {
            "scaffolded": scaffold_created,
            "migrations_directory": str(migrations_dir),
            "files": openupgrade_files_planned if dry_run else [
                str(migrations_dir / f) for f in ("pre-migration.py", "post-migration.py", "README.md")
            ],
        },
        "enterprise_audit": {
            "edition_requirement": edition_requirement,
            "enterprise_dependencies": enterprise_deps,
            "has_studio_customizations": has_studio,
            "notes": enterprise_notes,
        },
        "python_audit": python_issues,
        "suggestions": suggestions,
    }


MAX_OWL_FILE_SIZE = 500_000  # 500 KB

ODOO_MODULE_PATTERN = re.compile(r'/\*\*[\s*]*@odoo-module[\s*]*\*/')
LEGACY_ODOO_DEFINE_PATTERN = re.compile(r'\bodoo\.define\s*\(\s*([\'"][^\'"]+[\'"])')
LEGACY_WIDGET_EXTEND_PATTERN = re.compile(r'(\bWidget\.extend\b|\brequire\s*\(\s*[\'"]web\.Widget[\'"]\s*\))')
LEGACY_REQUIRE_MODULE_PATTERN = re.compile(r'\brequire\s*\(\s*[\'"](web\.[a-zA-Z0-9_]+)[\'"]\s*\)')
LEGACY_PATCH_PATTERN = re.compile(r'\bpatch\s*\(\s*([a-zA-Z0-9_$.]+)\s*,\s*([\'"][^\'"]+[\'"])\s*,')
OWL_COMPONENT_CLASS_PATTERN = re.compile(r'\bclass\s+([A-Za-z0-9_$]+)\s+extends\s+Component\b')
OWL_STATIC_TEMPLATE_PATTERN = re.compile(r'\bstatic\s+template\s*=\s*([\'"][^\'"]+[\'"])')
REGISTRY_ADD_PATTERN = re.compile(r'registry\s*\.\s*category\s*\(\s*([\'"][^\'"]+[\'"])\s*\)\s*\.\s*add\s*\(\s*([\'"][^\'"]+[\'"])')
OBSOLETE_T_RAW_PATTERN = re.compile(r'\bt-raw\s*=\s*([\'"][^\'"]+[\'"])')
T_NAME_PATTERN = re.compile(r'\bt-name\s*=\s*([\'"][^\'"]+[\'"])')


@mcp.tool()
def analyze_odoo_owl_component(
    path: str,
    addon_path: str | None = None,
) -> dict:
    """Analyze Odoo frontend JavaScript, OWL 2 components, and QWeb XML templates.

    Detects legacy Odoo JavaScript (odoo.define, Widget.extend, require('web.core')),
    missing '/** @odoo-module **/' headers, obsolete patch() 3-argument syntax,
    obsolete 't-raw' template attributes, and cross-references component static templates
    against declared XML <t t-name="..."> templates.

    Args:
        path: Path to a single .js file, .xml template file, or directory (e.g., static/src/).
        addon_path: Optional path to containing addon directory to enforce sandboxed access.
    """
    if not isinstance(path, str) or not path.strip():
        return {
            "success": False,
            "error": "Please provide a file or directory path as text.",
        }

    try:
        resolved_path = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "success": False,
            "error": f"Invalid path: {exc}",
        }

    if not resolved_path.exists():
        return {
            "success": False,
            "error": f"Path does not exist: {resolved_path}",
        }

    if addon_path is not None:
        if not isinstance(addon_path, str) or not addon_path.strip():
            return {
                "success": False,
                "error": "addon_path must be a non-empty string when provided.",
            }
        try:
            resolved_addon = Path(addon_path).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            return {
                "success": False,
                "error": f"Invalid addon path: {exc}",
            }

        if not resolved_addon.is_dir():
            return {
                "success": False,
                "error": f"Addon directory does not exist: {resolved_addon}",
            }

        try:
            resolved_path.relative_to(resolved_addon)
        except ValueError:
            return {
                "success": False,
                "error": (
                    f"Path '{resolved_path}' is outside the addon directory '{resolved_addon}'."
                ),
            }
    else:
        resolved_addon = None

    files_to_scan = []
    if resolved_path.is_file():
        if resolved_path.suffix.lower() not in (".js", ".xml"):
            return {
                "success": False,
                "error": "Unsupported file extension. Please provide a .js or .xml file or a directory.",
            }
        try:
            file_size = resolved_path.stat().st_size
        except OSError as exc:
            return {
                "success": False,
                "error": f"Unable to access file: {exc}",
            }
        if file_size > MAX_OWL_FILE_SIZE:
            return {
                "success": False,
                "error": (
                    f"File is too large to inspect ({file_size} bytes). "
                    f"Maximum allowed size is {MAX_OWL_FILE_SIZE} bytes."
                ),
            }
        files_to_scan.append(resolved_path)
    else:
        for f in sorted(resolved_path.glob("**/*")):
            if f.is_file() and f.suffix.lower() in (".js", ".xml"):
                if not any(part.startswith(".") for part in f.parts) and "_migration_backups" not in f.parts:
                    files_to_scan.append(f)

    if not files_to_scan and resolved_path.is_dir():
        return {
            "success": True,
            "valid": True,
            "path": str(resolved_path),
            "summary": {
                "files_scanned": 0,
                "js_files_count": 0,
                "xml_files_count": 0,
                "components_found": 0,
                "templates_found": 0,
                "registries_found": 0,
                "error_count": 0,
                "warning_count": 0,
                "info_count": 0,
            },
            "components": [],
            "templates": [],
            "registries": [],
            "errors": [],
            "warnings": [],
            "info": [],
            "suggestions": ["No .js or .xml files found in directory."],
        }

    errors = []
    warnings = []
    info = []
    suggestions = []
    components = []
    templates = []
    registries = []
    js_files_count = 0
    xml_files_count = 0

    base_dir = resolved_addon if resolved_addon else (resolved_path if resolved_path.is_dir() else resolved_path.parent)

    for file_path in files_to_scan:
        rel_path = str(file_path.relative_to(base_dir)) if file_path.is_relative_to(base_dir) else str(file_path)
        try:
            file_size = file_path.stat().st_size
        except OSError as exc:
            errors.append({
                "code": "FILE_ACCESS_ERROR",
                "message": f"Unable to access file: {exc}",
                "file": rel_path,
                "line": 1,
                "element": None,
            })
            continue

        if file_size > MAX_OWL_FILE_SIZE:
            errors.append({
                "code": "FILE_EXCEEDS_SIZE",
                "message": f"File is too large ({file_size} bytes). Maximum allowed size is {MAX_OWL_FILE_SIZE} bytes.",
                "file": rel_path,
                "line": 1,
                "element": None,
            })
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append({
                "code": "UNICODE_DECODE_ERROR",
                "message": f"Unable to decode file as UTF-8: {exc}",
                "file": rel_path,
                "line": 1,
                "element": None,
            })
            continue

        if file_path.suffix.lower() == ".js":
            js_files_count += 1
            if not content.strip():
                errors.append({
                    "code": "EMPTY_JS_FILE",
                    "message": "JavaScript file is empty.",
                    "file": rel_path,
                    "line": 1,
                    "element": None,
                })
                continue

            has_odoo_module = bool(ODOO_MODULE_PATTERN.search(content))
            has_es_imports = bool(re.search(r'\bimport\s+.*\s+from\s+[\'"][^\'"]+[\'"]', content))
            has_components = bool(OWL_COMPONENT_CLASS_PATTERN.search(content))

            if (has_es_imports or has_components) and not has_odoo_module:
                warnings.append({
                    "code": "MISSING_ODOO_MODULE_HEADER",
                    "message": "Missing '/** @odoo-module **/' header. Modern Odoo 16/17/18/19 ES modules require this header at the beginning of the file.",
                    "file": rel_path,
                    "line": 1,
                    "element": None,
                })

            for m in LEGACY_ODOO_DEFINE_PATTERN.finditer(content):
                line = content[:m.start()].count('\n') + 1
                errors.append({
                    "code": "LEGACY_ODOO_DEFINE",
                    "message": f"Legacy 'odoo.define({m.group(1)}, ...)' detected. Modern Odoo uses standard ES modules with '/** @odoo-module **/'.",
                    "file": rel_path,
                    "line": line,
                    "element": m.group(0),
                })

            for m in LEGACY_WIDGET_EXTEND_PATTERN.finditer(content):
                line = content[:m.start()].count('\n') + 1
                errors.append({
                    "code": "LEGACY_WIDGET_EXTEND",
                    "message": f"Legacy widget syntax '{m.group(1)}' detected. The old Widget class was completely removed in modern Odoo (use Owl Component).",
                    "file": rel_path,
                    "line": line,
                    "element": m.group(1),
                })

            for m in LEGACY_REQUIRE_MODULE_PATTERN.finditer(content):
                line = content[:m.start()].count('\n') + 1
                errors.append({
                    "code": "LEGACY_REQUIRE_MODULE",
                    "message": f"Legacy require('{m.group(1)}') detected. In modern Odoo, import ES modules directly (e.g. from '@web/core/...').",
                    "file": rel_path,
                    "line": line,
                    "element": m.group(0),
                })

            for m in LEGACY_PATCH_PATTERN.finditer(content):
                line = content[:m.start()].count('\n') + 1
                target = m.group(1)
                patch_name = m.group(2)
                warnings.append({
                    "code": "LEGACY_PATCH_SYNTAX",
                    "message": f"Legacy 3-argument patch syntax detected on '{target}' with name {patch_name}. In Odoo 17/18/19, patch() takes 2 arguments: patch(target, patchDef).",
                    "file": rel_path,
                    "line": line,
                    "element": m.group(0),
                })

            for m in OWL_COMPONENT_CLASS_PATTERN.finditer(content):
                comp_name = m.group(1)
                line = content[:m.start()].count('\n') + 1
                class_chunk = content[m.start():]
                tmpl_match = OWL_STATIC_TEMPLATE_PATTERN.search(class_chunk)
                template_name = tmpl_match.group(1).strip("'\"") if tmpl_match else None

                components.append({
                    "class_name": comp_name,
                    "template": template_name,
                    "file": rel_path,
                    "line": line,
                })

                if not template_name:
                    info.append({
                        "code": "NO_STATIC_TEMPLATE",
                        "message": f"Component '{comp_name}' does not declare a 'static template'. Ensure it renders dynamically or inherits a template.",
                        "file": rel_path,
                        "line": line,
                        "element": comp_name,
                    })

            for m in REGISTRY_ADD_PATTERN.finditer(content):
                category = m.group(1).strip("'\"")
                reg_name = m.group(2).strip("'\"")
                line = content[:m.start()].count('\n') + 1
                registries.append({
                    "category": category,
                    "name": reg_name,
                    "file": rel_path,
                    "line": line,
                })

        elif file_path.suffix.lower() == ".xml":
            xml_files_count += 1
            if not content.strip():
                errors.append({
                    "code": "EMPTY_TEMPLATE_FILE",
                    "message": "OWL template file is empty.",
                    "file": rel_path,
                    "line": 1,
                    "element": None,
                })
                continue

            try:
                root = ET.fromstring(content)
                if root.tag != "templates" and not root.findall(".//templates"):
                    warnings.append({
                        "code": "UNEXPECTED_TEMPLATE_ROOT",
                        "message": f"Expected <templates> root element in OWL template file, found <{root.tag}>.",
                        "file": rel_path,
                        "line": 1,
                        "element": root.tag,
                    })
            except ET.ParseError as exc:
                line = exc.position[0] if hasattr(exc, "position") and exc.position else 1
                errors.append({
                    "code": "XML_SYNTAX_ERROR",
                    "message": f"XML parse error in template: {exc}",
                    "file": rel_path,
                    "line": line,
                    "element": None,
                })

            for m in T_NAME_PATTERN.finditer(content):
                t_name = m.group(1).strip("'\"")
                line = content[:m.start()].count('\n') + 1
                templates.append({
                    "name": t_name,
                    "file": rel_path,
                    "line": line,
                })

            for m in OBSOLETE_T_RAW_PATTERN.finditer(content):
                line = content[:m.start()].count('\n') + 1
                raw_expr = m.group(1).strip("'\"")
                errors.append({
                    "code": "OBSOLETE_T_RAW",
                    "message": f"Obsolescent 't-raw=\"{raw_expr}\"' detected. Modern Owl and QWeb security removed t-raw in favor of 't-out'.",
                    "file": rel_path,
                    "line": line,
                    "element": m.group(0),
                })

    # Cross-referencing: Check unresolved templates if templates were found or XML files scanned
    if xml_files_count > 0:
        template_names_set = {t["name"] for t in templates}
        for comp in components:
            tmpl = comp.get("template")
            if tmpl and tmpl not in template_names_set:
                warnings.append({
                    "code": "UNRESOLVED_OWL_TEMPLATE",
                    "message": f"Component '{comp['class_name']}' in {comp['file']} references template '{tmpl}', but no matching <t t-name=\"{tmpl}\"> was found in scanned XML templates.",
                    "file": comp["file"],
                    "line": comp["line"],
                    "element": tmpl,
                })

    # Check duplicate templates
    seen_templates = set()
    for t in templates:
        t_name = t["name"]
        if t_name in seen_templates:
            errors.append({
                "code": "DUPLICATE_TEMPLATE_NAME",
                "message": f"Duplicate template name '{t_name}' found in {t['file']} (line {t['line']}).",
                "file": t["file"],
                "line": t["line"],
                "element": t_name,
            })
        seen_templates.add(t_name)

    valid = len(errors) == 0

    if errors:
        suggestions.append(
            "Resolve errors to ensure compatibility with modern Odoo ES modules and Owl 2."
        )
    if any(w["code"] == "LEGACY_PATCH_SYNTAX" for w in warnings):
        suggestions.append(
            "Update patch() calls from patch(target, 'name', {...}) to patch(target, {...}) for Odoo 17/18/19 compatibility."
        )
    if any(w["code"] == "MISSING_ODOO_MODULE_HEADER" for w in warnings):
        suggestions.append(
            "Add '/** @odoo-module **/' at the top of JavaScript files so Odoo bundles them as ES modules."
        )
    if any(w["code"] == "UNRESOLVED_OWL_TEMPLATE" for w in warnings):
        suggestions.append(
            "Ensure component static template names match defined <t t-name=\"...\"> attributes in static/src/**/*.xml."
        )
    if valid and not warnings:
        suggestions.append(
            "Frontend files passed static OWL and ES module checks."
        )

    return {
        "success": True,
        "valid": valid,
        "path": str(resolved_path),
        "summary": {
            "files_scanned": len(files_to_scan),
            "js_files_count": js_files_count,
            "xml_files_count": xml_files_count,
            "components_found": len(components),
            "templates_found": len(templates),
            "registries_found": len(registries),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "info_count": len(info),
        },
        "components": components,
        "templates": templates,
        "registries": registries,
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "suggestions": suggestions,
    }


def _extract_addon_models_for_testing(addon_path: Path) -> list[dict]:
    """Extract model, field, constraint, action, and compute definitions from an addon for test generation."""
    models_found = []
    py_files = sorted(addon_path.glob("**/*.py"))

    for py_file in py_files:
        if (
            py_file.name.startswith(".")
            or "tests" in py_file.parts
            or "migrations" in py_file.parts
            or "_migration_backups" in py_file.parts
            or py_file.name in ("__manifest__.py", "__init__.py")
        ):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
        except Exception:
            continue

        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not _is_odoo_model_class(node):
                continue

            class_name = node.name
            model_name = None
            class_fields = []
            constrains = []
            actions = []
            computes = []

            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            if target.id == "_name" and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                                model_name = stmt.value.value
                            elif target.id == "_inherit" and not model_name and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                                model_name = stmt.value.value

                        if isinstance(target, ast.Name) and not target.id.startswith("_") and isinstance(stmt.value, ast.Call):
                            func = stmt.value.func
                            field_type = None
                            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "fields":
                                field_type = func.attr
                            elif isinstance(func, ast.Name):
                                field_type = func.id

                            if field_type:
                                is_required = False
                                comp_attr = None
                                for kw in stmt.value.keywords:
                                    if kw.arg == "required" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                        is_required = True
                                    elif kw.arg == "compute" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                                        comp_attr = kw.value.value
                                class_fields.append({
                                    "name": target.id,
                                    "type": field_type,
                                    "required": is_required,
                                    "compute": comp_attr,
                                })

                elif isinstance(stmt, ast.FunctionDef):
                    fn_name = stmt.name
                    if fn_name.startswith("action_"):
                        actions.append(fn_name)
                    elif fn_name.startswith("_compute_"):
                        computes.append(fn_name)

                    for dec in stmt.decorator_list:
                        is_constrains = False
                        if isinstance(dec, ast.Call):
                            if isinstance(dec.func, ast.Attribute) and dec.func.attr == "constrains":
                                is_constrains = True
                            elif isinstance(dec.func, ast.Name) and dec.func.id == "constrains":
                                is_constrains = True
                            if is_constrains:
                                c_fields = [
                                    arg.value for arg in dec.args
                                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                                ]
                                constrains.append({"method": fn_name, "fields": c_fields})

            if model_name:
                models_found.append({
                    "class_name": class_name,
                    "model_name": model_name,
                    "fields": class_fields,
                    "constrains": constrains,
                    "actions": actions,
                    "computes": computes,
                    "file": str(py_file.relative_to(addon_path)),
                })

    return models_found


def _extract_addon_security_rules(addon_path: Path) -> dict[str, list[dict]]:
    """Parse security/ir.model.access.csv to extract security groups by model."""
    rules_by_model = {}
    sec_csv = addon_path / "security" / "ir.model.access.csv"
    if not sec_csv.is_file():
        return rules_by_model

    try:
        text = sec_csv.read_text(encoding="utf-8")
        reader = csv.reader(text.splitlines())
        rows = [r for r in reader if r and not r[0].strip().startswith("#")]
        if not rows:
            return rules_by_model
        header = [h.strip() for h in rows[0]]
        if "model_id:id" not in header:
            return rules_by_model

        model_idx = header.index("model_id:id")
        group_idx = header.index("group_id:id") if "group_id:id" in header else -1
        read_idx = header.index("perm_read") if "perm_read" in header else -1
        write_idx = header.index("perm_write") if "perm_write" in header else -1

        for row in rows[1:]:
            if len(row) <= model_idx:
                continue
            m_raw = row[model_idx].strip()
            m_name = m_raw[6:] if m_raw.startswith("model_") else m_raw
            m_dotted = m_name.replace("_", ".")
            group = row[group_idx].strip() if group_idx >= 0 and len(row) > group_idx else ""
            entry = {
                "group": group,
                "read": row[read_idx].strip() == "1" if read_idx >= 0 and len(row) > read_idx else False,
                "write": row[write_idx].strip() == "1" if write_idx >= 0 and len(row) > write_idx else False,
            }
            rules_by_model.setdefault(m_dotted, []).append(entry)
            rules_by_model.setdefault(m_name, []).append(entry)
    except Exception:
        pass

    return rules_by_model


def _build_model_test_code(
    model_info: dict,
    security_rules: list[dict] | None,
    addon_name: str,
    test_type: str = "all",
) -> str:
    """Generate production-ready Odoo TransactionCase unit test file content for a model."""
    class_name = model_info["class_name"]
    model_name = model_info["model_name"]
    fields = model_info.get("fields", [])
    constrains = model_info.get("constrains", [])
    actions = model_info.get("actions", [])
    computes = model_info.get("computes", [])

    sample_dict = {}
    has_name = any(f["name"] == "name" for f in fields)
    if has_name:
        sample_dict["name"] = f"Test {class_name}"

    for f in fields:
        fname = f["name"]
        ftype = f["type"]
        if fname in sample_dict:
            continue
        if f.get("required"):
            if ftype in ("Char", "Text", "Html"):
                sample_dict[fname] = f"Test {fname}"
            elif ftype == "Integer":
                sample_dict[fname] = 10
            elif ftype == "Float":
                sample_dict[fname] = 10.0
            elif ftype == "Boolean":
                sample_dict[fname] = True
            elif ftype == "Date":
                sample_dict[fname] = "2026-01-01"
            elif ftype == "Datetime":
                sample_dict[fname] = "2026-01-01 12:00:00"
            elif ftype == "Selection":
                sample_dict[fname] = "draft"

    if not sample_dict:
        sample_dict["name"] = f"Test {class_name}"

    field_setup_lines = []
    for k, v in sample_dict.items():
        if isinstance(v, str):
            field_setup_lines.append(f'            "{k}": "{v}",')
        elif isinstance(v, bool):
            field_setup_lines.append(f'            "{k}": {v},')
        else:
            field_setup_lines.append(f'            "{k}": {v},')

    lines = [
        "# -*- coding: utf-8 -*-",
        "from odoo.tests.common import TransactionCase, tagged",
        "from odoo.exceptions import ValidationError, AccessError, UserError",
        "",
        "",
        '@tagged("post_install", "-at_install")',
        f"class Test{class_name}(TransactionCase):",
        f'    """Automated test suite for model {model_name}."""',
        "",
        "    @classmethod",
        "    def setUpClass(cls):",
        "        super().setUpClass()",
        f'        cls.record = cls.env["{model_name}"].create({{',
    ]
    lines.extend(field_setup_lines)
    lines.extend([
        "        })",
        "",
        "    def test_record_creation(self):",
        '        """Test successful creation and initial field attributes."""',
        '        self.assertTrue(self.record.id, "Record should have been assigned a database ID.")',
    ])

    for k in list(sample_dict.keys())[:3]:
        val = sample_dict[k]
        if isinstance(val, str):
            lines.append(f'        self.assertEqual(self.record.{k}, "{val}")')
        elif isinstance(val, bool):
            lines.append(f"        self.assertEqual(self.record.{k}, {val})")

    lines.extend([
        "",
        "    def test_record_write(self):",
        '        """Test updating record field values."""',
    ])
    if "name" in sample_dict:
        lines.extend([
            '        self.record.write({"name": "Updated Record"})',
            '        self.assertEqual(self.record.name, "Updated Record")',
        ])
    else:
        first_key = list(sample_dict.keys())[0]
        lines.extend([
            f'        self.record.write({{"{first_key}": self.record.{first_key}}})',
            f'        self.assertTrue(self.record.{first_key})',
        ])

    lines.extend([
        "",
        "    def test_record_unlink(self):",
        '        """Test deleting a record and verify it no longer exists."""',
        f'        rec = self.env["{model_name}"].create({{',
    ])
    lines.extend(field_setup_lines)
    lines.extend([
        "        })",
        "        rec_id = rec.id",
        "        rec.unlink()",
        f'        self.assertFalse(self.env["{model_name}"].browse(rec_id).exists())',
    ])

    if test_type in ("all", "unit"):
        for c in constrains:
            mname = c["method"]
            c_fields = c["fields"]
            lines.extend([
                "",
                f"    def test_constraint_{mname}(self):",
                f'        """Verify @api.constrains {mname} raises ValidationError on invalid data."""',
                "        with self.assertRaises(ValidationError):",
            ])
            if c_fields:
                cf = c_fields[0]
                lines.append(f'            self.record.write({{"{cf}": False}})')
            else:
                lines.append('            self.record.write({"name": ""})')

        for action in actions:
            lines.extend([
                "",
                f"    def test_{action}(self):",
                f'        """Verify execution of action method {action}."""',
                f"        res = self.record.{action}()",
                "        self.assertTrue(res is None or isinstance(res, (dict, bool)))",
            ])

        for comp in computes:
            lines.extend([
                "",
                f"    def test_{comp}(self):",
                f'        """Verify computed method {comp} executes cleanly."""',
                f"        self.record.{comp}()",
                "        self.assertTrue(self.record.id)",
            ])

    if (test_type in ("all", "access_rights")) and security_rules:
        lines.extend([
            "",
            "    def test_access_rights(self):",
            '        """Verify model security read access."""',
            f'        self.assertTrue(self.env["{model_name}"].check_access_rights("read", raise_exception=False))',
        ])

    return "\n".join(lines).strip() + "\n"


def _build_module_install_test_code(addon_name: str) -> str:
    """Generate baseline module installation verification test."""
    return f"""# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestModuleInstall(TransactionCase):
    \"\"\"Verify clean module installation and dependencies.\"\"\"

    def test_module_installed(self):
        module = self.env["ir.module.module"].search([("name", "=", "{addon_name}")])
        self.assertTrue(module.exists(), "Module {addon_name} should exist in ir.module.module.")
        self.assertEqual(module.state, "installed", "Module {addon_name} should be installed.")
"""


def _build_tests_init_code(existing_init: str | None, module_names: list[str]) -> str:
    """Generate or update tests/__init__.py with test imports."""
    existing_lines = []
    existing_modules = set()
    if existing_init:
        existing_lines = existing_init.splitlines()
        for line in existing_lines:
            stripped = line.strip()
            if stripped.startswith("from . import"):
                parts = stripped.replace("from . import", "").split(",")
                for p in parts:
                    existing_modules.add(p.strip())

    new_imports = [f"from . import {m}" for m in module_names if m not in existing_modules]

    if not existing_lines:
        lines = ["# -*- coding: utf-8 -*-", ""]
        lines.extend(new_imports)
        return "\n".join(lines).strip() + "\n"

    result = list(existing_lines)
    for imp in new_imports:
        result.append(imp)
    return "\n".join(result).strip() + "\n"


@mcp.tool()
def scaffold_odoo_tests(
    addon_path: str,
    test_type: str = "all",
    models: list[str] | None = None,
    dry_run: bool = True,
    overwrite: bool = False,
) -> dict:
    """Scaffold comprehensive, production-ready Odoo automated test suites (tests/test_*.py
    and tests/__init__.py) tailored directly to an addon's Python models, fields, constraints,
    actions, and security access rules.

    Args:
        addon_path: Path to the Odoo addon directory.
        test_type: Test type to scaffold: 'all', 'unit', or 'access_rights' (default: 'all').
        models: Optional list of model technical names (e.g. ['sale.order']) to restrict test scaffolding.
        dry_run: If True, previews generated test suites without writing files to disk.
        overwrite: If True, overwrites existing test files in tests/.
    """
    if not isinstance(addon_path, str) or not addon_path.strip():
        return {
            "success": False,
            "error": "Please provide an addon directory path as text.",
        }

    try:
        resolved_addon = Path(addon_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "success": False,
            "error": f"Invalid addon path: {exc}",
        }

    if not resolved_addon.exists():
        return {
            "success": False,
            "error": f"Addon directory does not exist: {resolved_addon}",
        }

    if not resolved_addon.is_dir():
        return {
            "success": False,
            "error": f"Provided path is a file, not a directory: {resolved_addon}",
        }

    manifest_path = resolved_addon / "__manifest__.py"
    if not manifest_path.is_file():
        return {
            "success": False,
            "error": f"No __manifest__.py found in addon directory: {resolved_addon}",
        }

    clean_test_type = str(test_type).strip().lower()
    if clean_test_type not in ("all", "unit", "access_rights"):
        clean_test_type = "all"

    all_models = _extract_addon_models_for_testing(resolved_addon)
    security_rules = _extract_addon_security_rules(resolved_addon)

    if models and isinstance(models, (list, tuple)):
        model_filter = set(str(m).strip() for m in models if str(m).strip())
        target_models = [m for m in all_models if m["model_name"] in model_filter]
    else:
        target_models = all_models

    tests_dir = resolved_addon / "tests"
    init_path = tests_dir / "__init__.py"
    files_plan = {}
    test_module_names = []
    tests_scaffolded_count = 0

    if target_models:
        for m in target_models:
            clean_m_name = re.sub(r"[^a-zA-Z0-9]+", "_", m["model_name"]).strip("_").lower()
            mod_name = f"test_{clean_m_name}"
            test_module_names.append(mod_name)
            file_path = tests_dir / f"{mod_name}.py"
            m_sec = security_rules.get(m["model_name"])
            code = _build_model_test_code(m, m_sec, resolved_addon.name, test_type=clean_test_type)
            files_plan[file_path] = code
            # Rough estimate of tests generated
            tests_scaffolded_count += 3  # creation, write, unlink
            tests_scaffolded_count += len(m.get("constrains", []))
            tests_scaffolded_count += len(m.get("actions", []))
            tests_scaffolded_count += len(m.get("computes", []))
            if m_sec and clean_test_type in ("all", "access_rights"):
                tests_scaffolded_count += 1
    else:
        # Fallback to module installation test
        mod_name = "test_module_install"
        test_module_names.append(mod_name)
        file_path = tests_dir / f"{mod_name}.py"
        files_plan[file_path] = _build_module_install_test_code(resolved_addon.name)
        tests_scaffolded_count += 1

    existing_init_text = None
    if init_path.is_file():
        try:
            existing_init_text = init_path.read_text(encoding="utf-8")
        except Exception:
            pass

    init_code = _build_tests_init_code(existing_init_text, test_module_names)
    files_plan[init_path] = init_code

    files_written = []
    files_skipped = []
    file_results = []

    if not dry_run:
        tests_dir.mkdir(parents=True, exist_ok=True)
        for p_obj, content in files_plan.items():
            rel = str(p_obj.relative_to(resolved_addon))
            if p_obj.exists() and not overwrite and p_obj != init_path:
                files_skipped.append(p_obj)
                file_results.append({
                    "file": rel,
                    "status": "skipped_exists",
                    "content": p_obj.read_text(encoding="utf-8"),
                })
            else:
                p_obj.write_text(content, encoding="utf-8")
                files_written.append(p_obj)
                file_results.append({
                    "file": rel,
                    "status": "written",
                    "content": content,
                })
    else:
        for p_obj, content in files_plan.items():
            rel = str(p_obj.relative_to(resolved_addon))
            file_results.append({
                "file": rel,
                "status": "planned" if not p_obj.exists() else ("planned_overwrite" if overwrite else "skipped_exists"),
                "content": content,
            })

    suggestions = []
    if dry_run:
        suggestions.append(
            "Dry run complete. Review generated test suites above. To scaffold files to disk, run with dry_run=False."
        )
    else:
        suggestions.append(
            f"Automated test suites created under '{resolved_addon.name}/tests/'."
        )

    suggestions.append(
        f"Run tests in Odoo with CLI: odoo-bin -c odoo.conf -i {resolved_addon.name} --test-enable --test-tags /{resolved_addon.name} --stop-after-init"
    )

    return {
        "success": True,
        "addon_name": resolved_addon.name,
        "addon_path": str(resolved_addon),
        "test_type": clean_test_type,
        "dry_run": dry_run,
        "tests_directory": str(tests_dir),
        "summary": {
            "files_planned": len(files_plan),
            "files_written": len(files_written),
            "files_skipped": len(files_skipped),
            "models_covered": len(target_models),
            "tests_scaffolded": tests_scaffolded_count,
        },
        "files": file_results,
        "suggestions": suggestions,
    }


def format_cli_report(result: dict, title: str) -> str:
    """Format structured analyzer output into human-readable terminal report."""
    lines = [
        "=" * 80,
        f" Odoo Developer Assistant: {title}",
        "=" * 80,
    ]
    if "addon_name" in result:
        lines.append(f"Addon: {result['addon_name']}")
    if "path" in result:
        lines.append(f"Path: {result['path']}")
    elif "addon_path" in result:
        lines.append(f"Path: {result['addon_path']}")
    elif "file" in result:
        lines.append(f"File: {result['file']}")

    status_val = result.get("health_status") or result.get("status")
    if status_val:
        lines.append(f"Status: [{status_val}]")

    summary = result.get("summary", {})
    if summary:
        lines.append("\nSummary:")
        for k, v in summary.items():
            lines.append(f"  - {k.replace('_', ' ').capitalize()}: {v}")

    errors = result.get("errors", [])
    if errors:
        lines.append(f"\nErrors ({len(errors)}):")
        for err in errors:
            line_info = f" [Line {err['line']}]" if err.get("line") else ""
            msg = err.get("message", str(err))
            code = f"{err['code']}: " if err.get("code") else ""
            lines.append(f"  [ERROR]{line_info} {code}{msg}")

    warnings = result.get("warnings", [])
    if warnings:
        lines.append(f"\nWarnings ({len(warnings)}):")
        for warn in warnings:
            line_info = f" [Line {warn['line']}]" if warn.get("line") else ""
            msg = warn.get("message", str(warn))
            code = f"{warn['code']}: " if warn.get("code") else ""
            lines.append(f"  [WARN]{line_info} {code}{msg}")

    cross_file = result.get("cross_file_issues", [])
    if cross_file:
        lines.append(f"\nCross-File Consistency Issues ({len(cross_file)}):")
        for cf in cross_file:
            lines.append(f"  [CONSISTENCY] {cf.get('issue_type', '')}: {cf.get('message', str(cf))}")

    suggestions = result.get("suggestions", [])
    if suggestions:
        lines.append("\nSuggestions:")
        for s in suggestions:
            lines.append(f"  * {s}")

    lines.append("=" * 80)
    return "\n".join(lines)


def cli_check(path_str: str, fail_on_warning: bool = False, addons_dirs: list[str] | None = None) -> int:
    """Run static check on an addon directory or individual file."""
    try:
        target = Path(path_str).expanduser().resolve()
    except Exception as exc:
        print(f"[ERROR] Invalid path: {exc}", file=sys.stderr)
        return 2

    if not target.exists():
        print(f"[ERROR] Path does not exist: {target}", file=sys.stderr)
        return 2

    if target.is_dir():
        manifest_file = target / "__manifest__.py"
        if manifest_file.is_file():
            res = check_odoo_addon_health(str(target), addons_directories=addons_dirs)
            if not res.get("success"):
                print(f"[ERROR] {res.get('error', 'Health check failed')}", file=sys.stderr)
                return 2
            report = format_cli_report(res, f"Addon Health Check ({target.name})")
            print(report)
            status = res.get("health_status") or res.get("status")
            if status == "CRITICAL_ISSUES":
                return 1
            if fail_on_warning and status != "HEALTHY":
                return 1
            return 0
        else:
            js_or_xml = list(target.glob("**/*.js")) + list(target.glob("**/*.xml"))
            if js_or_xml:
                res = analyze_odoo_owl_component(str(target))
                if not res.get("success"):
                    print(f"[ERROR] {res.get('error')}", file=sys.stderr)
                    return 2
                report = format_cli_report(res, f"OWL Component Audit ({target.name})")
                print(report)
                if res.get("errors"):
                    return 1
                if fail_on_warning and res.get("warnings"):
                    return 1
                return 0
            else:
                print(f"[ERROR] Directory is not an Odoo addon (no __manifest__.py found): {target}", file=sys.stderr)
                return 2

    ext = target.suffix.lower()
    if ext == ".xml":
        res = analyze_odoo_xml_view(str(target))
        title = f"XML View Analysis ({target.name})"
    elif ext == ".py":
        res = analyze_odoo_python_model(str(target))
        title = f"Python Model Analysis ({target.name})"
    elif ext == ".csv":
        res = analyze_odoo_security(str(target))
        title = f"Security Access CSV Analysis ({target.name})"
    elif ext == ".js":
        res = analyze_odoo_owl_component(str(target))
        title = f"OWL JavaScript Analysis ({target.name})"
    else:
        print(f"[ERROR] Unsupported file extension '{ext}'. Expected .xml, .py, .csv, or .js.", file=sys.stderr)
        return 2

    if not res.get("success"):
        print(f"[ERROR] {res.get('error', 'Analysis failed')}", file=sys.stderr)
        return 2

    report = format_cli_report(res, title)
    print(report)

    if res.get("errors"):
        return 1
    if fail_on_warning and res.get("warnings"):
        return 1
    return 0


def cli_migrate(addon_path: str, target_version: str = "19.0", apply: bool = False) -> int:
    """Run Odoo addon migration via CLI."""
    res = migrate_odoo_addon(addon_path, target_version=target_version, dry_run=not apply)
    if not res.get("success"):
        print(f"[ERROR] {res.get('error', 'Migration failed')}", file=sys.stderr)
        return 2

    print("=" * 80)
    mode_str = "APPLIED CHANGES" if apply else "DRY RUN PREVIEW"
    print(f" Odoo Developer Assistant: Migration to Odoo {target_version} [{mode_str}]")
    print("=" * 80)
    print(f"Addon: {res.get('addon_name')}")
    print(f"Path: {res.get('addon_path')}")
    if res.get("backup_path"):
        print(f"Backup created at: {res.get('backup_path')}")

    summary = res.get("summary", {})
    if summary:
        print("\nSummary:")
        for k, v in summary.items():
            print(f"  - {k.replace('_', ' ').capitalize()}: {v}")

    modified_files = res.get("modified_files", [])
    if modified_files:
        print(f"\nModified Files ({len(modified_files)}):")
        for mf in modified_files:
            print(f"\n--- {mf['file']} ({mf['type']}, {mf['changes_count']} change(s)) ---")
            if "diff" in mf and mf["diff"]:
                print(mf["diff"])

    suggestions = res.get("suggestions", [])
    if suggestions:
        print("\nSuggestions:")
        for s in suggestions:
            print(f"  * {s}")
    print("=" * 80)
    return 0


def cli_scaffold_tests(
    addon_path: str,
    test_type: str = "all",
    apply: bool = False,
    overwrite: bool = False,
) -> int:
    """Scaffold automated test suites via CLI."""
    res = scaffold_odoo_tests(
        addon_path,
        test_type=test_type,
        dry_run=not apply,
        overwrite=overwrite,
    )
    if not res.get("success"):
        print(f"[ERROR] {res.get('error', 'Test scaffolding failed')}", file=sys.stderr)
        return 2

    print("=" * 80)
    mode_str = "WRITTEN TO DISK" if apply else "DRY RUN PREVIEW"
    print(f" Odoo Developer Assistant: Test Scaffolding [{mode_str}]")
    print("=" * 80)
    print(f"Addon: {res.get('addon_name')}")
    print(f"Target Directory: {res.get('tests_directory')}")

    summary = res.get("summary", {})
    if summary:
        print("\nSummary:")
        for k, v in summary.items():
            print(f"  - {k.replace('_', ' ').capitalize()}: {v}")

    files = res.get("files", [])
    if files:
        print(f"\nFiles ({len(files)}):")
        for f in files:
            print(f"  - {f['file']} [{f['status']}]")

    suggestions = res.get("suggestions", [])
    if suggestions:
        print("\nSuggestions:")
        for s in suggestions:
            print(f"  * {s}")
    print("=" * 80)
    return 0


def cli_install_hook(git_dir: str = ".git") -> int:
    """Install git pre-commit hook into repository."""
    try:
        git_path = Path(git_dir).expanduser().resolve()
    except Exception as exc:
        print(f"[ERROR] Invalid git directory path: {exc}", file=sys.stderr)
        return 2

    if not git_path.exists() or not git_path.is_dir():
        print(f"[ERROR] Git directory not found: {git_path}. Are you in a git repository?", file=sys.stderr)
        return 2

    hooks_dir = git_path / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_file = hooks_dir / "pre-commit"

    hook_script = """#!/usr/bin/env bash
# Odoo Developer Assistant - Git Pre-Commit Hook
# Statically validates modified Odoo files and addons before commit.

set -e

STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM)

if [ -z "$STAGED_FILES" ]; then
    exit 0
fi

# Detect runner
RUNNER=""
if command -v odoo-developer-assistant >/dev/null 2>&1; then
    RUNNER="odoo-developer-assistant"
elif [ -x "./.venv/bin/odoo-developer-assistant" ]; then
    RUNNER="./.venv/bin/odoo-developer-assistant"
elif [ -x "./.venv/bin/python" ] && [ -f "server.py" ]; then
    RUNNER="./.venv/bin/python server.py"
elif command -v python3 >/dev/null 2>&1 && [ -f "server.py" ]; then
    RUNNER="python3 server.py"
elif command -v python >/dev/null 2>&1 && [ -f "server.py" ]; then
    RUNNER="python server.py"
fi

if [ -z "$RUNNER" ]; then
    echo "[odoo-assistant] Warning: Could not locate odoo-developer-assistant or python environment. Skipping check."
    exit 0
fi

FAILED=0

for FILE in $STAGED_FILES; do
    case "$FILE" in
        *.py|*.xml|*.csv|*.js)
            if [ -f "$FILE" ]; then
                echo "[odoo-assistant] Checking $FILE..."
                $RUNNER check "$FILE" || FAILED=1
            fi
            ;;
    esac
done

if [ $FAILED -ne 0 ]; then
    echo ""
    echo "======================================================================"
    echo "COMMIT REJECTED: Odoo Developer Assistant detected issues in staged files."
    echo "Fix the errors above before committing or use git commit --no-verify."
    echo "======================================================================"
    exit 1
fi

exit 0
"""
    try:
        hook_file.write_text(hook_script, encoding="utf-8")
        hook_file.chmod(hook_file.stat().st_mode | 0o111)
        print(f"[SUCCESS] Git pre-commit hook installed to: {hook_file}")
        return 0
    except Exception as exc:
        print(f"[ERROR] Failed to write git hook: {exc}", file=sys.stderr)
        return 2


def cli_main(args: list[str] | None = None) -> int:
    """Main CLI command line entry dispatcher."""
    if args is None:
        args = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="odoo-developer-assistant",
        description="Odoo Developer Assistant: Fast CLI runner and pre-commit hook for Odoo developers.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    check_p = subparsers.add_parser("check", help="Static health check for an addon directory or file.")
    check_p.add_argument("path", help="Path to addon directory or file (.xml, .py, .csv, .js)")
    check_p.add_argument("--fail-on-warning", action="store_true", help="Exit with code 1 if warnings are found.")
    check_p.add_argument("--addons-dir", action="append", dest="addons_dirs", help="Additional addons directory for dependency checks.")

    migrate_p = subparsers.add_parser("migrate", help="Migrate an addon to Odoo 19.")
    migrate_p.add_argument("addon_path", help="Path to addon directory")
    migrate_p.add_argument("--target-version", default="19.0", help="Target Odoo version (default: 19.0)")
    migrate_p.add_argument("--apply", action="store_true", help="Apply changes directly to disk (creates backup).")

    tests_p = subparsers.add_parser("scaffold-tests", help="Scaffold automated test suites for an addon.")
    tests_p.add_argument("addon_path", help="Path to addon directory")
    tests_p.add_argument("--test-type", default="all", choices=["all", "unit", "access_rights"], help="Test type (default: all)")
    tests_p.add_argument("--apply", action="store_true", help="Write generated test files to disk.")
    tests_p.add_argument("--overwrite", action="store_true", help="Overwrite existing test files.")

    hook_p = subparsers.add_parser("install-hook", help="Install git pre-commit hook in a repository.")
    hook_p.add_argument("--git-dir", default=".git", help="Path to .git directory (default: .git)")

    subparsers.add_parser("server", help="Run in MCP server mode.")

    parsed = parser.parse_args(args)

    if not parsed.command or parsed.command == "server":
        mcp.run()
        return 0

    if parsed.command == "check":
        return cli_check(parsed.path, fail_on_warning=parsed.fail_on_warning, addons_dirs=parsed.addons_dirs)
    elif parsed.command == "migrate":
        return cli_migrate(parsed.addon_path, target_version=parsed.target_version, apply=parsed.apply)
    elif parsed.command == "scaffold-tests":
        return cli_scaffold_tests(parsed.addon_path, test_type=parsed.test_type, apply=parsed.apply, overwrite=parsed.overwrite)
    elif parsed.command == "install-hook":
        return cli_install_hook(git_dir=parsed.git_dir)

    return 0


def main(argv: list[str] | None = None) -> None:
    """Main entry point: launches MCP server by default or dispatches CLI runner."""
    args = sys.argv[1:] if argv is None else argv
    if not args or args == ["--stdio"] or args == ["server"]:
        mcp.run()
        return

    cli_subcommands = {"check", "migrate", "scaffold-tests", "install-hook", "-h", "--help"}
    if args and (args[0] in cli_subcommands):
        sys.exit(cli_main(args))

    mcp.run()


if __name__ == "__main__":
    main()
