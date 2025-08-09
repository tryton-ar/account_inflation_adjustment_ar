# This file is part of the account_inflation_adjustment_ar module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.

from trytond.pool import Pool
from . import account

__all__ = ['register']


def register():
    Pool.register(
        account.Period,
        account.Account,
        account.Move,
        account.InflationAdjustment,
        account.InflationAdjustmentPeriod,
        account.SetAccountInflationAdjustableStart,
        module='account_inflation_adjustment_ar', type_='model')
    Pool.register(
        account.SetAccountInflationAdjustable,
        account.OpenInflationAdjustmentMove,
        module='account_inflation_adjustment_ar', type_='wizard')
