# syntax=docker/dockerfile:1

#####################################################################
# Build stage: compiles login-server, char-server and map-server
#####################################################################
FROM debian:bookworm-slim AS build

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        g++ make libmariadb-dev libmariadb-dev-compat zlib1g-dev libpcre3-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Only what the compiler needs, so editing conf/db/npc does not trigger a recompile
COPY configure Makefile.in ./
COPY 3rdparty ./3rdparty
COPY src ./src

# Extra ./configure flags, e.g. --enable-prere=yes --enable-vip=yes
# (PACKETVER comes from src/custom/defines_pre.hpp)
ARG CONFIGURE_FLAGS=""

RUN sed -i 's/\r$//' configure \
 && chmod +x configure \
 && ./configure ${CONFIGURE_FLAGS} \
 && make -j"$(nproc)" login char map

#####################################################################
# Runtime stage
#####################################################################
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libmariadb3 zlib1g libpcre3 tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --home-dir /rathena --shell /usr/sbin/nologin rathena

WORKDIR /rathena

COPY conf ./conf
COPY db ./db
COPY npc ./npc
COPY tools/docker/entrypoint.sh /usr/local/bin/rathena-entrypoint

# Same as "make import": fill the import folders from their templates where files are missing
RUN for d in conf conf/msg_conf db; do \
        mkdir -p "$d/import"; \
        for f in "$d"/import-tmpl/*; do \
            [ -e "$d/import/$(basename "$f")" ] || cp "$f" "$d/import/"; \
        done; \
    done \
 && sed -i 's/\r$//' /usr/local/bin/rathena-entrypoint \
 && chmod +x /usr/local/bin/rathena-entrypoint \
 && mkdir -p log \
 && chown -R rathena:rathena /rathena

COPY --from=build --chown=rathena:rathena \
     /build/login-server /build/char-server /build/map-server ./

USER rathena

# login / char / map
EXPOSE 6900 6121 5121

ENTRYPOINT ["rathena-entrypoint"]
CMD ["login"]
