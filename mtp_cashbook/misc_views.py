from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.generic import View, TemplateView, FormView
from mtp_common.auth import api_client

from mtp_cashbook import READ_ML_BRIEFING_FLAG
from mtp_cashbook.utils import save_user_flags


class BaseView(View):
    """
    Base class for all cashbook and disbursement views:
    - forces login
    - ensures "read ML briefing" flag is set or redirects to ML briefing confirmation screen
    """

    @method_decorator(login_required)
    def dispatch(self, request, **kwargs):
        if not request.read_ml_briefing and getattr(self, 'requires_reading_ml_briefing', True):
            return redirect('ml-briefing-confirmation')
        return super().dispatch(request, **kwargs)


class LandingView(BaseView, TemplateView):
    template_name = 'landing.html'

    def get_context_data(self, **kwargs):
        if self.request.user.has_perm('auth.change_user'):
            response = api_client.get_api_session(self.request).get('requests/', params={'page_size': 1})
            kwargs['user_request_count'] = response.json().get('count')

        cards = [
            {
                'heading': _('Digital cashbook'),
                'link': reverse_lazy('new-credits'),
                'description': _('Credit money into a prisoner’s account'),
            },
            {
                'heading': _('Digital disbursements'),
                'link': reverse_lazy('disbursements:start'),
                'description': _('Send money out of a prisoner’s account by bank transfer or cheque'),
            },
        ]

        kwargs.update(
            start_page_url=settings.START_PAGE_URL,
            cards=cards,
        )
        return super().get_context_data(**kwargs)


class MLBriefingConfirmationForm(forms.Form):
    read_briefing = forms.ChoiceField(
        label=_('Have you read the money laundering briefing?'),
        required=True,
        choices=(
            ('yes', _('Yes')),
            ('no', _('No')),
        ), error_messages={
            'required': _('Please select ‘yes’ or ‘no’'),
        },
    )

    def clean_read_briefing(self):
        read_briefing = self.cleaned_data.get('read_briefing')
        if read_briefing:
            read_briefing = read_briefing == 'yes'
        return read_briefing


class MLBriefingConfirmationView(BaseView, FormView):
    title = _('Have you read the money laundering briefing?')
    form_class = MLBriefingConfirmationForm
    template_name = 'ml-briefing-confirmation.html'
    success_url = reverse_lazy('home')

    requires_reading_ml_briefing = False

    def dispatch(self, request, **kwargs):
        if request.read_ml_briefing:
            return redirect(self.get_success_url())
        return super().dispatch(request, **kwargs)

    def form_valid(self, form):
        read_briefing = form.cleaned_data['read_briefing']
        if read_briefing:
            save_user_flags(self.request, READ_ML_BRIEFING_FLAG)
            messages.success(self.request, _('Thank you, please carry on with your work.'))
            return super().form_valid(form)
        else:
            return redirect('ml-briefing')


class MLBriefingView(BaseView, TemplateView):
    title = _('You need to read the money laundering briefing')
    template_name = 'ml-briefing.html'

    requires_reading_ml_briefing = False

    def dispatch(self, request, **kwargs):
        if request.read_ml_briefing:
            return redirect(MLBriefingConfirmationView.success_url)
        return super().dispatch(request, **kwargs)


class PolicyChangeInfo(BaseView, TemplateView):
    if settings.BANK_TRANSFERS_ENABLED:
        title = _('How Nov 2nd policy changes will affect you')
    else:
        title = _('Policy changes made on Nov 2nd 2020 that may affect your work')

    def get_template_names(self):
        if settings.BANK_TRANSFERS_ENABLED:
            return ['policy-change-warning.html']
        else:
            return ['policy-change-info.html']


class FAQView(TemplateView):
    title = _('What do you need help with?')
    template_name = 'faq.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context['breadcrumbs_back'] = reverse_lazy('home')
        context['reset_password_url'] = reverse_lazy('reset_password')
        context['sign_up_url'] = reverse_lazy('sign-up')

        return context
