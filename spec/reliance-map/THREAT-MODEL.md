# Reliance Map 0.1 threat model

Protected conclusions are graph acceptance, correction acceptance, descendant
status, conditional action, path certificates, summary counts, and report
commitment.

Hostile cases include duplicate JSON members, unsafe integers, cycles, unknown
nodes, duplicate nodes or edges, invalid typed edges, noncanonical ordering,
duplicate artifact digests, graph-root substitution, cross-graph replay,
forged or stale correction authority, reordered or forked corrections,
target absence, path fabrication, incomplete ancestry presented as clear,
withheld graph members, and parser resource exhaustion.

The verifier assumes the external context itself was obtained through an
appropriate trust process. Acceptance of a finite graph does not prove that it
contains every dependency outside the supplied set.
