"""List the newest exports of the proxies feed, then download the most recent daily export
to proxies.parquet and verify its checksum. It needs an API key with the PROXY_FEEDS scope.

    SYNTHIENT_API_KEY=... uv run examples/download_export.py
"""

import hashlib
import sys
from pathlib import Path

import synthient


def main() -> None:
    with synthient.Client() as client:
        daily = None
        for export in client.proxies.iter_exports():
            rows = f"{export.row_count:>10} rows"
            print(f"{export.id:<16} {export.kind:<6} {rows} {export.size_bytes:>12} bytes")
            if export.kind == "daily":
                daily = export
                break
        if daily is None:
            sys.exit("no daily export found")

        digest = hashlib.sha256()
        with (
            client.proxies.download(daily.id) as response,
            Path("proxies.parquet").open("wb") as file,
        ):
            for chunk in response.iter_bytes():
                file.write(chunk)
                digest.update(chunk)

    if digest.hexdigest() != daily.checksum:
        sys.exit("checksum mismatch")
    print("saved", daily.id, "to proxies.parquet")


if __name__ == "__main__":
    main()
