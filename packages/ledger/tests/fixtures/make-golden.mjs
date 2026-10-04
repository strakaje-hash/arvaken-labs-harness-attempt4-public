// Produces canonical-golden.txt from canonical-input.json with the TS reference implementation
// (packages/core canonicalJson). Run: node packages/ledger/tests/fixtures/make-golden.mjs
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { canonicalJson } from '../../../core/dist/index.js';

const here = dirname(fileURLToPath(import.meta.url));
const input = JSON.parse(readFileSync(join(here, 'canonical-input.json'), 'utf8'));
const out = canonicalJson(input);
writeFileSync(join(here, 'canonical-golden.txt'), out);
console.log(out);
