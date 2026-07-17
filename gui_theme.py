"""Central visual settings for the Job Traveler desktop interface."""

from __future__ import annotations

import platform
import tkinter as tk
from tkinter import ttk


# Search this file for "CUSTOMIZE:" to find the safe visual settings.

# ===== CUSTOMIZE: COLORS =====
# CUSTOMIZE: Main window/canvas background. Use a Tk color such as "#000000".
BACKGROUND = "#000000"
# CUSTOMIZE: Panel and form surfaces. Keep enough contrast from BACKGROUND.
SURFACE = "#131722"
RAISED_SURFACE = "#1E222D"
# CUSTOMIZE: Primary and muted text. Check contrast if either background changes.
TEXT = "#FFFFFF"
SECONDARY_TEXT = "#D1D4DC"
MUTED_TEXT = "#787B86"
# CUSTOMIZE: Subtle borders, keyboard focus, and selected-input colors.
BORDER = "#2A2E39"
FOCUS = "#2962FF"
SELECTION = "#2962FF"
# CUSTOMIZE: Semantic action/status colors; preserve their shop-floor meaning.
SUCCESS = "#089981"
SUCCESS_ACTIVE = "#067A67"
PASS = "#089981"
FAIL = "#F23645"
WARNING = "#F0B90B"

# ===== CUSTOMIZE: FONTS =====
# CUSTOMIZE: Windows uses Segoe UI; replace fallback names with installed fonts.
FONT_FAMILY = "Segoe UI" if platform.system() == "Windows" else "DejaVu Sans"
MONO_FONT_FAMILY = "Consolas" if platform.system() == "Windows" else "DejaVu Sans Mono"
# CUSTOMIZE: Tuple numbers are point sizes; changing these affects all screens.
FONT_BODY = (FONT_FAMILY, 11)
FONT_SMALL = (FONT_FAMILY, 10)
FONT_FIELD = (FONT_FAMILY, 10, "bold")
FONT_SECTION = (FONT_FAMILY, 15, "bold")
FONT_TITLE = (FONT_FAMILY, 26, "bold")
FONT_MONO = (MONO_FONT_FAMILY, 10)

# ===== CUSTOMIZE: SPACING AND DIMENSIONS =====
# CUSTOMIZE: Shared pixel spacing. Larger values make every form roomier.
SPACE_TIGHT = 8
SPACE_CONTROL = 12
SPACE_GROUP = 16
SPACE_SECTION = 24
# CUSTOMIZE: Table row height and button padding affect density and touch targets.
TABLE_ROW_HEIGHT = 32
BUTTON_PADDING = (16, 10)
# CUSTOMIZE: Preview tables use these widths as their minimum visible footprint.
PREVIEW_TABLE_HEIGHT = 5


def apply_shopos_theme(root: tk.Misc) -> ttk.Style:
    """Apply the flat ShopOS ttk theme and classic-widget defaults."""
    style = ttk.Style(root)
    style.theme_use("clam")

    # ===== CUSTOMIZE: MAIN SURFACES =====
    style.configure(".", background=BACKGROUND, foreground=TEXT, font=FONT_BODY)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("App.TFrame", background=BACKGROUND)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure(
        "Panel.TFrame", background=SURFACE, bordercolor=BORDER,
        relief="solid", borderwidth=1,
    )
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("Title.TLabel", background=BACKGROUND, foreground=TEXT, font=FONT_TITLE)
    style.configure("Heading.TLabel", background=BACKGROUND, foreground=TEXT, font=FONT_SECTION)
    style.configure(
        "Subheading.TLabel", background=BACKGROUND, foreground=SECONDARY_TEXT, font=FONT_SMALL
    )
    style.configure("Field.TLabel", background=SURFACE, foreground=SECONDARY_TEXT, font=FONT_FIELD)
    style.configure("Value.TLabel", background=SURFACE, foreground=TEXT, font=FONT_SMALL)
    style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED_TEXT, font=FONT_SMALL)
    style.configure("PanelTitle.TLabel", background=SURFACE, foreground=TEXT, font=FONT_SECTION)
    style.configure("PreviewTitle.TLabel", background=SURFACE, foreground=TEXT, font=FONT_TITLE)
    style.configure("PreviewSubtitle.TLabel", background=SURFACE, foreground=SECONDARY_TEXT, font=FONT_SMALL)
    style.configure("PanelHeading.TLabel", background=SURFACE, foreground=SECONDARY_TEXT, font=FONT_FIELD)
    style.configure("Status.TLabel", background=SURFACE, foreground=WARNING, font=FONT_FIELD)
    style.configure("Warning.TLabel", background=SURFACE, foreground=WARNING, font=FONT_SMALL)
    style.configure("Success.TLabel", background=SURFACE, foreground=PASS, font=FONT_FIELD)
    style.configure("Fail.TLabel", background=SURFACE, foreground=FAIL, font=FONT_FIELD)
    # ===== CUSTOMIZE: STATUS BADGES =====
    # CUSTOMIZE: Badge backgrounds encode workflow state; keep white text legible.
    style.configure("Completed.Badge.TLabel", background=PASS, foreground=TEXT, font=FONT_FIELD, padding=(8, 4))
    style.configure("Progress.Badge.TLabel", background=FOCUS, foreground=TEXT, font=FONT_FIELD, padding=(8, 4))
    style.configure("Pending.Badge.TLabel", background=WARNING, foreground=BACKGROUND, font=FONT_FIELD, padding=(8, 4))
    style.configure("Failed.Badge.TLabel", background=FAIL, foreground=TEXT, font=FONT_FIELD, padding=(8, 4))
    style.configure("Unknown.Badge.TLabel", background=BORDER, foreground=SECONDARY_TEXT, font=FONT_FIELD, padding=(8, 4))
    style.configure("TSeparator", background=BORDER)
    style.configure(
        "TLabelframe", background=SURFACE, bordercolor=BORDER, relief="solid", borderwidth=1
    )
    style.configure(
        "TLabelframe.Label", background=SURFACE, foreground=TEXT, font=FONT_SECTION
    )

    # ===== CUSTOMIZE: BUTTONS =====
    # CUSTOMIZE: Action is neutral navigation; Primary is green save/create work.
    style.configure(
        "TButton", background=RAISED_SURFACE, foreground=TEXT,
        bordercolor=BORDER, lightcolor=RAISED_SURFACE, darkcolor=RAISED_SURFACE,
        relief="flat", padding=BUTTON_PADDING, font=FONT_BODY,
    )
    style.configure("Action.TButton", background=RAISED_SURFACE, foreground=TEXT)
    style.configure("Section.TButton", background=RAISED_SURFACE, foreground=TEXT, padding=(12, 10))
    style.configure("Primary.TButton", background=SUCCESS, foreground=TEXT, font=FONT_FIELD)
    style.configure("Danger.TButton", background="#8B1E1E", foreground=TEXT, font=FONT_FIELD)
    style.map(
        "TButton",
        background=[("pressed", BORDER), ("active", "#2A2E39")],
        foreground=[("disabled", "#707070"), ("!disabled", TEXT)],
        bordercolor=[("focus", FOCUS), ("!focus", BORDER)],
    )
    style.map("Primary.TButton", background=[("pressed", SUCCESS_ACTIVE), ("active", SUCCESS_ACTIVE)])
    style.map("Danger.TButton", background=[("pressed", "#651515"), ("active", "#A32626")])

    # ===== CUSTOMIZE: INPUTS AND TABLES =====
    style.configure(
        "TEntry", fieldbackground=RAISED_SURFACE, foreground=TEXT,
        insertcolor=TEXT, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
        padding=(10, 8), relief="flat",
    )
    style.map("TEntry", bordercolor=[("focus", FOCUS)], fieldbackground=[("disabled", SURFACE)])
    style.configure(
        "TCombobox", fieldbackground=RAISED_SURFACE, background=RAISED_SURFACE,
        foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
        lightcolor=BORDER, darkcolor=BORDER, padding=(10, 8), relief="flat",
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", RAISED_SURFACE), ("focus", RAISED_SURFACE)],
        foreground=[("readonly", TEXT)], bordercolor=[("focus", FOCUS)],
        selectbackground=[("readonly", SELECTION)], selectforeground=[("readonly", TEXT)],
    )
    style.configure(
        "Treeview", background=RAISED_SURFACE, fieldbackground=RAISED_SURFACE,
        foreground=TEXT, bordercolor=BORDER, rowheight=TABLE_ROW_HEIGHT, relief="flat",
    )
    style.map(
        "Treeview",
        background=[("selected", SELECTION)],
        foreground=[("selected", TEXT), ("disabled", MUTED_TEXT)],
        bordercolor=[("focus", FOCUS), ("!focus", BORDER)],
    )
    style.configure(
        "Treeview.Heading", background=SURFACE, foreground=TEXT,
        bordercolor=BORDER, relief="flat", padding=(8, 8), font=FONT_FIELD,
    )
    style.map("Treeview.Heading", background=[("active", BORDER)])
    style.configure("TScrollbar", background=RAISED_SURFACE, troughcolor=BACKGROUND, bordercolor=BACKGROUND)

    # Classic Tk widgets (Text, Canvas, and popup listboxes) do not inherit ttk.
    root.option_add("*Font", FONT_BODY)
    root.option_add("*Background", BACKGROUND)
    root.option_add("*Foreground", TEXT)
    root.option_add("*selectBackground", SELECTION)
    root.option_add("*selectForeground", TEXT)
    root.option_add("*insertBackground", TEXT)
    root.option_add("*TCombobox*Listbox.background", RAISED_SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECTION)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    return style


def style_text_widget(widget: tk.Text, *, monospace: bool = False) -> None:
    """Give a classic Text/ScrolledText widget the same dark input styling."""
    widget.configure(
        background=RAISED_SURFACE,
        foreground=TEXT,
        insertbackground=TEXT,
        selectbackground=SELECTION,
        selectforeground=TEXT,
        highlightbackground=BORDER,
        highlightcolor=FOCUS,
        highlightthickness=1,
        relief="flat",
        font=FONT_MONO if monospace else FONT_SMALL,
    )
