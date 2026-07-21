# Routed-inference external implementer instructions

This package has two different uses. Keep them separate.

## 1. Reproduce the supplied fixtures

The checker imports no Bulla code. Its digest and profile-predicate rungs use only
the Python standard library, but the canonical expected reports reach identity depth
and therefore require PyNaCl:

```sh
python3 -m pip install pynacl
```

Then run the supplied zero-Bulla checker:

```sh
python3 check.py
```

This should report `14/14`. It proves that your Python environment reproduced the
published fixture verdicts. It is not an independent implementation.

Verify one trace and use its exit status:

```sh
python3 check.py verify 01-honest-balanced.json --json
```

Exit `0` means `CONFORMS`, `2` means `VIOLATES`, `3` means `UNDETERMINED`, and
`64` means the invocation or input could not be read.

Without PyNaCl, the checker still recomputes digests and profile predicates, but it
must downgrade authority-dependent results to `UNDETERMINED` at digest depth; that
reduced-depth run will not match the canonical 14/14 identity-depth reports.

## 2. Independently implement the profile

Use `PROFILE.md` as the normative source. Write your own canonicalization, hash,
ActionReceipt v0.3 preimage, signature, transition, budget, grounding, and ordering
checks. The supplied `check.py` is an oracle for comparison, not source to copy.

Return `CONFORMANCE-REPORT-TEMPLATE.json` with:

- a public commit and digest for your checker;
- whether it was independently written;
- environment and cryptographic dependency details;
- all fourteen actual reports;
- every divergence and ambiguity;
- a declaration that running the supplied checker alone is not being reported as an
  independent reproduction.

The profile remains draft and `external_reproductions` remains zero until such a report
is received and its checker can be run from a clean checkout.

## Boundaries

The package supports one router, one provider, full term disclosure, no discharge,
signed-declaration accounting, and unverified operational recourse. It contains no
provider adapter, witness network, settlement rail, stake, private fixture key,
selective-disclosure scheme, attenuation lattice, or normative WitnessBundle.
