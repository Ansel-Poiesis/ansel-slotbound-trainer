"""Project-owned PNG resources, checked without a window or image server."""
import binascii
import hashlib
import struct
import unittest
import zlib

from symbol_assets import ICON_FILES, SYMBOL_NAMES, asset_directory, asset_manifest, load_symbol_images

ASSET_EVIDENCE = {}


class FakePhoto:
    def __init__(self, master, file):
        from pathlib import Path
        self.master = master
        self.size = struct.unpack(">II", Path(file).read_bytes()[16:24])

    def width(self):
        return self.size[0]

    def height(self):
        return self.size[1]

    def zoom(self, amount):
        result = object.__new__(FakePhoto)
        result.master = self.master
        result.size = tuple(value * amount for value in self.size)
        return result

    def subsample(self, amount):
        result = object.__new__(FakePhoto)
        result.master = self.master
        result.size = tuple((value + amount - 1) // amount for value in self.size)
        return result


class SymbolAssetTests(unittest.TestCase):
    def test_all_six_project_pngs_are_complete_transparent_and_match_manifest(self):
        manifest = asset_manifest()
        self.assertEqual(set(manifest["symbols"]), set(SYMBOL_NAMES))
        for symbol, filename in ICON_FILES.items():
            with self.subTest(symbol=symbol):
                item = manifest["symbols"][symbol]
                self.assertEqual(item["name"], SYMBOL_NAMES[symbol])
                self.assertEqual(item["file"], filename)
                data = (asset_directory() / filename).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                position, compressed, transparent, ended = 8, b"", False, False
                while position < len(data):
                    length = struct.unpack_from(">I", data, position)[0]
                    kind = data[position + 4:position + 8]
                    payload = data[position + 8:position + 8 + length]
                    crc = struct.unpack_from(">I", data, position + 8 + length)[0]
                    self.assertEqual(binascii.crc32(kind + payload) & 0xffffffff, crc)
                    if kind == b"IHDR":
                        width, height, depth, color = struct.unpack(">IIBB", payload[:10])
                        self.assertIn(width, (32, 36))
                        self.assertEqual(width, height)
                        transparent |= color in (4, 6)
                    elif kind == b"tRNS":
                        transparent = True
                    elif kind == b"IDAT":
                        compressed += payload
                    elif kind == b"IEND":
                        ended = True
                    position += length + 12
                self.assertTrue(ended and transparent)
                self.assertTrue(zlib.decompress(compressed))
        ASSET_EVIDENCE.update(manifest)

    def test_photo_factory_retains_icons_and_sizes_for_supported_scales(self):
        owner = object()
        for scale in (1, 1.25, 1.5):
            for logical in (18, 32):
                with self.subTest(scale=scale, logical=logical):
                    images = load_symbol_images(owner, scale, logical, photo_factory=FakePhoto)
                    self.assertEqual(set(images), set(SYMBOL_NAMES))
                    for photo in images.values():
                        self.assertIs(photo.master, owner)
                        self.assertEqual((photo.width(), photo.height()), (round(logical * scale),) * 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
