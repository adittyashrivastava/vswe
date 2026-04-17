"""Container entrypoint for VSWE training/compute jobs.

Reads configuration from environment variables, auto-detects and installs
the script's dependencies, then executes the script.

Environment variables (set by the JobScheduler):
    VSWE_SCRIPT_PATH        — path to the Python script (relative to workspace)
    VSWE_WORKSPACE_PATH     — workspace directory on EFS
    VSWE_EFS_MOUNT          — EFS mount point (e.g. /mnt/efs)
    VSWE_JOB_ID             — unique job identifier
    VSWE_CHECKPOINT_DIR     — where to save checkpoints
    VSWE_FRAMEWORK          — detected framework (pytorch/tensorflow/jax/unknown)
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import time


# ---------------------------------------------------------------------------
# Well-known stdlib modules (not on PyPI) — skip these during dep detection
# ---------------------------------------------------------------------------

_STDLIB_MODULES: set[str] = {
    "abc", "argparse", "ast", "asyncio", "base64", "bisect", "builtins",
    "calendar", "cmath", "codecs", "collections", "colorsys", "concurrent",
    "configparser", "contextlib", "copy", "csv", "ctypes", "dataclasses",
    "datetime", "decimal", "difflib", "dis", "email", "enum", "errno",
    "faulthandler", "filecmp", "fileinput", "fnmatch", "fractions",
    "ftplib", "functools", "gc", "getopt", "getpass", "glob", "gzip",
    "hashlib", "heapq", "hmac", "html", "http", "imaplib", "importlib",
    "inspect", "io", "ipaddress", "itertools", "json", "keyword",
    "linecache", "locale", "logging", "lzma", "mailbox", "math",
    "mimetypes", "mmap", "multiprocessing", "netrc", "numbers", "operator",
    "os", "pathlib", "pdb", "pickle", "platform", "plistlib", "pprint",
    "profile", "pstats", "py_compile", "queue", "quopri", "random", "re",
    "readline", "reprlib", "resource", "rlcompleter", "runpy", "sched",
    "secrets", "select", "selectors", "shelve", "shlex", "shutil", "signal",
    "site", "smtplib", "socket", "socketserver", "sqlite3", "ssl", "stat",
    "statistics", "string", "struct", "subprocess", "sys", "sysconfig",
    "syslog", "tempfile", "termios", "test", "textwrap", "threading",
    "time", "timeit", "token", "tokenize", "tomllib", "trace", "traceback",
    "tracemalloc", "tty", "turtle", "types", "typing", "unicodedata",
    "unittest", "urllib", "uuid", "venv", "warnings", "wave", "weakref",
    "webbrowser", "xml", "xmlrpc", "zipfile", "zipimport", "zlib",
    "_thread", "__future__",
}

# Map common import names to their PyPI package names
_IMPORT_TO_PYPI: dict[str, str] = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "attr": "attrs",
    "dotenv": "python-dotenv",
    "git": "gitpython",
    "dateutil": "python-dateutil",
}


def extract_imports(script_path: str) -> list[str]:
    """Parse a Python script and return top-level import names."""
    try:
        with open(script_path, encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, FileNotFoundError) as exc:
        print(f"[runner] Warning: could not parse {script_path}: {exc}")
        return []

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])

    return sorted(imports)


def resolve_dependencies(imports: list[str]) -> list[str]:
    """Convert import names to pip-installable package names.

    Skips stdlib modules, the vswe_checkpoint package itself, and anything
    already importable in the current environment.
    """
    packages: list[str] = []
    for name in imports:
        # Skip stdlib
        if name in _STDLIB_MODULES:
            continue
        # Skip our own package
        if name == "vswe_checkpoint":
            continue
        # Check if already installed
        try:
            __import__(name)
            continue
        except ImportError:
            pass
        # Map to PyPI name if needed
        pypi_name = _IMPORT_TO_PYPI.get(name, name)
        packages.append(pypi_name)

    return packages


def install_dependencies(packages: list[str]) -> None:
    """pip install the given packages."""
    if not packages:
        print("[runner] No additional dependencies to install.")
        return

    print(f"[runner] Installing dependencies: {', '.join(packages)}")
    start = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--no-cache-dir"] + packages,
        capture_output=True,
        text=True,
    )
    elapsed = time.time() - start

    if result.returncode != 0:
        print(f"[runner] pip install failed (exit {result.returncode}):")
        print(result.stderr)
        # Don't abort — the script might still work if some packages are optional
    else:
        print(f"[runner] Dependencies installed in {elapsed:.1f}s")


def main() -> None:
    script_path = os.environ.get("VSWE_SCRIPT_PATH", "")
    workspace_path = os.environ.get("VSWE_WORKSPACE_PATH", "")
    efs_mount = os.environ.get("VSWE_EFS_MOUNT", "/mnt/efs")
    job_id = os.environ.get("VSWE_JOB_ID", "unknown")

    print(f"[runner] VSWE Training Runner — job {job_id}")
    print(f"[runner] Script: {script_path}")
    print(f"[runner] Workspace: {workspace_path}")

    # VSWE_SCRIPT_PATH is an absolute path resolved by the API server.
    # Both the API and job containers mount EFS at the same path (/efs),
    # so the path works directly.
    full_script_path = script_path

    if not os.path.isfile(full_script_path):
        print(f"[runner] ERROR: Script not found: {full_script_path}")
        sys.exit(1)

    print(f"[runner] Resolved script: {full_script_path}")

    # Check for a requirements.txt alongside the script
    script_dir = os.path.dirname(full_script_path)
    req_file = os.path.join(script_dir, "requirements.txt")
    if os.path.isfile(req_file):
        print(f"[runner] Found requirements.txt, installing from it...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--no-cache-dir", "-r", req_file],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"[runner] Warning: pip install -r requirements.txt failed:")
            print(result.stderr)
    else:
        # Auto-detect and install dependencies from imports
        imports = extract_imports(full_script_path)
        print(f"[runner] Detected imports: {imports}")
        packages = resolve_dependencies(imports)
        install_dependencies(packages)

    # Run the script
    print(f"[runner] Executing: {full_script_path}")
    print("=" * 60)

    # Set working directory to script's directory
    os.chdir(script_dir)

    # Execute the script in a subprocess so it gets a clean process
    result = subprocess.run(
        [sys.executable, full_script_path],
        cwd=script_dir,
    )

    print("=" * 60)
    print(f"[runner] Script exited with code {result.returncode}")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
