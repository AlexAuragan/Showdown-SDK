import json
from typing import TYPE_CHECKING

from python_showdown.classes.pokemon.moves import AvailableMove
from python_showdown.classes.pokemon.pokemon import (
    EnemyPokemon,
    PartyPokemon,
    Stats,
    Unknown,
)
from python_showdown.classes.pokemon.stats import Status

from .utils import parse_formats

if TYPE_CHECKING:
    from .client import Client


class LogHandler:

    def _resolve_enemy(self, client: Client, pokemon_id: str) -> EnemyPokemon | None:
        if not client.battle_player_id:
            return None
        player, _species = pokemon_id.split(": ", 1)
        if player.startswith(client.battle_player_id):
            return None
        return client.combat_handler.battle_state.get_enemy_pokemon(
            pokemon_id, not_found_ok=True
        )

    def _resolve_self(
        self, client: Client, pokemon_id: str
    ) -> PartyPokemon | None:

        if not client.battle_player_id:
            return None
        try:
            player, _species = pokemon_id.split(": ", 1)
        except ValueError:
            return None
        if not player.startswith(client.battle_player_id):
            return None
        state = client.combat_handler.battle_state
        return next((p for p in state.team if p.id == pokemon_id), None)

    @staticmethod
    def _parse_hp(raw: str) -> tuple[int, bool]:
        raw = raw.strip()
        if raw == "fnt":
            return 0, True
        fainted = raw.endswith("fnt")
        head = raw.split()[0]
        if "/" not in head:
            # "0 fnt" with no slash.
            return 0, True
        curr_str, _max_str = head.split("/", 1)
        return int(curr_str), fainted

    def _handle_item_reveal(self, client: Client, line: str) -> None:
        parts = line.split("|")

        item = next(
            (
                part.removeprefix("[from] item: ")
                for part in parts
                if part.startswith("[from] item: ")
            ),
            None,
        )
        if item is None:
            return

        pokemon_id = next(
            (
                part.removeprefix("[of] ")
                for part in parts
                if part.startswith("[of] ")
            ),
            None,
        )

        # Most item effects target the Pokémon named immediately after the command.
        if pokemon_id is None and len(parts) >= 3:
            pokemon_id = parts[2]

        if pokemon_id is None:
            return

        pokemon = self._resolve_enemy(client, pokemon_id)
        if pokemon is not None:
            pokemon.item = item

    async def handle_line(self, client: Client, line: str) -> None:

        if line == "|":
            return

        for symbol in ["|t:|", "|upkeep"]:
            if line.startswith(symbol):
                return

        if line.startswith("|updateuser|"):
            self._handle_update_user(client, line)
            return

        if line.startswith("|nametaken|"):
            self._handle_name_taken(client, line)
            return

        if line.startswith("|formats|"):
            self._handle_formats(client, line)
            return

        if line.startswith("|pm|"):
            self._handle_pm(client, line)
            return

        if line.startswith(">"):
            client.room_id = line.removeprefix(">")
            return

        self._handle_item_reveal(client, line) # The item reveal can be in any line, so we catch them here
        # ex: |-heal|p2a: Snorlax|76/100|[from] item: Leftovers

        if line.startswith("|init|battle"):
            client.active_battle_room = client.room_id
            client.combat_handler.reset()
            client.battle_player_id = ""
            return

        if client.room_id != client.active_battle_room:
            return

        if line.startswith("|request|"):
            self._handle_request(client, line)
            return

        if line.startswith("|turn|"):
            turn_count = line.removeprefix("|turn|")
            client.turn_count = int(turn_count)
            client.log_manager.battle.info(
                f"* Turn {client.turn_count} *",
                extra={"room_id": client.room_id},
            )
            client.start_action_timeout()
            return

        if line.startswith("|move|"):
            self._handle_move(client, line)
            return

        if line.startswith("|switch|"):
            self._handle_switch_in(client, line)
            return

        if line.startswith("|drag|"):
            self._handle_drag(client, line)
            return

        if line.startswith("|player|"):
            self._handle_player(client, line)
            return

        if line.startswith("|win|"):
            client.finish_battle(line.removeprefix("|win|"))
            return
        if line.strip() == "|tie":
            client.finish_battle(None)
            return

        if line.startswith("|-transform|"):
            self._handle_transform(client, line)
            return

        if line.startswith("|-start|"):
            self._handle_start(client, line)
            return

        if line.startswith("|-end|"):
            self._handle_end(client, line)
            return

        if line.startswith("|-boost|"):
            self._handle_boost(client, line, unboost=False)
            return

        if line.startswith("|-unboost|"):
            self._handle_boost(client, line, unboost=True)
            return

        if line.startswith("|-status|"):
            self._handle_status(client, line)
            return

        if line.startswith("|-curestatus|"):
            self._handle_curestatus(client, line)
            return

        if line.startswith("|-damage|"):
            self._handle_damage(client, line)
            return

        if line.startswith("|-heal|"):
            self._handle_heal(client, line)
            return

        if line.startswith("|faint|"):
            self._handle_faint(client, line)
            return

        if line.startswith("|cant|"):
            self._handle_cant(client, line)
            return

        if line.startswith("|-mustrecharge|"):
            self._handle_must_recharge(client, line)
            return

        if line.startswith("|-prepare|"):
            self._handle_prepare(client, line)
            return

        if line.startswith("|-activate|"):
            self._handle_activate(client, line)
            return

        if line.startswith("|-weather|"):
            self._handle_weather(client, line)
            return

        if line.startswith("|-item|"):
            self._handle_item(client, line)
            return

        if line.startswith("|-enditem|"):
            self._handle_end_item(client, line)
            return

        client.log_manager.battle.debug(
            "Unhandled battle line: %s",
            line,
            extra={"room_id": client.room_id},
        )

    ## Sub-handlers
    def _handle_update_user(self, client: Client, line: str) -> None:
        parts = line.split("|", 5)

        if len(parts) < 5:
            raise RuntimeError(f"Malformed updateuser message: {line!r}")

        current_username = parts[2].strip()
        named = parts[3] == "1"

        client.named = named
        expected_username = client.username
        if expected_username is None:
            return

        if (
            named
            and current_username.lower() == expected_username.lower()
        ):
            client.username = current_username
            client.ready.set()

    def _handle_name_taken(self, client: Client, line: str) -> None:
        client.ready.clear()
        raise RuntimeError(f"Username was rejected by the server: {line}")

    def _handle_formats(self, client: Client, line: str) -> None:
        formats = parse_formats(line)
        client.formats = formats

    def _handle_pm(self, client: Client, line: str) -> None:
        parts = line.split("|", 4)

        if len(parts) < 5:
            raise RuntimeError(f"Malformed PM message: {line!r}")

        sender = parts[2].strip()
        receiver = parts[3].strip()
        message = parts[4]

        future = client.challenge_future
        challenged_user = client.challenged_user

        if (
            future is None
            or future.done()
            or challenged_user is None
        ):
            return

        if (
            client.username
            and sender.lower() == client.username.lower()
            and receiver.lower() == challenged_user.lower()
            and message.startswith("/challenge ")
        ):
            format_id = message.split("|", 1)[0].removeprefix("/challenge ")
            future.set_result(format_id)

    def _handle_request(self, client: Client, line: str):
        raw_data = line.removeprefix("|request|")
        data = json.loads(raw_data)

        available_moves: list[AvailableMove] = []
        available_pokemons: list[PartyPokemon] = []
        # id: str = data["side"]["id"]
        # name: str = data["side"]["name"]
        rqid: int | None = data["rqid"]
        force_switch: bool = False
        if "active" not in data:
            force_switch = True

        if data.get("wait", False):
            rqid = None

        # TODO update for duo game
        if not force_switch:
            if len(data["active"][0]["moves"]) == 1:
                move = data["active"][0]["moves"][0]
                available_moves.append(
                    AvailableMove(
                        name=move["move"],
                        id=move["id"],
                        curr_pp=move.get("pp", 100),
                        max_pp=move.get("maxpp", 100),
                        target=move.get("target", "normal"),
                        disabled=False,
                    )
                )
            else:
                for raw_move in data["active"][0]["moves"]:
                    available_moves.append(
                        AvailableMove(
                            name=raw_move["move"],
                            id=raw_move["id"],
                            curr_pp=raw_move["pp"],
                            max_pp=raw_move["maxpp"],
                            target=raw_move.get("target", "normal"),
                            disabled=raw_move["disabled"],
                        )
                    )

        for raw_pkmn in data["side"]["pokemon"]:
            if (cond := raw_pkmn["condition"]) == "0 fnt":
                curr_hp, max_hp = 0, 0
            else:
                curr_hp, max_hp = cond.split("/")
                # max_hp may carry a status suffix, e.g. "100 slp"
                if " " in max_hp:
                    max_hp = max_hp.split(" ")[0]
                curr_hp = int(curr_hp)
                max_hp = int(max_hp)
            stats = Stats(
                atk=raw_pkmn["stats"]["atk"],
                def_=raw_pkmn["stats"]["def"],
                spa=raw_pkmn["stats"]["spa"],
                spd=raw_pkmn["stats"]["spd"],
                spe=raw_pkmn["stats"]["spe"],
                max_hp=int(max_hp),
            )

            available_pokemons.append(
                PartyPokemon(
                    id=raw_pkmn["ident"],
                    details=raw_pkmn["details"],
                    lvl=(
                        int(raw_pkmn["details"].replace(", M", "").replace(", F", "").split(", L")[1])
                        if ", L" in raw_pkmn["details"]
                        else 100
                    ),  # Can happen for ditto in gen 1
                    active=raw_pkmn["active"],
                    stats=stats,
                    moves=raw_pkmn["moves"],
                    base_ability=raw_pkmn["baseAbility"],
                    item=raw_pkmn["item"],
                    pokeball=raw_pkmn["pokeball"],
                    status=Status(),
                    curr_hp=curr_hp,
                    max_hp=max_hp,
                )
            )

        assert len(available_pokemons) <= 6
        client.combat_handler.update(
            available_moves=available_moves,
            available_pokemons=available_pokemons,
            request_id=rqid,
            force_switch=force_switch,
        )

    def _handle_move(self, client: Client, line: str) -> None:
        parts = line.removeprefix("|move|").split("|")

        pkmn = parts[0]
        move = parts[1]
        extras = parts[3:]

        player, _pkmn = pkmn.split(": ", 1)

        if not client.battle_player_id:
            return

        if player.startswith(client.battle_player_id):
            return

        if "[from] Mirror Move" in extras:
            return # Mirror move copy the last move, we don't need to learn this move



        client.combat_handler.battle_state.witness_move(move)

    def _handle_switch_in(self, client: Client, line: str):
        ident, details, _hp, *_ = line.removeprefix("|switch|").split("|")
        player, _species = ident.split(": ")
        gender: str | None = None
        shiny = False
        if ", shiny" in details:
            shiny = True
            details = details.replace(", shiny", "")
        if ", M" in details:
            gender = "M"
            details = details.replace(", M", "")
        elif ", L" in details:
            gender = "L"
            details = details.replace(", F", "")
        else:
            gender = None
        # Ditto doesn't have a level in gen 1.
        if ", L" in details:
            lvl = int(details.split(", L")[1])
        else:
            lvl = 100

        if client.battle_player_id and not player.startswith(
            client.battle_player_id
        ):
            battle_state = client.combat_handler.battle_state
            battle_state.witness_switch_in(ident, lvl, gender=gender, shiny=shiny)
            enemy = battle_state.get_enemy_pokemon(
                battle_state.curr_enemy_pokemon, not_found_ok=True
            )
            if enemy is not None:
                enemy.reset_on_switch_in()

    def _handle_player(self, client: Client, line: str):
        slot, name = line.removeprefix("|player|").split("|", 2)[:2]
        if name == client.username:
            client.battle_player_id = slot

    def _handle_transform(self, client: Client, line: str) -> None:
        pokemon_id, target_id, *_ = line.removeprefix("|-transform|").split("|")

        player, _species = pokemon_id.split(": ", 1)

        if (
            client.battle_player_id
            and not player.startswith(client.battle_player_id)
        ):
            client.combat_handler.battle_state.witness_transform(
                pokemon_id=pokemon_id,
                target_id=target_id,
            )


    def _handle_boost(self, client: Client, line: str, unboost: bool) -> None:
        pokemon_id, stat, n, *_ = line.removeprefix(
            "|-" + ("unboost" if unboost else "boost") + "|"
        ).split("|")
        pokemon = self._resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return
        if stat is None:
            client.log_manager.battle.debug(
                "Unknown stat in boost line %r", line,
                extra={"room_id": client.room_id},
            )
            return
        amount = int(n)

        if unboost:
            pokemon.status.unboost(stat, amount)  # type: ignore[arg-type]
        else:
            pokemon.status.boost(stat, amount)  # type: ignore[arg-type]

    def _handle_status(self, client: Client, line: str) -> None:
        parts = line.removeprefix("|-status|").split("|", 2)
        pokemon_id = parts[0]
        status = parts[1]
        pokemon = self._resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return

        pokemon.status.set_status(status)  # type: ignore[arg-type]

    def _handle_curestatus(self, client: Client, line: str) -> None:
        parts = line.removeprefix("|-curestatus|").split("|", 2)
        pokemon_id = parts[0]
        status = parts[1]
        pokemon = self._resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return
        pokemon.status.clear_status(status)  # type: ignore[arg-type]

    def _handle_damage(self, client: Client, line: str) -> None:
        parts = line.removeprefix("|-damage|").split("|", 2)
        if len(parts) < 2:
            return
        pokemon_id = parts[0]
        curr, fainted = self._parse_hp(parts[1])

        enemy = self._resolve_enemy(client, pokemon_id)
        if enemy is not None:
            # HP Percentage Mod is on for gen1randombattle, so the server
            # already reports enemy HP as `X/100`; `curr` *is* the percent.
            enemy.curr_hp_percent = curr
            if fainted:
                enemy.fainted = True
            return

        # Own side: keep PartyPokemon.curr_hp in sync between |request| frames.
        if fainted:
            curr = 0
        own = self._resolve_self(client, pokemon_id)
        if own is not None:
            own.curr_hp = curr

    def _handle_heal(self, client: Client, line: str) -> None:
        parts = line.removeprefix("|-heal|").split("|", 2)
        if len(parts) < 2:
            return
        pokemon_id = parts[0]
        curr, fainted = self._parse_hp(parts[1])

        enemy = self._resolve_enemy(client, pokemon_id)
        if enemy is not None:
            enemy.curr_hp_percent = curr
            if fainted:
                enemy.fainted = True
            return

        if fainted:
            curr = 0
        own = self._resolve_self(client, pokemon_id)
        if own is not None:
            own.curr_hp = curr

    def _handle_faint(self, client: Client, line: str) -> None:
        pokemon_id = line.removeprefix("|faint|").split("|", 1)[0]

        enemy = self._resolve_enemy(client, pokemon_id)
        if enemy is not None:
            enemy.fainted = True
            enemy.active = False
            enemy.curr_hp_percent = 0
            return

        own = self._resolve_self(client, pokemon_id)
        if own is not None:
            own.curr_hp = 0

    def _handle_cant(self, client: Client, line: str) -> None:

        parts = line.removeprefix("|cant|").split("|", 2)
        if len(parts) < 2:
            return
        pokemon_id = parts[0]
        reason = parts[1]

        enemy = self._resolve_enemy(client, pokemon_id)
        if enemy is None:
            return

        if reason == "recharge":
            # The must-recharge flag set on the previous turn is now consumed.
            enemy.status.must_recharge = False
        elif reason == "flinch":
            # One-off effect of a move, we don't keep track of that since it starts and ends
            # within the same turn
            client.log_manager.battle.debug(
                "Enemy %s flinched", pokemon_id,
                extra={"room_id": client.room_id},
            )
        else:
            # slp / par / frz: status already tracked; still safe to clear any
            # stale must-recharge flag since the pokemon obviously acted anyway.
            enemy.status.must_recharge = False

    def _handle_must_recharge(self, client: Client, line: str) -> None:
        # |-mustrecharge|<id>  (gen1 Hyper Beam no-KO)
        pokemon_id = line.removeprefix("|-mustrecharge|").split("|", 1)[0]
        enemy = self._resolve_enemy(client, pokemon_id)
        if enemy is None:
            return
        enemy.status.must_recharge = True

    def _handle_prepare(self, client: Client, line: str) -> None:
        # 2-turn moves (Sky Attack, Skull Bash, Solar Beam, Dig, Fly)
        parts = line.removeprefix("|-prepare|").split("|", 2)
        if len(parts) < 2:
            return
        pokemon_id = parts[0]
        move = parts[1]
        enemy = self._resolve_enemy(client, pokemon_id)
        if enemy is not None:
            client.combat_handler.battle_state.witness_move(move)

    def _handle_start(self, client: Client, line: str) -> None:
        payload = line.removeprefix("|-start|")
        parts = payload.split("|")
        pokemon_id = parts[0]
        effect = parts[1]

        pokemon = self._resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return

        if effect == "Mimic":
            # |-start|p2a: Vaporeon|Mimic|Earthquake
            move = parts[2]
            pokemon.temporary_moves.append(move)
        elif effect == "confusion":
            pokemon.status.set_conf()
        elif effect == "Substitute":
            pokemon.status.has_substitute = True
        elif effect == "Reflect":
            # Reflect/Light Screen affect damage calculation; not tracked in
            # Status for now. Log so it shows up if someone wants to add it.
            client.log_manager.battle.debug(
                "Ignoring Reflect start for %s", pokemon_id,
                extra={"room_id": client.room_id},
            )
        else:
            client.log_manager.battle.debug(
                "Unhandled -start effect %r", line,
                extra={"room_id": client.room_id},
            )

    def _handle_end(self, client: Client, line: str) -> None:
        payload = line.removeprefix("|-end|")
        parts = payload.split("|")
        pokemon_id = parts[0]
        effect = parts[1]

        pokemon = self._resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return

        if effect == "confusion":
            pokemon.status.clear_conf()
        elif effect == "Substitute":
            pokemon.status.has_substitute = False
        else:
            client.log_manager.battle.debug(
                "Unhandled -end effect %r", line,
                extra={"room_id": client.room_id},
            )

    def _handle_drag(self, client: Client, line: str) -> None:
        switch_line = "|switch|" + line.removeprefix("|drag|")
        self._handle_switch_in(client, switch_line)

    def _handle_activate(self, client: Client, line: str) -> None:
        parts = line.removeprefix("|-activate|").split("|")

        if len(parts) < 2:
            raise RuntimeError(f"Malformed activate message: {line!r}")

        pokemon_id = parts[0]
        effect = parts[1]

        pokemon = self._resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return

        if effect == "move: Mimic":
            if len(parts) < 3:
                raise RuntimeError(f"Malformed Mimic activation: {line!r}")

            copied_move = parts[2]
            pokemon.temporary_moves.append(copied_move)


    def _handle_weather(self, client: Client, line: str) -> None:
        # |-weather|SunnyDay                -> SunnyDay
        # |-weather|SunnyDay|[upkeep]      -> SunnyDay (upkeep marker, no state change)
        # |-weather|RainDance|[from] move: Rain Dance -> RainDance
        # |-weather|none                   -> None (weather cleared)
        parts = line.removeprefix("|-weather|").split("|", 2)
        weather = parts[0] if parts else ""
        client.combat_handler.battle_state.weather = weather if weather != "none" else None

    def _handle_item(self, client: Client, line: str) -> None:
        parts = line.removeprefix("|-item|").split("|")
        if len(parts) < 2:
            return

        pokemon_id = parts[0]
        item = parts[1]

        pokemon = self._resolve_enemy(client, pokemon_id)
        if pokemon is not None:
            pokemon.item = item


    def _handle_end_item(self, client: Client, line: str) -> None:
        parts = line.removeprefix("|-enditem|").split("|")
        if len(parts) < 2:
            return

        pokemon_id = parts[0]
        item = parts[1]

        pokemon = self._resolve_enemy(client, pokemon_id)
        if pokemon is not None:
            if pokemon.item != item and pokemon.item != Unknown.VALUE:
                raise RuntimeError(f"The item help by the pokemon in the logs is not the one in the battle state, {pokemon.item=} vs {item=}")
            pokemon.item = None
