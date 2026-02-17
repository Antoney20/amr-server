from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenBlacklistView

from .views import (
    InviteUserView, VerifyInviteView, AcceptInviteView,
    LoginView, LogoutView, user_me,
    PasswordResetRequestView, PasswordResetConfirmView, ChangePasswordView,
)


urlpatterns = [
    path('invite/',                InviteUserView.as_view(),  name='invite-user'),
    path('invite/verify/<uuid:token>/', VerifyInviteView.as_view(), name='verify-invite'),
    path('invite/accept/',         AcceptInviteView.as_view(), name='accept-invite'),

    path('login/',                 LoginView.as_view(),        name='login'),
    path('logout/',                LogoutView.as_view(),       name='logout'),
    path('token/refresh/',         TokenRefreshView.as_view(), name='token-refresh'),
    path('me/',                    user_me,                    name='user-me'),

    path('password/reset/',        PasswordResetRequestView.as_view(),  name='password-reset-request'),
    path('password/reset/confirm/',PasswordResetConfirmView.as_view(),  name='password-reset-confirm'),
    path('password/change/',       ChangePasswordView.as_view(),        name='password-change'),
]