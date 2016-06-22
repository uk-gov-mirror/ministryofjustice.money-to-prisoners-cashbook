from django.conf import settings
from django.conf.urls import url
from django.core.urlresolvers import reverse_lazy
from mtp_common.auth import views

urlpatterns = [
    url(
        r'^login/$', views.login, {
            'template_name': 'mtp_auth/login.html',
            'restrict_applications': (settings.API_CLIENT_ID,),
        }, name='login'
    ),
    url(
        r'^logout/$', views.logout, {
            'template_name': 'mtp_auth/login.html',
            'next_page': reverse_lazy('login'),
        }, name='logout'
    ),
    url(
        r'^password_change/$', views.password_change, {
            'template_name': 'mtp_common/auth/password_change.html',
            'cancel_url': reverse_lazy('dashboard'),
        }, name='password_change'
    ),
    url(
        r'^password_change_done/$', views.password_change_done, {
            'template_name': 'mtp_common/auth/password_change_done.html',
            'cancel_url': reverse_lazy('dashboard'),
        }, name='password_change_done'
    ),
    url(
        r'^reset-password/$', views.reset_password, {
            'template_name': 'mtp_common/auth/reset-password.html',
            'cancel_url': reverse_lazy('dashboard'),
        }, name='reset_password'
    ),
    url(
        r'^reset-password-done/$', views.reset_password_done, {
            'template_name': 'mtp_common/auth/reset-password-done.html',
            'cancel_url': reverse_lazy('dashboard'),
        }, name='reset_password_done'
    ),
]
