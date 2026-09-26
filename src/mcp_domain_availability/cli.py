#!/usr/bin/env python3
"""Command line wrapper, handy for checking the server's logic without an MCP client."""

import asyncio
import json
import sys

from mcp_domain_availability.main import check_single_domain, suggest_domains


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--domain"]
    if not args:
        print("Usage: mcp-domain-availability-cli <domain-or-name> [tld-category]")
        print("Examples:")
        print("  mcp-domain-availability-cli example.com")
        print("  mcp-domain-availability-cli mysite popular")
        sys.exit(1)

    target = args[0]
    if "." in target:
        result = asyncio.run(check_single_domain(target))
    else:
        category = args[1] if len(args) > 1 else "popular"
        result = asyncio.run(suggest_domains(target, category))

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") != "invalid" and "error" not in result else 1)


if __name__ == "__main__":
    main()
