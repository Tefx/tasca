"""Pure validation rules for Markdown saying attachments."""

from dataclasses import dataclass
from enum import StrEnum

import deal

from tasca.core.domain.saying import AttachmentInput

MAX_ATTACHMENTS_PER_SAYING = 8
MAX_ATTACHMENT_NAME_CHARACTERS = 128
MAX_ATTACHMENT_BYTES = 256 * 1024
MAX_ATTACHMENTS_TOTAL_BYTES = 1024 * 1024


class SayingValidationKind(StrEnum):
    """Machine-readable saying and attachment validation categories."""

    CONTENT = "content"
    ATTACHMENT_COUNT = "attachment_count"
    ATTACHMENT_NAME = "attachment_name"
    ATTACHMENT_CONTENT = "attachment_content"
    ATTACHMENT_BYTES = "attachment_bytes"
    ATTACHMENT_TOTAL_BYTES = "attachment_total_bytes"


@dataclass(frozen=True)
class SayingValidationError:
    """A deterministic validation failure for a saying payload."""

    kind: SayingValidationKind
    message: str
    attachment_index: int | None = None
    limit: int | None = None
    actual: int | None = None


@deal.pre(lambda value: isinstance(value, str))
@deal.post(lambda result: result is None or result >= 0)
def utf8_size(value: str) -> int | None:
    """Return exact UTF-8 bytes, or ``None`` for invalid Unicode scalar text.

    >>> utf8_size("é")
    2
    >>> utf8_size(chr(0xD800)) is None
    True
    """
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


@deal.pre(lambda name: isinstance(name, str))
@deal.post(lambda result: isinstance(result, bool))
def is_valid_attachment_name(name: str) -> bool:
    """Return whether a Markdown attachment name meets the v1 contract.

    >>> is_valid_attachment_name("notes.md")
    True
    >>> is_valid_attachment_name(" ../notes.md")
    False
    """
    return (
        1 <= len(name) <= MAX_ATTACHMENT_NAME_CHARACTERS
        and name == name.strip()
        and name.endswith((".md", ".markdown"))
        and not any(character in name for character in ("/", "\\", "\x00"))
        and utf8_size(name) is not None
    )


@deal.pre(lambda index, attachment: index >= 0 and isinstance(attachment, AttachmentInput))
@deal.post(
    lambda result: (
        (isinstance(result[0], int) and result[0] >= 0 and result[1] is None)
        or (result[0] is None and isinstance(result[1], SayingValidationError))
    )
)
def validate_attachment_input(
    index: int,
    attachment: AttachmentInput,
) -> tuple[int | None, SayingValidationError | None]:
    """Validate one attachment and return its exact UTF-8 size.

    >>> validate_attachment_input(0, AttachmentInput(name="note.md", content="note"))[0]
    4
    >>> validate_attachment_input(1, AttachmentInput(name="empty.md", content=""))[0]
    0
    >>> validate_attachment_input(2, AttachmentInput(name="bad.txt", content="note"))[1].attachment_index
    2
    """
    if not is_valid_attachment_name(attachment.name):
        return None, SayingValidationError(
            kind=SayingValidationKind.ATTACHMENT_NAME,
            message=(
                "Attachment names must be 1..128 characters, have no surrounding "
                "whitespace, end in .md or .markdown, and contain no slash, backslash, or NUL"
            ),
            attachment_index=index,
        )
    byte_size = utf8_size(attachment.content)
    if byte_size is None:
        return None, SayingValidationError(
            kind=SayingValidationKind.ATTACHMENT_CONTENT,
            message="Attachment content must be valid UTF-8 text",
            attachment_index=index,
        )
    if byte_size > MAX_ATTACHMENT_BYTES:
        return None, SayingValidationError(
            kind=SayingValidationKind.ATTACHMENT_BYTES,
            message=f"Attachment content exceeds {MAX_ATTACHMENT_BYTES} UTF-8 bytes",
            attachment_index=index,
            limit=MAX_ATTACHMENT_BYTES,
            actual=byte_size,
        )
    return byte_size, None


@deal.pre(
    lambda content, attachments: (
        isinstance(content, str)
        and isinstance(attachments, list)
        and all(isinstance(attachment, AttachmentInput) for attachment in attachments)
    )
)
@deal.post(lambda result: result is None or isinstance(result, SayingValidationError))
def validate_saying_payload(
    content: str,
    attachments: list[AttachmentInput],
) -> SayingValidationError | None:
    """Validate nonblank Markdown body and all attachment v1 boundaries.

    Validation uses Unicode character counts for names and exact UTF-8 byte
    counts for attachment content. Limits are inclusive.

    >>> validate_saying_payload("Body", []) is None
    True
    >>> validate_saying_payload(" ", []).kind
    <SayingValidationKind.CONTENT: 'content'>
    >>> validate_saying_payload("Body", [AttachmentInput(name="notes.md", content="日本語")]) is None
    True
    >>> validate_saying_payload("Body", [AttachmentInput(name="empty.md", content="")]) is None
    True
    >>> validate_saying_payload("Body", [AttachmentInput(name="../notes.md", content="x")]).kind
    <SayingValidationKind.ATTACHMENT_NAME: 'attachment_name'>
    """
    if not content.strip():
        return SayingValidationError(
            kind=SayingValidationKind.CONTENT,
            message="Saying content must contain non-whitespace Markdown",
        )
    if utf8_size(content) is None:
        return SayingValidationError(
            kind=SayingValidationKind.CONTENT,
            message="Saying content must be valid UTF-8 text",
        )
    if len(attachments) > MAX_ATTACHMENTS_PER_SAYING:
        return SayingValidationError(
            kind=SayingValidationKind.ATTACHMENT_COUNT,
            message=f"A saying may contain at most {MAX_ATTACHMENTS_PER_SAYING} attachments",
            limit=MAX_ATTACHMENTS_PER_SAYING,
            actual=len(attachments),
        )

    total_bytes = 0
    for index, attachment in enumerate(attachments):
        byte_size, error = validate_attachment_input(index, attachment)
        if error is not None:
            return error
        assert byte_size is not None
        total_bytes += byte_size

    if total_bytes > MAX_ATTACHMENTS_TOTAL_BYTES:
        return SayingValidationError(
            kind=SayingValidationKind.ATTACHMENT_TOTAL_BYTES,
            message=f"Attachment content total exceeds {MAX_ATTACHMENTS_TOTAL_BYTES} UTF-8 bytes",
            limit=MAX_ATTACHMENTS_TOTAL_BYTES,
            actual=total_bytes,
        )
    return None
