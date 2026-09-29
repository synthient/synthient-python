"""Print what Synthient knows about an IP address. Each lookup costs one credit.

SYNTHIENT_API_KEY=... uv run examples/lookup_ip.py 8.8.8.8
"""

import sys

import synthient


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: lookup_ip.py <ip>")

    with synthient.Client() as client:
        try:
            info = client.lookup_ip(sys.argv[1])
        except synthient.APIError as error:
            if error.status_code == 402:
                sys.exit("out of lookup credits")
            raise

    print("ip:", info.ip)
    if info.network is not None:
        print(f"network: {info.network.isp} (AS{info.network.asn}, {info.network.type})")
    if info.location is not None:
        print(f"location: {info.location.city}, {info.location.country}")
    if info.intelligence is None:
        print("no intelligence available")
        return
    print("risk score:", info.intelligence.risk_score)
    print("categories:", ", ".join(info.intelligence.categories))
    for provider in info.intelligence.providers:
        last_seen = f"{provider.last_seen:%Y-%m-%d %H:%M:%S}"
        print(f"provider: {provider.name} ({provider.type}), last seen {last_seen}")


if __name__ == "__main__":
    main()
