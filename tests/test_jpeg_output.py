from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image

from image_creator.jpeg_output import NATIVE_JPEG_INFO_KEY, write_rendered_jpeg


def _jpeg_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 16), "navy").save(output, "JPEG")
    return output.getvalue()


class JpegOutputTests(unittest.TestCase):
    def test_native_bytes_are_written_without_reencoding(self):
        image = MagicMock()
        encoded = _jpeg_bytes()
        image.info = {NATIVE_JPEG_INFO_KEY: encoded}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "deck.jpg"
            reused = write_rendered_jpeg(image, target)
            self.assertTrue(reused)
            self.assertEqual(target.read_bytes(), encoded)
        image.save.assert_not_called()

    def test_pillow_fallback_preserves_requested_options(self):
        image = MagicMock()
        image.info = {}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "deck.jpg"
            reused = write_rendered_jpeg(
                image,
                target,
                quality=90,
                optimize=False,
            )
            self.assertFalse(reused)
            image.save.assert_called_once()
            kwargs = image.save.call_args.kwargs
            self.assertEqual(kwargs["format"], "JPEG")
            self.assertEqual(kwargs["quality"], 90)
            self.assertFalse(kwargs["optimize"])


if __name__ == "__main__":
    unittest.main()
