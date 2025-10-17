# This file is part of the account_inflation_adjustment_ar module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.

from trytond.model import fields
from trytond.pool import PoolMeta


class Move(metaclass=PoolMeta):
    __name__ = 'account.move'

    @classmethod
    def _get_origin(cls):
        return super()._get_origin() + ['account.inflation.adjustment']


class Line(metaclass=PoolMeta):
    __name__ = 'account.move.line'

    @fields.depends('move_origin')
    def on_change_with_party_required(self, name=None):
        if self.move_origin:
            model = str(self.move_origin)
            origin = model[:model.find(',')]
            if origin == 'account.inflation.adjustment':
                return False
        return super().on_change_with_party_required()
