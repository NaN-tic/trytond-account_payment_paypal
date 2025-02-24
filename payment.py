# This file is part account_payment_gateway_paypal module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import iso8601
import logging
import requests
import json
from datetime import datetime
from decimal import Decimal
from requests.auth import HTTPBasicAuth
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from trytond.pyson import Eval, Equal
from trytond.modules.account_payment_gateway.tools import unaccent
from trytond.model import (ModelSQL, ModelView, fields)


class AccountPaymentJournal(metaclass=PoolMeta):
    __name__ = 'account.payment.journal'

    # Two methods: REST SDK (Paypal App) + SOAP (Classic)
    paypal_account = fields.Many2One(
        'account.payment.paypal.account', 'Account', ondelete='RESTRICT',
        states={
            'required': Eval('process_method') == 'paypal',
            'invisible': Eval('process_method') != 'paypal',
        })
    paypal_url = fields.Char('Paypal URL', states={
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

    paypal_payment_id = fields.Char('Paypal Payment ID', states={
            'required': Eval('process_method') == 'paypal',
            'invisible': Eval('process_method') != 'paypal',
        } )
    secret_identifier = fields.Char('Temporary Secret Identifier')

    @classmethod
    def create_paypal_payment(cls, party, amount, currency, payment_journal,
            paypal_account, url_ok, url_ko, secret_identifier = None):
        response = paypal_account.get_paypal_access_token()
        url = ''
        if paypal_account.paypal_mode == 'sandbox':
            url =  'https://api-m.sandbox.paypal.com/v1/payments/payment'
        else:
            url =  'https://api-m.paypal.com/v1/payments/payment'

        headers = {
        'Authorization': f'Bearer {response}'
            }
        payload = {
        "intent": "sale",
        "payer": {
            "payment_method": "paypal"
            },
        "redirect_urls": {
            "return_url": url_ok,
            "cancel_url": url_ko
            },
        "transactions": [{
            "amount": {
                "total": str(amount),
                "currency": currency.code
            },
            }]
        }
        payment_response = requests.post(url, headers=headers, json=payload)
        payment = payment_response.json()
        payment_id = payment['id']

        pool = Pool()
        Payment = pool.get('account.payment')

        payment_tryton = Payment()
        payment_tryton.journal = payment_journal
        payment_tryton.party = party
        payment_tryton.currency = currency
        payment_tryton.amount = amount
        payment_tryton.paypal_payment_id = payment_id
        payment_tryton.kind = 'receivable'
        if secret_identifier:
            print(secret_identifier)
            payment_tryton.secret_identifier = secret_identifier
        payment_tryton.save()

        return payment

    @classmethod
    def get_payment_status(cls, paymentID, paypal_account):
        response = paypal_account.get_paypal_access_token()
        url = ''
        if paypal_account.paypal_mode == 'sandbox':
            url = f'https://api-m.sandbox.paypal.com/v1/payments/payment/{paymentID}'
        else:
            url = f'https://api-m.paypal.com/v1/payments/payment/{paymentID}'
        headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {response}'
            }
        response = requests.get(url, headers=headers)
        return response.json()

    @classmethod
    def execute_paypal_payment(cls, paymentID, paypal_account):
        Payment = Pool().get('account.payment')
        response = paypal_account.get_paypal_access_token()
        payment_status = Payment.get_payment_status(paymentID, paypal_account)
        payer_id = payment_status['payer']['payer_info']['payer_id']
        url = ''
        if paypal_account.paypal_mode == 'sandbox':
            url = f'https://api-m.sandbox.paypal.com/v1/payments/payment/{paymentID}/execute'
        else:
            url = f'https://api-m.paypal.com/v1/payments/payment/{paymentID}/execute'
        headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {response}'
            }
        data = '{"payer_id": "' + str(payer_id) + '"}'
        response = requests.post(url, headers=headers, data=data)
        if response.status_code == 200:
            payment = Payment.search([('paypal_payment_id', '=', paymentID)], limit=1)
            if payment:
                Payment.submit(payment)
                for single_payment in payment:
                    single_payment.secret_identifier = ''
                Payment.save(payment)
            else:
                return False
        else:
            return False

class Account(ModelSQL, ModelView):
    'Paypal Account'
    __name__ = 'account.payment.paypal.account'

    paypal_email = fields.Char('Email', help='Paypal Email Account')
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

    def get_paypal_access_token(self):
        url = ''
        if self.paypal_mode == 'sandbox':
            url =  'https://api-m.sandbox.paypal.com/v1/oauth2/token'
        else:
            url =  'https://api-m.paypal.com/v1/oauth2/token'
        client_id = self.paypal_client_id
        secret = self.paypal_client_secret
        headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
        data = {
                'grant_type': 'client_credentials'
            }
        response = requests.post(url, headers=headers, data=data, auth=HTTPBasicAuth(client_id, secret))
        return response.json()['access_token']
