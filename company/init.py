"""Initialize an Obsidian vault without model or Telegram credentials."""

import argparse
from pathlib import Path

from .vault import Vault


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path, default=Path("vault"))
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    args = parser.parse_args()
    Vault(args.vault, args.workspace)
    print(f"Vault initialized at {args.vault.resolve()}")


if __name__ == "__main__":
    main()
