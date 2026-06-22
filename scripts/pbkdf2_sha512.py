#!/usr/bin/env python3

import sys
import argparse
import getpass
from passlib.hash import pbkdf2_sha512


def get_and_validate_password(pwd):
    if pwd is not None:
        pwd = pwd.strip()
    elif not sys.stdin.isatty():
        pwd = sys.stdin.read().strip()
    else:
        pwd = getpass.getpass("Enter password: ")

    if not pwd:
        print("Error: password cannot be empty", file=sys.stderr)
        sys.exit(1)

    return pwd


def main():
    parser = argparse.ArgumentParser(
        description="Securely generate a PBKDF2-SHA512 crypt hash"
    )
    parser.add_argument(
        "password",
        nargs="?",
        help="The password to hash (omit to prompt securely via stdin)"
    )
    parser.add_argument(
        "--salt",
        help="Optional custom salt string"
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=310000,
        help="Number of iterations (default: 310000)"
    )

    args = parser.parse_args()
    password = get_and_validate_password(args.password)
    salt = args.salt.encode() if args.salt else None
    print(pbkdf2_sha512.using(rounds=args.rounds, salt=salt).hash(password))


if __name__ == "__main__":
    main()
