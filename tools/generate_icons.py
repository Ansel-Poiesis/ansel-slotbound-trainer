"""Draw original vector icons and export PNG/ICO. Copyright 2026 Ansel, MIT."""
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets/symbols"


class Icon:
    def __init__(self):
        self.image = Image.new("RGBA", (32, 32))
        self.draw = ImageDraw.Draw(self.image)
        self.svg = []

    def polygon(self, points, color):
        self.draw.polygon(points, fill=color)
        self.svg.append('<polygon points="%s" fill="%s"/>' % (" ".join(f"{x},{y}" for x, y in points), color))

    def line(self, points, color, width=2):
        self.draw.line(points, fill=color, width=width)
        self.svg.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%s"/>' %
                        (" ".join(f"{x},{y}" for x, y in points), color, width))

    def save(self, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(target.with_suffix(".png"))
        target.with_suffix(".svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">\n'
            '<!-- Original Ansel artwork, MIT License -->\n' + "\n".join(self.svg) + "\n</svg>\n", encoding="utf-8")


def make_icons():
    cream, dark, gold = "#edf0e8", "#22262a", "#ffa34a"
    icons = {}
    warrior = Icon()
    warrior.polygon([(5, 10), (16, 5), (27, 10), (25, 23), (16, 29), (7, 23)], gold)
    warrior.polygon([(9, 12), (16, 9), (23, 12), (22, 21), (16, 25), (10, 21)], dark)
    warrior.polygon([(15, 9), (18, 6), (18, 20), (15, 20)], cream)
    warrior.line([(11, 20), (22, 20)], cream)
    warrior.line([(16, 20), (16, 25)], gold)
    icons["warrior"] = warrior

    archer = Icon()
    archer.line([(12, 4), (21, 9), (25, 16), (21, 23), (12, 28)], "#87d4a0", 3)
    archer.line([(12, 4), (17, 16), (12, 28)], cream, 1)
    archer.line([(4, 16), (28, 16)], gold, 2)
    archer.polygon([(28, 16), (22, 11), (22, 21)], gold)
    archer.line([(5, 12), (9, 16), (5, 20)], cream, 2)
    icons["archer"] = archer

    spear = Icon()
    spear.line([(7, 28), (23, 9)], gold, 3)
    spear.polygon([(20, 6), (29, 2), (26, 13), (21, 15), (18, 11)], "#91cce9")
    spear.line([(21, 10), (26, 5)], cream, 1)
    spear.line([(12, 17), (18, 22)], cream, 2)
    icons["spear"] = spear

    mage = Icon()
    mage.line([(7, 28), (22, 10)], gold, 3)
    mage.polygon([(24, 2), (30, 9), (23, 16), (16, 9)], "#c7a1f1")
    mage.polygon([(24, 4), (25, 9), (21, 11), (19, 9)], cream)
    mage.line([(5, 7), (5, 13)], gold, 2)
    mage.line([(2, 10), (8, 10)], gold, 2)
    mage.line([(12, 2), (12, 6)], cream, 1)
    icons["mage"] = mage

    duelist = Icon()
    duelist.polygon([(4, 3), (11, 6), (25, 24), (23, 26), (6, 10)], cream)
    duelist.polygon([(28, 3), (21, 6), (7, 24), (9, 26), (26, 10)], "#ef9c94")
    duelist.line([(20, 27), (27, 20)], gold, 3)
    duelist.line([(5, 20), (12, 27)], gold, 3)
    duelist.line([(25, 26), (28, 29)], cream, 3)
    duelist.line([(7, 26), (4, 29)], cream, 3)
    icons["duelist"] = duelist

    horse = Icon()
    horse.polygon([(9, 29), (12, 21), (18, 15), (11, 16), (6, 13), (12, 5), (17, 6),
                   (20, 2), (22, 7), (26, 12), (27, 19), (25, 29)], "#e2bc89")
    horse.polygon([(20, 6), (26, 12), (27, 19), (25, 28), (21, 28), (23, 17)], gold)
    horse.line([(12, 11), (15, 11)], dark, 2)
    horse.line([(10, 28), (25, 28)], cream, 2)
    icons["horse"] = horse

    names = {"W": ("warrior", "战士"), "A": ("archer", "弓手"), "S": ("spear", "枪兵"),
             "M": ("mage", "法师"), "D": ("duelist", "决斗者"), "H": ("horse", "骑兵")}
    manifest = {"author": "Ansel", "license": "MIT", "source": "tools/generate_icons.py",
                "provenance": "Original code-native vector artwork; no extracted game assets", "symbols": {}}
    for key, (file, name) in names.items():
        icons[file].save(OUT / file)
        manifest["symbols"][key] = {"name": name, "file": file + ".png",
                                    "sha256": hashlib.sha256((OUT / (file + ".png")).read_bytes()).hexdigest()}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    app = Icon()
    app.polygon([(16, 1), (29, 8), (29, 24), (16, 31), (3, 24), (3, 8)], gold)
    app.polygon([(16, 5), (25, 10), (25, 22), (16, 27), (7, 22), (7, 10)], dark)
    for x, y in ((10, 13), (16, 10), (22, 13)):
        app.polygon([(x, y), (x+2, y+3), (x, y+6), (x-2, y+3)], cream)
    app.line([(10, 23), (22, 23)], gold, 2)
    app.save(ROOT / "assets/app")
    app.image.resize((256, 256), Image.Resampling.NEAREST).save(ROOT / "assets/app.ico", sizes=[(32, 32), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    make_icons()
