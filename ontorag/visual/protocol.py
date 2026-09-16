"""Bounded, path-free wire protocol shared by the API and model worker."""

import base64
import binascii
import math

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_ENCODED_IMAGE = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
MAX_REFERENCES = 4
MAX_BATCH = 8


def decode_image(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > MAX_ENCODED_IMAGE:
        raise ValueError("Image must be base64 encoded and at most 4 MiB")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid base64 image") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image must contain between 1 byte and 4 MiB")
    return data


def validate_references(references):
    if not isinstance(references, list) or not 1 <= len(references) <= MAX_REFERENCES:
        raise ValueError("Provide between 1 and 4 visual references")
    for ref in references:
        if not isinstance(ref, dict) or set(ref) - {"image", "box"}:
            raise ValueError("A visual reference contains image and optional box")
        decode_image(ref.get("image"))
        box = ref.get("box")
        if box is not None:
            if (
                not isinstance(box, (list, tuple))
                or len(box) != 4
                or any(
                    type(v) not in (int, float)
                    or not math.isfinite(v)
                    or not 0 <= v <= 1
                    for v in box
                )
                or box[0] >= box[2]
                or box[1] >= box[3]
            ):
                raise ValueError(
                    "box must be normalized [x1, y1, x2, y2] with positive area"
                )
