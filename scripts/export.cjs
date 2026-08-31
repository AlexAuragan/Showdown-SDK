#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const {Dex} = require('@pkmn/dex');
const {Generations} = require('@pkmn/data');

function parseArgs(argv) {
  const args = {
    output: process.cwd(),
    gens: [1, 2, 3, 4, 5, 6, 7, 8, 9],
    compact: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--output') {
      args.output = argv[++i];
    } else if (arg === '--gens') {
      args.gens = argv[++i].split(',').map(Number);
    } else if (arg === '--compact') {
      args.compact = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return args;
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function keyed(iterable) {
  const output = {};
  for (const entry of iterable) {
    const value = plain(entry);
    const id = value.id || String(value.name).toLowerCase().replace(/[^a-z0-9]+/g, '');
    output[id] = value;
  }
  return output;
}

function writeJson(filename, value, compact) {
  const spacing = compact ? 0 : 2;
  fs.writeFileSync(filename, JSON.stringify(value, null, spacing) + '\n', 'utf8');
}

function packageVersion(packageName) {
  let current = path.dirname(require.resolve(packageName));
  while (true) {
    const candidate = path.join(current, 'package.json');
    if (fs.existsSync(candidate)) {
      const packageJson = JSON.parse(fs.readFileSync(candidate, 'utf8'));
      if (packageJson.name === packageName) return packageJson.version;
    }
    const parent = path.dirname(current);
    if (parent === current) return 'unknown';
    current = parent;
  }
}

async function exportLearnsets(dex, species) {
  const result = {};
  for (const pokemon of species) {
    const learnset = await dex.learnsets.get(pokemon.id);
    if (learnset.exists) result[pokemon.id] = plain(learnset);
  }
  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const outputRoot = path.resolve(args.output);
  const generations = new Generations(Dex);
  const generatedAt = new Date().toISOString();
  const versions = {
    '@pkmn/dex': packageVersion('@pkmn/dex'),
    '@pkmn/data': packageVersion('@pkmn/data'),
  };

  fs.mkdirSync(outputRoot, {recursive: true});

  for (const number of args.gens) {
    if (!Number.isInteger(number) || number < 1 || number > 9) {
      throw new Error(`Unsupported generation: ${number}`);
    }

    const generation = generations.get(number);
    const dex = Dex.forGen(number);
    const directory = path.join(outputRoot, `gen${number}`);
    fs.mkdirSync(directory, {recursive: true});

    // @pkmn/data iterators apply generation-aware existence/legality filtering.
    const speciesList = Array.from(generation.species);

    const files = {
      'moves.json': keyed(generation.moves),
      'abilities.json': keyed(generation.abilities),
      'items.json': keyed(generation.items),
      'species.json': keyed(speciesList),
      'types.json': keyed(generation.types),
      'natures.json': keyed(generation.natures),
      'conditions.json': plain(dex.data.Conditions),
      'learnsets.json': await exportLearnsets(dex, speciesList),
    };

    for (const [filename, data] of Object.entries(files)) {
      writeJson(path.join(directory, filename), data, args.compact);
    }

    const counts = Object.fromEntries(
      Object.entries(files).map(([filename, data]) => [filename, Object.keys(data).length]),
    );

    writeJson(
      path.join(directory, 'metadata.json'),
      {
        generation: number,
        generatedAt,
        packages: versions,
        counts,
      },
      args.compact,
    );

    console.log(`gen${number}: ${counts['species.json']} species, ${counts['moves.json']} moves`);
  }

  writeJson(
    path.join(outputRoot, 'metadata.json'),
    {
      generatedAt,
      generations: args.gens,
      packages: versions,
      source: 'Pokémon Showdown data resolved through @pkmn/dex and @pkmn/data',
    },
    args.compact,
  );
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exitCode = 1;
});
