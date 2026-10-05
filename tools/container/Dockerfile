# syntax=docker/dockerfile:1
#
# The image that runs the boundary: the analyser, the check script, the register of open questions
# and the checklist for each target query, from a state folder and a requests folder.
#
# Build it from the root of the project, so that it can copy core/schemalyser:
#
#     docker build -f tools/container/Dockerfile -t schemalyser-boundary:dev .
#
# Run it with no network and read-only inputs:
#
#     docker run --rm --network none --read-only --tmpfs /tmp \
#         -v STATE:/state:ro -v REQUESTS:/requests:ro -v OUT:/out schemalyser-boundary:dev
#
# The base is the official Python 3.13 slim image, pinned by the digest of its multi-platform index,
# so that the same image is used on Apple Silicon and on an x86 runner.
FROM python:3.13.16-slim-trixie@sha256:bb2988715db2cf7ace7b53f38f3cffbef7c7046a656bee66245eb0ed386e2e81

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# The pinned packages are installed from wheels whose checksums are given, and pip is then removed,
# so that the image holds no package installer that could reach the network.
COPY tools/container/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-deps --only-binary :all: --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && python -m pip uninstall -y pip \
    && useradd --system --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin boundary

# Only the package is copied. It belongs to root, so that the user who runs it cannot change it.
COPY core/schemalyser /app/schemalyser
RUN python -m compileall -q /app/schemalyser

USER boundary
WORKDIR /app
ENTRYPOINT ["python", "-m", "schemalyser.boundary", "--state", "/state", "--requests", "/requests", "--out", "/out"]
