import ast
from pathlib import Path
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

if __name__ == "__main__":
    mcp.run()
