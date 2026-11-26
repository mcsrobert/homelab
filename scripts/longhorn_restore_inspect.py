"""Summarize a restored Longhorn volume and check every SQLite database in it.

longhorn_restore.py inspect runs this in a temporary Job as
python3 -c SOURCE VOLUME SCRATCH, with VOLUME mounted read-only. It only uses
the standard library of the plain Python inspection image.
"""

import os
import shutil
import sqlite3
import sys
from contextlib import closing

SQLITE_HEADER = b"SQLite format 3\0"


def summarize(root):
    """Count files and bytes per top-level entry and find SQLite databases."""
    sizes, databases = {}, []
    for directory, subdirectories, files in os.walk(root):
        subdirectories.sort()
        for file in sorted(files):
            path = os.path.join(directory, file)
            relative = os.path.relpath(path, root)
            top = relative.split(os.sep)[0]
            count, total = sizes.get(top, (0, 0))
            sizes[top] = (count + 1, total + os.lstat(path).st_size)
            if is_sqlite(path):
                databases.append(relative)
    return sizes, databases


def is_sqlite(path):
    if not os.path.isfile(path) or os.path.islink(path):
        return False
    with open(path, "rb") as handle:
        return handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER


def integrity_check(root, relative, copy):
    """Check a copy with its WAL or journal, so the restored files stay untouched."""
    os.makedirs(copy)
    try:
        for suffix in ("", "-wal", "-journal"):
            source = os.path.join(root, relative + suffix)
            if os.path.exists(source):
                shutil.copy(source, os.path.join(copy, "db" + suffix))
        try:
            with closing(sqlite3.connect(os.path.join(copy, "db"))) as connection:
                return [row[0] for row in connection.execute("PRAGMA integrity_check")]
        except sqlite3.Error as error:
            return [str(error)]
    finally:
        shutil.rmtree(copy)


def main(root, scratch):
    sizes, databases = summarize(root)
    for top, (count, total) in sorted(sizes.items()):
        print(f"FILES {top}: {count} file(s), {total} bytes")
    failed = 0
    for index, relative in enumerate(databases):
        result = integrity_check(root, relative, os.path.join(scratch, str(index)))
        failed += result != ["ok"]
        print(f"SQLITE {relative}: {'; '.join(result[:5])}")
    if not sizes:
        print("ERROR: volume contains no files")
    return 1 if failed or not sizes else 0


if __name__ == "__main__":
    volume, scratch = sys.argv[1:]
    sys.exit(main(volume, scratch))
