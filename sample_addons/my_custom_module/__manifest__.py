{
    "name": "My Custom Module",
    "version": "19.0.1.0.0",
    "summary": "A sample module for MCP inspection",
    "description": """
        A demonstration Odoo addon used to test
        the Odoo Developer Assistant MCP server.
    """,
    "depends": [
        "base",
        "sale",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/sale_order_views.xml",
    ],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
