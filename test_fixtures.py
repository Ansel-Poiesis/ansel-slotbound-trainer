"""Synthetic trainer fixtures, authored here; contains no extracted game code."""
import json
import re
from sbmod_params import CONSTANTS_EDITS


def constants_source():
    root = {}
    for path, (value, _, _) in CONSTANTS_EDITS["src/data/constants.lua"].items():
        keys = [int(key) if key.isdigit() else key for key in re.findall(r"[^.\[\]]+", path)]
        table = root
        for key in keys[:-1]:
            table = table.setdefault(key, {})
        table[keys[-1]] = value

    def encode(value):
        if isinstance(value, dict):
            return "{" + ",".join("[%s]=%s" % (encode(key), encode(item)) for key, item in value.items()) + "}"
        return json.dumps(value)
    return "return " + encode(root)
