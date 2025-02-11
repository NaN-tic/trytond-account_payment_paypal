# This file is part account_payment_gateway_paypal module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import iso8601
import logging
from datetime import datetime
from decimal import Decimal
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from trytond.pyson import Eval, Equal
from trytond.modules.account_payment_gateway.tools import unaccent
from trytond.model import (ModelSQL, ModelView, fields)

PAYPAL_METHODS = []
try:
    import paypalrestsdk
    PAYPAL_METHODS.append(('restsdk', 'REST SDK'))
except ImportError:
    pass
try:
    from paypal import PayPalInterface
    PAYPAL_METHODS.append(('soap', 'SOAP (Classic)'))
except ImportError:
    pass

logger = logging.getLogger(__name__)

_PAYPAL_STATE = {
    'created': 'draft',
    'approved': 'authorized',
    'failed': 'cancelled',
    'pending': 'draft',
    'canceled': 'cancelled',
    'expired': 'cancelled',
    'in_progress': 'authorized',
    'Pending': 'draft',
    'Processing': 'authorized',
    'Success': 'authorized',
    'Denied': 'cancelled',
    'Reversed': 'cancelled',
    'Completed': 'authorized',
    }
_PAYPAL_KEYS = (
    'L_TRANSACTIONID',
    'L_STATUS',
    'L_NAME',
    'L_TIMEZONE',
    'L_TIMESTAMP',
    'L_CURRENCYCODE',
    'L_TYPE',
    'L_EMAIL',
    'L_AMT',
    'L_NETAMT',
    )


class AccountPaymentJournal(metaclass=PoolMeta):
    __name__ = 'account.payment.journal'

    # Two methods: REST SDK (Paypal App) + SOAP (Classic)
    paypal_account = fields.Many2One(
        'account.payment.paypal.account', 'Account', ondelete='RESTRICT',
        states={
            'required': Eval('process_method') == 'paypal',
            'invisible': Eval('process_method') != 'paypal',
        })

    server_prefix = fields.Char('Server Prefix', states={
            'required': Eval('process_method') == 'paypal',
            'invisible': Eval('process_method') != 'paypal',
        })

    @classmethod
    def __setup__(cls):
        super().__setup__()
        paypal_method = ('paypal', 'Paypal')
        if paypal_method not in cls.process_method.selection:
            cls.process_method.selection.append(paypal_method)

class PaymentGroup(metaclass=PoolMeta):
    __name__ = 'account.payment.group'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons['succeed']['invisible'] |= (
            Eval('process_method') == 'paypal')

class Payment(metaclass=PoolMeta):
    __name__ = 'account.payment'

    @classmethod
    def paypal_ipn(cls, payment_journal, merchant_parameters, signature):
        pool = Pool()
        Payment = pool.get('account.payment')
        """
        Signal Redsys confirmation payment

        Redsys request form data:
            - Ds_Date
            - Ds_SecurePayment
            - Ds_Card_Country
            - Ds_AuthorisationCode
            - Ds_MerchantCode
            - Ds_Amount
            - Ds_ConsumerLanguage
            - Ds_Response
            - Ds_Order
            - Ds_TransactionType
            - Ds_Terminal
            - Ds_Signature
            - Ds_Currency
            - Ds_Hour
        """
        sandbox = False
        if payment_journal.redsys_account.mode == 'sandbox':
            sandbox = True

        merchant_code = payment_journal.redsys_account.merchant_code
        merchant_secret_key = payment_journal.redsys_account.secret_key

        redsyspayment = None
        redsyspayment = Client(business_code=merchant_code,
            secret_key=merchant_secret_key, sandbox=sandbox)
        valid_signature = redsyspayment.redsys_check_response(
            signature.encode('utf-8'), merchant_parameters.encode('utf-8'))
        if not valid_signature:
            #TODO: handle errors in voyager
            return '500'

        merchant_parameters = redsyspayment.decode_parameters(merchant_parameters)

        reference = merchant_parameters.get('Ds_Order')
        authorisation_code = merchant_parameters.get('Ds_AuthorisationCode')
        amount = merchant_parameters.get('Ds_Amount', 0)
        response = merchant_parameters.get('Ds_Response')

        log = "\n".join([('%s: %s' % (k, v)) for k, v in
            merchant_parameters.items()])

        # Search payment
        payments = Payment.search([
            ('redsys_reference_gateway', '=', reference),
            ('state', '=', 'draft'),
            ], limit=1)
        if payments:
            payment, = payments
            payment.redsys_authorisation_code = authorisation_code
            payment.amount = Decimal(amount)/100
            payment.redsys_gateway_log = log
            payment.save()
        else:
            payment = Payment()
            payment.description = reference
            payment.redsys_authorisation_code = authorisation_code
            payment.journal = payment_journal
            payment.redsys_reference_gateway = reference
            payment.amount = Decimal(amount)/100
            payment.redsys_gateway_log = log
            payment.save()

        # Process transaction 0000 - 0099: Done
        if int(response) < 100:
            Payment.confirm([payment])
            return response
        Payment.cancel([payment])
        return response

    @classmethod
    def create_paypal_payment(cls, party, amount, currency, payment_journal,
            paypal_account):
        pool = Pool()
        Payment = pool.get('account.payment')
        sandbox = paypal_account.paypal_mode

        payment = Payment()
        payment.journal = payment_journal
        payment.party = party
        payment.currency = currency
        payment.amount = amount
        payment.save()

class Account(ModelSQL, ModelView):
    'Paypal Account'
    __name__ = 'account.payment.paypal.account'

    paypal_method = fields.Selection(PAYPAL_METHODS, 'Paypal Methods',
        help='Select a API Paypal method to connect.')
    paypal_email = fields.Char('Email', help='Paypal Email Account')
    paypal_username = fields.Char('Username',
        states={
            'invisible': (~(Equal(Eval('paypal_method'), 'soap'))),
            'required': (Equal(Eval('paypal_method'), 'soap')),
        }, help='Paypal Username Soap API')
    paypal_password = fields.Char('Password', strip=False,
        states={'invisible': ~(Equal(Eval('paypal_method'), 'soap')),
            'required': (Equal(Eval('paypal_method'), 'soap')),
        }, help='Paypal Password Soap API')
    paypal_signature = fields.Char('Signature',
        states={
            'invisible': (~(Equal(Eval('paypal_method'), 'soap'))),
            'required': ((Equal(Eval('paypal_method'), 'soap'))),
        }, help='Paypal Signature Soap API')
    paypal_client_id = fields.Char('Client ID',
        states={
            'invisible': (~(Equal(Eval('paypal_method'), 'restsdk'))),
            'required': ((Equal(Eval('paypal_method'), 'restsdk'))),
        }, help='Paypal Rest APP Client ID')
    paypal_client_secret = fields.Char('Client Secret',
        states={
            'invisible': (~(Equal(Eval('paypal_method'), 'restsdk'))),
            'required': ((Equal(Eval('paypal_method'), 'restsdk'))),
        }, help='Paypal Rest APP Client Secret')
    paypal_mode = fields.Selection([('sandbox', 'Sandbox'), ('live', 'Live')],
        'Account Mode', states={'required': True})
