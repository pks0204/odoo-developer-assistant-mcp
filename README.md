# Odoo Developer Assistant MCP

[![CI](https://github.com/pks0204/odoo-developer-assistant-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pks0204/odoo-developer-assistant-mcp/actions/workflows/ci.yml)

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

### 5. Odoo Python Model Analyzer

The `analyze_odoo_python_model` tool statically analyzes Python model files using Python's standard `ast` module to detect structural errors, missing attributes, decorator misconfigurations, and common mistakes without executing untrusted code.

Capabilities include:

- **Model Class Inspection**: Detects classes inheriting from `models.Model`, `models.TransientModel`, or `models.AbstractModel`.
- **Model Attribute Validation**: Checks for `_name`, `_inherit`, and flags new models missing a `_description` attribute.
- **Relational Field Checks**: Flags `fields.Many2one` or `fields.Many2many` missing comodel names, and `fields.One2many` missing inverse field names.
- **On-delete Validation**: Warns about suspicious `ondelete` configurations on `Many2one` fields.
- **Decorator Diagnostics**: Flags `@api.depends()`, `@api.onchange()`, or `@api.constrains()` decorators with empty arguments.
- **File Safety**: Enforces a 500 KB maximum file size limit, handles encoding gracefully, and supports optional `addon_path` sandboxing.

### 6. Odoo Security Analyzer

The `analyze_odoo_security` tool inspects Odoo security files (`ir.model.access.csv` and security XML records) to detect syntax mistakes, missing model prefixes, invalid permission flags, and malformed rules before module loading.

Capabilities include:

- **Exact CSV Header Verification**: Verifies the strict 8-column header (`id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink`).
- **Model ID Prefix Validation**: Checks that `model_id:id` has the mandatory `model_` prefix (e.g. `model_custom_order`, not `custom.order`).
- **Binary Permission Flags**: Validates that all permission columns (`perm_read`, `perm_write`, `perm_create`, `perm_unlink`) are strictly `0` or `1`.
- **Integrity Checks**: Detects duplicate access rule IDs and column count mismatches.
- **Global Rule Detection**: Identifies rules with empty `group_id:id` that grant global permissions across all users.
- **Security XML Analysis**: Validates `ir.rule` record definitions to ensure `model_id` and `domain_force` are declared.

### 7. Unified Odoo Addon Health Check

The `check_odoo_addon_health` tool acts as a single, comprehensive audit command for an entire Odoo addon directory. It orchestrates manifest, view, model, and security analyzers into a consolidated diagnostic report.

Capabilities include:

- **Manifest & Dependency Audit**: Verifies manifest integrity and checks cross-directory dependency resolution.
- **Views & Data Audit**: Discovers and audits all declared and directory XML view/data files.
- **Python Models Audit**: Automatically discovers and inspects all model files in `models/`.
- **Security Audit**: Audits `security/ir.model.access.csv` and security XML rules.
- **Cross-File Consistency Audit**: Cross-references models, views, and security files within the addon to detect custom models missing security access rules and XML view `<field>` tags referencing undeclared model fields.
- **Consolidated Health Scoring**: Assigns an overall status (`HEALTHY`, `NEEDS_ATTENTION`, `CRITICAL_ISSUES`) with file counts, error/warning summaries, cross-file issues, and prioritized action suggestions.

### 8. Live Odoo Metadata Verifier

The `verify_odoo_live_metadata` tool enables developers to connect to an active Odoo instance (local development Docker, test instance, or staging server) via read-only XML-RPC to dynamically verify models, fields, and external XML IDs before deployment.

Capabilities include:
- **Model Verification**: Queries `ir.model` to check whether referenced models exist in the target database.
- **Field Verification**: Queries `ir.model.fields` to verify that field names, types (`ttype`), and attributes exist on the target model.
- **External XML ID Verification**: Queries `ir.model.data` to verify external IDs (e.g. `base.group_user`, `sale.action_orders`).
- **Read-Only Safety**: Strictly executes read-only `search_read` calls; never writes to or modifies the database.
- **Credential Protection**: Passwords and API keys are never leaked or stored in responses.
- **Zero Dependencies**: Powered exclusively by Python's standard library `xmlrpc.client`.

### 9. Odoo Migration & OpenUpgrade Scaffolder

The `migrate_odoo_addon` tool automates the code migration of custom addons from Odoo 16, 17, and 18 to Odoo 19 (Community and Enterprise) and scaffolds OCA OpenUpgrade database migration templates.

Capabilities include:
- **XML View Modernization**: Automatically converts deprecated `<tree>` tags to `<list>` (mandatory in Odoo 18/19), refactors legacy `attrs="{...}"` dictionaries into direct Python expressions (`invisible="..."`, `readonly="..."`, `required="..."`), and transforms `states="..."` attributes into boolean domain expressions.
- **Python AST Modernization & Diagnostics**: Statically inspects Python model files to detect breaking API changes:
  - Detects legacy `name_get()` methods (removed in Odoo 17+) and provides drop-in `_compute_display_name()` implementations.
  - Flags model `create()` overrides missing `@api.model_create_multi` and provides batch-compatible `vals_list` signatures.
  - Flags deprecated ORM calls (e.g. `fields.Date.to_string`, `fields.Datetime.from_string`, obsolete `_name_search` signatures).
  - Flags manual transaction calls (`cr.commit()`) that break test suites and rollback safety.
  - Flags obsolete legacy decorators (`@api.multi`, `@api.one`, `@api.returns`).
- **Enterprise vs Community Edition Audit**: Inspects module dependencies against known Odoo Enterprise apps (e.g. `web_studio`, `account_reports`, `helpdesk`, `sign`) and warns if an Enterprise license is required for Odoo 19 or if the module can run on Community.
- **Manifest Version Bumping**: Updates `version` declarations in `__manifest__.py` to target version `19.0.x.x` while preserving custom sub-versioning formats.
- **OpenUpgrade Database Scaffolding**: Automatically scaffolds OCA standard `migrations/19.0.1.0/` structure including:
  - `pre-migration.py`: Standard template using `openupgradelib.openupgrade` for table, column, and model renaming before Odoo ORM models load.
  - `post-migration.py`: Standard template for data migrations, compute field backfills, and XML data reloading after models load.
  - `README.md`: Migration execution guide and CLI commands.
- **Safety First (Dry-Run & Auto-Backup)**:
  - Defaults to `dry_run=True`, generating git-compatible unified diffs without modifying files on disk.
  - When `dry_run=False` is specified, creates a timestamped full backup under `_migration_backups/<timestamp>/` before applying any changes to disk.

### 10. Modern OWL & JavaScript Analyzer

The `analyze_odoo_owl_component` tool inspects custom Odoo frontend files (`static/src/**/*.js` and `static/src/**/*.xml`) to ensure compatibility with modern ES modules and Owl 2 (Odoo 16/17/18/19).

Capabilities include:
- **Legacy Framework Detection**: Flags obsolete Odoo JavaScript patterns including `odoo.define(...)`, `Widget.extend`, `require("web.Widget")`, and `require("web.core")`.
- **ES Module Headers**: Verifies that modern JavaScript files declare the mandatory `/** @odoo-module **/` header comment for bundling.
- **OWL 2 Patch Modernization**: Detects obsolete 3-argument `patch(target, "name", {...})` calls and guides migration to modern 2-argument `patch(target, {...})` syntax (Odoo 17+).
- **Component & Registry Inspection**: Identifies components extending `Component` and tracks registrations into categories (`fields`, `views`, `actions`, `services`).
- **QWeb Template Validation**: Verifies `<templates>` root structure, unique `t-name` declarations, and flags security-deprecated `t-raw` attributes in favor of `t-out`.
- **Cross-File Template Resolution**: When inspecting a directory or addon, cross-references component `static template = "..."` declarations against defined `<t t-name="...">` templates to catch missing or mistyped template names before runtime.

### 11. Automated OpenUpgrade Script Generator

The `generate_openupgrade_scripts` tool generates production-ready OCA OpenUpgrade migration scripts (`pre-migration.py`, `post-migration.py`, `README.md`) in `migrations/<target_version>.1.0/` to handle relational schema and database record migrations.

Capabilities include:
- **Pre-Migration Column & Model Renames**: Emits concrete `openupgradelib.openupgrade.rename_columns()` and `openupgrade.rename_models()` mapping dictionaries to rename tables/columns in PostgreSQL before Odoo ORM models load.
- **Pre-Migration XML ID Renames**: Generates `openupgrade.rename_xmlids()` lists to preserve database record identity when XML views or action IDs change across versions.
- **Post-Migration Selection Key Migrations**: Formulates safe, parameterized SQL updates (`env.cr.execute(...)`) to transform legacy selection field keys into new values.
- **Post-Migration Compute Field Backfills**: Produces batch ORM search and recompute code (`records._compute_<field>()`) to backfill new stored computed fields for historical records.
- **Safety & Idempotency**: Defaults to `dry_run=True` to preview generated Python code without touching disk; provides `overwrite=False` safeguards to protect existing manual migration scripts from accidental overwrite.

### 12. Automated Odoo Test Scaffolder

The `scaffold_odoo_tests` tool inspects an addon's Python models, fields, constraints, business actions, and security access rules to automatically generate comprehensive Odoo `TransactionCase` automated test suites (`tests/test_*.py` and `tests/__init__.py`).

Capabilities include:
- **Model CRUD & Fixture Generation**: Scaffolds `setUpClass` with valid default values for required model fields, along with `test_record_creation`, `test_record_write`, and `test_record_unlink`.
- **Constraint Validation Tests**: Detects `@api.constrains` decorators and generates `with self.assertRaises(ValidationError)` test methods to verify data integrity guards.
- **Action & Compute Method Tests**: Discovers public action methods (`action_*`) and computed methods (`_compute_*`), generating verification test cases.
- **Access Rights Verification**: Parses `security/ir.model.access.csv` and scaffolds security permission tests (`check_access_rights("read")`).
- **Clean Registration & Overwrite Protection**: Maintains `tests/__init__.py` imports, defaults to `dry_run=True` for safe code previewing, and respects `overwrite=False` to preserve existing custom test code.

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

The current test suite contains 139 tests covering error classification, addon inspection, manifest validation, XML view analysis, Python model analysis, security analysis, cross-file consistency, live metadata verification, addon migration & OpenUpgrade scaffolding, modern OWL & JavaScript component analysis, automated OpenUpgrade script generation, automated test scaffolding, entry points, and edge cases.

## Client Configuration

You can connect this MCP server to any MCP-compliant client (Claude Desktop, Cursor, Zed, etc.).

### Claude Desktop

Add the following to your Claude Desktop configuration file:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

#### Option A: Direct execution with `uvx` (No local clone needed)

```json
{
  "mcpServers": {
    "odoo-developer-assistant": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/pks0204/odoo-developer-assistant-mcp",
        "odoo-developer-assistant"
      ]
    }
  }
}
```

#### Option B: Local Repository

```json
{
  "mcpServers": {
    "odoo-developer-assistant": {
      "command": "/absolute/path/to/odoo-developer-assistant/.venv/bin/odoo-developer-assistant"
    }
  }
}
```

### Cursor / Zed

Configure the command in your editor's MCP settings:
- **Command**: `odoo-developer-assistant` (if installed in PATH or via `uvx`)
- **Transport**: `stdio`

### Example Assistant Prompts

Once configured, you can prompt your AI assistant with queries like:
- *"Audit the health of my custom addon at `/path/to/my_module` before I install it."*
- *"Check this Odoo log traceback and explain the root cause: `<paste traceback>`"*
- *"Inspect the XML view at `views/sale_order_views.xml` for structural errors or deprecated syntax."*
- *"Analyze `models/res_partner.py` for missing inverse fields or incomplete @api decorators."*
- *"Verify my `security/ir.model.access.csv` permissions file."*
- *"Migrate my Odoo 16 module at `/path/to/my_addon` to Odoo 19: preview the diffs first with dry_run, then scaffold OpenUpgrade database scripts."*
- *"Inspect my frontend component at `static/src/components/counter.js` and its QWeb XML template for modern OWL 2 compatibility."*
- *"Generate OpenUpgrade migration scripts for `/path/to/my_addon` to rename column `client_order_ref` to `customer_reference` on `sale_order` and backfill computed field `amount_tax`."*
- *"Scaffold comprehensive automated unit tests for all models in my addon at `/path/to/my_addon`, including CRUD, constraints, and access rights."*

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

The `tests/fixtures/` directory provides sample XML view files (`valid_view.xml`, `malformed_view.xml`, `suspicious_view.xml`), a sample Python model file (`sample_model.py`), and sample security files (`ir.model.access.csv`, `security_rules.xml`) for testing and demonstration in MCP Inspector.

## Project Structure

```text
odoo-developer-assistant/
├── .github/
│   └── workflows/
│       └── ci.yml
├── .gitignore
├── LICENSE
├── README.md
├── pyproject.toml
├── pytest.ini
├── requirements.txt
├── run_inspector.sh
├── server.py
├── sample_addons/
│   └── my_custom_module/
│       └── __manifest__.py
└── tests/
    ├── fixtures/
    │   ├── ir.model.access.csv
    │   ├── malformed_view.xml
    │   ├── sample_model.py
    │   ├── security_rules.xml
    │   ├── suspicious_view.xml
    │   └── valid_view.xml
    └── test_server.py
```

## Limitations

- Error analysis is based on recognizable text patterns and is not a confirmed diagnosis.
- Dependency inspection only checks the supplied addon directories.
- Manifest validation does not guarantee that an addon will install successfully in Odoo.
- XML view analysis is static. It verifies syntax, structural conventions, and reference declarations, but does not guarantee that model fields or referenced external IDs exist in an active Odoo database.
- Python model analysis is static. It inspects AST structure, required attributes, relational field declarations, and decorator syntax, but does not execute Python code or connect to an Odoo database.
- Security analysis is static. It verifies CSV access rule syntax, model ID prefixes, and XML record rule definitions, but does not confirm whether referenced security groups exist in an active Odoo database.
- The unified addon health check performs static analysis across files; complete behavioral verification requires testing the addon inside an active Odoo runtime environment.
- The tools do not modify an Odoo database (live inspection via `verify_odoo_live_metadata` is strictly read-only).

## Future Improvements

- Recognize additional Odoo error patterns and OWL UI tracebacks.
- Support batch live metadata verification for large enterprise schemas.
- Expand automated tests for unusual and edge-case inputs.

## License

This project is licensed under the GNU Lesser General Public License v3.0 (LGPL-3.0) — see the [LICENSE](LICENSE) file for details.
