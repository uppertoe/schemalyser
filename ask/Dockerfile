# syntax=docker/dockerfile:1
# Built from the repository's root, so that the service embeds the same role contract as the page:
#   docker build -f ask/Dockerfile -t schemalyser-ask .
FROM golang:1.23-alpine AS build
WORKDIR /src
COPY ask/go.mod ask/go.sum ./
RUN go mod download
COPY ask/ ./
COPY core/schemalyser/rolemodel/contract.json internal/rolemodel/contract.json
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/ask .
RUN mkdir -p /data && chown 65532:65532 /data

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/ask /app
COPY --from=build --chown=65532:65532 /data /data
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/app"]
