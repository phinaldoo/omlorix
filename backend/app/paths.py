from pathlib import Path


APP_PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_PACKAGE_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent

DATA_DIR = APP_PACKAGE_DIR / "data"
LOG_DIR = APP_PACKAGE_DIR / "logs"
