# Independent boundary-checker packet

Implement from the written v0.5 profile only:

- canonical JSON parsing and hashing;
- claim-flow kinds;
- candidate, adoption, and applicability separation;
- authority and semantic-epoch checks;
- finality classification;
- canonical result hashing.

Do not rebuild invention, Golden, witnesses, or the Bulla CLI. Do not import,
copy, or inspect Bulla's checker until outputs are committed. Return source,
environment, actual vectors, hashes, every ambiguity, and an authorship and
prior-exposure declaration. The implementer owns the code and need not endorse
Bulla.
