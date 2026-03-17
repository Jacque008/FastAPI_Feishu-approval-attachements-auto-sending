from zoneinfo import ZoneInfo
from datetime import datetime
import dropbox
from dropbox.files import WriteMode
from services.attachment import AttachmentInfo

STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")


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
    ) -> str:
        """Build Dropbox path: /{year}/{Mon}/{approval_name}/{serial_number}-{filename}"""
        dt = datetime.fromtimestamp(end_time_ms / 1000, tz=STOCKHOLM_TZ)
        folder = dt.strftime("/%Y/%b")+"_code"
        return f"{folder}/{approval_name}/{serial_number}-{filename}"

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
    ) -> list[str]:
        """Upload all attachments to Dropbox. Returns list of uploaded paths."""
        uploaded_paths = []
        for att in attachments:
            if not att.content:
                print(f"Skipping attachment {att.name}: no content")
                continue
            path = self._build_path(end_time_ms, approval_name, serial_number, att.name)
            try:
                uploaded = self.upload_file(att.content, path)
                print(f"Uploaded to Dropbox: {uploaded}")
                uploaded_paths.append(uploaded)
            except Exception as e:
                print(f"Failed to upload {att.name} to Dropbox: {e}")
        return uploaded_paths
