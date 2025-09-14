from pathlib import Path

RES_DIR = Path(".") / "res"
"""资源路径"""
DATA_DIR = Path(".") / "data"
"""数据资源路径"""
FONT_DIR = RES_DIR / "font"
"""字体资源路径"""
HTML_DIR = RES_DIR / "html"
"""html资源路径"""
IMG_DIR = RES_DIR / "img"
"""图片资源路径"""
RECORD_DIR = RES_DIR / "record"
"""声音资源路径"""
TEXT_DIR = RES_DIR / "text"
"""文本资源路径"""

CONFIG_DIR = Path(".") / "data" / "config"
"""插件设置路径"""
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

PLUGIN_DATA_DIR = Path(".") / "data" / "plugins"
"""插件数据路径"""
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

DB_DIR = Path(".") / "data" / "sql"
"""数据库路径"""
DB_DIR.mkdir(parents=True, exist_ok=True)

TEMP_DIR = Path(".") / "data" / "temp"
"""临时文件路径"""
TEMP_DIR.mkdir(parents=True, exist_ok=True)
