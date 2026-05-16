FROM rust:1.78-slim AS builder

WORKDIR /build
COPY . .
RUN cargo build --release -p veyn-core

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/target/release/veyn-core /usr/local/bin/veyn

WORKDIR /app
EXPOSE 7700 7701

ENTRYPOINT ["veyn", "serve"]
