import asyncio
import contextlib
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from hllrcon.connection import RconConnection
from hllrcon.rconv2 import AsyncHLLRconV2

if TYPE_CHECKING:
    from hllrcon.pooled.rcon import PooledRcon


class PooledRconWorker:
    """A worker for executing RCON commands in a pooled RCON client."""

    def __init__(self, host: str, port: int, password: str, pool: "PooledRcon") -> None:
        """Initializes a new pooled RCON worker.

        Parameters
        ----------
        host : str
            The hostname or IP address of the RCON server.
        port : int
            The port number of the RCON server.
        password : str
            The password for the RCON connection.
        pool : PooledRcon
            The pooled RCON client that manages this worker.
        """
        self.host = host
        self.port = port
        self.password = password
        self.pool = pool

        self._connection: asyncio.Future[RconConnection | AsyncHLLRconV2] | None = None
        self._busy = False
        self._disconnected = False
        self._version = 2  # Default to v2

    async def _get_connection(self, version: int = 2) -> RconConnection | AsyncHLLRconV2:
        """Get the RCON connection for this worker.

        A new connection will be established the first time this method is called.

        Raises
        ------
        HLLConnectionError
            The address and port could not be resolved.
        HLLConnectionRefusedError
            The server refused the connection.
        HLLAuthError
            The provided password is incorrect.

        Returns
        -------
        RconConnection | AsyncHLLRconV2
            The RCON connection for this worker.
        """
        if self._connection:
            return await asyncio.shield(self._connection)

        self._version = version
        self._connection = asyncio.Future()

        try:
            if version == 2:
                connection = AsyncHLLRconV2(self.host, self.port, self.password)
                await connection.connect()
            else:
                connection = await RconConnection.connect(
                    host=self.host,
                    port=self.port,
                    password=self.password,
                )
                if hasattr(connection, 'on_disconnect'):
                    connection.on_disconnect = self.on_disconnect
                else:
                    connection.on_disconnect = lambda: self.on_disconnect()

            self._connection.set_result(connection)
        except Exception as e:
            self._connection.set_exception(e)
            self.on_disconnect()
            raise
        else:
            return connection

    async def wait_until_connected(self) -> None:
        await self._get_connection(self._version)

    def disconnect(self) -> None:
        if self._connection:
            if self._connection.done():
                if (
                    not self._connection.cancelled()
                    and not self._connection.exception()
                ):
                    conn = self._connection.result()
                    if hasattr(conn, 'close'):
                        asyncio.create_task(conn.close())
                    else:
                        conn.disconnect()
            else:
                self._connection.cancel()

        self.on_disconnect()

    def is_busy(self) -> bool:
        """Check if the worker is currently busy executing a command.

        Returns
        -------
        bool
            True if the worker is busy, False otherwise.
        """
        return self._busy

    def is_connected(self) -> bool:
        """Check if the worker is connected to the RCON server.

        Returns
        -------
        bool
            True if the worker is connected, False otherwise.
        """
        return (
            self._connection is not None
            and not self._connection.cancelled()
            and self._connection.done()
            and not self._connection.exception()
            and self._connection.result().is_connected()
        )

    def is_disconnected(self) -> bool:
        """Check if the worker is disconnected from the RCON server.

        Returns
        -------
        bool
            True if the worker is disconnected, False otherwise.
        """
        return self._disconnected

    def on_disconnect(self) -> None:
        """Clean up the worker when the connection is lost."""
        self._busy = False
        self._disconnected = True
        with contextlib.suppress(ValueError):
            self.pool._workers.remove(self)

    @contextlib.asynccontextmanager
    async def connect(self, version: int = 2) -> AsyncGenerator[RconConnection | AsyncHLLRconV2, None]:
        self._busy = True
        try:
            connection = await self._get_connection(version)
            yield connection
        finally:
            self._busy = False

    async def execute(
        self,
        command: str,
        version: int = 2,
        body: str | dict[str, Any] = "",
    ) -> str:
        """Execute a command on the RCON server.

        Parameters
        ----------
        command : str
            The command to execute.
        version : int
            The version of the command to execute.
        body : str | dict[str, Any], optional
            The body of the command, by default an empty string.

        Returns
        -------
        str
            The response from the server.
        """
        async with self.connect(version) as connection:
            return await connection.execute(command, version, body)
