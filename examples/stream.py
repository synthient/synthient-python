"""Print proxy sightings from the real-time proxies feed until interrupted, reconnecting
whenever the server closes the stream. It needs an API key with the PROXY_FIREHOSE scope.

    SYNTHIENT_API_KEY=... uv run examples/stream.py
"""

import contextlib
import time

import synthient


def main() -> None:
    with synthient.Client() as client, contextlib.suppress(KeyboardInterrupt):
        while True:
            try:
                with client.proxies.stream() as events:
                    for event in events:
                        at = event.timestamp.isoformat() if event.timestamp else "-"
                        print(at, event.ip, event.provider, event.type, event.country_code)
            except synthient.SynthientError as error:
                print("stream:", error)

            # The server closes each stream after about 30 minutes. Pause before
            # reconnecting so repeated failures stay under the connection rate limit.
            time.sleep(2)


if __name__ == "__main__":
    main()
