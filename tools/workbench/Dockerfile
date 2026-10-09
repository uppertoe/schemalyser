# syntax=docker/dockerfile:1.7
#
# The image of the local workbench: the core, the workbench, DuckDB, the invented world, htmx and Alpine.js, and a
# built copy of the page Describe the record when site/dist exists. Build it from the root of the project:
#
#     docker build -f tools/workbench/Dockerfile -t schemalyser-workbench:dev .
#
# The target "sqlserver" adds the Docker command line, which the SQL Server harness uses to reach its container;
# compose.yaml builds it for the sqlserver profile only. README.md in this folder says how to run either.
#
# The base is the official Python 3.13 slim image, pinned by the digest of its multi-platform index.
FROM python:3.13.16-slim-trixie@sha256:bb2988715db2cf7ace7b53f38f3cffbef7c7046a656bee66245eb0ed386e2e81 AS python

# htmx and Alpine.js are fetched once, at build time, from the npm registry, and checked against their checksums.
# The running workbench serves them itself and fetches nothing.
FROM python AS vendor
ADD --checksum=sha256:9fc84a15e2ccd7242248b1eac5d6103b8875dd5d140815897241949d91d80c69 \
    https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.7.tgz /vendor/htmx.tgz
ADD --checksum=sha256:09b0b1c4218a5b1a8f5f650f69161c44413cc1e04dabb79e8a93d85e445dc4e9 \
    https://registry.npmjs.org/alpinejs/-/alpinejs-3.15.0.tgz /vendor/alpine.tgz
RUN cd /vendor \
    && tar -xzf htmx.tgz -O package/dist/htmx.min.js > htmx.min.js \
    && tar -xzf alpine.tgz -O package/dist/cdn.min.js > alpine.min.js \
    && rm htmx.tgz alpine.tgz

FROM python AS core
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/core \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# The pinned packages are installed from wheels whose checksums are given, and pip is then removed, so that the image
# holds no package installer that could reach the network.
COPY tools/workbench/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-deps --only-binary :all: --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && python -m pip uninstall -y pip \
    && useradd --uid 10001 --create-home --home-dir /home/workbench --shell /usr/sbin/nologin workbench

# The code and the invented world belong to root, so that the user who runs the workbench cannot change them.
COPY core/pyproject.toml /app/core/pyproject.toml
COPY core/schemalyser /app/core/schemalyser
COPY fixtures /app/fixtures
COPY tools/sqlserver/harness.py /app/tools/sqlserver/harness.py
COPY --from=vendor /vendor/htmx.min.js /vendor/alpine.min.js /app/core/schemalyser/workbench/static/
# The page Describe the record is copied only when it has been built (npm run build in site/).
RUN --mount=type=bind,target=/context \
    if [ -f /context/site/dist/index.html ]; then mkdir -p /app/site && cp -R /context/site/dist /app/site/dist; fi \
    && python -m compileall -q /app/core/schemalyser

USER workbench
WORKDIR /project
EXPOSE 8765
ENTRYPOINT ["python", "-m", "schemalyser.workbench", "--project", "/project", "--host", "0.0.0.0", "--port", "8765"]

# The same image with the Docker command line, for the sqlserver profile: the harness reaches SQL Server with
# docker exec and docker cp, through the Docker socket that compose.yaml mounts for that profile alone.
FROM core AS sqlserver
USER root
ARG TARGETARCH
ADD --checksum=sha256:de54e37157f45a43f42f6271302372d95c0eb992cc35ecaee74989bb14058c94 \
    https://download.docker.com/linux/static/stable/aarch64/docker-28.5.1.tgz /tmp/docker-arm64.tgz
ADD --checksum=sha256:5c0d19f31fece1accd0358bb8cff591fe25d7b6cba19f0fd412cbfdc07f75ff6 \
    https://download.docker.com/linux/static/stable/x86_64/docker-28.5.1.tgz /tmp/docker-amd64.tgz
RUN tar -xzf /tmp/docker-${TARGETARCH}.tgz -C /tmp docker/docker \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker /tmp/docker-*.tgz
USER workbench

# The default target, which docker build makes when no --target is given.
FROM core AS workbench
