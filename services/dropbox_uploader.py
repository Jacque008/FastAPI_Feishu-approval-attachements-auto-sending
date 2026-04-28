from zoneinfo import ZoneInfo
from datetime import datetime
import re
import dropbox
from dropbox.files import WriteMode
from services.attachment import AttachmentInfo

STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")

_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|\r\n]')


def _sanitize(s: str) -> str:
    """Remove filename-unsafe characters and strip whitespace."""
    return _UNSAFE_CHARS.sub("", s).strip()


class DropboxUploader:
    def __init__(self, refresh_token: str, app_key: str, app_secret: str):
        self.dbx = dropbox.Dropbox(
            oauth2_refresh_token=refresh_token,
            app_key=app_key,
            app_secret=app_secret,
        )

    def _build_path(
        self,
        end_time_ms: int,
        approval_name: str,
        serial_number: str,
        filename: str,
        amount: str = "",
        invoice_date: str = "",
        project: str = "",
        index: int = 0,
        is_payment_voucher: bool = False,
    ) -> str:
        """Build Dropbox path: /{year}_code/{Mon}/{approval_name}/{amount}-{date}-{project}-{serial_number}.{ext}

        When an approval has multiple attachments with the same extension, pass
        index=1,2,3... to append a suffix and avoid name collisions.
        """
        dt = datetime.fromtimestamp(end_time_ms / 1000, tz=STOCKHOLM_TZ)
        folder = dt.strftime("/%Y_code/%b")
        ext = ("." + filename.rsplit(".", 1)[1]) if "." in filename else ""
        suffix = f"-{index}" if index > 0 else ""
        serial_part = (
            f"支付凭证-{_sanitize(serial_number)}"
            if is_payment_voucher
            else _sanitize(serial_number)
        )
        new_name = (
            f"{_sanitize(amount)}-{_sanitize(invoice_date)}"
            f"-{_sanitize(project)}-{serial_part}{suffix}{ext}"
        )
        return f"{folder}/{approval_name}/{new_name}"

    def upload_file(self, content: bytes, dropbox_path: str) -> str:
        """Upload file to Dropbox.
        - Same name, same size → overwrite (duplicate)
        - Same name, different size → auto-rename with (1), (2)...
        """
        try:
            existing = self.dbx.files_get_metadata(dropbox_path)
            if existing.size == len(content):
                # Same file, overwrite silently
                result = self.dbx.files_upload(content, dropbox_path, mode=WriteMode("overwrite"))
            else:
                # Different content, keep both with versioning
                result = self.dbx.files_upload(content, dropbox_path, mode=WriteMode("add"))
        except dropbox.exceptions.ApiError:
            # File doesn't exist yet, normal upload
            result = self.dbx.files_upload(content, dropbox_path, mode=WriteMode("add"))
        return result.path_display

    def upload_attachments(
        self,
        attachments: list[AttachmentInfo],
        end_time_ms: int,
        serial_number: str,
        approval_name: str,
        amount: str = "",
        invoice_date: str = "",
        project: str = "",
        payment_deadline: str = "",
    ) -> list[str]:
        """Upload all attachments to Dropbox. Returns list of uploaded paths.

        When multiple attachments share the same extension, an index suffix
        (-1, -2, ...) is added so they don't overwrite each other.

        Payment vouchers use payment_deadline as the date if provided,
        otherwise fall back to invoice_date.
        """
        from collections import Counter

        # Count per (is_payment_voucher, ext) so vouchers and receipts
        # each get their own index series and don't collide with each other.
        type_ext_counts: Counter = Counter(
            (att.is_payment_voucher, ("." + att.name.rsplit(".", 1)[1].lower()) if "." in att.name else "")
            for att in attachments
            if att.content
        )
        type_ext_idx: dict[tuple, int] = {}

        uploaded_paths = []
        for att in attachments:
            if not att.content:
                print(f"Skipping attachment {att.name}: no content")
                continue
            ext_key = ("." + att.name.rsplit(".", 1)[1].lower()) if "." in att.name else ""
            type_key = (att.is_payment_voucher, ext_key)
            index = 0
            if type_ext_counts[type_key] > 1:
                type_ext_idx[type_key] = type_ext_idx.get(type_key, 0) + 1
                index = type_ext_idx[type_key]
            # Vouchers use payment_deadline as date (if available), others use invoice_date
            date = payment_deadline if (att.is_payment_voucher and payment_deadline) else invoice_date
            path = self._build_path(
                end_time_ms, approval_name, serial_number, att.name,
                amount, date, project, index=index,
                is_payment_voucher=att.is_payment_voucher,
            )
            try:
                uploaded = self.upload_file(att.content, path)
                print(f"Uploaded to Dropbox: {uploaded}")
                uploaded_paths.append(uploaded)
            except Exception as e:
                print(f"Failed to upload {att.name} to Dropbox: {e}")
        return uploaded_paths
