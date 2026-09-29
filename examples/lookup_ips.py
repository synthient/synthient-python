"""Look up several IP addresses in one batch request and print the risk score and
categories of each.

    SYNTHIENT_API_KEY=... uv run examples/lookup_ips.py 8.8.8.8 1.1.1.1
"""

import sys

import synthient


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: lookup_ips.py <ip>...")

    with synthient.Client() as client:
        results = client.lookup_ips(sys.argv[1:])

    for result in results:
        if result.intelligence is None:
            print(f"{result.ip!s:<39} no intelligence available")
            continue
        categories = ", ".join(result.intelligence.categories)
        print(f"{result.ip!s:<39} risk {result.intelligence.risk_score:>3}  {categories}")


if __name__ == "__main__":
    main()
