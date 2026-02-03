import json
from dataclasses import dataclass
from typing import Optional

from .feishu_client import FeishuClient


@dataclass
class AttachmentInfo:
    file_token: str
    name: str
    mime_type: str = ""
    content: Optional[bytes] = None
    download_url: str = ""


class AttachmentService:
    def __init__(self, feishu_client: FeishuClient):
        self.client = feishu_client

    def extract_attachments_from_form(self, form_json: str) -> list[AttachmentInfo]:
        """Extract attachment info from approval form JSON.

        The form is a JSON array of form controls. Attachment controls have
        type "attachmentV2" or "attachment" and contain file info in their value.

        This also handles nested attachments inside fieldList (费用明细) controls.
        """
        attachments = []
        try:
            form_data = json.loads(form_json)
        except json.JSONDecodeError:
            return attachments

        self._extract_attachments_recursive(form_data, attachments)
        return attachments

    def _extract_attachments_recursive(self, controls: list, attachments: list[AttachmentInfo]):
        """Recursively extract attachments from form controls."""
        for control in controls:
            if not isinstance(control, dict):
                continue

            control_type = control.get("type", "")

            # Handle fieldList (费用明细) - contains nested controls
            if control_type == "fieldList":
                value = control.get("value", [])
                if isinstance(value, list):
                    for row in value:
                        if isinstance(row, list):
                            # Each row is a list of controls
                            self._extract_attachments_recursive(row, attachments)
                continue

            # Handle attachment controls
            if control_type not in ("attachment", "attachmentV2"):
                continue

            value = control.get("value")
            if not value:
                continue

            # Value can be a JSON string or already parsed
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    # Could be a single URL string
                    if value.startswith("http"):
                        attachments.append(
                            AttachmentInfo(
                                file_token="",
                                name="attachment",
                                download_url=value,
                            )
                        )
                    continue

            # Get filename from ext field if available
            ext = control.get("ext")
            ext_filenames = []
            if isinstance(ext, str) and ext:
                # ext may contain multiple filenames separated by comma
                ext_filenames = [name.strip() for name in ext.split(",")]
            elif isinstance(ext, dict):
                name = ext.get("name") or ext.get("file_name")
                if name:
                    ext_filenames = [name]
            elif isinstance(ext, list):
                ext_filenames = ext

            # Handle both single file and list of files
            files = value if isinstance(value, list) else [value]
            for i, file_info in enumerate(files):
                # Handle direct URL strings (common in attachmentV2)
                if isinstance(file_info, str) and file_info.startswith("http"):
                    # Use corresponding ext filename if available
                    filename = ext_filenames[i] if i < len(ext_filenames) else f"attachment_{i+1}"
                    attachments.append(
                        AttachmentInfo(
                            file_token="",
                            name=filename,
                            download_url=file_info,
                        )
                    )
                    continue

                if not isinstance(file_info, dict):
                    continue

                file_token = file_info.get("file_token") or file_info.get("token") or ""
                file_name = file_info.get("name") or file_info.get("file_name", f"attachment_{i+1}")
                download_url = file_info.get("url") or file_info.get("download_url") or ""

                attachments.append(
                    AttachmentInfo(
                        file_token=file_token,
                        name=file_name,
                        mime_type=file_info.get("mime_type", ""),
                        download_url=download_url,
                    )
                )

    def extract_email_from_form(
        self,
        form_json: str,
        email_field_name: str,
        default_email: str = "",
    ) -> str:
        """Extract target email address from approval form.

        Looks for a field matching email_field_name and returns its value.
        Falls back to default_email if not found.
        """
        try:
            form_data = json.loads(form_json)
        except json.JSONDecodeError:
            return default_email

        for control in form_data:
            name = control.get("name", "")
            if name == email_field_name:
                value = control.get("value", "")
                # Handle select/radio controls where value might be JSON
                if isinstance(value, str):
                    # Try to parse as JSON in case it's a select option
                    try:
                        parsed = json.loads(value)
                        if isinstance(parsed, list) and parsed:
                            # Select control returns list of selected options
                            return parsed[0] if isinstance(parsed[0], str) else str(parsed[0])
                        elif isinstance(parsed, str):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    # Return as-is if it looks like an email
                    if "@" in value:
                        return value.strip()
                elif isinstance(value, list) and value:
                    return str(value[0])

        return default_email

    async def download_attachments(
        self,
        attachments: list[AttachmentInfo],
    ) -> list[AttachmentInfo]:
        """Download all attachments and populate their content."""
        if not attachments:
            return []

        # Get download URLs for attachments that only have file_token
        file_tokens = [a.file_token for a in attachments if a.file_token and not a.download_url]
        token_to_url = {}
        if file_tokens:
            token_to_url = await self.client.get_file_download_urls(file_tokens)

        downloaded = []
        for attachment in attachments:
            # Use direct download_url if available, otherwise lookup by file_token
            url = attachment.download_url or token_to_url.get(attachment.file_token)
            if not url:
                print(f"No download URL for {attachment.name}")
                continue
            try:
                content = await self.client.download_file(url)
                attachment.content = content
                downloaded.append(attachment)
            except Exception as e:
                print(f"Failed to download {attachment.name}: {e}")

        return downloaded
