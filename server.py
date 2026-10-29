import ast
import io
from pathlib import Path
import re
import xml.sax

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
                "line": line,
                "has_arch": False,
                "arch_children": 0,
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

    def endElement(self, name):
        if name == "record":
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


if __name__ == "__main__":
    mcp.run()
