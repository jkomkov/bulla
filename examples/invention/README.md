# Experimental predicate invention

Run the finite reference engine:

    bulla experimental invent bulla/examples/invention/definable.json -o result.json

Replay the package:

    bulla experimental verify-invention bulla/examples/invention/definable.json result.json

This surface is experimental. The generated PredicatePackage is not a stable
Bulla wire format.
