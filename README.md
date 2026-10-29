# Odoo Developer Assistant MCP

An MCP (Model Context Protocol) server that helps Odoo developers investigate common errors, inspect custom addons, and validate module manifests.

The project provides initial troubleshooting suggestions and static validation. It does not modify an Odoo database or claim that a suggested diagnosis is confirmed.

## Features

### 1. Odoo Error Analyzer

The `analyze_odoo_error` tool classifies common Odoo errors and suggests troubleshooting steps.

Currently recognized categories include:

- XML ID errors
- Access rights errors
- OWL frontend errors
- Database schema errors
- XML data errors
- Key and lookup errors
- Generic RPC errors

Unknown errors receive general troubleshooting guidance.

### 2. Odoo Module Inspector

The `inspect_odoo_module` tool reads an addon's `__manifest__.py` file and reports:

- Module name and version
- Dependencies
- Data file references
- Installation settings
- Dependency availability across supplied addon directories

Dependency checks cover only the directories supplied to the tool. A dependency reported as missing may still exist elsewhere in an Odoo installation.

### 3. Odoo Manifest Validator

The `validate_odoo_manifest` tool checks:

- Required manifest fields
- Field types
- Referenced data files
- Missing files
- Unsafe file paths
- Manifest syntax

The manifest is parsed without executing its Python code.

### 4. Odoo XML View Analyzer

The `analyze_odoo_xml_view` tool performs static analysis on Odoo XML data and view files to detect structural errors, syntax mistakes, and suspicious references before loading them into an Odoo database.

Capabilities include:

- **XML Syntax Validation**: Identifies malformed XML with exact line and column numbers.
- **Root Element Checks**: Verifies expected `<odoo>` or legacy `<openerp>` root tags.
- **Record Identifier Checks**: Checks for required `id` and `model` attributes on `<record>` tags, flags whitespace in IDs, and detects duplicate record IDs across the file.
- **View Architecture Inspection**: Verifies that `ir.ui.view` records define non-empty `<field name="arch" type="xml">` blocks.
- **External ID Reference Extraction**: Extracts `ref` attributes and `ref('...')` expressions as informational items requiring verification in manifest dependencies or database models.
- **File Safety**: Enforces a 500 KB maximum file size limit, handles encoding issues, rejects directories, and supports optional `addon_path` confinement to prevent path traversal or symlink escapes.


## Requirements

- Python 3.12 or a compatible Python version
- MCP Python SDK 2.3.0
- MCP Inspector for interactive testing

## Installation

Clone or download the repository, then enter the project directory.

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the Python dependency:

```bash
python -m pip install -r requirements.txt
```

Install pytest for development and testing:

```bash
python -m pip install pytest
```

## Run the Automated Tests

From the project root:

```bash
pytest -v
```

The current test suite contains 39 tests covering error classification, addon inspection, manifest validation, XML view analysis, and edge cases.

## Run with MCP Inspector

Ensure MCP Inspector is available in your environment.

Start the project using:

```bash
./run_inspector.sh
```

Open the Inspector URL displayed by the command in your browser.

The launcher starts the Inspector with `server.py` and handles process cleanup when the session ends.

## Sample Addon & Fixtures

The `sample_addons/my_custom_module` directory contains a sample manifest for testing.

Some files referenced by the sample manifest are intentionally absent. This allows the manifest validator to demonstrate how it reports missing files.

The `tests/fixtures/` directory provides sample XML view files (`valid_view.xml`, `malformed_view.xml`, `suspicious_view.xml`) for testing and demonstration in MCP Inspector.

## Project Structure

```text
odoo-developer-assistant/
├── .gitignore
├── README.md
├── pytest.ini
├── requirements.txt
├── run_inspector.sh
├── server.py
├── sample_addons/
│   └── my_custom_module/
│       └── __manifest__.py
└── tests/
    ├── fixtures/
    │   ├── malformed_view.xml
    │   ├── suspicious_view.xml
    │   └── valid_view.xml
    └── test_server.py
```

## Limitations

- Error analysis is based on recognizable text patterns and is not a confirmed diagnosis.
- Dependency inspection only checks the supplied addon directories.
- Manifest validation does not guarantee that an addon will install successfully in Odoo.
- XML view analysis is static. It verifies syntax, structural conventions, and reference declarations, but does not guarantee that model fields or referenced external IDs exist in an active Odoo database.
- The tools do not currently connect to or modify an Odoo database.

## Future Improvements

- Recognize additional Odoo error patterns.
- Expand automated tests for unusual and invalid inputs.
- Improve manifest validation and dependency diagnostics.
- Add more detailed examples and usage documentation.

## License

No project license has been specified yet. Add a license before distributing the project for reuse.
