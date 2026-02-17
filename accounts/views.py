import logging
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenBlacklistView
from rest_framework import generics, permissions, status, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken



from django.contrib.auth import get_user_model,login
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.utils.encoding import force_str
from django.conf import settings
from django.utils import timezone
from django.db.models import Q

from .models import CustomUser
from .serializers import (
    AcceptInviteSerializer,
    ChangePasswordSerializer,
    InviteSerializer,
    LoginSerializer,
    LogoutSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    UserListSerializer,
    UserSerializer,
)


User = get_user_model()
logger = logging.getLogger(__name__)


from core.emails.invite import send_invite_email, send_invite_success_email, send_password_reset_email, send_password_change_confirmation


class UserListCreateView(generics.ListCreateAPIView):
    """List users + invite new user (admin only)"""
    queryset = User.objects.filter(is_active=True).order_by("-date_joined")
    serializer_class = UserListSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return InviteSerializer
        return UserListSerializer






class InviteUserView(generics.CreateAPIView):
    """POST /api/auth/invite/  — admin creates & sends invite"""
    serializer_class   = InviteSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        if not request.user.is_superuser and not request.user.is_admin:
            return Response({"success": False, "message": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        user.invited_by = request.user
        user.save(update_fields=['invited_by'])

        invite_link = f"{settings.FRONTEND_URL}/auth/accept-invite/{user.invite_token}/"
        try:
            send_invite_email(invite_user=user, invite_link=invite_link, invited_by=request.user)
        except Exception as exc:
            logger.exception("Invite email failed: %s", exc)

        return Response({
            "success":    True,
            "message":    "Invitation sent.",
            "data": {
                "user_id":    user.id,
                "email":      user.email,
                "invite_link": invite_link,
                "expires_at": user.invite_expires_at.isoformat() if user.invite_expires_at else None,
            }
        }, status=status.HTTP_201_CREATED)


class VerifyInviteView(APIView):
    """GET /api/auth/invite/verify/<token>/  — check token validity"""
    permission_classes = [permissions.AllowAny]

    def get(self, request, token):
        try:
            user = User.objects.get(invite_token=token, is_invited=True, is_active=False)
        except User.DoesNotExist:
            return Response({"is_valid": False, "message": "Invalid or already-used invitation."}, status=status.HTTP_404_NOT_FOUND)

        if user.invite_expires_at and user.invite_expires_at < timezone.now():
            return Response({"is_valid": False, "message": "Invitation has expired."}, status=status.HTTP_400_BAD_REQUEST)

        invited_by = ""
        if user.invited_by:
            invited_by = f"{user.invited_by.first_name} {user.invited_by.last_name}".strip() or user.invited_by.email

        return Response({
            "is_valid":   True,
            "email":      user.email,
            "first_name": user.first_name,
            "last_name":  user.last_name,
            "invited_by": invited_by,
        }, status=status.HTTP_200_OK)


class AcceptInviteView(generics.GenericAPIView):
    """POST /api/auth/invite/accept/  — set username & password, activate account"""
    serializer_class   = AcceptInviteSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            user = serializer.save()
            try:
                send_invite_success_email(user)
            except Exception as exc:
                logger.exception("Welcome email failed: %s", exc)
            return Response({
                "success":  True,
                "message":  "Account activated. You can now log in.",
                "email":    user.email,
                "username": user.username,
            }, status=status.HTTP_200_OK)
        except serializers.ValidationError as exc:
            return Response({"success": False, "errors": exc.detail}, status=status.HTTP_400_BAD_REQUEST)
        except User.DoesNotExist:
            return Response({"success": False, "message": "Invalid or expired invitation."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:
            logger.exception("AcceptInvite error: %s", exc)
            return Response({"success": False, "message": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



class LoginView(APIView):
    """POST /api/auth/login/"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cred = serializer.validated_data['username_or_email']
        pwd  = serializer.validated_data['password']

        user = User.objects.filter(
            Q(username__iexact=cred) | Q(email__iexact=cred)
        ).first()

        if not user:
            return Response({"success": False, "message": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)
        if not user.is_active:
            return Response({"success": False, "message": "Account inactive."}, status=status.HTTP_403_FORBIDDEN)
        if not user.check_password(pwd):
            return Response({"success": False, "message": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)

        refresh = RefreshToken.for_user(user)
        login(request, user)
        logger.info("Login success: %s", user.email)

        return Response({
            "success": True,
            "tokens": {
                "access":  str(refresh.access_token),
                "refresh": str(refresh),
            },
            "user": UserSerializer(user).data,
        }, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """POST /api/auth/logout/"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            serializer = LogoutSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            RefreshToken(serializer.validated_data['refresh']).blacklist()
            logger.info("Logout: %s", request.user.email)
            return Response({"success": True, "message": "Logged out."}, status=status.HTTP_200_OK)
        except Exception as exc:
            logger.exception("Logout error: %s", exc)
            return Response({"success": False, "message": "Logout failed."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def user_me(request):
    """GET /api/auth/me/"""
    return Response({"success": True, "user": UserSerializer(request.user).data})


class PasswordResetRequestView(generics.GenericAPIView):
    """POST /api/auth/password/reset/  — send reset link"""
    permission_classes = [permissions.AllowAny]
    serializer_class   = PasswordResetRequestSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        val = serializer.validated_data['email_or_username'].strip()
        try:
            user = User.objects.get(email=val) if '@' in val else User.objects.get(username=val)
        except User.DoesNotExist:
            # Silent — don't expose user existence
            return Response({"success": True, "message": "If the account exists, a reset link has been sent."}, status=status.HTTP_200_OK)

        token_gen  = PasswordResetTokenGenerator()
        token      = token_gen.make_token(user)
        uid        = urlsafe_base64_encode(force_bytes(user.pk))
        reset_link = f"{settings.FRONTEND_URL}/auth/reset-password?uid={uid}&token={token}"

        try:
            send_password_reset_email(user, reset_link)
        except Exception as exc:
            logger.exception("Password reset email failed: %s", exc)
            return Response({"success": False, "message": "Could not send reset email."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"success": True, "message": "If the account exists, a reset link has been sent."}, status=status.HTTP_200_OK)


class PasswordResetConfirmView(generics.GenericAPIView):
    """POST /api/auth/password/reset/confirm/"""
    permission_classes = [permissions.AllowAny]
    serializer_class   = PasswordResetConfirmSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uid   = serializer.validated_data['uid']
        token = serializer.validated_data['token']
        pwd   = serializer.validated_data['new_password']

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user    = User.objects.get(pk=user_id)
        except Exception:
            return Response({"success": False, "message": "Invalid reset link."}, status=status.HTTP_400_BAD_REQUEST)

        if not PasswordResetTokenGenerator().check_token(user, token):
            return Response({"success": False, "message": "Link is invalid or has expired."}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(pwd)
        user.save()
        send_password_change_confirmation(user)
        return Response({"success": True, "message": "Password reset. You can now log in."}, status=status.HTTP_200_OK)


class ChangePasswordView(generics.GenericAPIView):
    """POST /api/auth/password/change/"""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = ChangePasswordSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.check_password(serializer.validated_data['old_password']):
            return Response({"success": False, "message": "Current password is incorrect."}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(serializer.validated_data['new_password'])
        user.save()
        send_password_change_confirmation(user)
        return Response({"success": True, "message": "Password changed successfully."}, status=status.HTTP_200_OK)
