# This file is part account_payment_paypal module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool
from . import payment

def register():
    Pool.register(
        payment.AccountPaymentJournal,
        payment.Account,
        payment.Payment,
        module='account_payment_paypal', type_='model')
    Pool.register(
        module='account_payment_paypal', type_='wizard')
    Pool.register(
        module='account_payment_paypal', type_='report')
