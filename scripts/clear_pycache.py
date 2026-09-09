import shutil
from pathlib import Path

PLUGIN_DIRS = [
    Path(__file__).resolve().parent.parent / "ATRI",
    Path(__file__).resolve().parent.parent / "plugins",
    Path(__file__).resolve().parent.parent / "ATRI-LK-plugin" / "plugins",
]


def clear_pycache() -> int:
    """Remove every __pycache__ directory under the plugin directory."""
    removed = 0
    for PLUGIN_DIR in PLUGIN_DIRS:
        if not PLUGIN_DIR.is_dir():
            print(f"Plugin directory not found: {PLUGIN_DIR}")
            continue
        for pycache_dir in PLUGIN_DIR.rglob("__pycache__"):
            if pycache_dir.is_dir():
                shutil.rmtree(pycache_dir)
                removed += 1
                print(f"Removed: {pycache_dir}")
    print(f"Removed {removed} __pycache__ director{'y' if removed == 1 else 'ies'}.")
    return removed


if __name__ == "__main__":
    clear_pycache()
