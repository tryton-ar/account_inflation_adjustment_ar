# This file is part of the account_inflation_adjustment_ar module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from decimal import Decimal
from dateutil.relativedelta import relativedelta

from trytond.model import ModelView, ModelSQL, Workflow, fields
from trytond.wizard import Wizard, StateView, StateTransition, StateAction, \
    Button
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction
from trytond.exceptions import UserError
from trytond.i18n import gettext


class Period(metaclass=PoolMeta):
    __name__ = 'account.period'

    inflation_index = fields.Numeric('Inflation Adjustment Index',
        digits=(16, 4))

    def compute_inflation_index(self, actual_period):
        if not self.inflation_index:
            raise UserError(gettext('account_inflation_adjustment_ar.'
                'msg_period_no_inflation_index',
                period=self.name))
        if not actual_period.inflation_index:
            raise UserError(gettext('account_inflation_adjustment_ar.'
                'msg_period_no_inflation_index',
                period=actual_period.name))
        res = (actual_period.inflation_index / self.inflation_index).quantize(
            Decimal(str(10 ** -4)))
        print('# Período %s: %s / %s = %s' % (self.name,
            str(actual_period.inflation_index), str(self.inflation_index),
            str(res)))
        return res


class Account(metaclass=PoolMeta):
    __name__ = 'account.account'

    inflation_adjustment = fields.Boolean('Inflation adjustable', select=True)
    adjustment_account = fields.Many2One('account.account',
        'Adjustment Account', help="Adjust inflation in a different account.",
        domain=[
            ('type', '!=', None),
            ('closed', '!=', True),
            ('company', '=', Eval('company', -1)),
            ])

    @staticmethod
    def default_inflation_adjustment():
        return False


class Move(metaclass=PoolMeta):
    __name__ = 'account.move'

    @classmethod
    def _get_origin(cls):
        return super()._get_origin() + ['account.inflation.adjustment']


class InflationAdjustment(Workflow, ModelSQL, ModelView):
    'Inflation Adjustment'
    __name__ = 'account.inflation.adjustment'

    _states = {'readonly': Eval('state') != 'draft'}

    name = fields.Char('Name', required=True,
        states=_states)
    company = fields.Many2One('company.company', 'Company', required=True,
        states=_states)
    date = fields.Date('Date', required=True,
        states=_states)
    periods = fields.Many2Many('account.inflation.adjustment.period',
        'adjustment', 'period', 'Periods', required=True,
        domain=[('company', '=', Eval('company', -1))],
        states=_states)
    account = fields.Many2One('account.account', 'Account', required=True,
        domain=[
            ('type', '!=', None),
            ('closed', '!=', True),
            ('company', '=', Eval('company', -1)),
            ],
        states=_states)
    accounting_date = fields.Date('Accounting Date', required=True,
        states=_states)
    journal = fields.Many2One('account.journal', 'Journal', required=True,
        states=_states, context={'company': Eval('company', -1)},
        depends={'company'})
    opening_move = fields.Many2One('account.move', 'Opening Move',
        domain=[('state', '=', 'posted')],
        states=_states)
    move = fields.Many2One('account.move', 'Move', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('calculated', 'Calculated'),
        ('posted', 'Posted'),
        ], 'State', required=True, readonly=True, select=True)

    del _states

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._transitions |= set((
            ('draft', 'calculated'),
            ('calculated', 'draft'),
            ('calculated', 'posted'),
            ))
        cls._buttons.update({
            'draft': {
                'invisible': Eval('state') != 'calculated',
                'depends': ['state'],
                },
            'compute': {
                'invisible': Eval('state') != 'draft',
                'depends': ['state'],
                },
            'post': {
                'invisible': Eval('state') != 'calculated',
                'depends': ['state'],
                },
            'open_move': {
                'invisible': ~Eval('state').in_(['calculated', 'posted']),
                'depends': ['state'],
                },
            })

    @staticmethod
    def default_state():
        return 'draft'

    @staticmethod
    def default_company():
        return Transaction().context.get('company')

    @staticmethod
    def default_date():
        Date = Pool().get('ir.date')
        return Date.today()

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, adjustments):
        for adjustment in adjustments:
            adjustment._draft_adjustment()

    def _draft_adjustment(self):
        Move = Pool().get('account.move')
        Move.delete([self.move])

    @classmethod
    @ModelView.button
    @Workflow.transition('calculated')
    def compute(cls, adjustments):
        for adjustment in adjustments:
            adjustment._validate_adjustment()
            adjustment._compute_adjustment()

    def _validate_adjustment(self):
        pass

    def _compute_adjustment(self):
        pool = Pool()
        Account = pool.get('account.account')
        Period = pool.get('account.period')
        Move = pool.get('account.move')
        MoveLine = pool.get('account.move.line')

        exp = Decimal(str(10 ** -2))

        accounts = Account.search([('inflation_adjustment', '=', True)])
        vals = dict((
            a.adjustment_account and a.adjustment_account.id or a.id,
            Decimal('0.0')) for a in accounts)

        actual_period_id = Period.find(self.company.id,
            date=self.accounting_date)
        actual_period = Period(actual_period_id)

        opening_move = self.opening_move
        if opening_move:
            prev_year_last_period_id = Period.find(self.company.id,
                date=opening_move.date + relativedelta(months=-1))
            prev_year_last_period = Period(prev_year_last_period_id)
            index = prev_year_last_period.compute_inflation_index(
                actual_period)
            lines = MoveLine.search([
                ('move.period', 'in', [p.id for p in self.periods]),
                ('account.inflation_adjustment', '=', True),
                ('state', '=', 'valid'),
                ('move', '=', opening_move),
                ])
            for line in lines:
                val = line.debit - line.credit
                adjmt = (val * index).quantize(exp) - val
                account = line.account.adjustment_account or line.account
                vals[account.id] += adjmt
                print(' [%s] (%s - %s) * index: %s - %s = %s' % (
                    str(line.account.id), str(line.debit), str(line.credit),
                    str((val * index).quantize(exp)), str(val), str(adjmt)))

        for period in self.periods:
            index = period.compute_inflation_index(actual_period)
            lines = MoveLine.search([
                ('move.period', '=', period),
                ('account.inflation_adjustment', '=', True),
                ('state', '=', 'valid'),
                ('move', '!=', opening_move),
                ])
            for line in lines:
                val = line.debit - line.credit
                adjmt = (val * index).quantize(exp) - val
                account = line.account.adjustment_account or line.account
                vals[account.id] += adjmt
                print(' [%s] (%s - %s) * index: %s - %s = %s' % (
                    str(line.account.id), str(line.debit), str(line.credit),
                    str((val * index).quantize(exp)), str(val), str(adjmt)))

        move_lines = []
        accounts_party_required = []
        total = Decimal('0.0')
        for account_id, value in vals.items():
            if not value:
                continue
            move_lines.append(MoveLine(
                account=account_id,
                debit=value if value > 0 else Decimal('0.0'),
                credit=abs(value) if value < 0 else Decimal('0.0'),
                ))
            account = Account(account_id)
            if account.party_required:
                accounts_party_required.append(account)
            total += value
        move_lines.append(MoveLine(
            account=self.account.id,
            debit=abs(total) if total < 0 else Decimal('0.0'),
            credit=total if total > 0 else Decimal('0.0'),
            ))
        if self.account.party_required:
            accounts_party_required.append(self.account)

        if accounts_party_required:
            Account.write(accounts_party_required,
                {'party_required': False})

        move = Move()
        move.journal = self.journal
        move.period = actual_period
        move.date = self.accounting_date
        move.origin = self
        move.company = self.company
        move.lines = move_lines
        move.save()

        if accounts_party_required:
            Account.write(accounts_party_required,
                {'party_required': True})

        self.move = move
        self.save()

    @classmethod
    @ModelView.button
    @Workflow.transition('posted')
    def post(cls, adjustments):
        for adjustment in adjustments:
            adjustment._post_adjustment()

    def _post_adjustment(self):
        pool = Pool()
        Account = pool.get('account.account')
        Move = pool.get('account.move')

        accounts_party_required = []
        for line in self.move.lines:
            if line.account.party_required:
                accounts_party_required.append(line.account)

        if accounts_party_required:
            Account.write(accounts_party_required,
                {'party_required': False})

        Move.post([self.move])

        if accounts_party_required:
            Account.write(accounts_party_required,
                {'party_required': True})

    @classmethod
    @ModelView.button_action(
        'account_inflation_adjustment_ar.wizard_open_adjustment_move')
    def open_move(cls, adjustments):
        pass


class InflationAdjustmentPeriod(ModelSQL):
    'Inflation Adjustment - Period'
    __name__ = 'account.inflation.adjustment.period'

    adjustment = fields.Many2One('account.inflation.adjustment',
        'Inflation Adjustment', ondelete='CASCADE', select=True, required=True)
    period = fields.Many2One('account.period', 'Period', ondelete='CASCADE',
        select=True, required=True)


class SetAccountInflationAdjustableStart(ModelView):
    'Set Account as Inflation Adjustable'
    __name__ = 'account.set_account_inflation_adjustment.start'

    inflation_adjustment = fields.Boolean('Inflation adjustable')

    @staticmethod
    def default_inflation_adjustment():
        return True


class SetAccountInflationAdjustable(Wizard):
    'Set Account as Inflation Adjustable'
    __name__ = 'account.set_account_inflation_adjustment'

    start = StateView('account.set_account_inflation_adjustment.start',
        'account_inflation_adjustment_ar.set_account_inflation_adjustment'
        '_start_view', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Confirm', 'confirm', 'tryton-ok', default=True),
        ])
    confirm = StateTransition()

    def transition_confirm(self):
        Account = Pool().get('account.account')
        inflation_adjustment = self.start.inflation_adjustment
        accounts = Account.browse(Transaction().context.get('active_ids'))
        while accounts:
            Account.write(accounts, {
                'inflation_adjustment': inflation_adjustment,
                })
            accounts = [c for a in accounts for c in a.childs]
        return 'end'


class OpenInflationAdjustmentMove(Wizard):
    'Open Inflation Adjustment Move'
    __name__ = 'account.open_adjustment_move'

    start = StateAction('account_inflation_adjustment_ar.act_move')

    def do_start(self, action):
        Adjustment = Pool().get('account.inflation.adjustment')
        adjustment = Adjustment(Transaction().context['active_id'])
        data = {'res_id': [adjustment.move and adjustment.move.id or None]}
        action['views'].reverse()
        return action, data
