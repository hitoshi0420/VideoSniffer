"""GUI 主题样式定义"""

from enum import Enum


class Theme(Enum):
    DARK = "dark"
    LIGHT = "light"


# 暗色主题
DARK_THEME = {
    "bg_primary": "#1a1a2e",
    "bg_secondary": "#16213e",
    "bg_card": "#0f3460",
    "bg_input": "#1a1a3e",
    "text_primary": "#e0e0e0",
    "text_secondary": "#a0a0b0",
    "text_muted": "#707080",
    "accent": "#e94560",
    "accent_hover": "#ff6b81",
    "success": "#2ecc71",
    "warning": "#f39c12",
    "error": "#e74c3c",
    "info": "#3498db",
    "border": "#2a2a4a",
    "progress_bg": "#1a1a3e",
    "progress_fill": "#e94560",
    "scrollbar_bg": "#16213e",
    "scrollbar_fg": "#0f3460",
    "status_waiting": "#707080",
    "status_downloading": "#3498db",
    "status_merging": "#f39c12",
    "status_completed": "#2ecc71",
    "status_failed": "#e74c3c",
    "status_paused": "#f39c12",
    "status_cancelled": "#95a5a6",
}

# 亮色主题
LIGHT_THEME = {
    "bg_primary": "#f5f5f5",
    "bg_secondary": "#ffffff",
    "bg_card": "#e8e8e8",
    "bg_input": "#ffffff",
    "text_primary": "#2c2c2c",
    "text_secondary": "#555555",
    "text_muted": "#999999",
    "accent": "#e94560",
    "accent_hover": "#c0392b",
    "success": "#27ae60",
    "warning": "#e67e22",
    "error": "#c0392b",
    "info": "#2980b9",
    "border": "#dddddd",
    "progress_bg": "#e0e0e0",
    "progress_fill": "#e94560",
    "scrollbar_bg": "#f0f0f0",
    "scrollbar_fg": "#cccccc",
    "status_waiting": "#999999",
    "status_downloading": "#2980b9",
    "status_merging": "#e67e22",
    "status_completed": "#27ae60",
    "status_failed": "#c0392b",
    "status_paused": "#e67e22",
    "status_cancelled": "#7f8c8d",
}

_current_theme: dict = DARK_THEME


def get_theme() -> dict:
    return _current_theme


def set_theme(theme_name: str):
    global _current_theme
    if theme_name == "light":
        _current_theme = LIGHT_THEME
    else:
        _current_theme = DARK_THEME


def get(name: str) -> str:
    return _current_theme.get(name, "")


def status_color(status: str) -> str:
    key = f"status_{status}"
    return _current_theme.get(key, _current_theme["text_muted"])
