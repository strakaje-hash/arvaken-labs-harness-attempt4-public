"""A call's first receipt by the harness, and the process behind it, carried hop to hop (attempt 4, A2 and A3).

Between an agent and the mock world there may be one, two or three harness-owned hops: the Tier B egress proxy
(every runner scenario steers the agent's HTTP through it), the credential gateway (out-of-process controls), and the
world itself. The OS revealed the first of those the hard way: on every runner scenario the world's socket peer was
the harness process, not the agent, because the egress proxy had re-issued the request. Which means two facts the
verdicts need are only knowable at the FIRST harness hop the call reaches:

  * **when the harness first received it** -- the receipt of record that A2 orders effects by;
  * **which process sent it** -- the pid owning the socket, which A3 attributes effects by.

Each hop calls `receive` on the way in. It resolves its own peer from the kernel and applies one rule:

    a hop trusts the hop headers it received only when its own peer IS the harness process.

A process that is not the harness cannot own a socket the harness owns, so a header arriving on a socket the
kernel attributes to the harness pid came from an upstream harness hop; a header arriving on any other socket came
from the agent and is dropped and noted. No policy flag decides trust: the kernel does. The world writes what the
first hop saw (`hop_arrived_mono_ns`, `hop`, the pid) and what it saw itself, side by side.

Two shapes stay unattributed on purpose. A CONNECT tunnel the egress proxy bridges carries no headers, and a call the
harness process makes itself carries none either; in both the world's peer is the harness with no upstream hop
headers, and it cannot tell the two apart, so it records neither as anyone's.
"""
from __future__ import annotations

import os
from typing import Any, Mapping

from .clock import mono_ns
from .process_identity import owner_pid

PEER_PID_HEADER = "X-Mark-Hop-Peer-Pid"
ARRIVED_HEADER = "X-Mark-Hop-Arrived-Ns"
HOP_HEADER = "X-Mark-Hop"
# How the first hop established the pid (kernel | resolver | unavailable | none), and, when it established none, why.
# Without these the world could only say "forwarded by an upstream harness hop" -- which on attempt 4's first matrix run
# was all 64 propagation replications could say, while the real reason sat in the egress log (founder ruling 2026-09-23).
ATTRIBUTION_HEADER = "X-Mark-Hop-Attribution"
REASON_HEADER = "X-Mark-Hop-Pid-Reason"
HOP_HEADERS = (PEER_PID_HEADER, ARRIVED_HEADER, HOP_HEADER, ATTRIBUTION_HEADER, REASON_HEADER)
ATTRIBUTIONS = ("kernel", "resolver", "unavailable", "none")
_LOWER = tuple(h.lower() for h in HOP_HEADERS)


def receive(headers: Mapping[str, str], *, client_port: int | None, server_port: int | None, hop: str, arrived: int | None = None) -> tuple[dict[str, str], dict[str, Any]]:
    """Called by a harness hop as a request arrives. Returns (the headers to forward, with the hop headers set for the
    next hop; the facts this hop established). `arrived` is the receipt stamp the hop already took, when it took one
    before calling here -- one event, one stamp; the world passes its own receipt so that, with nothing in front, the
    stamp of record IS the receipt. Never raises: an unresolved peer is a recorded fact."""
    arrived = arrived if arrived is not None else mono_ns()
    incoming = {k.lower(): v for k, v in headers.items() if k.lower() in _LOWER}
    supplied = bool(incoming)
    peer, method = None, "no client port"
    if client_port:
        try:
            peer, method = owner_pid(client_port, server_port=server_port)
        except Exception as e:  # noqa: BLE001
            peer, method = None, f"resolver raised {type(e).__name__}: {e}"[:200]
    peer_is_harness = peer is not None and peer == os.getpid()
    up_pid = incoming.get(PEER_PID_HEADER.lower())
    up_arrived = incoming.get(ARRIVED_HEADER.lower())
    trusted = peer_is_harness and up_arrived is not None and up_arrived.isdigit()
    if trusted:
        pid = int(up_pid) if up_pid and up_pid.isdigit() else None
        arrived_of_record, first_hop = int(up_arrived), incoming.get(HOP_HEADER.lower()) or "upstream"
        up_att = incoming.get(ATTRIBUTION_HEADER.lower())
        attribution = up_att if up_att in ATTRIBUTIONS else ("kernel" if pid is not None else "none")
        up_reason = incoming.get(REASON_HEADER.lower())
        pid_source = (f"forwarded by an upstream harness hop ({attribution})" if pid is not None
                      else (up_reason or "forwarded by an upstream harness hop, which established no pid and gave no reason"))
    elif peer_is_harness:
        # the harness process itself, or a tunnel it bridged without headers: not attributable to any agent process
        pid, arrived_of_record, first_hop, attribution = None, arrived, hop, "none"
        pid_source = "the peer is the harness process and no upstream hop headers arrived: unattributed (a direct harness call, or a bridged tunnel)"
    else:
        from .process_identity import attribution_of

        pid, arrived_of_record, first_hop = peer, arrived, hop
        attribution = attribution_of(peer, method)
        pid_source = method if peer is not None else (method if attribution == "unavailable" else f"unresolved: {method}")
    facts = {"hop": hop, "peer_pid": peer, "peer_resolution": method, "peer_is_harness": peer_is_harness, "trusted_upstream": trusted,
             "dropped_supplied": supplied and not trusted, "pid": pid, "pid_source": pid_source, "arrived_mono_ns": arrived_of_record, "first_hop": first_hop,
             "this_hop_arrived_mono_ns": arrived, "attribution": attribution}
    out = {k: v for k, v in headers.items() if k.lower() not in _LOWER}
    if pid is not None:
        out[PEER_PID_HEADER] = str(pid)
    else:
        # one line, header-safe: the reason is evidence for the record, not a value anything parses
        out[REASON_HEADER] = " ".join(str(pid_source).split())[:400].encode("ascii", "replace").decode("ascii")
    out[ARRIVED_HEADER] = str(arrived_of_record)
    out[HOP_HEADER] = first_hop
    out[ATTRIBUTION_HEADER] = attribution
    return out, facts
