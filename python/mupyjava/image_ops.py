"""Bounded image decoding and model-aware conversion; Pillow is optional."""

import base64
import io
import warnings
from typing import Optional, Protocol

from .cancel import CancellationToken
from .capabilities import ModelCapabilities
from .tool_result import ImageContent, TextContent, ToolResult


MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_PIXELS = 25_000_000


def detect_image_mime(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM") and len(data) >= 26:
        declared = int.from_bytes(data[2:6], "little")
        pixels = int.from_bytes(data[10:14], "little")
        dib = int.from_bytes(data[14:18], "little")
        if dib == 12:
            planes = int.from_bytes(data[22:24], "little")
            depth = int.from_bytes(data[24:26], "little")
        elif 40 <= dib <= 124 and len(data) >= 30:
            planes = int.from_bytes(data[26:28], "little")
            depth = int.from_bytes(data[28:30], "little")
        else:
            return None
        if (pixels >= 14 + dib and (declared == 0 or declared >= 26 and pixels < declared)
                and planes == 1 and depth in {1, 4, 8, 16, 24, 32}):
            return "image/bmp"
    return None


class ImageOperations(Protocol):
    def process(self, data: bytes, mime: str, capabilities: ModelCapabilities,
                cancel: Optional[CancellationToken] = None) -> ToolResult: ...


class PillowImageOperations:
    def process(self, data: bytes, mime: str, capabilities: ModelCapabilities,
                cancel: Optional[CancellationToken] = None) -> ToolResult:
        if cancel is not None:
            cancel.raise_if_cancelled()
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError("Image exceeds the 20 MiB source limit")
        try:
            from PIL import Image, ImageOps, UnidentifiedImageError
        except ImportError:
            return ToolResult.from_text(
                f"Read image file [{mime}]\n[Image omitted: install the optional Pillow dependency.]",
                {"image_omitted": True, "omission_reason": "missing_image_processor", "source_mime_type": mime})
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as source:
                    actual_mime = Image.MIME.get(source.format)
                    if actual_mime != mime:
                        raise ValueError("Image header does not match its decoded format")
                    original = source.size
                    if source.width * source.height > MAX_SOURCE_PIXELS:
                        raise ValueError("Image exceeds the 25 million pixel source limit")
                    if getattr(source, "is_animated", False):
                        return ToolResult.from_text(
                            f"Read image file [{mime}]\n[Image omitted: animated images are not supported.]",
                            {"image_omitted": True, "omission_reason": "animated_image",
                             "source_mime_type": mime, "original_width": original[0], "original_height": original[1]})
                    source.load()
                    oriented = source.getexif().get(274, 1) != 1
                    image = ImageOps.exif_transpose(source)
                    image.thumbnail((capabilities.max_image_dimension, capabilities.max_image_dimension))
                    changed = oriented or image.size != original or mime == "image/bmp"
                    output = data
                    output_mime = mime
                    if changed or 4 * ((len(output) + 2) // 3) > capabilities.max_image_base64_bytes:
                        output_mime = "image/jpeg" if mime == "image/jpeg" else "image/png"
                        image = image.convert("RGB" if output_mime == "image/jpeg" else "RGBA")
                        while True:
                            if cancel is not None:
                                cancel.raise_if_cancelled()
                            buffer = io.BytesIO()
                            image.save(buffer, format="JPEG" if output_mime == "image/jpeg" else "PNG", quality=85)
                            output = buffer.getvalue()
                            if 4 * ((len(output) + 2) // 3) <= capabilities.max_image_base64_bytes:
                                break
                            if image.size == (1, 1):
                                raise ValueError("Image cannot fit the model attachment limit")
                            image = image.resize((max(1, image.width // 2), max(1, image.height // 2)))
                        changed = True
                    if cancel is not None:
                        cancel.raise_if_cancelled()
                    details = {"source_mime_type": mime, "mime_type": output_mime,
                               "original_width": original[0], "original_height": original[1],
                               "width": image.width, "height": image.height, "image_omitted": False,
                               "processed": changed, "output_bytes": len(output)}
                    note = f"Read image file [{output_mime}] ({image.width}×{image.height})"
                    if changed:
                        note += f"\n[Image converted/oriented/resized from {original[0]}×{original[1]}]"
                    return ToolResult((TextContent(note), ImageContent(base64.b64encode(output).decode("ascii"), output_mime)), details)
        except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ValueError("Image cannot be safely decoded") from error
