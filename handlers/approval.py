import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any
from config import Settings
from services import FeishuClient, AttachmentService, DropboxUploader, EmailSender

_STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")


def _format_date(value) -> str:
    """Format a Feishu date field value to YYYYMMDD string."""
    if not value:
        return ""
    try:
        ts = int(str(value))
        if ts > 99999999:
            dt = datetime.fromtimestamp(ts / 1000, tz=_STOCKHOLM_TZ)
            return dt.strftime("%Y%m%d")
    except (ValueError, TypeError):
        pass
    cleaned = str(value).replace("-", "").replace("/", "").strip()
    if len(cleaned) >= 8 and cleaned[:8].isdigit():
        return cleaned[:8]
    return str(value).strip()


class ApprovalHandler:
    KNOWN_APPROVAL_NAMES = {"费用报销 - SHiC", "对公支付申请 - SHiC(298)"}

    # approval_name → settings attribute for target Fortnox email
    APPROVAL_EMAIL_ATTRS = {
        "费用报销 - SHiC": "email_expense",
        "对公支付申请 - SHiC(298)": "email_payment_shic",
    }

    def __init__(self, settings: Settings):
        self.settings = settings
        self.feishu_client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
        )
        self.attachment_service = AttachmentService(self.feishu_client)
        self.dropbox_uploader = DropboxUploader(
            refresh_token=settings.dropbox_refresh_token,
            app_key=settings.dropbox_app_key,
            app_secret=settings.dropbox_app_secret,
        )
        self.email_sender = EmailSender(
            api_key=settings.resend_api_key,
            from_email=settings.resend_from_email,
        )

    def _extract_form_metadata(self, form_json: str) -> dict:
        """Extract amount, invoice_date, payment_deadline, and project from form JSON."""
        result = {"amount": "", "invoice_date": "", "payment_deadline": "", "project": ""}
        try:
            form_data = json.loads(form_json)
        except (json.JSONDecodeError, TypeError):
            return result

        project_parts: list[str] = []

        for field in form_data:
            field_name = field.get("name", "")
            field_type = field.get("type", "")
            value = field.get("value", "")

            if field_name in ("金额", "付款金额") and field_type == "amount":
                ext = field.get("ext", {})
                currency = ext.get("currency", "") if isinstance(ext, dict) else ""
                amount_str = str(value).replace(".", ",")
                result["amount"] = f"{amount_str}{currency}"

            elif field_name == "发生日期（票面日期）":
                result["invoice_date"] = _format_date(value)

            elif field_name == "付款截止日期":
                result["payment_deadline"] = _format_date(value)

            elif field_name == "付款事由" and field_type in ("input", "textarea"):
                if not project_parts:
                    project_parts = [str(value).strip()]

            elif field_type == "fieldList":
                # Amount from fieldList sumItems (费用报销)
                if not result["amount"]:
                    ext = field.get("ext", [])
                    if isinstance(ext, list):
                        for item in ext:
                            if item.get("type") == "amount":
                                sum_items = item.get("sumItems", "")
                                if sum_items:
                                    try:
                                        sums = json.loads(sum_items)
                                        if sums:
                                            s = sums[0]
                                            v = str(s.get("value", "")).replace(".", ",")
                                            c = s.get("currency", "")
                                            result["amount"] = f"{v}{c}"
                                    except json.JSONDecodeError:
                                        result["amount"] = str(item.get("value", "")).replace(".", ",")
                                break
                # Project from 报销内容 rows (费用报销)
                rows = value if isinstance(value, list) else []
                for row in rows:
                    if isinstance(row, list):
                        for cell in row:
                            if cell.get("name") == "报销内容":
                                content = str(cell.get("value", "")).strip()
                                if content:
                                    project_parts.append(content)

        if project_parts:
            result["project"] = "-".join(project_parts)
        return result

    def _get_target_email(self, approval_name: str) -> str:
        """Return the Fortnox target email for this approval type, or empty string."""
        attr_name = self.APPROVAL_EMAIL_ATTRS.get(approval_name, "")
        return getattr(self.settings, attr_name, "") if attr_name else ""

    async def handle_event(self, event: dict[str, Any]) -> bool:
        """Handle approval status changed event."""
        header = event.get("header", {})
        event_type = header.get("event_type", "")
        if event_type and "approval_instance" not in event_type:
            print(f"Skipping non-instance event type: {event_type}")
            return False

        event_data = event.get("event", {})
        status = (
            event_data.get("status")
            or event_data.get("instance_status")
            or event_data.get("object", {}).get("status")
        )
        if status != "APPROVED":
            print(f"Skipping event with status: {status}")
            return False

        instance_code = (
            event_data.get("instance_code")
            or event_data.get("approval_code")
            or event_data.get("object", {}).get("instance_code")
        )
        if not instance_code:
            print("No instance_code found in event")
            return False

        print(f"Processing approved instance: {instance_code}")
        try:
            return await self._process_approval(instance_code)
        except Exception as e:
            print(f"Error processing approval {instance_code}: {e}")
            raise

    async def _process_approval(self, instance_code: str) -> bool:
        """Process an approved instance: upload to Dropbox AND send email to Fortnox."""
        # 1. Fetch instance
        print(f"Fetching approval instance details for {instance_code}...")
        instance = await self.feishu_client.get_approval_instance(instance_code)
        approval_name = instance.get("approval_name", "")
        form_json = instance.get("form", "[]")
        print(f"Got instance data, approval_name: {approval_name!r}")

        # 2. Filter to known approval types
        if approval_name not in self.KNOWN_APPROVAL_NAMES:
            print(f"Approval '{approval_name}' not in known types, skipping {instance_code}")
            return False

        # 3. Extract identifiers and metadata
        serial_number = instance.get("serial_number", instance_code)
        end_time_ms = int(instance.get("end_time", 0))
        meta = self._extract_form_metadata(form_json)

        # 4. Extract attachments (支付凭证 fields auto-marked via field name)
        attachments = self.attachment_service.extract_attachments_from_form(form_json)

        # For 对公支付申请: also extract bank transfer receipt from 办理 step
        if approval_name == "对公支付申请 - SHiC(298)":
            task_attachments = self.attachment_service.extract_task_attachments(instance)
            if task_attachments:
                print(f"Found {len(task_attachments)} task (办理) attachments")
                attachments.extend(task_attachments)

        if not attachments:
            print(f"No attachments found for instance {instance_code}")
            return False

        print(f"Found {len(attachments)} attachments, downloading...")

        # 5. Download all attachments once
        downloaded = await self.attachment_service.download_attachments(attachments)
        if not downloaded:
            print(f"Failed to download any attachments for instance {instance_code}")
            return False

        # 6a. Upload to Dropbox (tickets and vouchers get distinct filenames)
        print(f"Uploading {len(downloaded)} attachments to Dropbox for {instance_code}...")
        uploaded = self.dropbox_uploader.upload_attachments(
            downloaded, end_time_ms, serial_number, approval_name,
            amount=meta["amount"], invoice_date=meta["invoice_date"], project=meta["project"],
            payment_deadline=meta["payment_deadline"],
        )
        folder = "/".join(uploaded[0].split("/")[:4]) + "/" if uploaded else ""
        print(f"Uploaded {len(uploaded)} files to Dropbox:{folder} for {instance_code}")

        # 6b. Send email to Fortnox with all attachments
        target_email = self._get_target_email(approval_name)
        if target_email:
            subject = (
                f"[{approval_name}] {serial_number}"
                + (f" {meta['amount']}" if meta["amount"] else "")
                + (f" {meta['project']}" if meta["project"] else "")
            )
            body = (
                f"审批已通过\n\n"
                f"审批类型: {approval_name}\n"
                f"申请编号: {serial_number}\n"
                f"金额: {meta['amount']}\n"
                f"日期: {meta['invoice_date']}\n"
                f"项目: {meta['project']}\n"
                f"附件数量: {len(downloaded)}\n"
            )
            print(f"Sending email to {target_email} for {instance_code}...")
            await self.email_sender.send_with_attachments(
                to_email=target_email,
                subject=subject,
                body=body,
                attachments=downloaded,
            )
            print(f"Email successfully sent to {target_email} for {instance_code}")
        else:
            print(f"No target email configured for '{approval_name}', skipping email")

        return len(uploaded) > 0
