"""Download the newest day of the geolocation dataset as a MaxMind DB to geolocation.mmdb
and verify its checksum. It needs an API key with the GEOLOCATION_FEED scope.

    SYNTHIENT_API_KEY=... uv run examples/download_dataset.py
"""

import hashlib
import sys
from pathlib import Path

import synthient


def main() -> None:
    with synthient.Client() as client:
        meta = client.geolocation.export_meta("latest", format="mmdb")
        # "latest" can move to a new day between calls, so download the day it named.
        day = meta.date.date()
        digest = hashlib.sha256()
        with (
            client.geolocation.download(day, format="mmdb") as response,
            Path("geolocation.mmdb").open("wb") as file,
        ):
            for chunk in response.iter_bytes():
                file.write(chunk)
                digest.update(chunk)

    if digest.hexdigest() != meta.checksum:
        sys.exit("checksum mismatch")
    print(f"saved {day} ({meta.size} bytes) to geolocation.mmdb")


if __name__ == "__main__":
    main()
