import collections
import datetime
from decimal import Decimal
import logging
from math import ceil, floor
import re
from urllib.parse import quote_plus, urlencode

from django import forms
from django.conf import settings
from django.core.validators import RegexValidator
from django.utils import timezone
from django.utils.encoding import force_text
from django.utils.dateformat import format as date_format
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.functional import cached_property
from django.utils.html import format_html, format_html_join
from django.utils.translation import (
    gettext, gettext_lazy as _, override as override_locale
)
from extended_choices import Choices
from form_error_reporting import GARequestErrorReportingMixin
from mtp_common.auth.api_client import get_api_session
from mtp_common.bank_accounts import (
    roll_number_required, roll_number_valid_for_account
)
from mtp_common.auth.exceptions import HttpNotFoundError, Forbidden
from requests.exceptions import RequestException

from disbursements import metrics

logger = logging.getLogger('mtp')

SENDING_METHOD = Choices(
    ('BANK_TRANSFER', 'bank_transfer', _('Bank transfer')),
    ('CHEQUE', 'cheque', _('Cheque')),
)


class ConfirmationForm(GARequestErrorReportingMixin, forms.Form):
    confirmation = forms.ChoiceField(required=True, choices=(
        ('yes', _('Yes')),
        ('no', _('No')),
    ), error_messages={
        'required': _('Please select ‘yes’ or ‘no’'),
        'cannot_proceed': _('You cannot proceed if you select ‘no’'),
    })


class DisbursementForm(GARequestErrorReportingMixin, forms.Form):
    @classmethod
    def unserialise_from_session(cls, request, valid_form_data):
        session = request.session

        def get_value(f):
            value = session[f]
            if hasattr(cls, 'unserialise_%s' % f) and value is not None:
                value = getattr(cls, 'unserialise_%s' % f)(value)
            return value

        try:
            data = {
                field: get_value(field)
                for field in cls.base_fields
            }
        except KeyError:
            data = None
        return cls(request=request, data=data)

    @classmethod
    def delete_from_session(cls, request):
        for field in cls.base_fields:
            request.session.pop(field, None)

    @classmethod
    def serialise_data(cls, session, data):
        for field in cls.base_fields:
            value = data[field]
            if hasattr(cls, 'serialise_%s' % field):
                value = getattr(cls, 'serialise_%s' % field)(value)
            session[field] = value

    def __init__(self, request=None, **kwargs):
        super().__init__(**kwargs)
        self.request = request


class PrisonerForm(DisbursementForm):
    prisoner_number = forms.CharField(
        label=_('Prisoner number'),
        help_text=_('For example, A1234BC'),
        max_length=7,
    )
    error_messages = {
        'connection': _('This service is currently unavailable'),
        'not_found': _('No prisoner matches the details you’ve supplied'),
        'wrong_prison': _('This prisoner does not appear to be in a prison that you manage'),
    }

    @classmethod
    def delete_from_session(cls, request):
        super().delete_from_session(request)
        request.session.pop('prisoner_name', None)
        request.session.pop('prisoner_dob', None)
        request.session.pop('prison', None)

    def clean_prisoner_number(self):
        prisoner_number = self.cleaned_data.get('prisoner_number')
        if prisoner_number:
            prisoner_number = prisoner_number.upper()
            session = get_api_session(self.request)
            try:
                prisoner = session.get(
                    '/prisoner_locations/{prisoner_number}/'.format(
                        prisoner_number=quote_plus(prisoner_number)
                    )
                ).json()

                self.cleaned_data['prisoner_name'] = prisoner['prisoner_name']
                self.cleaned_data['prisoner_dob'] = prisoner['prisoner_dob']
                self.cleaned_data['prison'] = prisoner['prison']
            except HttpNotFoundError:
                raise forms.ValidationError(
                    self.error_messages['not_found'], code='not_found')
            except Forbidden:
                raise forms.ValidationError(
                    self.error_messages['wrong_prison'], code='wrong_prison')
            except (RequestException, ValueError):
                logger.exception('Could not look up prisoner location')
                raise forms.ValidationError(
                    self.error_messages['connection'], code='connection')
        return prisoner_number


def serialise_amount(amount):
    return '{0:.2f}'.format(amount / 100)


def unserialise_amount(amount_text):
    amount_text = force_text(amount_text)
    return Decimal(amount_text)


class AmountField(forms.DecimalField):
    def to_python(self, value):
        if isinstance(value, str):
            value = value.lstrip('£').replace(',', '')
        return super().to_python(value)


class AmountForm(DisbursementForm):
    amount = AmountField(
        label=_('Amount to send'),
        min_value=Decimal('0.01'),
        decimal_places=2,
        error_messages={
            'invalid': _('Enter amount as a number'),
            'min_value': _('Amount should be 1p or more'),
            'max_decimal_places': _('Only use 2 decimal places'),
        }
    )
    serialise_amount = serialise_amount
    unserialise_amount = unserialise_amount
    error_messages = {
        'exceeds_funds': _(
            'There is not enough money in the prisoner’s private account. '
            'Use NOMIS to move money from other accounts into the private '
            'account, then click ‘Update balances’'),
    }

    def __init__(self, nomis_balances=None, **kwargs):
        super().__init__(**kwargs)
        self.nomis_balances = nomis_balances

    def clean_amount(self):
        amount = floor(Decimal(self.cleaned_data['amount']) * 100)
        if self.nomis_balances and amount > self.nomis_balances['cash']:
            raise forms.ValidationError(
                self.error_messages['exceeds_funds'], code='exceeds_funds')
        return amount


class SendingMethodForm(DisbursementForm):
    method = forms.ChoiceField(
        label=_('Sending method'),
        choices=SENDING_METHOD,
        widget=forms.RadioSelect(),
    )
    method_choices_help_text = [
        _('The recipient’s bank account is directly credited in 5-7 working days'),
        _('The recipient gets a cheque in the post from SSCL in 5-7 working days'),
    ]


class RecipientContactForm(DisbursementForm):
    recipient_type = forms.ChoiceField(
        required=True, choices=(
            ('person', _('Person')),
            ('company', _('Company')),
        ), error_messages={
            'required': _('Please select ‘Person’ or ‘Company’'),
        }
    )
    recipient_first_name = forms.CharField(
        label=_('Their first name'),
        required=False
    )
    recipient_last_name = forms.CharField(
        label=_('Last name'),
        required=False,
    )
    recipient_company_name = forms.CharField(
        label=_('Company name'),
        required=False
    )

    recipient_email = forms.EmailField(
        label=_('Their email address'),
        help_text=_(
            'The recipient will be notified about this request by email '
            'if given here and otherwise by letter'
        ),
        required=False,
    )

    @classmethod
    def serialise_data(cls, session, data):
        recipient_is_company = data.get('recipient_is_company')
        if recipient_is_company is True:
            data['recipient_type'] = 'company'
            data['recipient_company_name'] = data['recipient_last_name']
            data['recipient_first_name'] = ''
            data['recipient_last_name'] = ''
        elif recipient_is_company is False:
            data['recipient_type'] = 'person'
            data['recipient_company_name'] = ''
        super().serialise_data(session, data)

    def clean(self):
        cleaned_data = super().clean()
        recipient_type = cleaned_data.get('recipient_type')
        if recipient_type == 'person':
            recipient_first_name = cleaned_data.get('recipient_first_name')
            recipient_last_name = cleaned_data.get('recipient_last_name')
            if not recipient_first_name:
                self.add_error(
                    'recipient_first_name',
                    self['recipient_first_name'].field.error_messages['required']
                )
            if not recipient_last_name:
                self.add_error(
                    'recipient_last_name',
                    self['recipient_last_name'].field.error_messages['required']
                )
            cleaned_data['recipient_company_name'] = ''
        elif recipient_type == 'company':
            recipient_company_name = cleaned_data.get('recipient_company_name')
            if not recipient_company_name:
                self.add_error(
                    'recipient_company_name',
                    self['recipient_company_name'].field.error_messages['required']
                )
            cleaned_data['recipient_first_name'] = ''
            cleaned_data['recipient_last_name'] = ''
        return cleaned_data


class RecipientPostcodeForm(DisbursementForm):
    postcode = forms.CharField(label=_('Postcode'))

    def clean_postcode(self):
        postcode = self.cleaned_data.get('postcode')
        if postcode:
            postcode = postcode.upper()
            if len(postcode.replace(' ', '')) < 5:
                raise forms.ValidationError(_('Enter a full valid UK postcode'))
        return postcode


class RecipientAddressForm(RecipientPostcodeForm):
    address_line1 = forms.CharField(label=_('Their address'))
    address_line2 = forms.CharField(label=_('Address line 2'), required=False)
    city = forms.CharField(label=_('Town or city'))


validate_account_number = RegexValidator(r'^\d{8}$', message=_('The account number should be 8 digits long'))
validate_sort_code = RegexValidator(r'^\d\d-?\d\d-?\d\d$', message=_('The sort code should be 6 digits long'))


class RecipientBankAccountForm(DisbursementForm):
    account_number = forms.CharField(
        label=_('Bank account number'),
        help_text=_('For example, 12345678'),
        validators=[validate_account_number],
    )
    sort_code = forms.CharField(
        label=_('Sort code'),
        help_text=_('For example, 10-20-30'),
        validators=[validate_sort_code],
    )
    roll_number = forms.CharField(
        required=False,
        label=_('Building society roll number (if applicable)'),
        help_text=_(
            'Up to 18 characters, which can include A to Z, 0 to 9, space, / and -'
        ),
    )
    error_messages = {
        'roll_number_required': (
            gettext('This is a building society account.') + ' ' +
            gettext(
                'Contact the prisoner to get the building society '
                'roll number or send a cheque.'
            )
        ),
        'roll_number_invalid': (
            gettext(
                'This roll number is not valid for this '
                'type of account.'
            ) + ' ' +
            gettext(
                'Contact the prisoner to get the correct building '
                'society roll number or send a cheque.'
            )
        ),
        'roll_number_unneeded': _(
            'You don’t need a roll number because this is not a '
            'building society account.'
        )
    }

    def clean(self):
        cleaned_data = super().clean()
        sort_code = cleaned_data.get('sort_code')
        account_number = cleaned_data.get('account_number')
        if sort_code and account_number:
            if roll_number_required(sort_code, account_number):
                roll_number = cleaned_data.get('roll_number')
                if not roll_number:
                    self.add_error(
                        'roll_number',
                        self.error_messages['roll_number_required']
                    )
                elif not roll_number_valid_for_account(
                    sort_code, account_number, roll_number
                ):
                    self.add_error(
                        'roll_number',
                        self.error_messages['roll_number_invalid']
                    )
            elif cleaned_data.get('roll_number'):
                self.add_error(
                    'roll_number', self.error_messages['roll_number_unneeded']
                )
        return cleaned_data

    def clean_sort_code(self):
        sort_code = self.cleaned_data.get('sort_code')
        if sort_code:
            sort_code = sort_code.replace('-', '')
        return sort_code


class RemittanceDescriptionForm(DisbursementForm):
    remittance = forms.ChoiceField(
        label=_('Would you like to add to the description?'),
        required=True, choices=(
            ('yes', _('Yes')),
            ('no', _('No')),
        ), error_messages={
            'required': _('Please select ‘yes’ or ‘no’'),
        })
    remittance_description = forms.CharField(
        label=_('Payment description'),
        max_length=60, required=False,
    )

    @classmethod
    def unserialise_from_session(cls, request, valid_form_data):
        form = super().unserialise_from_session(request, valid_form_data)
        form.set_prisoner_name(valid_form_data.get(PrisonerForm.__name__, {}).get('prisoner_name'))
        return form

    @classmethod
    def serialise_data(cls, session, data):
        if 'remittance' not in data:
            data['remittance'] = 'yes'
        super().serialise_data(session, data)

    def set_prisoner_name(self, prisoner_name):
        if not prisoner_name:
            return
        self['remittance_description'].field.initial = 'Payment from %s' % prisoner_name

    def clean_remittance_description(self):
        remittance_description = self.cleaned_data.get('remittance_description')
        if remittance_description:
            remittance_description = re.sub(r'\s+', ' ', remittance_description)
        return remittance_description

    def clean(self):
        super().clean()
        remittance = self.cleaned_data.get('remittance')
        if remittance == 'no':
            self.cleaned_data['remittance_description'] = self['remittance_description'].initial
        elif (
            remittance == 'yes' and
            self.cleaned_data.get('remittance_description') == self['remittance_description'].initial
        ):
            self.cleaned_data['remittance'] = 'no'
        return self.cleaned_data


class RejectDisbursementForm(GARequestErrorReportingMixin, forms.Form):
    reason = forms.CharField(label=_('Why do you want to cancel?'), required=False)

    def reject(self, request, disbursement_id):
        api_session = get_api_session(request)
        api_session.post(
            'disbursements/actions/reject/',
            json={'disbursement_ids': [disbursement_id]}
        )
        metrics.rejected_counter.inc()
        reason = self.cleaned_data['reason']
        if reason:
            reasons = [{
                'disbursement': disbursement_id,
                'comment': reason,
                'category': 'reject',
            }]
            api_session.post('/disbursements/comments/', json=reasons)


def insert_blank_option(choices, title):
    new_choices = [('', title)]
    new_choices.extend(choices)
    return new_choices


def validate_range_field(field_name, bound_ordering_msg):
    def inner(cls):
        lower = field_name + '__gte'
        upper = field_name + '__lt'

        base_clean = cls.clean

        def clean(self):
            base_clean(self)
            lower_value = self.cleaned_data.get(lower)
            upper_value = self.cleaned_data.get(upper)
            if lower_value is not None and upper_value is not None and lower_value > upper_value:
                msg = bound_ordering_msg or _('Must be greater than the lower bound')
                self.add_error(upper, forms.ValidationError(msg, code='bound_ordering'))
            return self.cleaned_data

        cls.clean = clean
        return cls

    return inner


class BaseSearchForm(GARequestErrorReportingMixin, forms.Form):
    """
    Base form for searching, always uses initial values as defaults
    """
    page = forms.IntegerField(min_value=1, initial=1)
    page_size = 20

    date_fields = ()
    exclusive_date_params = ()

    filtered_description_template = NotImplemented
    unfiltered_description_template = NotImplemented
    description_templates = ()
    description_capitalisation = {}

    def __init__(self, request, **kwargs):
        super().__init__(**kwargs)
        if self.is_bound:
            data = {
                name: field.initial
                for name, field in self.fields.items()
                if field.initial is not None
            }
            data.update(self.initial)
            data.update(self.data)
            self.data = data
        self.request = request
        self.total_count = None
        self.page_count = 0

    @cached_property
    def session(self):
        return get_api_session(self.request)

    def get_query_data(self, allow_parameter_manipulation=True):
        """
        Serialises the form into a dictionary stripping empty and pagination fields.
        NB: Forms can sometimes manipulate parameters so this is not always reversible.
        :param allow_parameter_manipulation: turn off to make serialisation reversible
        :return: collections.OrderedDict
        """
        data = collections.OrderedDict()
        for field in self:
            if field.name == 'page':
                continue
            value = self.cleaned_data.get(field.name)
            if value in [None, '', []]:
                continue
            data[field.name] = value
        return data

    def get_api_request_params(self):
        filters = self.get_query_data()
        for param in filters:
            if param in self.exclusive_date_params:
                filters[param] += datetime.timedelta(days=1)
        return filters

    def parse_date_fields(self, object_list, date_fields):
        """
        MTP API responds with string date/time fields, this filter converts them to python objects
        """
        parsers = (parse_datetime, parse_date)

        def convert(credit):
            for field in date_fields:
                value = credit.get(field)
                if not value or not isinstance(value, str):
                    continue
                for parser in parsers:
                    try:
                        value = parser(value)
                        if isinstance(value, datetime.datetime):
                            value = timezone.localtime(value)
                        credit[field] = value
                        break
                    except (ValueError, TypeError):
                        pass
            return credit

        return list(map(convert, object_list)) if object_list else object_list

    def get_object_list_endpoint_path(self):
        raise NotImplementedError

    def get_object_list(self):
        """
        Gets the object list
        :return: list
        """
        page = self.cleaned_data.get('page')
        if not page:
            return []
        offset = (page - 1) * self.page_size
        filters = self.get_api_request_params()
        try:
            data = self.session.get(
                self.get_object_list_endpoint_path(),
                params=dict(offset=offset, limit=self.page_size, **filters)
            ).json()
        except RequestException:
            logger.exception('Error loading disbursements')
            self.add_error(None, _('This service is currently unavailable'))
            return []
        count = data.get('count', 0)
        self.total_count = count
        self.page_count = int(ceil(count / self.page_size))
        return self.parse_date_fields(data.get('results', []), self.date_fields)

    @cached_property
    def query_string(self):
        return urlencode(self.get_query_data(allow_parameter_manipulation=False), doseq=True)

    def _get_value_text(self, bf):
        f = bf.field
        v = self.cleaned_data.get(bf.name) or f.initial
        if isinstance(f, forms.ChoiceField):
            v = dict(f.choices).get(v)
            if not v:
                return None
            v = str(v)
            capitalisation = self.description_capitalisation.get(bf.name)
            if capitalisation == 'preserve':
                return v
            if capitalisation == 'lowerfirst':
                return v[0].lower() + v[1:]
            return v.lower()
        if isinstance(f, forms.DateField) and v is not None:
            return date_format(v, 'j M Y')
        if isinstance(f, forms.IntegerField) and v is not None:
            return str(v)
        return v or None

    @cached_property
    def search_description(self):
        with override_locale(settings.LANGUAGE_CODE):
            description_kwargs = {
                'ordering_description': self._get_value_text(self['ordering']),
            }

            filters = {}
            for bound_field in self:
                if bound_field.name in ('page', 'ordering'):
                    continue
                description_override = 'describe_field_%s' % bound_field.name
                if hasattr(self, description_override):
                    value = getattr(self, description_override)()
                else:
                    value = self._get_value_text(bound_field)
                if value is None:
                    continue
                filters[bound_field.name] = format_html('<strong>{}</strong>', value)
            if any(field in filters for field in ('prisoner_number', 'prisoner_name')):
                filters['prison_preposition'] = 'in'
            else:
                filters['prison_preposition'] = 'from'

            descriptions = []
            for template_group in self.description_templates:
                for filter_template in template_group:
                    try:
                        descriptions.append(format_html(filter_template, **filters))
                        break
                    except KeyError:
                        continue

            if descriptions:
                description_template = self.filtered_description_template
                if len(descriptions) > 1:
                    all_but_last = format_html_join(', ', '{}', ((d,) for d in descriptions[:-1]))
                    filter_description = format_html('{0} and {1}', all_but_last, descriptions[-1])
                else:
                    filter_description = descriptions[0]
                description_kwargs['filter_description'] = filter_description
                has_filters = True
            else:
                description_template = self.unfiltered_description_template
                has_filters = False

            return {
                'has_filters': has_filters,
                'description': format_html(description_template, **description_kwargs),
            }


validate_prisoner_number = RegexValidator(
    r'^[a-z]\d{4}[a-z]{2}$', flags=re.IGNORECASE,
    message=_('The prisoner number should be in the form A1234AB')
)


@validate_range_field('date', _('Must be after the ‘from’ date'))
class SearchForm(BaseSearchForm):
    # fields
    ordering = forms.ChoiceField(label=_('Order by'), required=False,
                                 initial='-created',
                                 choices=[
                                     ('created', _('Entry date (oldest to newest)')),
                                     ('-created', _('Entry date (newest to oldest)')),
                                     ('amount', _('Amount (ascending)')),
                                     ('-amount', _('Amount (descending)')),
                                     ('recipient_name', _('Recipient name (A to Z)')),
                                     ('-recipient_name', _('Recipient name (Z to A)')),
                                     ('prisoner_name', _('Prisoner name (A to Z)')),
                                     ('-prisoner_name', _('Prisoner name (Z to A)')),
                                     ('prisoner_number', _('Prisoner number (A to Z)')),
                                     ('-prisoner_number', _('Prisoner number (Z to A)')),
                                     ('resolution', _('Status (A to Z)')),
                                     ('-resolution', _('Status (Z to A)')),
                                     ('method', _('Sending method (A to Z)')),
                                     ('-method', _('Sending method (Z to A)')),
                                 ])

    date_filter = forms.ChoiceField(label=_('Date filter'), required=False,
                                    initial='confirmed',
                                    choices=[('created', _('Date entered')),
                                             ('confirmed', _('Date confirmed'))])
    date__gte = forms.DateField(label=_('From'), help_text=_('For example, 17/01/2018'),
                                required=False)
    date__lt = forms.DateField(label=_('To'), help_text=_('For example, 18/01/2018'),
                               required=False)

    prisoner_name = forms.CharField(label=_('Prisoner name'), required=False)
    prisoner_number = forms.CharField(label=_('Prisoner number'), required=False,
                                      validators=[validate_prisoner_number])
    recipient_name = forms.CharField(label=_('Recipient name'), required=False)
    nomis_transaction_id = forms.CharField(label=_('NOMIS reference'), required=False)
    invoice_number = forms.CharField(label=_('Invoice number'), required=False)

    resolutions = [
        # NB: not all resolutions can be filtered
        ('confirmed', _('Confirmed')),
        ('sent', _('Sent')),
    ]
    resolution = forms.ChoiceField(label=_('Status'), required=False,
                                   choices=insert_blank_option(resolutions, _('Any status')))
    method = forms.ChoiceField(label=_('Sending method'), required=False,
                               choices=insert_blank_option(SENDING_METHOD, _('Any method')))

    # form config
    page_size = 10
    date_fields = ('created',)
    exclusive_date_params = ('date__lt',)

    # search descriptions
    filtered_description_template = 'Showing disbursements {filter_description}, ' \
                                    'ordered by {ordering_description}.'
    unfiltered_description_template = 'Showing all disbursements ordered by {ordering_description}.'
    description_templates = (
        ('that are {resolution}',),
        ('by {method}',),
        ('with {date_filter} between {date__gte} and {date__lt}',
         'with {date_filter} since {date__gte}',
         'with {date_filter} before {date__lt}',),
        ('confirmed between {confirmed__gte} and {confirmed__lt}',
         'confirmed since {confirmed__gte}',
         'confirmed before {confirmed__lt}',),
        ('from prisoner {prisoner_name} ({prisoner_number})',
         'from prisoners named ‘{prisoner_name}’',
         'from prisoner {prisoner_number}',),
        ('to ‘{recipient_name}’',),
        ('with NOMIS reference {nomis_transaction_id}',),
    )

    def get_api_request_params(self):
        filters = super().get_api_request_params()
        date_filter = filters.pop('date_filter', 'confirmed')
        date__gte = filters.pop('date__gte', None)
        date__lt = filters.pop('date__lt', None)
        if date__gte or date__lt:
            filters['log__action'] = date_filter
            if date__gte:
                filters['logged_at__gte'] = date__gte
            if date__lt:
                filters['logged_at__lt'] = date__lt
        filters['resolution'] = filters.get('resolution') or ['confirmed', 'sent']
        return filters

    def get_object_list_endpoint_path(self):
        return '/disbursements/'

    def get_object_list(self):
        object_list = super().get_object_list()

        def format_staff_name(log_item):
            username = log_item['user']['username'] or _('Unknown user')
            log_item['staff_name'] = ' '.join(filter(None, (log_item['user']['first_name'],
                                                            log_item['user']['last_name']))) or username
            return log_item

        for disbursement in object_list:
            disbursement['log_set'] = sorted(
                map(format_staff_name, self.parse_date_fields(disbursement.get('log_set', []), ('created',))),
                key=lambda log_item: log_item['created'], reverse=True
            )
        return object_list
