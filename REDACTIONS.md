# Changes from the frozen tree

This tree is the Arvaken Labs harness exactly as frozen at `attempt4-freeze-4` (commit
`af1675dca1b08454baf4b8a924a3a7d0f98a1cc8` of the Lab's private repository), restricted to the harness, with the changes
below and nothing else, on the founder's rulings of 2026-09-24. The SHA-256 of each changed file as frozen:

| file | change | SHA-256 as frozen |
| --- | --- | --- |
| `pyproject.toml` | The three product packages (`mark-record`, `mark-product`, `mark-connectors`), which are not in this tree, removed from the workspace's dependencies, members, sources and test paths. Nothing else in the file changed. | `f58a817e16a7502cda630c47df12d89121eefb3bc3368e7652327c547e800464` |
| `uv.lock` | Re-locked after that edit, with uv 0.11.15. Exactly those three packages, and three libraries only they used (`pg8000`, `scramp`, `asn1crypto`), left the lock; nothing was added. Each of the 386 remaining packages resolves identically to the frozen lock (version, source, source archive and wheel hashes), checked package by package; the workspace root's own dependency list is the only other difference. | `5f317751086287517c42ddade3c1067e4cae20b7adb694187fba94d208374860` |
| `docs/attempt4-c2-operator-of-record.md` | An AWS account id replaced with `123456789012`, AWS's documented example account id (3 occurrences). | `de1e7dac008dc2e7f4dc988506d2456c6bdd8f53bdfafa3bfef71079223e435d` |
| `packages/platform/tests/test_operator.py` | The same id, replaced the same way (7 occurrences). | `84c20b505a7305534daa3bb2911df748a22860cadc1d2df3213576cadd3edbf9` |
| `packages/platform/tests/test_operator_bundle.py` | The same id, replaced the same way (1 occurrence). | `554b45f4c86a7f2f2108dd5b9f4df8906ce65623aea4a8e66219dc847f53519b` |

The account is the one the operator-of-record tests describe as the customer's. Its id changes no code path: the tests
sign their own identity document at run time, and all 16 tests in the two files pass on this tree (2026-09-24).

**Kept, on the founder's ruling.** Runpod pod ids appear in six files: `benchmarks/attempt4-preregistration-freeze4.md`
and `benchmarks/attempt4-preregistration.md` (both signed and anchored), `benchmarks/attempt4-agent-controls.yaml`,
`benchmarks/workloads.yaml`, `benchmarks/scope-corrections.yaml` and `targets/registry.yaml`. Removing them would change the
bytes of signed and hashed files and break their hashes. Every pod they name (`i2nil82qz0k50d`, `kviu0mir9u07f4`,
`r2tx7twfwe7ox5`) was checked on 2026-09-24 and is terminated: the Runpod account that ran them lists no pods and returns
"not found" for each id, and its billing records show each pod billed on that account (last on 2026-09-22, 2026-09-23 and
2026-09-23) and nothing after.

**Added:** `LICENSE` (Apache-2.0), `NOTICE`, `README.md`, this file, `FROZEN-BLOBS.txt`, `release_check.py` (runs the
suite and passes only if the failures are exactly the ones `README.md` explains), and
`benchmarks/attempt4-preregistration-freeze4.signed.json`: the gate-key signature over the freeze-4 pre-registration,
committed after the frozen commit, SHA-256 `a519e0aff462b42be97b759aab5d1a226a5813050ec447434c91dd55565a13e0` (anchored).

`FROZEN-BLOBS.txt` lists the git blob id of every one of the 382 files taken from the frozen commit, as frozen and before
any change, so each file can be compared with the frozen commit once the full repository is published.
`scripts/guard_pass.py` is one of them: it was left out of the first selection by mistake, and seven tests that need it
failed until it was restored (founder ruling 2026-09-24).
