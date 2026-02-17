from datetime import timezone
from rest_framework import serializers
from .models import CustomUser
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth import get_user_model
import datetime

User = get_user_model()

class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ["id", "email", "username", "full_name", "role", "organization", "is_active"]



class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'username', 'first_name', 'last_name', 'is_active', 'date_joined']
        read_only_fields = ['id', 'date_joined']


class InviteSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name']

    def create(self, validated_data):
        return User.objects.create(
            **validated_data,
            is_active=False,
            is_invited=True,
            invited_at=timezone.now(),
            invite_expires_at=timezone.now() + datetime.timedelta(days=7),
        )


class AcceptInviteSerializer(serializers.Serializer):
    token            = serializers.UUIDField()
    username         = serializers.CharField(max_length=150)
    password         = serializers.CharField(validators=[validate_password])
    confirm_password = serializers.CharField()

    def validate(self, data):
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        return data

    def save(self):
        token = self.validated_data['token']
        user  = User.objects.get(invite_token=token, is_invited=True, is_active=False)
        user.username  = self.validated_data['username']
        user.is_active = True
        user.set_password(self.validated_data['password'])
        user.save()
        return user


class LoginSerializer(serializers.Serializer):
    username_or_email = serializers.CharField()
    password          = serializers.CharField()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    email_or_username = serializers.CharField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid          = serializers.CharField()
    token        = serializers.CharField()
    new_password = serializers.CharField(validators=[validate_password])


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField()
    new_password = serializers.CharField(validators=[validate_password])