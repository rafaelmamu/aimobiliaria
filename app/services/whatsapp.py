import logging

import httpx

logger = logging.getLogger(__name__)

META_API_BASE = "https://graph.facebook.com/v21.0"


class WhatsAppService:
    """Client for Meta WhatsApp Cloud API."""

    def __init__(self, phone_number_id: str, access_token: str):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.base_url = f"{META_API_BASE}/{phone_number_id}"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    async def send_text_message(self, to: str, text: str) -> dict:
        """Send a text message to a WhatsApp number."""
        # WhatsApp has a ~4096 char limit per message
        # Split long messages if needed
        messages = self._split_message(text, max_length=4000)
        results = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for msg in messages:
                payload = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": to,
                    "type": "text",
                    "text": {"preview_url": True, "body": msg},
                }
                response = await client.post(
                    f"{self.base_url}/messages",
                    headers=self.headers,
                    json=payload,
                )
                result = response.json()
                if response.status_code != 200:
                    logger.error(f"WhatsApp send error: {result}")
                else:
                    logger.info(f"Message sent to {to}: {result}")
                results.append(result)

        return results[-1] if results else {}

    async def send_image_message(
        self, to: str, image_url: str, caption: str = ""
    ) -> dict:
        """Send an image message (e.g., property photo).

        Downloads the image server-side and uploads it to Meta's Media
        endpoint first, then sends it by `media_id`. Meta silently drops
        outbound images whose `link` URL it can't fetch cleanly (odd
        User-Agent filters, cookies, redirects, etc.) — returning HTTP
        200 either way. Uploading sidesteps that by letting Meta host
        the bytes. Falls back to direct link mode if the upload fails.
        """
        media_id = await self._upload_media(image_url)

        if media_id:
            image_field: dict = {"id": media_id, "caption": caption}
        else:
            logger.warning(
                f"Media upload failed for {image_url}; falling back to link mode"
            )
            image_field = {"link": image_url, "caption": caption}

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "image",
            "image": image_field,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/messages",
                headers=self.headers,
                json=payload,
            )
            result = response.json()
            if response.status_code != 200:
                logger.error(f"WhatsApp send image error: {result}")
            return result

    async def _upload_media(self, image_url: str) -> str | None:
        """Download an image and upload it to Meta; returns the media_id.

        Returns None on any failure so the caller can fall back to link
        mode.
        """
        from urllib.parse import urlparse

        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True
            ) as client:
                r = await client.get(image_url)
                r.raise_for_status()
                content = r.content
                content_type = (
                    r.headers.get("content-type", "image/jpeg")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
        except Exception as e:
            logger.error(
                f"Failed to download image {image_url}: {type(e).__name__}: {e}"
            )
            return None

        # Meta rejects non-image content; guard the header
        if not content_type.startswith("image/"):
            logger.warning(
                f"Unexpected content-type {content_type!r} for {image_url}; "
                "skipping media upload"
            )
            return None

        filename = urlparse(image_url).path.rsplit("/", 1)[-1] or "image.jpg"
        upload_headers = {"Authorization": self.headers["Authorization"]}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{self.base_url}/media",
                    headers=upload_headers,
                    files={"file": (filename, content, content_type)},
                    data={
                        "messaging_product": "whatsapp",
                        "type": content_type,
                    },
                )
                if r.status_code != 200:
                    logger.error(
                        f"Meta media upload rejected ({r.status_code}): {r.text}"
                    )
                    return None
                media_id = (r.json() or {}).get("id")
                if media_id:
                    logger.info(
                        f"Media uploaded to Meta: {image_url} -> id={media_id} "
                        f"({len(content)} bytes, {content_type})"
                    )
                return media_id
        except Exception as e:
            logger.error(
                f"Meta media upload error for {image_url}: {type(e).__name__}: {e}"
            )
            return None

    async def mark_as_read(self, message_id: str) -> dict:
        """Mark a message as read (blue checkmarks)."""
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self.base_url}/messages",
                headers=self.headers,
                json=payload,
            )
            return response.json()

    def _split_message(self, text: str, max_length: int = 4000) -> list[str]:
        """Split a long message into multiple WhatsApp-compatible messages."""
        if len(text) <= max_length:
            return [text]

        messages = []
        current = ""
        paragraphs = text.split("\n\n")

        for paragraph in paragraphs:
            if len(current) + len(paragraph) + 2 > max_length:
                if current:
                    messages.append(current.strip())
                    current = paragraph
                else:
                    # Single paragraph too long, split by sentences
                    sentences = paragraph.split(". ")
                    for sentence in sentences:
                        if len(current) + len(sentence) + 2 > max_length:
                            messages.append(current.strip())
                            current = sentence
                        else:
                            current += (". " if current else "") + sentence
            else:
                current += ("\n\n" if current else "") + paragraph

        if current:
            messages.append(current.strip())

        return messages

    @staticmethod
    def parse_webhook_message(body: dict) -> dict | None:
        """Extract message data from WhatsApp webhook payload.

        Returns dict with: from_number, message_id, message_type, content, name
        Or None if not a valid message event.
        """
        try:
            entry = body.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})

            # Check if this is a message event (not status update)
            if "messages" not in value:
                return None

            message = value["messages"][0]
            contact = value.get("contacts", [{}])[0]

            result = {
                "from_number": message["from"],
                "message_id": message["id"],
                "message_type": message["type"],
                "timestamp": message.get("timestamp"),
                "name": contact.get("profile", {}).get("name"),
            }

            # Extract content based on message type
            if message["type"] == "text":
                result["content"] = message["text"]["body"]
            elif message["type"] == "image":
                result["content"] = message.get("image", {}).get("caption", "[Imagem]")
                result["media_id"] = message["image"]["id"]
            elif message["type"] == "audio":
                result["content"] = "[Áudio]"
                result["media_id"] = message["audio"]["id"]
            elif message["type"] == "document":
                result["content"] = message.get("document", {}).get(
                    "caption", "[Documento]"
                )
                result["media_id"] = message["document"]["id"]
            elif message["type"] == "interactive":
                # Button replies or list replies
                interactive = message.get("interactive", {})
                if interactive.get("type") == "button_reply":
                    result["content"] = interactive["button_reply"]["title"]
                elif interactive.get("type") == "list_reply":
                    result["content"] = interactive["list_reply"]["title"]
            else:
                result["content"] = f"[{message['type']}]"

            return result

        except (KeyError, IndexError) as e:
            logger.error(f"Error parsing webhook: {e}")
            return None
