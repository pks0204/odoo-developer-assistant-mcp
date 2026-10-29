import ast
import csv
import io
from pathlib import Path
import re
import socket
import urllib.parse
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
