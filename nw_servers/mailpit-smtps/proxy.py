from __future__ import annotations

import asyncio
import os
import ssl


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    backend_host, backend_port = os.getenv("SMTP_BACKEND", "mailpit:1025").rsplit(":", 1)
    try:
        backend_reader, backend_writer = await asyncio.open_connection(backend_host, int(backend_port))
        upstream = asyncio.create_task(relay(client_reader, backend_writer))
        downstream = asyncio.create_task(relay(backend_reader, client_writer))
        done, pending = await asyncio.wait({upstream, downstream}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
        backend_writer.close()
        await backend_writer.wait_closed()
    except (ConnectionError, OSError, ValueError):
        client_writer.close()
        await client_writer.wait_closed()


async def main() -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(os.environ["SMTP_TLS_CERT"], os.environ["SMTP_TLS_KEY"])
    server = await asyncio.start_server(handle, "0.0.0.0", 1465, ssl=context, limit=65536)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
