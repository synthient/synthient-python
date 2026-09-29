"""Print the account that owns the API key, its scopes, and its remaining lookup credits.

SYNTHIENT_API_KEY=... uv run examples/account.py
"""

from datetime import timedelta

import synthient


def main() -> None:
    with synthient.Client() as client:
        account = client.account()

    quota = account.lookup_quota
    print(f"{account.first_name} {account.last_name} <{account.email}>")
    print("organization:", account.organization.name)
    print("scopes:", ", ".join(account.scopes))
    print(f"credits: {quota.credits} (resets in {timedelta(seconds=quota.resets_in)})")


if __name__ == "__main__":
    main()
