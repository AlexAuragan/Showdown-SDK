"""Builds `MoveEvent` records from a cluster of consecutive protocol lines.

A `|move|` line opens a new in-progress event; the `|-damage|` /
`|-supereffective|` / `|-status|` / `|-boost|` / ... lines that follow decorate
it; turn / switch / drag / faint / `|move|` boundaries flush the event into
`BattleState.move_history`.

Extracted from `LogHandler` so the move-event-building concern is contained in
one type and is independently testable (feed `|move|` + `|-damage|` + ... into a
`MoveEventBuilder` without spinning the whole 1000-line parser). The
record-before-mutate ordering invariant lives in `LogHandler.handle_line`, which
calls `MoveEventBuilder.on_line` first, before any per-line state mutation.
"""
from typing import TYPE_CHECKING

from python_showdown.classes.client.utils import split_protocol
from python_showdown.classes.combat.move_history import MoveEvent, StatChange
from python_showdown.classes.pokemon.stats import MajorStatus, MinorStatus

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client


# Minor status effects recorded as `statuses_inflicted` when applied to the
# move's target by the move itself (the rest of |-start| is self-effects or
# field screens we don't treat as "inflicted status").
_INFLICTED_MINOR_EFFECTS = (MinorStatus.CONFUSION, MinorStatus.LEECH_SEED)


class MoveEventBuilder:
    """Owns the in-progress `MoveEvent` and the ability-boost cross-line flag.

    A single instance is held by `LogHandler` for the lifetime of one battle.
    `reset` is called on `|init|battle` so the builder is fresh for the next
    battle in the same client (mirroring the reset in `finish_battle`).
    """

    def __init__(self) -> None:
        self._current_move: MoveEvent | None = None
        # Set by |-ability|...|boost so the next |-boost|/-unboost| is treated as
        # ability-driven (not move-driven) and skipped from the history.
        self._ability_boost_pending: bool = False

    def on_line(self, client: Client, line: str) -> None:
        """Decorate the in-progress move event from protocol lines, or flush it
        at turn/switch/move boundaries."""
        # Boundaries flush the pending event before any state mutation.
        if line in ("|", "|upkeep") or line.startswith("|turn|"):
            self.flush(client)
            return
        if line.startswith(("|switch|", "|drag|", "|faint|")):
            self.flush(client)
            return

        if not client.battle_player_id:
            return

        if line.startswith("|move|"):
            self.flush(client)
            self._open_move(client, line)
            return

        event = self._current_move
        if event is None:
            return

        if line.startswith("|-miss|"):
            event.hit = False
            self._ability_boost_pending = False
            return
        if line.startswith("|-immune|"):
            event.effectiveness = 0.0
            self._ability_boost_pending = False
            return
        if line.startswith("|-fail|"):
            event.failed = True
            self._ability_boost_pending = False
            return
        if line.startswith("|-supereffective|"):
            event.effectiveness *= 2.0
        elif line.startswith("|-resisted|"):
            event.effectiveness *= 0.5
        elif line.startswith("|-crit|"):
            event.is_critical = True
        elif line.startswith("|-damage|"):
            self._record_damage(client, line, event)
        elif line.startswith("|-status|"):
            self._record_major_status(line, event)
        elif line.startswith("|-start|"):
            self._record_minor_status(line, event)
        elif line.startswith(("|-boost|", "|-unboost|")):
            self._record_stat_change(line, event)
        elif line.startswith("|-setboost|"):
            # e.g. Belly Drum sets atk to +6 -- a move-driven absolute set.
            if self._ability_boost_pending:
                self._ability_boost_pending = False
                return
            parts = split_protocol(
                line, "|-setboost|", min_parts=3, maxsplit=3
            )
            event.stat_changes.append(
                StatChange(target=parts[0], stat=parts[1], delta=int(parts[2]))
            )
        elif line.startswith("|-anim|"):
            # Charging moves (|[still]|) open with an empty target; the real
            # target arrives on the |-anim| line. Backfill it so subsequent
            # |-damage| lines link to this event.
            parts = split_protocol(line, "|-anim|", min_parts=2)
            if event.target == "" and len(parts) >= 3:
                event.target = parts[2]
        elif line.startswith("|-ability|"):
            # An adjacent |-ability|...|boost signals the following boost is
            # ability-driven; don't attribute it to the move.
            parts = split_protocol(line, "|-ability|", min_parts=2)
            if len(parts) >= 3 and parts[2] in ("boost", "unboost"):
                self._ability_boost_pending = True

    def flush(self, client: Client) -> None:
        if self._current_move is not None:
            client.battle_state.move_history.append(self._current_move)
            self._current_move = None
        self._ability_boost_pending = False

    def flush_history(self, client: Client) -> None:
        """Commit any in-progress move event (e.g. at the end of a replay)."""
        self.flush(client)

    def reset(self, client: Client) -> None:
        """Clear builder state for a fresh battle (called on |init|battle)."""
        self._current_move = None
        self._ability_boost_pending = False

    def _open_move(self, client: Client, line: str) -> None:
        # |move|p2a: Venusaur|Sleep Powder|p1a: Ponyta[[miss]]
        parts = split_protocol(line, "|move|", min_parts=3)
        user = parts[0]
        move = parts[1]
        target = parts[2]
        user_side = "self" if user.startswith(client.battle_player_id) else "enemy"
        hit = "[miss]" not in line
        self._current_move = MoveEvent(
            turn=client.turn_count, move=move, user=user, target=target,
            user_side=user_side, hit=hit,
        )

    def _record_damage(self, client: Client, line: str, event: MoveEvent) -> None:
        from python_showdown.classes.client.parser import (
            parse_hp,
            resolve_enemy,
            resolve_self,
        )
        parts = split_protocol(
            line, "|-damage|", min_parts=2, maxsplit=2
        )
        target_id = parts[0]
        if event.target and target_id != event.target:
            return  # recoil/hazard on the user -> out of scope for singles
        curr, _fainted = parse_hp(parts[1])

        enemy = resolve_enemy(client, target_id)
        if enemy is not None:
            before = enemy.curr_hp_percent
            event.damage = max(before - curr, 0)
            event.resulting_hp = curr
            return
        own = resolve_self(client, target_id)
        if own is not None:
            before = own.curr_hp
            event.damage = max(before - curr, 0)
            event.resulting_hp = curr

    def _record_major_status(self, line: str, event: MoveEvent) -> None:
        # |-status|p1a: Ponyta|slp|[from] move: Sleep Powder  (primary)
        # |-status|p1a: Omastar|brn                            (secondary burn)
        parts = split_protocol(
            line, "|-status|", min_parts=2, maxsplit=2
        )
        target_id, token = parts[0], parts[1]
        if target_id != event.target:
            return
        try:
            status = MajorStatus.from_server(token)
        except ValueError:
            return
        if status not in event.statuses_inflicted:
            event.statuses_inflicted.append(status)

    def _record_minor_status(self, line: str, event: MoveEvent) -> None:
        # |-start|pX|confusion|[from] move: Confuse Ray
        # Only effects applied to the move's target are "inflicted"; self/field
        # screens (Substitute, Reflect, Encore, ...) are skipped.
        parts = split_protocol(
            line, "|-start|", min_parts=2, maxsplit=3
        )
        target_id, effect = parts[0], parts[1]
        if target_id != event.target:
            return
        effect = effect.removeprefix("move: ")
        try:
            minor = MinorStatus(effect)
        except ValueError:
            return
        if (
            minor in _INFLICTED_MINOR_EFFECTS
            and minor not in event.statuses_inflicted
        ):
            event.statuses_inflicted.append(minor)

    def _record_stat_change(self, line: str, event: MoveEvent) -> None:
        # Skip ability-driven boosts (signalled by the preceding |-ability|).
        if self._ability_boost_pending:
            self._ability_boost_pending = False
            return
        parts = split_protocol(line, "|", min_parts=4)
        # parts: ['-boost' / '-unboost', pokemon_id, stat, n]
        pokemon_id, stat, n = parts[1], parts[2], parts[3]
        delta = int(n)
        if line.startswith("|-unboost|"):
            delta = -delta
        event.stat_changes.append(StatChange(target=pokemon_id, stat=stat, delta=delta))
