# Showdown SDK

An SDK for building [Pokémon Showdown](https://pokemonshowdown.com/) bots in
Python. `Showdown SDK` connects to a Showdown server over the websocket
protocol the server speaks, drives one or more battles, and exposes the
in-battle state that the protocol reveals so you can plug in your own decision
logic. The library handles the plumbing — connection, login, room tracking,
protocol parsing, action timeouts, and a structured battle-state model.
