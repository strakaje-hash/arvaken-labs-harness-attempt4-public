# Files removed from this public copy

This repository is the public copy of the Arvaken Labs harness for attempt 4, published with Arvaken Labs Paper 1,
*Where the stop happens* (DOI 10.5281/zenodo.23145196). It is the tree of commit
`25057738f8c90285a4dc6853b8dd798da35972ef` of the Lab's private harness repository, with the changes listed here: the
four web pages the Lab captured whole on 15 September 2026 as evidence for the enterprise deltas and the claimed sources
are replaced, and three of the Lab's own files are updated for this copy (below). The pages are the vendors', not the
Lab's, so this copy does not redistribute them.

- **Each is replaced at its path by a short note** naming the page, the revision the Lab read, when it was retrieved, and
  its SHA-256. The harness loads as frozen: its registry requires every cited evidence file to exist.
- **One test reads the pages' bytes and quotes:** `packages/platform/tests/test_registry.py`'s
  `test_every_cited_quote_is_in_its_preserved_document_and_every_document_is_byte_exact`. It is among the release check's
  explained failures in this copy.
- **Their licence:** these pages are their authors' and are not covered by this repository's Apache-2.0 licence
  (`NOTICE`). The projects' own MIT licence files stay in `targets/evidence/2026-09-15/`.
- **The originals:** `FROZEN-BLOBS.txt` still lists these four files' git blob ids as frozen. The anchored commit and its
  archive, `arvaken-labs-harness-attempt4-2505773.tar.gz` (SHA-256
  `b0d412c987c6ef3f02763427dca18ff556bfc8052ab73be07d094b6107b36b91`, Rekor log index 2942311003), hold the pages
  and are available on request (https://arvaken.com/contact).

| path | the page | revision the Lab read (the page's dateModified) | retrieved (UTC) | SHA-256 as retrieved | nearest Wayback Machine captures |
| --- | --- | --- | --- | --- | --- |
| `targets/evidence/2026-09-15/langgraph-interrupts.html` | https://docs.langchain.com/oss/python/langgraph/interrupts | 2026-09-15T11:23:42Z | 2026-09-15T14:33:30Z | `0cf18a7289dbc797067191a015a346c18fc4228b41a126d2da966f76b36ef3c6` | [2026-09-09](https://web.archive.org/web/20260909160513/https://docs.langchain.com/oss/python/langgraph/interrupts) (before), [2026-09-30](https://web.archive.org/web/20260930211838/https://docs.langchain.com/oss/python/langgraph/interrupts) (after) |
| `targets/evidence/2026-09-15/langsmith-deployments.html` | https://docs.langchain.com/langsmith/deployments | 2026-08-26T13:41:03Z | 2026-09-15T14:33:30Z | `13b999939ab7fe53b98ed402a9aeada0a0523e9d7920ccfe96b7cfeffd2705bf` | [2026-03-10](https://web.archive.org/web/20260310095921/https://docs.langchain.com/langsmith/deployments) (before, and older than the revision the Lab read; there is none after) |
| `targets/evidence/2026-09-15/openhands-cloud.html` | https://docs.openhands.dev/openhands/usage/cloud/openhands-cloud | 2026-01-26T12:36:31Z | 2026-09-15T14:35:03Z | `7570f74878f80afc0f5041c9bf0cf678ba501905071967a3697209754e6bf45b` | [2026-08-20](https://web.archive.org/web/20260820142321/https://docs.openhands.dev/openhands/usage/cloud/openhands-cloud) (before), [2026-09-29](https://web.archive.org/web/20260929030100/https://docs.openhands.dev/openhands/usage/cloud/openhands-cloud) (after) |
| `targets/evidence/2026-09-15/openhands-pause.html` | https://docs.openhands.dev/sdk/guides/convo-pause-and-resume | 2026-06-24T14:29:53Z | 2026-09-15T14:33:30Z | `0ea69fb0296579fe7843e02b2620ebc97b4bb7dfa6efcdfd54885ff55c49278f` | [2026-06-09](https://web.archive.org/web/20260609051452/https://docs.openhands.dev/sdk/guides/convo-pause-and-resume) (before, and older than the revision the Lab read; there is none after) |

A Wayback Machine capture is the Internet Archive's copy, not the Lab's. Its bytes will not match the SHA-256 above, and
a capture from another day may show another revision of the page. The captures were looked up in the Wayback Machine's
index on 2026-10-04.

## The other changes from the anchored commit

- `README.md`: the embargo paragraph is replaced by this copy's publication statement, and the README names the
  replaced pages, the thirteenth explained failure, the timing-sensitive test, and this copy's release-check run.
- `NOTICE`: adds the MIT licence notice of https://github.com/OpenHands/OpenHands, for
  `targets/evidence/2026-09-15/openhands-app-README.md`, which the anchored commit carried without it.
- `release_check.py`: expects a thirteenth failure, the registry test that reads the four pages byte for byte.
- `REMOVED-FILES.md`: this file, added.

Every other file is the anchored commit's, byte for byte.
