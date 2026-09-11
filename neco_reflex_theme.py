"""NECO Reflex palette and font stacks.

Constants only: no imports from project modules, no side effects. Every
brand color or font name the UI uses reads from here rather than being
written at a call site. The brand spec itself lives in Notion ("NECO
Reflex — Branding Decisions") and deliberately isn't restated here.

Not everything on screen is a brand color. The red error banners, the amber
warning banner, and the black behind the video panes keep their own values
where they are, and don't come from this module.
"""

CRIMSON = "#A42B35"
BURGUNDY = "#480011"
OFF_WHITE = "#FBF8F3"
GOLDEN = "#D89E61"
SANDSTONE = "#DEDDD3"
TAUPE = "#B89F90"
MAHOGANY = "#AF3E3A"
CHARCOAL = "#231E21"

# The recording pupil's fill. MAHOGANY is the sanctioned swap if CRIMSON
# reads muddy on the lab monitor -- change it here and in
# branding/mark-recording.svg together (test_reflex_mark.py checks).
RECORDING_PUPIL = CRIMSON

# OpenCV takes (B, G, R); derived here so the two spellings of one color
# can't drift apart.
BURGUNDY_BGR = (int(BURGUNDY[5:7], 16), int(BURGUNDY[3:5], 16), int(BURGUNDY[1:3], 16))

# First installed family wins. Franklin Gothic Book comes with Microsoft
# Office, not Windows -- a clinic PC without Office falls through to
# Franklin Gothic Medium, which Windows does ship. Cambria ships with
# Windows too; the rest are last resorts.
HEADING_FONTS = ("Cambria", "Georgia", "Times New Roman")
SUBHEADING_FONTS = ("Franklin Gothic Book", "Franklin Gothic Medium", "Segoe UI")  # set in all caps
BODY_FONTS = ("Franklin Gothic Book", "Franklin Gothic Medium", "Segoe UI")
