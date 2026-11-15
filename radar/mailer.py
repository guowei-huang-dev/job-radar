"""邮件发送。凭据全部走环境变量，不进代码库。"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)


class MailConfigError(RuntimeError):
    pass


def send(subject: str, html: str, text: str = "") -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    sender = os.getenv("MAIL_FROM") or user
    to = os.getenv("MAIL_TO")

    missing = [k for k, v in {
        "SMTP_HOST": host, "SMTP_USER": user, "SMTP_PASS": password, "MAIL_TO": to
    }.items() if not v]
    if missing:
        raise MailConfigError(f"缺少环境变量：{', '.join(missing)}")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(text or "这封邮件需要 HTML 客户端查看。")
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    else:                                  # 587 / STARTTLS
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)
    log.info("日报已发送至 %s", to)
