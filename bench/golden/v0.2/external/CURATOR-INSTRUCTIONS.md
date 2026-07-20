# Curator-custodian instructions

Do not begin case creation until the candidate freeze record is complete.

Each curator independently creates twelve cases: eight machine/property cases and four adjudication or open-world closure challenges. Cases should originate in the curator's own problem inventory or independently selected public material, not from implementation-team prompts that imply an answer.

For each case:

1. assign a random opaque case ID;
2. canonicalize the plaintext input using the written profile;
3. create the oracle or adjudication question;
4. generate at least 32 bytes of nonce material;
5. commit to case ID, canonical oracle output, and nonce;
6. contribute the plaintext input to the runner packet and the oracle/nonce only to the SOPS archive.

Before commitment, disclose compensation, organizational ties, conflicts, and prior exposure. Do not send age private keys, plaintext oracle material, or nonces to the implementation team. Two custodians jointly reveal only after both result submissions have receipts.
