"""Bounded CONNECT-only proxy: validate every DNS answer, connect the literal public IP."""

from __future__ import annotations

import asyncio
import socket
from urllib.parse import urlsplit

from .safe_http import public_ip, public_url


async def resolve_public(host, resolver=None):
    resolve = resolver or asyncio.get_running_loop().getaddrinfo
    answers = await resolve(host, 443, type=socket.SOCK_STREAM)
    addresses = {answer[4][0] for answer in answers}
    if not addresses or not all(public_ip(ip) for ip in addresses):
        raise ValueError("Non-public destination")
    return sorted(addresses, key=lambda ip: (":" in ip, ip))[0]


def connect_host(line):
    method, authority, version = line.split()
    parsed = urlsplit("https://" + authority)
    if method != "CONNECT" or version != "HTTP/1.1" or parsed.port != 443 or parsed.path:
        raise ValueError("Only HTTPS CONNECT443 is supported")
    return urlsplit(public_url("https://" + authority)).hostname


class Proxy:
    def __init__(self):
        self.slots = asyncio.Semaphore(24)

    async def handle(self, reader, writer):
        remote = None
        established = False
        try:
            async with self.slots, asyncio.timeout(20):
                header = await reader.readuntil(b"\r\n\r\n")
                if len(header) > 8192:
                    raise ValueError("Header too large")
                host = connect_host(header.split(b"\r\n", 1)[0].decode("ascii"))
                ip = await resolve_public(host)
                upstream, remote = await asyncio.open_connection(ip, 443)
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
                established = True
                remaining = 8 * 1024 * 1024

                async def pump(source, target):
                    nonlocal remaining
                    while chunk := await source.read(16384):
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise ValueError("Tunnel budget exceeded")
                        target.write(chunk)
                        await target.drain()

                tasks = [
                    asyncio.create_task(pump(reader, remote)),
                    asyncio.create_task(pump(upstream, writer)),
                ]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            if not established:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        finally:
            if remote:
                remote.close()
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def main():
    server = await asyncio.start_server(Proxy().handle, "0.0.0.0", 8080, limit=8192)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
