import os
import time
from urllib.parse import quote

import requests

from chat_utils import RUNTIME_DIR

IMAGES_DIR = os.path.join(RUNTIME_DIR, 'images')
os.makedirs(IMAGES_DIR, exist_ok=True)

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}"


class ImageGenError(Exception):
    """Raised when image generation fails."""


def generate_image(prompt: str, image_path: str | None = None,
                   output_dir: str | None = None,
                   width: int = 512,
                   height: int = 512,
                   **_kwargs) -> str:
    """Generate an image using the Pollinations.ai API (free, no API key).

    Args:
        prompt: A textual description of the image to generate.
        image_path: Not supported — raises ImageGenError if provided.
        output_dir: Directory to save the result. Defaults to runtime/images.
        width: Image width in pixels (default 512).
        height: Image height in pixels (default 512).

    Returns:
        The absolute path to the saved image file.

    Raises:
        ImageGenError: If generation fails.
    """
    if image_path:
        raise ImageGenError(
            "Image editing (img2img) is not supported by Pollinations.ai. "
            "Please remove the attached image and try again."
        )

    output_dir = output_dir or IMAGES_DIR
    os.makedirs(output_dir, exist_ok=True)

    url = POLLINATIONS_URL.format(prompt=quote(prompt))
    params = {
        "width": width,
        "height": height,
        "nologo": "true",
        "enhance": "true",
    }

    try:
        print(f"[image_gen] Requesting image from Pollinations.ai...")
        resp = requests.get(url, params=params, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise ImageGenError(f"Pollinations.ai request failed: {e}") from e

    content_type = resp.headers.get("Content-Type", "")
    if "image" not in content_type:
        raise ImageGenError(
            f"Pollinations.ai returned unexpected content type: {content_type}"
        )

    # Save with a timestamped filename
    stamp = time.strftime('%Y%m%d_%H%M%S')
    filename = f"gen_{stamp}.png"
    output_path = os.path.join(output_dir, filename)
    with open(output_path, "wb") as f:
        f.write(resp.content)

    print(f"[image_gen] Image saved to {output_path}")
    return output_path