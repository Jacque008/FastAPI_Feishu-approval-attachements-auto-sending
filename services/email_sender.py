import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.header import Header
from email import encoders

from .attachment import AttachmentInfo


class EmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_email: str,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_email = from_email

    async def send_with_attachments(
        self,
        to_email: str,
        subject: str,
        body: str,
        attachments: list[AttachmentInfo],
    ) -> None:
        """Send email with attachments via SMTP SSL."""
        msg = MIMEMultipart()
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg["Subject"] = Header(subject, "utf-8")

        # Add body
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Add attachments
        for attachment in attachments:
            if attachment.content is None:
                continue

            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.content)
            encoders.encode_base64(part)

            # Encode filename for Chinese characters
            encoded_name = Header(attachment.name, "utf-8").encode()
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=encoded_name,
            )
            msg.attach(part)

        # Send email - use STARTTLS for port 587, SSL for port 465
        if self.port == 587:
            # Microsoft 365 / Office 365 uses STARTTLS
            await aiosmtplib.send(
                msg,
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                start_tls=True,
            )
        else:
            # Port 465 uses SSL
            await aiosmtplib.send(
                msg,
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                use_tls=True,
            )
