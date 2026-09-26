"""Domain availability checking over RDAP, with DNS and WHOIS as fallbacks.

ICANN retired WHOIS as the required registration-data protocol in January 2025 in
favour of RDAP (RFC 7482/9082), so RDAP is the primary source here: a registry's
RDAP service answers 404 for an unregistered domain and 200 for a registered one,
which is unambiguous. WHOIS responses are free text whose wording differs per
registry -- and registries' own legal notices contain words like "available", so
scanning the body for markers produces false positives. WHOIS is therefore only
consulted for the TLDs that publish no RDAP service, and only its first lines.
"""

import asyncio
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

try:
    import dns.resolver
except ImportError:  # dnspython is a hard dependency, but stay usable without it
    dns = None  # type: ignore[assignment]

from mcp.server.mcpserver import MCPServer

# MCP Python SDK 2.x renamed FastMCP to MCPServer and moved the transport's host and
# port from the constructor to run(); see the SDK migration guide.
mcp = MCPServer("Domain Availability Checker", version="0.2.0")

USER_AGENT = "mcp-domain-availability (+https://github.com/imprvhub/mcp-domain-availability)"
RDAP_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
IANA_WHOIS_HOST = "whois.iana.org"

HTTP_TIMEOUT = 15.0
WHOIS_TIMEOUT = 10.0
DNS_TIMEOUT = 5.0
MAX_CONCURRENT_CHECKS = 10

POPULAR_TLDS = ["com", "net", "org", "io", "ai", "app", "dev", "co", "xyz", "me", "info", "biz"]

COUNTRY_TLDS = [
    "us", "uk", "ca", "au", "de", "fr", "it", "es", "nl", "jp", "kr", "cn", "in",
    "br", "mx", "ar", "cl", "pe", "ru", "pl", "cz", "ch", "at", "se", "no",
    "dk", "fi", "be", "pt", "gr", "tr", "za", "eg", "ma", "ng", "ke",
]

NEW_TLDS = [
    "tech", "online", "site", "website", "store", "shop", "cloud", "digital",
    "blog", "news", "agency", "studio", "design", "media", "photo", "video",
    "music", "art", "gallery", "education", "university", "academy", "training",
    "business", "company", "solutions", "services", "consulting", "finance",
    "legal", "health", "medical", "travel", "hotel", "restaurant", "food",
    "coffee", "bar", "club", "sport", "fitness", "games", "fun", "live",
    "world", "global", "international", "network", "email", "mobile",
]

TLD_CATEGORIES = {
    "popular": POPULAR_TLDS,
    "country": COUNTRY_TLDS,
    "new": NEW_TLDS,
}

# dict.fromkeys keeps insertion order, so results are reproducible run to run;
# the previous list(set(...)) reordered the TLD list on every start.
ALL_TLDS: List[str] = list(dict.fromkeys(POPULAR_TLDS + COUNTRY_TLDS + NEW_TLDS))
TLD_CATEGORIES["all"] = ALL_TLDS

TLD_MIN_LENGTH = {
    "com": 2, "net": 2, "org": 2, "info": 2, "io": 2, "ai": 2,
    "de": 2, "fr": 2, "it": 2, "es": 2,
}

LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
TLD_RE = re.compile(r"^[a-z]{2,63}$")

# Matched against the first lines of a WHOIS response only. Registry legal notices
# further down the body mention "available", which is why the whole text is not scanned.
WHOIS_FREE_MARKERS = (
    "no match", "not found", "no entries found", "no data found", "nothing found",
    "status: free", "status: available", "not registered", "no object found",
    "domain is available", "is free",
)
WHOIS_TAKEN_MARKERS = ("domain name:", "domain:", "domain_name:", "registrar:", "registrant:")


def get_min_length_for_tld(tld: str) -> int:
    return TLD_MIN_LENGTH.get(tld, 3)


def is_valid_domain_name(base_name: str, tld: str) -> bool:
    if not base_name or len(base_name) > 63:
        return False
    if len(base_name) < get_min_length_for_tld(tld):
        return False
    if "--" in base_name:
        return False
    if not LABEL_RE.match(base_name):
        return False
    # The TLD was previously unchecked, so anything after the dot reached the
    # network layer as-is.
    return bool(TLD_RE.match(tld)) if tld else False


def clean_domain_name(domain: str) -> str:
    domain = domain.strip().lower()
    domain = re.sub(r"^[a-z]+://", "", domain)
    domain = domain.split("/")[0].split("?")[0]
    if domain.endswith("."):
        domain = domain[:-1]
    return domain


def extract_domain_parts(domain: str) -> Tuple[str, str]:
    domain = clean_domain_name(domain)
    # Tolerated for backwards compatibility: the tool used to require a --domain flag.
    domain = domain.replace("--domain", "").strip()
    if "." in domain:
        parts = domain.split(".")
        return ".".join(parts[:-1]), parts[-1]
    return domain, ""


class RdapBootstrap:
    """TLD -> RDAP base URL, from IANA's published bootstrap file."""

    TTL_SECONDS = 24 * 60 * 60

    def __init__(self) -> None:
        self._map: Optional[Dict[str, str]] = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    def _fetch(self) -> Dict[str, str]:
        request = urllib.request.Request(RDAP_BOOTSTRAP_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            data = json.load(response)
        mapping: Dict[str, str] = {}
        for entry in data.get("services", []):
            tlds, urls = entry[0], entry[1]
            if not urls:
                continue
            for tld in tlds:
                mapping[tld.lower()] = urls[0].rstrip("/")
        return mapping

    async def get(self) -> Dict[str, str]:
        async with self._lock:
            fresh = self._map is not None and (time.time() - self._fetched_at) < self.TTL_SECONDS
            if not fresh:
                try:
                    self._map = await asyncio.to_thread(self._fetch)
                    self._fetched_at = time.time()
                except Exception as exc:  # fall back to WHOIS/DNS for this run
                    print(f"RDAP bootstrap unavailable ({exc}); falling back to WHOIS and DNS.", file=sys.stderr, flush=True)
                    self._map = self._map or {}
            return self._map or {}

    async def server_for(self, tld: str) -> Optional[str]:
        return (await self.get()).get(tld)


bootstrap = RdapBootstrap()


def _rdap_status(base_url: str, domain: str) -> Optional[bool]:
    """True if registered, False if not, None if the registry did not say."""
    url = f"{base_url}/domain/{domain}"
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/rdap+json, application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        return None
    except Exception:
        return None


def _whois_query(query: str, server: str, timeout: float = WHOIS_TIMEOUT) -> Optional[str]:
    try:
        with socket.create_connection((server, 43), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall((query + "\r\n").encode("utf-8", "ignore"))
            chunks: List[bytes] = []
            total = 0
            while total < 65536:
                block = sock.recv(4096)
                if not block:
                    break
                chunks.append(block)
                total += len(block)
        return b"".join(chunks).decode("utf-8", "replace")
    except Exception:
        return None


_whois_server_cache: Dict[str, Optional[str]] = {}


def _whois_server_for(tld: str) -> Optional[str]:
    if tld in _whois_server_cache:
        return _whois_server_cache[tld]
    response = _whois_query(tld, IANA_WHOIS_HOST)
    server = None
    if response:
        # Match only a whois: line that actually carries a value on the same line.
        match = re.search(r"^whois:[^\S\n]+(\S+)[^\S\n]*$", response, re.MULTILINE | re.IGNORECASE)
        if match:
            server = match.group(1)
    _whois_server_cache[tld] = server
    return server


def _whois_status(domain: str, tld: str) -> Optional[bool]:
    """True if registered, False if not, None if undetermined."""
    server = _whois_server_for(tld)
    if not server:
        return None
    response = _whois_query(domain, server)
    if not response:
        return None
    head = "\n".join(response.strip().splitlines()[:6]).lower()
    if any(marker in head for marker in WHOIS_FREE_MARKERS):
        return False
    if any(marker in head for marker in WHOIS_TAKEN_MARKERS):
        return True
    return None


def _dns_resolves(domain: str) -> Optional[bool]:
    """True if the name resolves, False on NXDOMAIN, None if undetermined."""
    if dns is not None:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = DNS_TIMEOUT
        resolver.timeout = DNS_TIMEOUT
        for record_type in ("A", "NS"):
            try:
                resolver.resolve(domain, record_type)
                return True
            except dns.resolver.NXDOMAIN:
                return False
            except dns.resolver.NoAnswer:
                continue
            except Exception:
                return None
        return None
    try:
        socket.getaddrinfo(domain, 80)
        return True
    except socket.gaierror:
        return None  # absence of an address is not proof the name is unregistered
    except Exception:
        return None


async def check_single_domain(domain: str) -> Dict:
    """Resolve one fully qualified domain to available / taken / undetermined."""
    started = time.time()
    base_name, tld = extract_domain_parts(domain)
    normalized = f"{base_name}.{tld}" if tld else base_name

    if not is_valid_domain_name(base_name, tld):
        return {
            "domain": normalized,
            "available": False,
            "status": "invalid",
            "reason": (
                f'"{base_name}" is not a valid name for .{tld}'
                if tld
                else "a top-level domain is required, e.g. example.com"
            ),
            "check_time": "0s",
        }

    rdap_base = await bootstrap.server_for(tld)
    source = "rdap"
    registered: Optional[bool] = None

    if rdap_base:
        registered = await asyncio.to_thread(_rdap_status, rdap_base, normalized)

    if registered is None:
        source = "whois"
        registered = await asyncio.to_thread(_whois_status, normalized, tld)

    if registered is None:
        resolves = await asyncio.to_thread(_dns_resolves, normalized)
        if resolves is True:
            source, registered = "dns", True
        else:
            source = "dns"

    if registered is True:
        status, available = "taken", False
    elif registered is False:
        status, available = "available", True
    else:
        status, available = "undetermined", False

    result = {
        "domain": normalized,
        "available": available,
        "status": status,
        "checked_via": source,
        "check_time": f"{round(time.time() - started, 2)}s",
    }
    if status == "undetermined":
        result["reason"] = (
            f"No RDAP service is published for .{tld} and its WHOIS server did not give a "
            "conclusive answer. Check with a registrar before assuming it is free."
        )
    return result


async def _check_many(base_name: str, tlds: List[str]) -> List[Dict]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)

    async def one(tld: str) -> Optional[Dict]:
        if not is_valid_domain_name(base_name, tld):
            return None
        async with semaphore:
            return await check_single_domain(f"{base_name}.{tld}")

    results = await asyncio.gather(*(one(tld) for tld in tlds), return_exceptions=True)
    checked: List[Dict] = []
    for tld, result in zip(tlds, results):
        if isinstance(result, dict):
            checked.append(result)
        elif isinstance(result, BaseException):
            # stderr: stdout carries the JSON-RPC stream and must not be written to.
            print(f"Error checking {base_name}.{tld}: {result}", file=sys.stderr, flush=True)
    return checked


def _summarize(results: List[Dict]) -> Dict:
    available = sorted((r for r in results if r["status"] == "available"), key=lambda r: r["domain"])
    taken = sorted((r for r in results if r["status"] == "taken"), key=lambda r: r["domain"])
    undetermined = sorted((r for r in results if r["status"] == "undetermined"), key=lambda r: r["domain"])
    return {
        "available_domains": available,
        "unavailable_domains": taken,
        "undetermined_domains": undetermined,
        "total_checked": len(results),
        "check_summary": {
            "total_available": len(available),
            "total_unavailable": len(taken),
            "total_undetermined": len(undetermined),
            "popular_available": sum(
                1 for r in available if r["domain"].rsplit(".", 1)[-1] in POPULAR_TLDS
            ),
            "country_available": sum(
                1 for r in available if r["domain"].rsplit(".", 1)[-1] in COUNTRY_TLDS
            ),
            "new_tlds_available": sum(
                1 for r in available if r["domain"].rsplit(".", 1)[-1] in NEW_TLDS
            ),
        },
    }


@mcp.tool()
async def check_domain(domain: str) -> Dict:
    """Check whether a specific domain name is registered.

    Give a full domain such as "example.com". Availability comes from the registry's
    RDAP service where one exists, which is authoritative; for TLDs without RDAP the
    registry's WHOIS server and DNS are used, and the result may come back
    "undetermined" rather than guessing.

    Args:
        domain: The domain to check, e.g. "mysite.com". A bare name with no
            top-level domain is checked across the popular TLDs instead.
    """
    base_name, tld = extract_domain_parts(domain)
    if not base_name:
        return {"error": 'Provide a domain name, e.g. "mysite.com".'}

    if not tld:
        results = await _check_many(base_name, POPULAR_TLDS)
        if not results:
            return {
                "requested_domain": None,
                "error": (
                    f'"{base_name}" could not be checked against any popular TLD. '
                    f"It is {len(base_name)} character(s) long, below the minimum registries accept, "
                    "or it contains characters a domain label cannot hold."
                ),
            }
        return {
            "requested_domain": None,
            "note": f'No top-level domain was given, so "{base_name}" was checked across the popular TLDs.',
            **_summarize(results),
        }

    exact = await check_single_domain(f"{base_name}.{tld}")
    return {"requested_domain": exact, **_summarize([exact])}


@mcp.tool()
async def suggest_domains(name: str, category: str = "popular") -> Dict:
    """Check one name across many top-level domains and report which are free.

    Args:
        name: The second-level name to check, without a dot, e.g. "mysite".
        category: Which TLD set to check - "popular" (12 TLDs, the default),
            "country" (36), "new" (49) or "all" (roughly 95). Larger sets take
            proportionally longer.
    """
    base_name, existing_tld = extract_domain_parts(name)
    if not base_name:
        return {"error": 'Provide a name to check, e.g. "mysite".'}

    category = (category or "popular").strip().lower()
    tlds = TLD_CATEGORIES.get(category)
    if tlds is None:
        return {
            "error": f'Unknown category "{category}".',
            "valid_categories": sorted(TLD_CATEGORIES),
        }

    if existing_tld and existing_tld not in tlds:
        tlds = [existing_tld] + list(tlds)

    too_short = [tld for tld in tlds if not is_valid_domain_name(base_name, tld)]
    results = await _check_many(base_name, tlds)
    payload = {"base_name": base_name, "category": category, **_summarize(results)}
    if too_short:
        payload["skipped_tlds"] = {
            "reason": f'"{base_name}" is {len(base_name)} characters, below the minimum these TLDs accept',
            "tlds": sorted(too_short)[:20],
        }
    return payload


def run() -> None:
    """Entry point for the console script.

    Defaults to stdio, which is what desktop MCP clients use. Set MCP_TRANSPORT to
    "streamable-http" (or the older "sse") to serve over HTTP instead, with HOST and
    PORT controlling the bind address.
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport in ("http", "streamable-http", "sse"):
        kind = "sse" if transport == "sse" else "streamable-http"
        mcp.run(
            kind,
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8080")),
        )
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    run()
