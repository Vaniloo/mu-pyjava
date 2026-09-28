"""Explicit input capabilities, independent of provider/model name guesses."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelCapabilities:
    supports_images: bool = False
    max_image_dimension: int = 2000
    max_image_base64_bytes: int = 4_718_592

    def __post_init__(self):
        if not isinstance(self.supports_images, bool):
            raise ValueError("supports_images must be boolean")
        for name, maximum in (("max_image_dimension", 2000), ("max_image_base64_bytes", 4_718_592)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer from 1 to {maximum}")
