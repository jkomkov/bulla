FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

WORKDIR /workspace
COPY bulla /workspace/bulla

LABEL org.opencontainers.image.title="Bulla Golden Gate v0.2 captive verifier"
LABEL org.opencontainers.image.description="Digest-pinned zero-import replay image; not an independent implementation"
LABEL org.opencontainers.image.source="https://github.com/Integral-Systems/res-agentica"

ENTRYPOINT ["python", "-I", "/workspace/bulla/scripts/verify_golden_v02.py"]
CMD ["/workspace/bulla/bench/golden/v0.2"]
