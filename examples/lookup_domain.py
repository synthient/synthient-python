"""Print the honeypot traffic Synthient has observed for a domain. Each lookup costs one
credit.

    SYNTHIENT_API_KEY=... uv run examples/lookup_domain.py example.com
"""

import sys

import synthient


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: lookup_domain.py <domain>")

    with synthient.Client() as client:
        domain = client.lookup_domain(sys.argv[1], limit=5)

    print("domain:", domain.domain, domain.status)
    print(f"events: {domain.stats.events_24h} in 24h, {domain.stats.total_events_30d} in 30d")
    print(f"unique IPs: {domain.unique_ips.value_24h} in 24h, {domain.unique_ips.value_30d} in 30d")
    for subdomain in domain.top_subdomains:
        print(f"subdomain: {subdomain.subdomain} ({subdomain.count} events)")
    for event in domain.recent_events:
        at = f"{event.timestamp:%Y-%m-%d %H:%M:%S}"
        print(f"event: {at} {event.source_ip_masked} -> {event.target_subdomain}:{event.port}")


if __name__ == "__main__":
    main()
