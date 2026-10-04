// Node side of the timing shim (packages/timing). Same contract as mark_timing: monoNs() for arithmetic,
// wallNs() for humans, clockSource() recorded next to every reading. Node exposes CLOCK_MONOTONIC via
// process.hrtime.bigint(), not CLOCK_MONOTONIC_RAW; the source string says so.
export const CLOCK_SOURCE = 'node:hrtime(CLOCK_MONOTONIC)';
export function monoNs() { return process.hrtime.bigint(); }
export function wallNs() { return BigInt(Date.now()) * 1000000n; }
export function clockSource() { return CLOCK_SOURCE; }
export function stamp() { return { mono_ns: monoNs(), wall_ns: wallNs(), clock: CLOCK_SOURCE }; }
