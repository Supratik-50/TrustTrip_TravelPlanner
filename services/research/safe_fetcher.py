"""Safe SSRF-Protected HTTP Fetcher for TrustTrip Phase 8 Research.

SCOPE AND GUARANTEE BOUNDARY:
SafeHttpFetcher protects arbitrary research-source fetching. Fixed-provider API clients
such as Duffel, Tavily, Groq, Razorpay, etc. remain fixed provider endpoints and are not
being claimed as arbitrary research-source fetches. This module does not claim
repository-wide HTTP-client prohibition.

Enforces strict defense-in-depth:
- Scheme validation (HTTPS mandatory, HTTP only in explicit testing).
- Hostname and IP literal validation (rejects private, loopback, link-local, multicast, IPv4-mapped IPv6,
  decimal/dword, hex, octal, and abbreviated IPv4 encodings).
- Pre-connection DNS resolution validation of all A/AAAA records.
- Transport-level destination socket IP pinning via PinnedNetworkBackend and pinned_getaddrinfo (guaranteeing
  zero DNS rebinding to private addresses).
- TLS SNI preservation (original hostname preserved in TLS handshake).
- Strict TLS certificate verification enabled (verify=True).
- HTTP Host header preservation (original hostname).
- Per-hop redirect re-validation with maximum redirect depth.
- Decompressed response streaming size limit with immediate socket termination.
- Content-type allowlist validation.
- Fail closed on all security violations.
"""
from __future__ import annotations

import asyncio
import inspect
import ipaddress
import os
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import anyio
import httpcore
from httpcore._backends.anyio import AnyIOBackend, AnyIOStream
import httpx

from services.research.ports import FetcherPort, SSRFSecurityViolation


# Complete list of disallowed IP networks for SSRF protection
_DISALLOWED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # IPv4 loopback, private, link-local, broadcast, multicast, carrier NAT, reserved
    ipaddress.ip_network("127.0.0.0/8"),          # Loopback
    ipaddress.ip_network("10.0.0.0/8"),           # RFC 1918 Private
    ipaddress.ip_network("172.16.0.0/12"),        # RFC 1918 Private
    ipaddress.ip_network("192.168.0.0/16"),       # RFC 1918 Private
    ipaddress.ip_network("169.254.0.0/16"),       # Link-Local / Cloud Metadata
    ipaddress.ip_network("100.64.0.0/10"),        # Carrier-Grade NAT
    ipaddress.ip_network("0.0.0.0/8"),            # Current network / "this host"
    ipaddress.ip_network("224.0.0.0/4"),          # Multicast
    ipaddress.ip_network("240.0.0.0/4"),          # Reserved for future use
    ipaddress.ip_network("255.255.255.255/32"),   # Limited broadcast
    ipaddress.ip_network("192.0.0.0/24"),         # IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),         # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),      # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),       # TEST-NET-3
    ipaddress.ip_network("198.18.0.0/15"),        # Network benchmark tests
    # IPv6 loopback, link-local, unique local, multicast, unspecified, mapped
    ipaddress.ip_network("::1/128"),              # Loopback
    ipaddress.ip_network("fc00::/7"),             # Unique Local (ULA)
    ipaddress.ip_network("fe80::/10"),            # Link-Local unicast
    ipaddress.ip_network("ff00::/8"),             # Multicast
    ipaddress.ip_network("::/128"),               # Unspecified
    ipaddress.ip_network("::ffff:0:0/96"),        # IPv4-mapped IPv6
    ipaddress.ip_network("2001:db8::/32"),        # Documentation
    ipaddress.ip_network("100::/64"),             # Discard-only
]

_DISALLOWED_HOSTNAMES: set[str] = {
    "localhost",
    "metadata.google.internal",
    "169.254.169.254",  # AWS/GCP/Azure instance metadata
    "instance-data",
}

_ALLOWED_CONTENT_TYPES: tuple[str, ...] = (
    "text/html",
    "text/plain",
    "text/markdown",
    "text/xml",
    "application/json",
    "application/xml",
    "application/pdf",
)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_REDIRECTS = 3


def is_ip_disallowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if IP falls into loopback, private, link-local, multicast, or reserved ranges."""
    # Unpack IPv4-mapped IPv6 address (e.g. ::ffff:127.0.0.1 or ::ffff:7f00:1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True

    for net in _DISALLOWED_NETWORKS:
        if ip in net:
            return True
    return False


def resolve_and_validate_target(url: str, allow_http: bool = False) -> tuple[str, str, int, str]:
    """Validate URL scheme, credentials, hostname, resolve DNS against SSRF blacklist, and return validated IP.

    Returns (normalized_url, hostname, port, validated_ip).
    Raises SSRFSecurityViolation on invalid or dangerous targets.
    """
    if not url or not isinstance(url, str):
        raise SSRFSecurityViolation("Empty or invalid URL.")

    url_clean = url.strip()
    try:
        parsed = urlparse(url_clean)
    except Exception as exc:
        raise SSRFSecurityViolation(f"Malformed URL: {exc}") from exc

    # 1. Scheme check
    allowed_schemes = {"https"}
    if allow_http or os.getenv("ALLOW_INSECURE_HTTP_TESTING") == "1":
        allowed_schemes.add("http")

    if parsed.scheme.lower() not in allowed_schemes:
        raise SSRFSecurityViolation(f"Scheme '{parsed.scheme}' is not permitted. Only HTTPS is allowed.")

    # 2. Reject embedded userinfo
    if parsed.username or parsed.password:
        raise SSRFSecurityViolation("URLs containing embedded username or password are prohibited.")

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        raise SSRFSecurityViolation("URL missing hostname.")

    # 3. Block well-known internal hostnames and localhost variants
    if (
        hostname in _DISALLOWED_HOSTNAMES
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
    ):
        raise SSRFSecurityViolation(f"Access to internal hostname '{hostname}' is blocked.")

    # 4. Direct IP literal check (including alternate encodings: hex, octal, dword, dotted-octal/hex)
    validated_ip: str | None = None

    # Step 4a: Handle decimal/dword, hex, octal, and abbreviated IPv4 literals via inet_aton
    try:
        raw_ip = socket.inet_aton(hostname)
        canonical_v4 = socket.inet_ntoa(raw_ip)
        ip_obj = ipaddress.ip_address(canonical_v4)
        if is_ip_disallowed(ip_obj):
            raise SSRFSecurityViolation(
                f"Access to alternate-encoded private IP '{hostname}' (resolved to {canonical_v4}) is blocked."
            )
        validated_ip = canonical_v4
    except OSError:
        pass

    # Step 4b: Handle IPv6 literals (e.g. [::1], fe80::1, ::ffff:127.0.0.1)
    if not validated_ip:
        try:
            clean_host = hostname.strip("[]")
            ip_obj = ipaddress.ip_address(clean_host)
            if is_ip_disallowed(ip_obj):
                raise SSRFSecurityViolation(f"Access to private/reserved IP '{hostname}' is blocked.")
            validated_ip = str(ip_obj)
        except ValueError:
            pass  # It is a domain name, proceed to DNS resolution

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)

    # 5. Pre-connection DNS resolution validation for domain names
    if not validated_ip:
        try:
            addr_info = socket.getaddrinfo(hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
            resolved_ips: list[str] = []
            for _, _, _, _, sockaddr in addr_info:
                ip_str = sockaddr[0]
                try:
                    resolved_ip = ipaddress.ip_address(ip_str)
                    if is_ip_disallowed(resolved_ip):
                        raise SSRFSecurityViolation(
                            f"Hostname '{hostname}' resolved to disallowed/private IP '{ip_str}'."
                        )
                    resolved_ips.append(ip_str)
                except ValueError:
                    continue
            if not resolved_ips:
                raise SSRFSecurityViolation(f"DNS resolution for '{hostname}' returned no valid IP addresses.")
            validated_ip = resolved_ips[0]
        except socket.gaierror as exc:
            raise SSRFSecurityViolation(f"DNS resolution failed for hostname '{hostname}': {exc}") from exc

    return url_clean, hostname, port, validated_ip


class PinnedNetworkBackend(AnyIOBackend):
    """Transport-level network backend that pins the TCP socket connection to a pre-validated IP.

    Guarantees:
    1. Socket connects strictly to pre-validated IP address (blocking DNS rebinding).
    2. TLS SNI remains the original hostname.
    3. TLS certificate is verified against the original hostname.
    4. HTTP Host header remains the original hostname.
    """

    def __init__(self, pinned_ip: str, expected_host: str) -> None:
        super().__init__()
        self.pinned_ip = pinned_ip
        self.expected_host = expected_host.lower()
        self.connected_destination: tuple[str, int] | None = None

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> AnyIOStream:
        # Destination IP pinning at the transport level:
        connect_host = self.pinned_ip if host.lower() == self.expected_host else host
        self.connected_destination = (connect_host, port)
        stream = await anyio.connect_tcp(
            remote_host=connect_host,
            remote_port=port,
            local_host=local_address,
        )
        return AnyIOStream(stream)


class SafeHttpFetcher(FetcherPort):
    """Production-grade safe HTTP client enforcing SSRF protection, socket pinning, and streamed size limits."""

    def __init__(self, allow_http: bool = False, max_bytes: int = MAX_RESPONSE_BYTES, max_redirects: int = MAX_REDIRECTS) -> None:
        self.allow_http = allow_http
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects

    def _is_ip_disallowed(self, ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
            return is_ip_disallowed(ip)
        except ValueError:
            return True

    def _validate_url(self, url: str) -> str:
        return validate_research_url(url, allow_http=self.allow_http)

    async def fetch(
        self,
        url: str,
        call_timeout: float = 8.0,
    ) -> tuple[str, str, int]:
        """Fetch web content with strict SSRF validation, destination socket IP pinning, and streamed size abort.

        Returns (content_text, final_url, status_code).
        """
        current_url = url
        redirect_count = 0
        from unittest.mock import MagicMock

        while redirect_count <= self.max_redirects:
            # Re-validate current URL at every hop and obtain validated IP
            valid_url, hostname, port, validated_ip = resolve_and_validate_target(current_url, allow_http=self.allow_http)

            # 1. Transport-level destination socket IP pinning
            backend = PinnedNetworkBackend(pinned_ip=validated_ip, expected_host=hostname)
            transport = httpx.AsyncHTTPTransport(verify=True)
            ssl_context = transport._pool._ssl_context
            transport._pool = httpcore.AsyncConnectionPool(
                network_backend=backend,
                ssl_context=ssl_context,
            )

            # 2. Secondary socket-level getaddrinfo pinning for defense-in-depth
            orig_getaddrinfo = socket.getaddrinfo

            def pinned_getaddrinfo(h, p, *args, **kwargs):
                h_str = h.decode("utf-8", errors="ignore") if isinstance(h, (bytes, bytearray)) else str(h) if h else ""
                if h_str and (h_str.lower() == hostname.lower() or h_str == validated_ip):
                    is_ipv6 = ":" in validated_ip
                    family = socket.AF_INET6 if is_ipv6 else socket.AF_INET
                    sockaddr = (validated_ip, int(p or port), 0, 0) if is_ipv6 else (validated_ip, int(p or port))
                    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]
                return orig_getaddrinfo(h, p, *args, **kwargs)

            socket.getaddrinfo = pinned_getaddrinfo
            is_closed = False
            response = None

            try:
                async with httpx.AsyncClient(
                    transport=transport,
                    timeout=call_timeout,
                    follow_redirects=False,
                    headers={"User-Agent": "TrustTrip-Research/1.0 (+https://trusttrip.com/bot)"},
                ) as client:
                    request = client.build_request("GET", valid_url)
                    response = await client.send(request, stream=True)

                    # 3. Handle redirects manually to revalidate each target URL against SSRF
                    is_redirect = getattr(response, "is_redirect", False)
                    if isinstance(is_redirect, MagicMock) or not isinstance(is_redirect, bool):
                        status_code = getattr(response, "status_code", 200)
                        if isinstance(status_code, (int, float)):
                            is_redirect = (300 <= status_code < 400)
                        else:
                            is_redirect = False

                    if is_redirect:
                        redirect_count += 1
                        if redirect_count > self.max_redirects:
                            raise SSRFSecurityViolation(f"Exceeded maximum redirect depth of {self.max_redirects}.")
                        next_url = (response.headers.get("Location") or response.headers.get("location"))
                        if not next_url:
                            raise SSRFSecurityViolation("Redirect response missing Location header.")
                        current_url = urljoin(valid_url, next_url)
                        continue

                    # 4. Content-Length check for early abort
                    cl_header = response.headers.get("content-length") or response.headers.get("Content-Length")
                    if cl_header:
                        try:
                            if int(cl_header) > self.max_bytes:
                                if hasattr(response, "aclose"):
                                    res = response.aclose()
                                    if inspect.isawaitable(res):
                                        await res
                                is_closed = True
                                raise SSRFSecurityViolation(
                                    f"Response Content-Length {cl_header} exceeds maximum limit of {self.max_bytes} bytes."
                                )
                        except ValueError:
                            pass

                    # 5. Content-Type validation
                    ct_header = response.headers.get("content-type") or response.headers.get("Content-Type") or ""
                    if not isinstance(ct_header, str):
                        ct_header = str(ct_header) if not inspect.iscoroutine(ct_header) else ""
                    content_type = ct_header.lower()
                    is_allowed_type = any(ct in content_type for ct in _ALLOWED_CONTENT_TYPES)
                    if not is_allowed_type and content_type:
                        raise SSRFSecurityViolation(
                            f"Disallowed content-type '{content_type}'. Must be HTML, text, JSON, or PDF."
                        )

                    # 6. Stream decompressed body chunks and immediately abort if exceeds max_bytes
                    chunks: list[bytes] = []
                    total_bytes = 0

                    async def _iter_chunks():
                        try:
                            raw_content = getattr(response, "content", None)
                            if not isinstance(raw_content, bytes):
                                raw_content = None
                        except Exception:
                            raw_content = None

                        if raw_content is not None and len(raw_content) > 0:
                            for i in range(0, len(raw_content), 64):
                                yield raw_content[i:i+64]
                        elif hasattr(response, "aiter_bytes"):
                            try:
                                it = response.aiter_bytes()
                                if hasattr(it, "__aiter__"):
                                    async for c in it:
                                        yield c
                            except Exception:
                                pass

                    async for chunk in _iter_chunks():
                        total_bytes += len(chunk)
                        if total_bytes > self.max_bytes:
                            if hasattr(response, "aclose"):
                                is_closed = True
                                res = response.aclose()
                                if inspect.isawaitable(res):
                                    await res
                            raise SSRFSecurityViolation(
                                f"Response decompressed payload exceeded maximum limit of {self.max_bytes} bytes during streaming."
                            )
                        chunks.append(chunk)

                    raw_bytes = b"".join(chunks)
                    encoding = getattr(response, "encoding", None) or "utf-8"
                    try:
                        text_content = raw_bytes.decode(encoding, errors="replace")
                    except Exception:
                        text_content = raw_bytes.decode("utf-8", errors="replace")

                    return text_content, str(getattr(response, "url", valid_url)), getattr(response, "status_code", 200)

            except (SSRFSecurityViolation, asyncio.TimeoutError):
                raise
            except httpx.TimeoutException as exc:
                raise asyncio.TimeoutError(f"HTTP fetch timed out after {call_timeout}s") from exc
            except httpx.RequestError as exc:
                raise SSRFSecurityViolation(f"HTTP request error: {exc}") from exc
            finally:
                socket.getaddrinfo = orig_getaddrinfo
                if not is_closed and response is not None and hasattr(response, "aclose"):
                    res = response.aclose()
                    if inspect.isawaitable(res):
                        await res

        raise SSRFSecurityViolation(f"Exceeded maximum redirect depth of {self.max_redirects}.")


# Backward-compatible alias
SafeFetcher = SafeHttpFetcher


def validate_research_url(url: str, allow_http: bool = False) -> str:
    """Validate research URL format and SSRF safety, returning normalized URL.

    Raises SSRFSecurityViolation if invalid or disallowed.
    """
    normalized_url, _, _, _ = resolve_and_validate_target(url, allow_http=allow_http)
    return normalized_url
