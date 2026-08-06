from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client

from python_showdown.classes.client.utils import (
    parse_formats,
    parse_hp,
    resolve_enemy,
    resolve_self,
    split_protocol,
)
from python_showdown.classes.combat.battle_state import SideCondition
from python_showdown.classes.combat.move_builder import MoveEventBuilder
from python_showdown.classes.pokemon.moves import AvailableMove
from python_showdown.classes.pokemon.pokemon import PartyPokemon, Unknown
from python_showdown.classes.pokemon.stats import MinorStatus, Stats, Status


class Parser:

    def __init__(self) -> None:
        # Move-event building is delegated to `MoveEventBuilder`: a `|move|`
        # line opens an event, effect lines decorate it, and turn/switch/faint
        # boundaries flush it into `BattleState.move_history`. `handle_line`
        # calls `on_line` first to enforce record-before-mutate ordering.
        self._move_builder = MoveEventBuilder()
        self._battle_handlers: list[tuple[str, Callable, dict]] = [
           ("|request|", self._handle_request, {}),
            ("|turn|", self._handle_turn, {}),
            ("|move|", self._handle_move, {}),
            ("|switch|", self._handle_switch_in, {}),
            ("|drag|", self._handle_drag, {}),
            ("|player|", self._handle_player, {}),
            ("|win|", self._handle_win, {}),
            ("|-transform|", self._handle_transform, {}),
            ("|-formechange|", self._handle_formechange, {}),
            ("|-sidestart|", self._handle_sidestart, {}),
            ("|-sideend|", self._handle_sideend, {}),
            ("|-start|", self._handle_start, {}),
            ("|-end|", self._handle_end, {}),
            ("|-boost|", self._handle_boost, {"unboost": False}),
            ("|-unboost|", self._handle_boost, {"unboost": True}),
            ("|-status|", self._handle_status, {}),
            ("|-curestatus|", self._handle_curestatus, {}),
            ("|-damage|", self._handle_damage, {}),
            ("|-heal|", self._handle_heal, {}),
            ("|faint|", self._handle_faint, {}),
            ("|cant|", self._handle_cant, {}),
            ("|-mustrecharge|", self._handle_must_recharge, {}),
            ("|-prepare|", self._handle_prepare, {}),
            ("|-activate|", self._handle_activate, {}),
            ("|-weather|", self._handle_weather, {}),
            ("|-item|", self._handle_item, {}),
            ("|-enditem|", self._handle_end_item, {}),
            ("|-sethp|", self._handle_sethp, {}),
            ("|-ability|", self._handle_ability, {}),
            ("|-cureteam|", self._handle_cureteam, {}),
            ("|-setboost|", self._handle_setboost, {}),
            ("|-clearallboost", self._handle_clearallboost, {}),
            ("|error|", self._handle_error, {}),
        ]

    def flush_move_history(self, client: Client) -> None:
        """Commit any in-progress move event (e.g. at the end of a replay).

        Preserved as a public method because the test replay harness and
        the snapshot generator call it directly on the `LogHandler`.
        """
        self._move_builder.flush_history(client)


    def _dispatch_battle_line(self, client: Client, line: str) -> bool:
        """Resolve `line` to one of `_BATTLE_HANDLERS` and invoke it.

        Returns True if a handler matched. The exact-match battle-end signal
        `|tie` is handled in `handle_line` before this dispatch because it is
        not a prefix match.
        """
        for prefix, handler, kwargs in self._battle_handlers:
            if line.startswith(prefix):
                handler(client, line, **kwargs)
                return True
        return False

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

        pokemon = resolve_enemy(client, pokemon_id)
        if pokemon is not None:
            pokemon.item = item

    def handle_line(self, client: Client, line: str) -> None:
        self._move_builder.on_line(client, line)

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

        # Item reveals can occur on any protocol line.
        # Example: |-heal|p2a: Snorlax|76/100|[from] item: Leftovers
        self._handle_item_reveal(client, line)

        if line.startswith("|init|battle"):
            client.active_battle_room = client.room_id
            client.battle_state.reset()
            client.request_id = None
            client.battle_player_id = ""
            self._move_builder.reset(client)
            return

        if client.room_id != client.active_battle_room:
            return

        # `|tie` is the lone EXACT-match battle-end signal (not a prefix), so it
        # stays explicit; `|win|` went through the registry as `_handle_win`.
        if line.strip() == "|tie":
            client.finish_battle(None)
            return

        if self._dispatch_battle_line(client, line):
            return

        client.log_manager.battle.debug(
            "Unhandled battle line: %s",
            line,
            extra={"room_id": client.room_id},
        )

    def _handle_turn(self, client: Client, line: str) -> None:
        turn_count = line.removeprefix("|turn|")
        client.turn_count = int(turn_count)
        client.log_manager.battle.info(
            f"* Turn {client.turn_count} *",
            extra={"room_id": client.room_id},
        )
        client.start_action_timeout()

    def _handle_win(self, client: Client, line: str) -> None:
        client.finish_battle(line.removeprefix("|win|"))

    def _handle_update_user(self, client: Client, line: str) -> None:
        parts = split_protocol(line, "", min_parts=5, maxsplit=5)

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
        parts = split_protocol(line, "", min_parts=5, maxsplit=4)

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

    def _handle_request(self, client: Client, line: str) -> None:
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
            status = Status()
            if (cond := raw_pkmn["condition"]) == "0 fnt":
                curr_hp = 0
                existing = next(
                    (p for p in client.battle_state.team
                     if p.id == raw_pkmn["ident"]),
                    None,
                )
                max_hp = existing.max_hp if existing is not None and existing.max_hp > 0 else 0
            else:
                curr_str, rest = cond.split("/", 1)
                curr_hp = int(curr_str)
                # rest may carry a status suffix, e.g. "100 slp"
                if " " in rest:
                    max_str, status_token = rest.split(" ", 1)
                    max_hp = int(max_str)
                    status.set_status(status_token)
                else:
                    max_hp = int(rest)
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
                        int(raw_pkmn["details"].replace(", shiny", "").replace(", M", "").replace(", F", "").split(", L")[1])
                        if ", L" in raw_pkmn["details"]
                        else 100
                    ),  # Can happen for ditto in gen 1
                    active=raw_pkmn["active"],
                    stats=stats,
                    moves=raw_pkmn["moves"],
                    base_ability=raw_pkmn["baseAbility"],
                    item=raw_pkmn["item"],
                    pokeball=raw_pkmn["pokeball"],
                    status=status,
                    curr_hp=curr_hp,
                    max_hp=max_hp,
                )
            )

        if len(available_pokemons) > 6:
            raise RuntimeError(
                f"Malformed request: expected at most 6 Pokémon, "
                f"got {len(available_pokemons)} in {line!r}"
            )

        battle_state = client.battle_state
        battle_state.update_team(available_pokemons)
        active = next((pkmn for pkmn in available_pokemons if pkmn.active), None)
        if active is not None:
            battle_state.set_active_pokemon(active.id)
        battle_state.update_moves(available_moves)
        battle_state.force_switch = force_switch
        client.request_id = rqid

    def _handle_move(self, client: Client, line: str) -> None:
        parts = split_protocol(line, "|move|", min_parts=2)

        pkmn = parts[0]
        move = parts[1]
        extras = parts[3:]

        player, _pkmn = pkmn.split(": ", 1)

        if not client.battle_player_id:
            return

        if player.startswith(client.battle_player_id):
            return

        if "[from] Mirror Move" in extras:
            # Mirror Move copies the previous move; it does not reveal a new slot.
            return

        client.battle_state.witness_move(move)

    def _handle_switch_in(self, client: Client, line: str) -> None:
        ident, details, _hp, *_ = split_protocol(
            line, "|switch|", min_parts=3
        )
        player, _species = ident.split(": ")
        gender: str | None = None
        shiny = False
        if ", shiny" in details:
            shiny = True
            details = details.replace(", shiny", "")
        if ", M" in details:
            gender = "M"
            details = details.replace(", M", "")
        elif ", F" in details:
            gender = "F"
            details = details.replace(", F", "")
        else:
            gender = None

        if ", L" in details:
            lvl = int(details.split(", L")[1])
        else:
            lvl = 100

        if client.battle_player_id and not player.startswith(
            client.battle_player_id
        ):
            battle_state = client.battle_state
            battle_state.witness_switch_in(ident, lvl, gender=gender, shiny=shiny)
            enemy = battle_state.get_enemy_pokemon(
                battle_state.curr_enemy_pokemon, not_found_ok=True
            )
            if enemy is not None:
                enemy.reset_on_switch_in()

    def _handle_player(self, client: Client, line: str) -> None:
        slot, name, *_ = split_protocol(
            line, "|player|", min_parts=2, maxsplit=2
        )
        if name == client.username:
            client.battle_player_id = slot

    def _handle_transform(self, client: Client, line: str) -> None:
        # |-transform|p2a: Ditto|p1a: Arceus -- the enemy copies our active.
        pokemon_id, target_id, *_ = split_protocol(
            line, "|-transform|", min_parts=2
        )

        player, _species = pokemon_id.split(": ", 1)

        if (
            client.battle_player_id
            and not player.startswith(client.battle_player_id)
        ):
            # If the transform target is our own active pokemon, we already know
            # its exact moves from |request|.
            target = resolve_self(client, target_id)
            copied_moves = list(target.moves) if target is not None else None
            client.battle_state.witness_transform(
                pokemon_id=pokemon_id,
                target_id=target_id,
                copied_moves=copied_moves,
            )

    def _handle_formechange(self, client: Client, line: str) -> None:
        # |-formechange|p2a: Castform|Castform-Rainy|[msg]|[from] ability: Forecast
        # A form change only relabels the species/type; it never alters the move
        # set. Reverting uses the base species name (e.g. "Castform").
        parts = split_protocol(
            line, "|-formechange|", min_parts=2, maxsplit=2
        )
        pokemon_id = parts[0]
        forme = parts[1]
        pokemon = resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return
        pokemon.forme = forme

    def _handle_sidestart(self, client: Client, line: str) -> None:
        # |-sidestart|p1: BOT1|Spikes          (one layer)
        # |-sidestart|p2: BOT2|move: Toxic Spikes
        # The server re-emits one line per added layer, so each occurrence bumps
        # the stack count for that (side, effect).
        parts = split_protocol(
            line, "|-sidestart|", min_parts=2, maxsplit=2
        )
        side_field = parts[0]
        token = parts[1].removeprefix("move: ").strip()
        slot = self._side_slot(side_field)
        if slot is None or token == "":
            return
        try:
            effect = SideCondition(token)
        except ValueError:
            client.log_manager.battle.debug(
                "Unhandled -sidestart effect %r", line,
                extra={"room_id": client.room_id},
            )
            return
        conds = client.battle_state.side_conditions
        conds.setdefault(slot, {})
        conds[slot][effect] = conds[slot].get(effect, 0) + 1

    def _handle_sideend(self, client: Client, line: str) -> None:
        # |-sideend|p2: BOT2|Spikes|[from] move: Rapid Spin|[of] p2a: Forretress
        # Rapid Spin clears every layer of that effect on that side at once.
        parts = split_protocol(
            line, "|-sideend|", min_parts=2, maxsplit=2
        )
        side_field = parts[0]
        token = parts[1].removeprefix("move: ").strip()
        slot = self._side_slot(side_field)
        if slot is None or token == "":
            return
        try:
            effect = SideCondition(token)
        except ValueError:
            client.log_manager.battle.debug(
                "Unhandled -sideend effect %r", line,
                extra={"room_id": client.room_id},
            )
            return
        conds = client.battle_state.side_conditions.get(slot)
        if conds is not None:
            conds.pop(effect, None)

    @staticmethod
    def _side_slot(side_field: str) -> str | None:
        # "p1: BOT1" / "p2: BOT2" -> the slot prefix "p1" / "p2".
        return side_field.split(":", 1)[0] or None

    def _handle_boost(self, client: Client, line: str, unboost: bool) -> None:
        pokemon_id, stat, n, *_ = line.removeprefix(
            "|-" + ("unboost" if unboost else "boost") + "|"
        ).split("|")
        pokemon = resolve_enemy(client, pokemon_id)
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
        pokemon = resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return

        pokemon.status.set_status(status)

    def _handle_curestatus(self, client: Client, line: str) -> None:
        parts = split_protocol(
            line, "|-curestatus|", min_parts=2, maxsplit=2
        )
        pokemon_id = parts[0]
        status = parts[1]
        pokemon = resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return
        pokemon.status.clear_status(status)

    def _handle_damage(self, client: Client, line: str) -> None:
        parts = split_protocol(
            line, "|-damage|", min_parts=2, maxsplit=2
        )
        pokemon_id = parts[0]
        curr, fainted = parse_hp(parts[1])

        enemy = resolve_enemy(client, pokemon_id)
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
        own = resolve_self(client, pokemon_id)
        if own is not None:
            own.curr_hp = curr

    def _handle_heal(self, client: Client, line: str) -> None:
        parts = split_protocol(
            line, "|-heal|", min_parts=2, maxsplit=2
        )
        pokemon_id = parts[0]
        curr, fainted = parse_hp(parts[1])

        enemy = resolve_enemy(client, pokemon_id)
        if enemy is not None:
            enemy.curr_hp_percent = curr
            if fainted:
                enemy.fainted = True
            return

        if fainted:
            curr = 0
        own = resolve_self(client, pokemon_id)
        if own is not None:
            own.curr_hp = curr

    def _handle_faint(self, client: Client, line: str) -> None:
        pokemon_id = line.removeprefix("|faint|").split("|", 1)[0]

        enemy = resolve_enemy(client, pokemon_id)
        if enemy is not None:
            enemy.fainted = True
            enemy.active = False
            enemy.curr_hp_percent = 0
            return

        own = resolve_self(client, pokemon_id)
        if own is not None:
            own.curr_hp = 0

    def _handle_cant(self, client: Client, line: str) -> None:

        parts = split_protocol(
            line, "|cant|", min_parts=2, maxsplit=2
        )
        pokemon_id = parts[0]
        reason = parts[1]

        enemy = resolve_enemy(client, pokemon_id)
        if enemy is None:
            return

        if reason == "recharge":
            # The must-recharge flag set on the previous turn is now consumed.
            enemy.status.must_recharge = False
        elif reason == "flinch":
            # One-off, intra-turn effect we don't track.
            client.log_manager.battle.debug(
                "Enemy %s flinched", pokemon_id,
                extra={"room_id": client.room_id},
            )
        else:
            # slp / par / frz: status already tracked; clear any stale
            # must-recharge flag since the pokemon obviously acted anyway.
            enemy.status.must_recharge = False

    def _handle_must_recharge(self, client: Client, line: str) -> None:
        # |-mustrecharge|<id>  (gen1 Hyper Beam no-KO)
        pokemon_id = line.removeprefix("|-mustrecharge|").split("|", 1)[0]
        enemy = resolve_enemy(client, pokemon_id)
        if enemy is None:
            return
        enemy.status.must_recharge = True

    def _handle_prepare(self, client: Client, line: str) -> None:
        # 2-turn moves (Sky Attack, Skull Bash, Solar Beam, Dig, Fly)
        parts = split_protocol(
            line, "|-prepare|", min_parts=2, maxsplit=2
        )
        pokemon_id = parts[0]
        move = parts[1]
        enemy = resolve_enemy(client, pokemon_id)
        if enemy is not None:
            client.battle_state.witness_move(move)

    def _handle_start(self, client: Client, line: str) -> None:
        payload = line.removeprefix("|-start|")
        parts = payload.split("|")
        pokemon_id = parts[0]
        effect = parts[1]

        pokemon = resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return

        if effect == "Mimic":
            # |-start|p2a: Vaporeon|Mimic|Earthquake -- the Mimic slot is
            # replaced by the copied move: disable Mimic, expose the copy.
            move = parts[2]
            if move not in pokemon.temporary_moves:
                pokemon.temporary_moves.append(move)
            if "Mimic" not in pokemon.disabled_moves:
                pokemon.disabled_moves.append("Mimic")
            return

        # Perish Song countdown: |-start|pX|perish3 ... perish0.
        if effect.startswith("perish"):
            try:
                pokemon.status.perish_count = int(effect.removeprefix("perish"))
            except ValueError:
                pass
            pokemon.status.add_minor(MinorStatus.PERISH_SONG)
            return

        token = effect.removeprefix("move: ").removeprefix("ability: ")
        try:
            minor = MinorStatus(token)
        except ValueError:
            # Reflect/Light Screen affect damage calculation; not tracked yet.
            client.log_manager.battle.debug(
                "Unhandled -start effect %r", line,
                extra={"room_id": client.room_id},
            )
            return

        pokemon.status.add_minor(minor)

    def _handle_end(self, client: Client, line: str) -> None:
        payload = line.removeprefix("|-end|")
        parts = payload.split("|")
        pokemon_id = parts[0]
        effect = parts[1]

        pokemon = resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return

        token = effect.removeprefix("move: ").removeprefix("ability: ")
        if token.startswith("perish"):
            pokemon.status.perish_count = None
            pokemon.status.remove_minor(MinorStatus.PERISH_SONG)
            return

        try:
            minor = MinorStatus(token)
        except ValueError:
            client.log_manager.battle.debug(
                "Unhandled -end effect %r", line,
                extra={"room_id": client.room_id},
            )
            return

        pokemon.status.remove_minor(minor)

    def _handle_drag(self, client: Client, line: str) -> None:
        switch_line = "|switch|" + line.removeprefix("|drag|")
        self._handle_switch_in(client, switch_line)

    def _handle_activate(self, client: Client, line: str) -> None:
        parts = split_protocol(line, "|-activate|", min_parts=2)

        pokemon_id = parts[0]
        effect = parts[1]

        pokemon = resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return

        if effect == "move: Mimic":
            if len(parts) < 3:
                raise RuntimeError(f"Malformed Mimic activation: {line!r}")

            copied_move = parts[2]
            if copied_move not in pokemon.temporary_moves:
                pokemon.temporary_moves.append(copied_move)
            if "Mimic" not in pokemon.disabled_moves:
                pokemon.disabled_moves.append("Mimic")

    def _handle_weather(self, client: Client, line: str) -> None:
        # |-weather|SunnyDay                -> SunnyDay
        # |-weather|SunnyDay|[upkeep]      -> SunnyDay (upkeep marker, no state change)
        # |-weather|RainDance|[from] move: Rain Dance -> RainDance
        # |-weather|none                   -> None (weather cleared)
        parts = line.removeprefix("|-weather|").split("|", 2)
        weather = parts[0] if parts else ""
        client.battle_state.weather = weather if weather != "none" else None

    def _handle_item(self, client: Client, line: str) -> None:
        parts = split_protocol(line, "|-item|", min_parts=2)
        pokemon_id = parts[0]
        item = parts[1]


        if "[from] move: Thief" in line or "[from] move: Knock Off" in line:
            of_target = next(
                (p.removeprefix("[of] ") for p in line.split("|")
                 if p.startswith("[of] ")),
                None,
            )
            if of_target is not None:
                victim = resolve_enemy(client, of_target)
                if victim is not None:
                    victim.item = None

        pokemon = resolve_enemy(client, pokemon_id)
        if pokemon is not None:
            pokemon.item = item

    def _handle_end_item(self, client: Client, line: str) -> None:
        parts = split_protocol(line, "|-enditem|", min_parts=2)

        pokemon_id = parts[0]
        item = parts[1]

        pokemon = resolve_enemy(client, pokemon_id)
        if pokemon is not None:
            if pokemon.item != item and pokemon.item is not Unknown.VALUE:
                raise RuntimeError(
                    "Item mismatch between protocol and battle state: "
                    f"{pokemon.item=} vs {item=}"
                )
            pokemon.item = None

    def _handle_sethp(self, client: Client, line: str) -> None:
        # |-sethp|p2a: Gengar|50/100|[from] move: Pain Split      (enemy = %)
        # |-sethp|p1a: Dusknoir|215/215|[from] move: Pain Split (own = absolute)
        parts = split_protocol(
            line, "|-sethp|", min_parts=2, maxsplit=2
        )
        pokemon_id = parts[0]
        curr, fainted = parse_hp(parts[1])

        enemy = resolve_enemy(client, pokemon_id)
        if enemy is not None:
            enemy.curr_hp_percent = curr
            if fainted:
                enemy.fainted = True
            return

        if fainted:
            curr = 0
        own = resolve_self(client, pokemon_id)
        if own is not None:
            own.curr_hp = curr

    def _handle_ability(self, client: Client, line: str) -> None:
        # |-ability|p2a: Zapdos|Pressure            (reveal)
        # |-ability|p1a: Staraptor|Intimidate|boost (reveal + a boost line follows)
        parts = split_protocol(
            line, "|-ability|", min_parts=2, maxsplit=2
        )
        pokemon_id = parts[0]
        ability = parts[1]
        pokemon = resolve_enemy(client, pokemon_id)
        if pokemon is not None:
            pokemon.base_ability = ability

    def _handle_cureteam(self, client: Client, line: str) -> None:
        # |-cureteam|p2a: Meganium|[from] move: Aromatherapy
        # Clears the major status of every pokemon on the named pokemon's side.
        pokemon_id = line.removeprefix("|-cureteam|").split("|", 1)[0]
        if not client.battle_player_id:
            return
        try:
            player, _ = pokemon_id.split(": ", 1)
        except ValueError:
            return
        if player.startswith(client.battle_player_id):
            return
        for pokemon in client.battle_state.enemy_team:
            pokemon.status.clear_all_major_status()

    def _handle_error(self, client: Client, line: str) -> None:
        # Showdown rejects a bad `/choose` with `|error|[Invalid choice] ...`
        # (e.g. we tried to switch a trapped Pokémon). The request stays open
        # on the server, so flag the client to re-draw a random move and resend
        # -- see `Client.retry_action` / the receive loop.
        body = line.removeprefix("|error|")
        if body.startswith("[Invalid choice]"):
            client.pending_choice_retry = True

    def _handle_setboost(self, client: Client, line: str) -> None:
        # |-setboost|p2a: Azumarill|atk|6|[from] move: Belly Drum
        parts = split_protocol(
            line, "|-setboost|", min_parts=3, maxsplit=3
        )

        pokemon_id, stat, n = parts[0], parts[1], parts[2]
        pokemon = resolve_enemy(client, pokemon_id)
        if pokemon is None:
            return
        pokemon.status.set_stage(stat, int(n))  # type: ignore[arg-type]

    def _handle_clearallboost(self, client: Client, line: str) -> None:
        # |-clearallboost -> reset every pokemon's stat stages (Haze / Clear Smog).
        for pokemon in client.battle_state.enemy_team:
            pokemon.status.reset_all_stages()
