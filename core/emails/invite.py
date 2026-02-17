import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from smtplib import SMTPException

from accounts.models import EmailLog

logger = logging.getLogger(__name__)

_FROM  = lambda: getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@amr.go.ke')
_REPLY = lambda: getattr(settings, 'REPLY_TO_EMAIL', _FROM())
_SUPP  = lambda: getattr(settings, 'SUPPORT_EMAIL',  'support@amr.go.ke')


def _send(subject, template, context, recipient, category):
    """Internal helper — render, log, send."""
    html = render_to_string(template, context)
    log  = EmailLog.objects.create(
        subject=subject, message=html, sender=_FROM(),
        recipient=recipient, category=category, status='initial',
    )
    try:
        log.mark_sending()
        msg = EmailMultiAlternatives(subject=subject, body='', from_email=_FROM(), to=[recipient])
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=False)
        log.mark_sent()
        logger.info("[%s] email sent → %s", category, recipient)
        return True
    except (SMTPException, Exception) as exc:
        log.mark_failed(exc)
        logger.error("[%s] email failed → %s | %s", category, recipient, exc, exc_info=True)
        return False


def send_invite_email(invite_user, invite_link, invited_by=None):
    context = {
        'user_name':  invite_user.first_name or invite_user.email,
        'invite_link': invite_link,
        'invited_by': invited_by.get_full_name() if invited_by else None,
        'expires_at': invite_user.invite_expires_at,
        'support_email': _SUPP(),
        'current_year': timezone.now().year,
    }
    return _send(
        subject='National AMR Programme — You have been invited',
        template='emails/invite.html',
        context=context,
        recipient=invite_user.email,
        category='invite',
    )


def send_invite_success_email(user):
    context = {
        'user_name':    user.first_name or user.username,
        'support_email': _SUPP(),
        'current_year': timezone.now().year,
    }
    return _send(
        subject='Welcome to the National AMR Programme',
        template='emails/invite_success.html',
        context=context,
        recipient=user.email,
        category='invite_success',
    )
    
    

def send_password_reset_email(user, reset_link):
    context = {
        'user_name':    user.first_name or user.username,
        'reset_link':   reset_link,
        'support_email': _SUPP(),
        'current_year': timezone.now().year,
    }
    return _send(
        subject='National AMR Programme — Password Reset Request',
        template='emails/password_reset.html',
        context=context,
        recipient=user.email,
        category='password_reset',
    )


def send_password_change_confirmation(user):
    context = {
        'user_name':    user.first_name or user.username,
        'support_email': _SUPP(),
        'current_year': timezone.now().year,
    }
    return _send(
        subject='National AMR Programme — Your Password Has Been Changed',
        template='emails/password_changed.html',
        context=context,
        recipient=user.email,
        category='password_changed',
    )