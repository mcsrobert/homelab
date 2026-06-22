#!/usr/bin/env python3

import argparse
import base64
import getpass
import hashlib
import os
import sys
from passlib.hash import pbkdf2_sha512


def main():
    """Generate a PBKDF2-SHA512 hash for a given password.

    Can also generate a hash in the format used by mosquitto (with $7$ prefix).
    """
    args = parse_args()
    password = get_valid_password(args.password)

    if args.mosquitto:
        print(hash_mosquitto(password, rounds=args.rounds))
    else:
        print(pbkdf2_sha512.using(rounds=args.rounds).hash(password))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Securely generate password hashes"
    )
    parser.add_argument(
        "password",
        nargs="?",
        help="The password to hash (omit to prompt securely via stdin)",
    )
    rounds_default = 310000
    parser.add_argument(
        "--rounds",
        type=int,
        default=rounds_default,
        help=f"Number of iterations (default: {rounds_default})",
    )
    parser.add_argument(
        "--mosquitto",
        action="store_true",
        help="Generate a mosquitto $7$ hash instead of passlib format",
    )
    return parser.parse_args()


def get_valid_password(password):
    if password is None:
        if not sys.stdin.isatty():
            password = sys.stdin.read()
        else:
            password = getpass.getpass("Enter password: ")

    password = password.strip()
    if not password:
        print("Error: password cannot be empty", file=sys.stderr)
        sys.exit(1)

    return password


def hash_mosquitto(password, rounds=101):
    """Generate mosquitto $7$ hash directly with a 12-byte salt."""
    salt = os.urandom(12)
    hash_bytes = hashlib.pbkdf2_hmac(
        "sha512",
        password.encode("utf-8"),
        salt,
        rounds,
        dklen=64,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(hash_bytes).decode("ascii")
    return f"$7${rounds}${salt_b64}${hash_b64}"


if __name__ == "__main__":
    main()
