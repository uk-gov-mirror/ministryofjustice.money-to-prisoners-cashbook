from django.conf.urls import url
from django.core.urlresolvers import reverse_lazy

from moj_auth import views

urlpatterns = [
    url(r'^login/$', views.login, {
        'template_name': 'mtp_auth/login.html',
        }, name='login'),
    url(
        r'^logout/$', views.logout, {
            'template_name': 'mtp_auth/login.html',
            'next_page': reverse_lazy('login'),
        }, name='logout'
    ),
    url(
        r'^password_change/$', views.password_change, {
            'template_name': 'auth/password_change.html'
        }, name='password_change'
    ),
    url(
        r'^password_change_done/$', views.password_change_done, {
            'template_name': 'auth/password_change_done.html'
        }, name='password_change_done'
    ),
]
