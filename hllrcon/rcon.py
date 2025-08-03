import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from typing_extensions import override
from hllrcon.client import RconClient
from hllrcon.connection import RconConnection
from .rconv2 import AsyncHLLRconV2

class Rcon(RconClient):
    def __init__(self, host: str, port: int, password: str) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.password = password
        self._connection: asyncio.Future[RconConnection | AsyncHLLRconV2] | None = None

    async def _get_connection(self) -> RconConnection | AsyncHLLRconV2:
        if (
            self._connection
            and self._connection.done()
            and (
                self._connection.exception()
                or not self._connection.result().is_connected()
            )
        ):
            self._connection = None
        if self._connection is None:
            self._connection = asyncio.Future()
            try:
                if hasattr(self, '_v2_version') and self._v2_version:
                    connection = AsyncHLLRconV2(self.host, self.port, self.password)
                    await connection.connect()
                else:
                    connection = await RconConnection.connect(
                        host=self.host,
                        port=self.port,
                        password=self.password,
                    )
                self._connection.set_result(connection)
            except Exception as e:
                self._connection.set_exception(e)
                self._connection = None
                raise
            else:
                return connection
        elif not self._connection.done():
            return await asyncio.shield(self._connection)
        else:
            return self._connection.result()

    @override
    def is_connected(self) -> bool:
        return (
            self._connection is not None
            and not self._connection.cancelled()
            and self._connection.done()
            and not self._connection.exception()
            and (
                (hasattr(self._connection.result(), 'is_connected') and self._connection.result().is_connected())
                or (hasattr(self._connection.result(), '_v2_version') and self._connection.result()._v2_version)
            )
        )

    @override
    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[None]:
        await self._get_connection()
        try:
            yield
        finally:
            self.disconnect()

    @override
    async def wait_until_connected(self) -> None:
        await self._get_connection()

    @override
    def disconnect(self) -> None:
        if self._connection:
            if self._connection.done():
                if not self._connection.exception():
                    if hasattr(self._connection.result(), 'close'):
                        asyncio.run_coroutine_threadsafe(self._connection.result().close(), asyncio.get_event_loop())
                    else:
                        self._connection.result().disconnect()
            else:
                self._connection.cancel()
        self._connection = None

    @override
    async def execute(
        self,
        command: str,
        version: int,
        body: str | dict[str, Any] = "",
    ) -> str:
        self._v2_version = version == 2
        connection = await self._get_connection()
        if version == 2:
            if isinstance(body, (dict, list)):
                body = json.dumps(body)
            return await connection.execute(command, body)
        else:
            return await connection.execute(command)

    def set_version(self, version: int) -> None:
        """Set the RCON version (1 or 2) for subsequent connections."""
        self._v2_version = version == 2
