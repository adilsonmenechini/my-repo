"""Console output helpers."""

from __future__ import annotations

import sys


class C:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[0;36m"
    BLUE = "\033[0;34m"
    BOLD = "\033[1m"
    END = "\033[0m"


def info(text: str) -> None:
    print(f"{C.CYAN}[*]{C.END} {text}")


def success(text: str) -> None:
    print(f"{C.GREEN}[+]{C.END} {text}")


def warning(text: str) -> None:
    print(f"{C.YELLOW}[!]{C.END} {text}")


def error(text: str) -> None:
    print(f"{C.RED}[-]{C.END} {text}", file=sys.stderr)


def title(text: str) -> None:
    print()
    print(f"{C.BOLD}{C.BLUE}{text}{C.END}")
    print("─" * 70)
