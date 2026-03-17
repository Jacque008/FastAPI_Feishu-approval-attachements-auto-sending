from typing import Any, Optional
from config import Settings
from services import FeishuClient, AttachmentService, DropboxUploader
# from services import EmailSender  # for email


class ApprovalHandler:
    KNOWN_APPROVAL_NAMES = {"费用报销", "付款-瑞典对公-SHIC"}

    # Mapping: approval_name (Chinese) -> settings attribute name (English)  # for email
    # APPROVAL_EMAIL_ATTRS = {  # for email
    #     "费用报销": "email_expense",  # for email
    #     "付款-瑞典对公-SHIC": "email_payment_sweden_shic",  # for email
    # }  # for email

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
        # self.email_sender = EmailSender(  # for email
        #     api_key=settings.resend_api_key,  # for email
        #     from_email=settings.resend_from_email,  # for email
        # )  # for email

    # def _get_target_email(self, approval_name: str) -> Optional[str]:  # for email
    #     """Get target email based on approval name from settings."""  # for email
    #     attr_name = self.APPROVAL_EMAIL_ATTRS.get(approval_name)  # for email
    #     if not attr_name:  # for email
    #         return None  # for email
    #     email = getattr(self.settings, attr_name, "")  # for email
    #     return email if email else None  # for email

    async def handle_event(self, event: dict[str, Any]) -> bool:
        """Handle approval status changed event.

        Only processes APPROVED status events from approval_instance event type.
        Returns True if email was sent successfully, False otherwise.
        """
        # Check event type - only process approval_instance events
        header = event.get("header", {})
        event_type = header.get("event_type", "")
        if event_type and "approval_instance" not in event_type:
            print(f"Skipping non-instance event type: {event_type}")
            return False

        # Extract event data - handle both v1 and v2 event formats
        event_data = event.get("event", {})

        # Get approval status - check multiple possible fields
        status = (
            event_data.get("status")
            or event_data.get("instance_status")
            or event_data.get("object", {}).get("status")
        )
        if status != "APPROVED":
            print(f"Skipping event with status: {status}")
            return False

        # Get instance code
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
        """Process an approved approval instance.

        Returns True if files were uploaded to Dropbox successfully, False otherwise.
        """
        # 1. Get approval instance details
        print(f"Fetching approval instance details for {instance_code}...")
        instance = await self.feishu_client.get_approval_instance(instance_code)
        approval_name = instance.get("approval_name", "")
        form_json = instance.get("form", "[]")
        print(f"Got instance data, approval_name: {approval_name!r}")

        # 2. Check approval name is known
        if approval_name not in self.KNOWN_APPROVAL_NAMES:
            print(f"Approval '{approval_name}' not in known types, skipping {instance_code}")
            return False

        # 3. Extract serial_number and end_time for Dropbox path
        serial_number = instance.get("serial_number", instance_code)
        end_time_ms = int(instance.get("end_time", 0))

        # 4. Extract attachments from form
        attachments = self.attachment_service.extract_attachments_from_form(form_json)
        if not attachments:
            print(f"No attachments found for instance {instance_code}")
            return False

        print(f"Found {len(attachments)} attachments, downloading...")

        # 5. Download attachments
        downloaded = await self.attachment_service.download_attachments(attachments)
        if not downloaded:
            print(f"Failed to download any attachments for instance {instance_code}")
            return False

        # 6. Upload to Dropbox
        print(f"Uploading {len(downloaded)} attachments to Dropbox for {instance_code}...")
        uploaded = self.dropbox_uploader.upload_attachments(
            downloaded, end_time_ms, serial_number, approval_name
        )
        folder = "/".join(uploaded[0].split("/")[:4]) + "/" if uploaded else ""
        print(f"Uploaded {len(uploaded)} files to Dropbox:{folder} for {instance_code}")
        return len(uploaded) > 0

        # --- for email (old flow) ---
        # # 3. Get approval title and amount from form
        # form_data = json.loads(form_json)
        # approval_title = ""
        # approval_amount = ""
        # expense_contents = []
        # for field in form_data:
        #     field_name = field.get("name", "")
        #     field_type = field.get("type", "")
        #     if field_name in ("名称", "付款事由") and field_type in ("input", "textarea"):
        #         if not approval_title:
        #             approval_title = field.get("value", "").strip()
        #     elif field_name in ("金额", "付款金额") and field_type == "amount":
        #         amount_value = field.get("value", "")
        #         ext = field.get("ext", {})
        #         currency = ext.get("currency", "SEK") if isinstance(ext, dict) else "SEK"
        #         approval_amount = f"{amount_value} {currency}"
        #     elif field_type == "fieldList":
        #         if not approval_amount:
        #             ext = field.get("ext", [])
        #             if isinstance(ext, list):
        #                 for item in ext:
        #                     if item.get("type") == "amount":
        #                         sum_items = item.get("sumItems", "")
        #                         if sum_items:
        #                             try:
        #                                 sums = json.loads(sum_items)
        #                                 parts = [f"{s.get('value', '')} {s.get('currency', '')}" for s in sums]
        #                                 approval_amount = ", ".join(parts)
        #                             except json.JSONDecodeError:
        #                                 approval_amount = item.get("value", "")
        #                             break
        #         rows = field.get("value", [])
        #         if isinstance(rows, list):
        #             for row in rows:
        #                 if isinstance(row, list):
        #                     for cell in row:
        #                         if cell.get("name") == "报销内容" and cell.get("type") == "input":
        #                             content = cell.get("value", "").strip()
        #                             if content:
        #                                 expense_contents.append(content)
        # if expense_contents:
        #     approval_title = "-".join(expense_contents)
        # elif not approval_title:
        #     approval_title = instance.get("serial_number", instance_code)
        # # 2. Get target email  # for email
        # target_email = self._get_target_email(approval_name)  # for email
        # if not target_email:  # for email
        #     print(f"Approval '{approval_name}' not in mapping, skipping {instance_code}")  # for email
        #     return False  # for email
        # # 6. Send email  # for email
        # subject = f"[{approval_name}]-{approval_title.replace(chr(10), ' ').replace(chr(13), '')}"  # for email
        # body = (  # for email
        #     f"审批已通过\n\n"  # for email
        #     f"审批类型: {approval_name}\n"  # for email
        #     f"审批标题: {approval_title}\n"  # for email
        #     f"审批金额: {approval_amount}\n"  # for email
        #     f"附件数量: {len(downloaded)}\n"  # for email
        # )  # for email
        # print(f"Sending email({instance_code}) to {target_email} with {len(downloaded)} attachments...")  # for email
        # await self.email_sender.send_with_attachments(  # for email
        #     to_email=target_email, subject=subject, body=body, attachments=downloaded,  # for email
        # )  # for email
        # print(f"Email({instance_code}) sent successfully to {target_email}")  # for email
        # return True  # for email
