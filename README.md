# Showdown SDK

An SDK for building [Pokémon Showdown](https://pokemonshowdown.com/) bots in
Python. `Showdown SDK` connects to a Showdown server over the websocket
protocol the server speaks, drives one or more battles, and exposes the
in-battle state that the protocol reveals so you can plug in your own decision
logic. The library handles the plumbing: connection, login, room tracking,
protocol parsing, action timeouts, and a structured battle-state model.

## Scope
As I currently am the only one working on this project, I limited the scope to
gen 4. I am not against implementing futur gens after, but each generation comes
with new mecanics and make the code more complicated and, more importantly, hard
to test.

## Testing
I cannot promise the code is free of bug, the ones I spent most time and energy
hunting for are BattleState discrepency with the actual battle state.
Thankfully, pokemon showdown code is easy enough to edit to surface a command
to access its internal battle state. While it would obviously be cheating, it
was a great tool to make sure our interpretation of what's happening in combat
is in sync with the real thing.

At some point, I implemented differencial testing, i.e. each battle that caused
a crash or desync with showdown was recorded in tests/sample_battles and they
now all pass. (We all wish I did that from the start but oh well).

Right now, the SDK can run over tenth of thousands of battle without a single
crash or desync, that's the best I can promise you.

## Usage
If your goal (like me) is to implement an AI that plays pokemon, the best is to
copy the code in `scripts/generate_battles` and replace the combat handler by
your own implementation of BaseCombatHandler. Feature extraction and vectorization
functions are in `showdown_sdk/features/` and in `showdown_sdk/vectorizer/`.

If needed, all the dex data is in `showdown_sdk/dex_data` in json format, you'll
find every relevent information about pokémons, moves, etc.
