"""Original Ansel recruitment icons and their player-facing names."""
from fractions import Fraction
import json
from pathlib import Path
import sys

SYMBOL_NAMES = {"W": "战士", "A": "弓手", "S": "枪兵", "M": "法师", "D": "决斗者", "H": "骑兵"}
ICON_FILES = dict(zip(SYMBOL_NAMES, ("warrior.png", "archer.png", "spear.png", "mage.png", "duelist.png", "horse.png")))


def asset_directory():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / "symbols"


def asset_manifest():
    return json.loads((asset_directory() / "manifest.json").read_text(encoding="utf-8"))


def load_symbol_images(master, scale=1, logical_size=32, photo_factory=None):
    """Keep pixel edges sharp with integer nearest-neighbour zoom/subsampling."""
    if photo_factory is None:
        from tkinter import PhotoImage
        photo_factory = PhotoImage
    target = max(1, round(logical_size * scale))
    result = {}
    for symbol, filename in ICON_FILES.items():
        image = photo_factory(master=master, file=str(asset_directory() / filename))
        ratio = Fraction(target, image.width())
        if ratio != 1:
            image = image.zoom(ratio.numerator).subsample(ratio.denominator)
        result[symbol] = image
    return result
