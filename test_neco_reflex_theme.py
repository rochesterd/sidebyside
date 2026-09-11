"""neco_reflex_theme.py stays constants-only and internally consistent."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

import neco_reflex_theme as theme

PALETTE = ("CRIMSON", "BURGUNDY", "OFF_WHITE", "GOLDEN", "SANDSTONE", "TAUPE", "MAHOGANY", "CHARCOAL")
# Families every supported Windows install has, for the end of each stack.
WINDOWS_FONTS = {"Segoe UI", "Times New Roman"}


class TestTheme(unittest.TestCase):
    def test_palette_is_hex(self):
        for name in PALETTE:
            with self.subTest(name):
                self.assertRegex(getattr(theme, name), re.compile(r"^#[0-9A-F]{6}$"))

    def test_recording_pupil_is_one_of_the_two_sanctioned_colors(self):
        self.assertIn(theme.RECORDING_PUPIL, (theme.CRIMSON, theme.MAHOGANY))

    def test_bgr_matches_hex(self):
        b, g, r = theme.BURGUNDY_BGR
        self.assertEqual(f"#{r:02X}{g:02X}{b:02X}", theme.BURGUNDY)

    def test_every_font_stack_ends_in_a_family_windows_ships(self):
        for stack in (theme.HEADING_FONTS, theme.SUBHEADING_FONTS, theme.BODY_FONTS):
            with self.subTest(stack=stack):
                self.assertIn(stack[-1], WINDOWS_FONTS)

    def test_has_no_imports(self):
        tree = ast.parse(Path(theme.__file__).read_text(encoding="utf-8"))
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertEqual(imports, [])


if __name__ == "__main__":
    unittest.main()
