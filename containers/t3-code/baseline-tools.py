"""Print the image's baseline tools as tool=version pairs.

The list comes from MISE_TOOLS in the Dockerfile and the versions from the
repository's mise.toml, read independently of the image build.
"""

import re
import sys
import tomllib
from pathlib import Path

here = Path(__file__).parent
dockerfile = (here / "Dockerfile").read_text()
tools = re.search(r'^ARG MISE_TOOLS="([^"]+)"$', dockerfile, re.M)[1].split()
with open(here / "../../mise.toml", "rb") as file:
    pins = tomllib.load(file)["tools"]
missing = [tool for tool in tools if not isinstance(pins.get(tool), str)]
if missing:
    sys.exit(f"mise.toml does not pin {', '.join(missing)} to a plain version")
print(" ".join(f"{tool}={pins[tool]}" for tool in tools))
