from typing import Any, Optional
from config import Settings
from services import FeishuClient, AttachmentService, EmailSender


class ApprovalHandler:
    # Mapping: approval_name (Chinese) -> settings attribute name (English)
    # Add new approval types here
    APPROVAL_EMAIL_ATTRS = {
        "费用报销": "email_expense",
        "付款-瑞典对公-SHIC": "email_payment_sweden_shic",
    }

    def __init__(self, settings: Settings):
        self.settings = settings
        self.feishu_client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
        )
        self.attachment_service = AttachmentService(self.feishu_client)
        self.email_sender = EmailSender(
            api_key=settings.resend_api_key,
            from_email=settings.resend_from_email,
        )

    def _get_target_email(self, approval_name: str) -> Optional[str]:
        """Get target email based on approval name from settings."""
        attr_name = self.APPROVAL_EMAIL_ATTRS.get(approval_name)
        if not attr_name:
            return None
        email = getattr(self.settings, attr_name, "")
        return email if email else None

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

        Returns True if email was sent successfully, False otherwise.
        """
        import json

        # 1. Get approval instance details
        print(f"Fetching approval instance details for {instance_code}...")
        instance = await self.feishu_client.get_approval_instance(instance_code)
        print(f"Got instance data, approval_name: {instance.get('approval_name', 'N/A')}")
        approval_name = instance.get("approval_name", "")
        form_json = instance.get("form", "[]")

        # 2. Get target email based on approval name
        target_email = self._get_target_email(approval_name)
        if not target_email:
            print(f"Approval '{approval_name}' not in mapping, skipping instance {instance_code}")
            return False

        print(f"Approval type: {approval_name} -> {target_email}")

        # 3. Get approval title and amount from form
        form_data = json.loads(form_json)
        approval_title = ""
        approval_amount = ""
        expense_contents = []  # For 费用报销: collect all 报销内容

        for field in form_data:
            field_name = field.get("name", "")
            field_type = field.get("type", "")

            # Get title from "名称" field (付款test) or "付款事由" field (付款-瑞典对公-SHIC)
            if field_name in ("名称", "付款事由") and field_type in ("input", "textarea"):
                if not approval_title:  # Don't overwrite if already set
                    approval_title = field.get("value", "").strip()

            # Get amount from "金额" or "付款金额" field
            elif field_name in ("金额", "付款金额") and field_type == "amount":
                amount_value = field.get("value", "")
                ext = field.get("ext", {})
                currency = ext.get("currency", "SEK") if isinstance(ext, dict) else "SEK"
                approval_amount = f"{amount_value} {currency}"

            # Handle fieldList (费用报销): extract 报销内容 and total amount
            elif field_type == "fieldList":
                # Get total amount from ext
                if not approval_amount:
                    ext = field.get("ext", [])
                    if isinstance(ext, list):
                        for item in ext:
                            if item.get("type") == "amount":
                                sum_items = item.get("sumItems", "")
                                if sum_items:
                                    try:
                                        sums = json.loads(sum_items)
                                        parts = [f"{s.get('value', '')} {s.get('currency', '')}" for s in sums]
                                        approval_amount = ", ".join(parts)
                                    except json.JSONDecodeError:
                                        approval_amount = item.get("value", "")
                                break

                # Get 报销内容 from each row
                rows = field.get("value", [])
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, list):
                            for cell in row:
                                if cell.get("name") == "报销内容" and cell.get("type") == "input":
                                    content = cell.get("value", "").strip()
                                    if content:
                                        expense_contents.append(content)

        # For 费用报销: use expense contents joined by "-" as title
        if expense_contents:
            approval_title = "-".join(expense_contents)
        elif not approval_title:
            approval_title = instance.get("serial_number", instance_code)

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

        # 6. Send email with format: [审批种类]-审批标题
        subject = f"[{approval_name}]-{approval_title.replace(chr(10), ' ').replace(chr(13), '')}"
        body = (
            f"审批已通过\n\n"
            f"审批类型: {approval_name}\n"
            f"审批标题: {approval_title}\n"
            f"审批金额: {approval_amount}\n"
            f"附件数量: {len(downloaded)}\n"
        )

        print(f"Sending email({instance_code}) to {target_email} with {len(downloaded)} attachments...")
        await self.email_sender.send_with_attachments(
            to_email=target_email,
            subject=subject,
            body=body,
            attachments=downloaded,
        )
        print(f"Email({instance_code}) sent successfully to {target_email}")
        return True
