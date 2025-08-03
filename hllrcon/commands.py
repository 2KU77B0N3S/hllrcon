import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, Literal, ParamSpec, TypeVar

from pydantic import BaseModel

from hllrcon.data import layers
from hllrcon.exceptions import HLLCommandError, HLLMessageError
from hllrcon.responses import (
    GetAdminLogResponse,
    GetBannedWordsResponse,
    GetCommandDetailsResponse,
    GetCommandsResponse,
    GetMapRotationResponse,
    GetPlayerResponse,
    GetPlayersResponse,
    GetServerConfigResponse,
    GetServerSessionResponse,
)

P = ParamSpec("P")
ModelT = TypeVar("ModelT", bound=BaseModel)

GameMode = Literal["Warfare", "Offensive", "Skirmish"]

def cast_response_to_model(
    model_type: type[ModelT],
) -> Callable[
    [Callable[P, Coroutine[Any, Any, str]]],
    Callable[P, Coroutine[Any, Any, ModelT]],
]:
    def decorator(
        func: Callable[P, Coroutine[Any, Any, str]],
    ) -> Callable[P, Coroutine[Any, Any, ModelT]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> ModelT:
            result = await func(*args, **kwargs)
            return model_type.model_validate_json(result)

        return wrapper

    return decorator

def cast_response_to_bool(
    status_codes: set[int],
) -> Callable[
    [Callable[P, Coroutine[Any, Any, None]],
    Callable[P, Coroutine[Any, Any, bool]],
]:
    def decorator(
        func: Callable[P, Coroutine[Any, Any, None]],
    ) -> Callable[P, Coroutine[Any, Any, bool]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> bool:
            try:
                await func(*args, **kwargs)
            except HLLCommandError as e:
                if e.status_code in status_codes:
                    return False
                raise
            return True

        return wrapper

    return decorator

class RconCommands(ABC):
    @abstractmethod
    async def execute(
        self,
        command: str,
        version: int,
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

    async def add_admin(self, player_id: str, admin_group: str, comment: str, version: int = 2) -> None:
        """Add a player to an admin group.

        Groups are defined in the server's configuration file. The group determines
        whether the player is able to enter admin camera and kick or ban players.

        Parameters
        ----------
        player_id : str
            The ID of the player to add as an admin.
        admin_group : str
            The group to add the player to.
        comment : str
            A comment to identify the admin. This is usually the name of the player.
        version: int = 2
            The RCON version to use.

        """
        if version == 2:
            await self.execute(
                "AddAdmin",
                version,
                {"PlayerId": player_id, "AdminGroup": admin_group, "Comment": comment},
            )
        else:
            await self.execute("adminadd", version, f"{player_id} {admin_group} {comment}")

    async def remove_admin(self, player_id: str, version: int = 2) -> None:
        """Remove a player from their admin group.

        Parameters
        ----------
        player_id : str
            The ID of the player to remove as an admin.
        version: int = 2
            The RCON version to use.

        """
        if version == 2:
            await self.execute(
                "RemoveAdmin",
                version,
                {"PlayerId": player_id},
            )
        else:
            await self.execute("admindel", version, player_id)

    @cast_response_to_model(GetAdminLogResponse)
    async def get_admin_log(self, seconds_span: int, filter_: str | None = None, version: int = 2) -> str:
        """Retrieve admin logs from the server.

        Parameters
        ----------
        seconds_span : int
            The number of seconds to look back in the logs.
        filter_ : str | None
            A filter string to apply to the logs, by default None.
        version: int = 2
            The RCON version to use.

        """
        if seconds_span < 0:
            msg = "seconds_span must be a non-negative integer"
            raise ValueError(msg)

        if version == 2:
            return await self.execute(
                "GetAdminLog",
                version,
                {
                    "LogBackTrackTime": seconds_span,
                    "Filters": filter_ or "",
                },
            )
        else:
            minutes = seconds_span // 60
            return await self.execute("showlog", version, str(minutes))

    async def change_map(self, map_name: str | layers.Layer, version: int = 2) -> None:
        """Change the current map to the specified map.

        Map changes are not immediate. Instead, a 60 second countdown is started.

        Parameters
        ----------
        map_name : str | Layer
            The name of the map to change to.
        version: int = 2
            The RCON version to use.

        """
        if version == 2:
            await self.execute(
                "ChangeMap",
                version,
                {
                    "MapName": str(map_name),
                },
            )
        else:
            await self.execute("map", version, str(map_name))

    async def get_available_sector_names(
        self,
        version: int = 2
    ) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
        """Retrieve a list of all sector names available on the current map.

        Returns
        -------
        tuple[list[str], list[str], list[str], list[str], list[str]]
            A list of sector names available on the current map.

        """
        if version == 2:
            details = await self.get_command_details("SetSectorLayout", version=version)
            parameters = details.dialogue_parameters
            if not parameters or not all(
                p.id.startswith("Sector_") for p in parameters[:5]
            ):
                msg = "Received unexpected response from server."
                raise HLLMessageError(msg)
            return (
                parameters[0].value_member.split(","),
                parameters[1].value_member.split(","),
                parameters[2].value_member.split(","),
                parameters[3].value_member.split(","),
                parameters[4].value_member.split(","),
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def set_sector_layout(
        self,
        sector1: str,
        sector2: str,
        sector3: str,
        sector4: str,
        sector5: str,
        version: int = 2
    ) -> None:
        """Immediately restart the map with the given sector layout.

        Parameters
        ----------
        sector1 : str
            The name of the first sector.
        sector2 : str
            The name of the second sector.
        sector3 : str
            The third sector.
        sector4 : str
            The fourth sector.
        sector5 : str
            The fifth sector.
        version: int = 2
            The RCON version to use.

        """
        if version == 2:
            await self.execute(
                "SetSectorLayout",
                version,
                {
                    "Sector_1": sector1,
                    "Sector_2": sector2,
                    "Sector_3": sector3,
                    "Sector_4": sector4,
                    "Sector_5": sector5,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def add_map_to_rotation(
        self,
        map_name: str | layers.Layer,
        index: int,
        version: int = 2
    ) -> None:
        """Add a map to the map rotation.

        Parameters
        ----------
        map_name : str | Layer
            The name of the map to add.
        index : int
            The index in the rotation to add the map at.
        version: int = 2
            The RCON version to use.

        """
        if version == 2:
            await self.execute(
                "AddMapToRotation",
                version,
                {
                    "MapName": str(map_name),
                    "Index": index,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def remove_map_from_rotation(self, index: int, version: int = 2) -> None:
        """Remove a map from the map rotation.

        Parameters
        ----------
        index : int
            The index of the map to remove from the rotation.
        version: int = 2
            The RCON version to use.

        """
        if version == 2:
            await self.execute(
                "RemoveMapFromRotation",
                version,
                {
                    "Index": index,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def add_map_to_sequence(
        self,
        map_name: str | layers.Layer,
        index: int,
        version: int = 2
    ) -> None:
        """Add a map to the map sequence.

        Parameters
        ----------
        map_name : str | Layer
            The name of the map to add.
        index : int
            The index in the sequence to add the map at.
        version: int = 2
            The RCON version to use.

        """
        if version == 2:
            await self.execute(
                "AddMapToSequence",
                version,
                {
                    "MapName": str(map_name),
                    "Index": index,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def remove_map_from_sequence(self, index: int, version: int = 2) -> None:
        """Remove a map from the map sequence.

        Parameters
        ----------
        index : int
            The index of the map to remove from the sequence.
        version: int = 2
            The RCON version to use.

        """
        if version == 2:
            await self.execute(
                "RemoveMapFromSequence",
                version,
                {
                    "Index": index,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def set_map_shuffle_enabled(self, *, enabled: bool, version: int = 2) -> None:
        """Enable or disable map shuffling of the map sequence.

        Parameters
        ----------
        enabled : bool
            Whether to enable or disable map shuffling.
        version: int = 2
            The RCON version to use.

        """
        if version == 2:
            await self.execute(
                "ShuffleMapSequence",
                version,
                {
                    "Enable": enabled,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def move_map_in_sequence(self, old_index: int, new_index: int, version: int = 2) -> None:
        """Move a map in the map sequence.

        Parameters
        ----------
        old_index : int
            The current index of the map in the sequence.
        new_index : int
            The new index to move the map to in the sequence.
        version: int = 2
            The RCON version to use.

        """
        if version == 2:
            await self.execute(
                "MoveMapInSequence",
                version,
                {
                    "CurrentIndex": old_index,
                    "NewIndex": new_index,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def get_available_maps(self, version: int = 2) -> list[str]:
        """Retrieve a list of all maps available on the server.

        Returns
        -------
        list[str]
            A list of map names available on the server.

        """
        if version == 2:
            details = await self.get_command_details("AddMapToRotation", version=version)
            parameters = details.dialogue_parameters
            if not parameters or parameters[0].id != "MapName":
                msg = "Received unexpected response from server."
                raise HLLMessageError(msg)
            return parameters[0].value_member.split(",")
        else:
            resp = await self.execute("get maps", version)
            # Parse v1 response (assume comma separated or lines)
            return [m.strip() for m in resp.split('\n') if m.strip()]

    @cast_response_to_model(GetCommandsResponse)
    async def get_commands(self, version: int = 2) -> str:
        """Retrieve a description of all the commands available on the server.

        Returns
        -------
        GetAllCommandsResponse
            A response containing a list of all commands available on the server.

        """
        if version == 2:
            return await self.execute("GetDisplayableCommands", version)
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def set_team_switch_cooldown(self, minutes: int, version: int = 2) -> None:
        """Set the cooldown for switching teams.

        Parameters
        ----------
        minutes : int
            The number of minutes to set the cooldown to. Set to 0 for no cooldown.

        """
        if version == 2:
            await self.execute(
                "SetTeamSwitchCooldown",
                version,
                {
                    "TeamSwitchTimer": minutes,
                },
            )
        else:
            await self.execute("setteamswitchcooldown", version, str(minutes))

    async def set_max_queued_players(self, num: int, version: int = 2) -> None:
        """Set the maximum number of players that can be queued for the server.

        Parameters
        ----------
        num : int
            The maximum number of players that can be queued. Must be between 0 and 6.

        """
        if version == 2:
            await self.execute(
                "SetMaxQueuedPlayers",
                version,
                {
                    "MaxQueuedPlayers": num,
                },
            )
        else:
            await self.execute("setmaxqueuedplayers", version, str(num))

    async def set_idle_kick_duration(self, minutes: int, version: int = 2) -> None:
        """Set the duration for kicking players for idling.

        Parameters
        ----------
        minutes : int
            The number of minutes a player can be idle for before being kicked.
            Set to 0 to disable.

        """
        if version == 2:
            await self.execute(
                "SetIdleKickDuration",
                version,
                {
                    "IdleTimeoutMinutes": minutes,
                },
            )
        else:
            await self.execute("setkickidletime", version, str(minutes))

    async def set_welcome_message(self, message: str, version: int = 2) -> None:
        """Set the welcome message for the server.

        The welcome message is displayed to players on the deployment screen and briefly
        when they first spawn in. The message will be briefly shown again when updated.

        Parameters
        ----------
        message : str
            The welcome message to set.

        """
        if version == 2:
            await self.execute(
                "SendServerMessage",
                version,
                {
                    "Message": message,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    @cast_response_to_model(GetPlayerResponse)
    async def get_player(self, player_id: str, version: int = 2) -> str:
        """Retrieve detailed information about a player currently on the server.

        Parameters
        ----------
        player_id : str
            The ID of the player to retrieve information about.
        version: int = 2
            The RCON version to use.

        Returns
        -------
        GetPlayerResponse
            Information about the player.

        """
        if version == 2:
            return await self.execute(
                "GetServerInformation",
                version,
                {"Name": "player", "Value": player_id},
            )
        else:
            resp = await self.execute("playerinfo", version, player_id)
            # Parse v1 response
            lines = resp.split('\n')
            d = {}
            for line in lines:
                if ':' in line:
                    key, value = line.split(':', 1)
                    d[key.strip()] = value.strip()
            # Map to GetPlayerResponse fields
            team_map = {"Axis": 0, "Allies": 1, "None": -1}
            # Role map (example, add full in data.py)
            role_map = {"Rifleman": 0, "Assault": 1, # ... add all roles
            }
            score = d.get("Score", "0 0 0 0").split()
            position = d.get("Position", "(0, 0, 0)").strip('() ').split(', ')
            player_data = {
                "Name": d.get("Name"),
                "ClanTag": "",  # v1 may not have
                "Id": d.get("SteamID"),
                "Platform": "steam",
                "EosId": "",
                "Level": int(d.get("Level", "0")),
                "Team": team_map.get(d.get("Team", "None"), -1),
                "Role": role_map.get(d.get("Role", 0), 0),
                "Platoon": d.get("Unit"),
                "Kills": int(d.get("Kills", "0")),
                "Deaths": int(d.get("Deaths", "0")),
                "ScoreData": {
                    "Combat": int(score[0] if score else 0),
                    "Offense": int(score[1] if len(score) >1 else 0),
                    "Defense": int(score[2] if len(score) >2 else 0),
                    "Support": int(score[3] if len(score) >3 else 0),
                },
                "Loadout": d.get("Loadout"),
                "WorldPosition": {
                    "X": float(position[0] if position else 0),
                    "Y": float(position[1] if len(position) >1 else 0),
                    "Z": float(position[2] if len(position) >2 else 0),
                },
            }
            return GetPlayerResponse.model_validate_json(json.dumps(player_data))

    @cast_response_to_model(GetPlayersResponse)
    async def get_players(self, version: int = 2) -> str:
        """Retrieve detailed information about all players currently on the server.

        This is equivalent to calling `get_player` for each player on the server.

        Returns
        -------
        GetPlayersResponse
            Information about all players.

        """
        if version == 2:
            return await self.execute(
                "GetServerInformation",
                version,
                {"Name": "players", "Value": ""},
            )
        else:
            resp = await self.execute("get playerids", version)
            # Parse v1 response
            players = []
            for line in resp.split('\n'):
                if ':' in line:
                    name, pid = line.split(':', 1)
                    players.append({
                        "name": name.strip(),
                        "iD": pid.strip(),
                        "platform": "steam"
                    })
            return GetPlayersResponse(players=players).model_validate_json(json.dumps({"players": players}))

    @cast_response_to_model(GetMapRotationResponse)
    async def get_map_rotation(self, version: int = 2) -> str:
        """Retrieve the current map rotation of the server.

        Returns
        -------
        GetMapRotationResponse
            The current map rotation of the server.

        """
        if version == 2:
            return await self.execute(
                "GetServerInformation",
                version,
                {"Name": "maprotation", "Value": ""},
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    @cast_response_to_model(GetMapRotationResponse)
    async def get_map_sequence(self, version: int = 2) -> str:
        """Retrieve the current map sequence of the server.

        Returns
        -------
        GetMapRotationResponse
            The current map sequence of the server.

        """
        if version == 2:
            return await self.execute(
                "GetServerInformation",
                version,
                {"Name": "mapsequence", "Value": ""},
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    @cast_response_to_model(GetServerSessionResponse)
    async def get_server_session(self, version: int = 2) -> str:
        """Retrieve information about the current server session.

        Returns
        -------
        GetServerSessionResponse
            Information about the current server session.

        """
        if version == 2:
            return await self.execute(
                "GetServerInformation",
                version,
                {"Name": "session", "Value": ""},
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    @cast_response_to_model(GetServerConfigResponse)
    async def get_server_config(self, version: int = 2) -> str:
        """Retrieve the server configuration.

        Returns
        -------
        GetServerConfigResponse
            The server configuration.

        """
        if version == 2:
            return await self.execute(
                "GetServerInformation",
                version,
                {"Name": "serverconfig", "Value": ""},
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    @cast_response_to_model(GetBannedWordsResponse)
    async def get_banned_words(self, version: int = 2) -> str:
        """Retrieve the list of banned words on the server.

        Returns
        -------
        GetBannedWordsResponse
            The list of banned words on the server.

        """
        if version == 2:
            return await self.execute(
                "GetServerInformation",
                version,
                {"Name": "bannedwords", "Value": ""},
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def broadcast(self, message: str, version: int = 2) -> None:
        """Broadcast a message to all players on the server.

        Broadcast messages are displayed top-left on the screen for all players.

        Parameters
        ----------
        message : str
            The message to broadcast to all players.

        """
        if version == 2:
            await self.execute(
                "ServerBroadcast",
                version,
                {
                    "Message": message,
                },
            )
        else:
            await self.execute("broadcast", version, message)

    async def set_high_ping_threshold(self, ms: int, version: int = 2) -> None:
        """Set the ping threshold for players.

        If a player's ping exceeds this threshold, they will be kicked from the server.

        Parameters
        ----------
        ms : int
            The ping threshold in milliseconds. Set to 0 to disable.

        """
        if version == 2:
            await self.execute(
                "SetHighPingThreshold",
                version,
                {
                    "HighPingThresholdMs": ms,
                },
            )
        else:
            await self.execute("sethighping", version, str(ms))

    @cast_response_to_model(GetCommandDetailsResponse)
    async def get_command_details(self, command: str, version: int = 2) -> str:
        """Retrieve detailed information about a specific command.

        Parameters
        ----------
        command : str
            The name of the command to retrieve information about.

        Returns
        -------
        GetCommandDetailsResponse
            Information about the command, including its parameters and description.

        """
        if version == 2:
            return await self.execute("GetClientReferenceData", version, command)
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def message_player(self, player_id: str, message: str, version: int = 2) -> None:
        """Send a message to a specific player on the server.

        The message will be displayed in a box in the top right corner of the player's
        screen.

        Parameters
        ----------
        player_id : str
            The ID of the player to send the message to.
        message : str
            The message to send to the player.

        """
        if version == 2:
            await self.execute(
                "MessagePlayer",
                version,
                {
                    "Message": message,
                    "PlayerId": player_id,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    @cast_response_to_bool({500})
    async def kill_player(self, player_id: str, message: str | None = None, version: int = 2) -> None:
        """Kill a specific player on the server.

        Parameters
        ----------
        player_id : str
            The ID of the player to kill.
        message : str | None
            The reason for killing the player. This will be displayed to the player, by
            default None.

        Returns
        -------
        bool
            Whether the player was successfully killed. If the player is not on the
            server or already dead, this will return `False`.

        """
        if version == 2:
            await self.execute(
                "PunishPlayer",
                version,
                {
                    "PlayerId": player_id,
                    "Reason": message,
                },
            )
        else:
            await self.execute("punish", version, f"{player_id} {message or ''}")

    @cast_response_to_bool({400})
    async def kick_player(self, player_id: str, message: str, version: int = 2) -> None:
        """Kick a specific player from the server.

        Parameters
        ----------
        player_id : str
            The ID of the player to kick.
        message : str
            The reason for kicking the player. This will be displayed to the player.

        Returns
        -------
        bool
            Whether the player was successfully kicked. If the player is not on the
            server, this will return `False`.

        """
        if version == 2:
            await self.execute(
                "KickPlayer",
                version,
                {
                    "PlayerId": player_id,
                    "Reason": message,
                },
            )
        else:
            await self.execute("kick", version, f"{player_id} {message}")

    async def ban_player(
        self,
        player_id: str,
        reason: str,
        admin_name: str,
        duration_hours: int | None = None,
        version: int = 2
    ) -> None:
        """Ban a specific player from the server.

        Parameters
        ----------
        player_id : str
            The ID of the player to ban.
        reason : str
            The reason for banning the player. This will be displayed to the player.
        admin_name : str
            The name of the admin performing the ban.
        duration_hours : int | None, optional
            The duration of the ban in hours. If `None`, the player will be permanently
            banned. Defaults to `None`.

        """
        if version == 2:
            if duration_hours:
                await self.execute(
                    "TemporaryBanPlayer",
                    version,
                    {
                        "PlayerId": player_id,
                        "Duration": duration_hours,
                        "Reason": reason,
                        "AdminName": admin_name,
                    },
                )
            else:
                await self.execute(
                    "PermanentBanPlayer",
                    version,
                    {
                        "PlayerId": player_id,
                        "Reason": reason,
                        "AdminName": admin_name,
                    },
                )
        else:
            if duration_hours:
                await self.execute("tempban", version, f"{player_id} {duration_hours} {reason} {admin_name}")
            else:
                await self.execute("permaban", version, f"{player_id} {reason} {admin_name}")

    @cast_response_to_bool({400})
    async def remove_temporary_ban(self, player_id: str, version: int = 2) -> None:
        """Remove a temporary ban from a player.

        Parameters
        ----------
        player_id : str
            The ID of the player to remove the temporary ban from.

        Returns
        -------
        bool
            Whether the player was successfully unbanned. If the player is not
            temporarily banned, this will return `False`.

        """
        if version == 2:
            await self.execute(
                "RemoveTemporaryBan",
                version,
                {
                    "PlayerId": player_id,
                },
            )
        else:
            await self.execute("pardontempban", version, player_id)

    @cast_response_to_bool({400})
    async def remove_permanent_ban(self, player_id: str, version: int = 2) -> None:
        """Remove a permanent ban from a player.

        Parameters
        ----------
        player_id : str
            The ID of the player to remove the permanent ban from.

        Returns
        -------
        bool
            Whether the player was successfully unbanned. If the player is not
            permanently banned, this will return `False`.

        """
        if version == 2:
            await self.execute(
                "RemovePermanentBan",
                version,
                {
                    "PlayerId": player_id,
                },
            )
        else:
            await self.execute("pardonpermaban", version, player_id)

    async def set_auto_balance(self, enabled: bool, version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "SetAutoBalance",
                version,
                {
                    "EnableAutoBalance": enabled,
                },
            )
        else:
            await self.execute("setautobalanceenabled", version, "on" if enabled else "off")

    async def set_auto_balance_threshold(self, player_threshold: int, version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "SetAutoBalanceThreshold",
                version,
                {
                    "AutoBalanceThreshold": player_threshold,
                },
            )
        else:
            await self.execute("setautobalancethreshold", version, str(player_threshold))

    async def set_vote_kick(self, enabled: bool, version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "SetVoteKick",
                version,
                {
                    "Enabled": enabled,
                },
            )
        else:
            await self.execute("setvotekickenabled", version, "on" if enabled else "off")

    async def reset_vote_kick_threshold(self, version: int = 2) -> None:
        if version == 2:
            await self.execute("ResetKickThreshold", version)
        else:
            await self.execute("resetvotekickthreshold", version)

    async def set_vote_kick_threshold(self, thresholds: list[tuple[int, int]], version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "SetVoteKickThreshold",
                version,
                {
                    "ThresholdValue": ",".join([f"{p},{v}" for p, v in thresholds]),
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def add_banned_words(self, words: list[str], version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "AddBannedWords",
                version,
                {
                    "BannedWords": ",".join(words),
                },
            )
        else:
            await self.execute("addprofanity", version, " ".join(words))

    async def remove_banned_words(self, words: list[str], version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "RemoveBannedWords",
                version,
                {
                    "BannedWords": ",".join(words),
                },
            )
        else:
            await self.execute("removeprofanity", version, " ".join(words))

    async def add_vip_player(self, player_id: str, description: str, version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "AddVipPlayer",
                version,
                {
                    "PlayerId": player_id,
                    "Description": description,
                },
            )
        else:
            await self.execute("vipadd", version, f"{player_id} {description}")

    async def remove_vip_player(self, player_id: str, version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "RemoveVipPlayer",
                version,
                {
                    "PlayerId": player_id,
                },
            )
        else:
            await self.execute("vipdel", version, player_id)

    async def set_match_timer(self, game_mode: GameMode, minutes: int, version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "SetMatchTimer",
                version,
                {
                    "GameMode": game_mode,
                    "MatchLength": minutes,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def remove_match_timer(self, game_mode: GameMode, version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "RemoveMatchTimer",
                version,
                {
                    "GameMode": game_mode,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def set_warmup_timer(self, game_mode: GameMode, minutes: int, version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "SetWarmupTimer",
                version,
                {
                    "GameMode": game_mode,
                    "WarmupLength": minutes,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def remove_warmup_timer(self, game_mode: GameMode, version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "RemoveWarmupTimer",
                version,
                {
                    "GameMode": game_mode,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")

    async def set_dynamic_weather_toggle(self, map_id: str, enable: bool, version: int = 2) -> None:
        if version == 2:
            await self.execute(
                "SetMapWeatherToggle",
                version,
                {
                    "MapId": map_id,
                    "Enable": enable,
                },
            )
        else:
            raise NotImplementedError("v1 not supported for this method")
