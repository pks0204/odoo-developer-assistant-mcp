# -*- coding: utf-8 -*-
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
