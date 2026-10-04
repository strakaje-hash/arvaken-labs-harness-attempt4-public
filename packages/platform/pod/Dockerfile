# platform-runtime: the image every measured run executes in, built by .github/workflows/platform-image.yml on a
# linux/amd64 runner and pinned by digest in pod/IMAGE and in every run manifest (pins.image_digest).
#
# EVERY INPUT IS PINNED AND VERIFIED (attempt 4, 2026-09-21). Until this rewrite the build fetched four things
# without checking any of them -- two installers piped straight into a shell (nodesource, astral.sh) and two release
# tarballs extracted as downloaded (otelcol, tempo) -- and the base image was named by a floating tag. The bytes
# were frozen by the resulting digest and reproducible from it; the inputs were never verified against their
# publishers. Every published bundle from attempts 1-3 cites a digest built that way, which is recorded in the
# provenance section beside the anchors. This is the first build that does not.
#
# What each input is verified against, and by whom:
#   base image  digest, resolved from the registry (was `:1.0.2-cu1281-torch280-ubuntu2404`, a tag that can move)
#   node        nodejs.org's SHASUMS256.txt (the project publishes it; it is itself GPG-signed). v22 LTS, which is
#               what package.json's engines field has required all along
#   uv          the release's own uv-...tar.gz.sha256 (the project publishes it per asset)
#   tempo       the release's SHA256SUMS (the project publishes it)
#   otelcol     a COSIGN SIGNATURE over the artifact, not a hash: this release publishes no .sha256 for the linux
#               tarball, and the signature is checked against a PINNED identity and issuer below, so "verified by
#               signature" means this workflow signed it and not merely that someone did
#   cosign      a sha256 recorded HERE, read on 2026-09-21 from sigstore/cosign v3.1.3's published
#               cosign_checksums.txt. This is the floor: cosign's own checksums file is attested by a sigstore
#               bundle that needs cosign to verify, so the chain bottoms out at one hash a person recorded, and the
#               build says so rather than implying it verifies all the way down.
#   apt         the distribution's package signatures. Versions are NOT pinned, and that is stated rather than
#               described as a hash, because "signed by the distro" and "pinned to a version" are different facts.
#
# Nothing is fetched at boot: pod/setup.sh no longer installs any of these and fails with the image digest it
# expected if one is missing. A pod that was not created from this image is not the instrument.
ARG BASE=runpod/pytorch@sha256:0a360022e8de4375af99430f84e8b38951acc397252163a37ceac7204d01be35
FROM ${BASE}

# Node 22 LTS, not 20: package.json declares `engines.node >= 22.13` and `packageManager pnpm@11.1.3`, and pnpm 11
# does not run on Node 20 (ERR_UNKNOWN_BUILTIN_MODULE). The image shipped Node 20 from `setup_20.x` since September
# and nothing noticed, because nothing on the pod ever executed pnpm -- the `pnpm --version` smoke below is what
# surfaced it, on the first build that ran the binaries it installs.
ARG NODE_VERSION=22.23.2
ARG NODE_SHA256=d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307
ARG PNPM_VERSION=11.1.3
ARG UV_VERSION=0.12.17
ARG UV_SHA256=fa82fd8dde8e8eefdecada6aa0889666556cfceb690d06e0c3bca49eb3070a63
ARG COSIGN_VERSION=3.1.3
ARG COSIGN_SHA256=4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71
ARG OTELCOL_VERSION=0.128.0
ARG OTELCOL_IDENTITY=https://github.com/open-telemetry/opentelemetry-collector-releases/.github/workflows/base-release.yaml@refs/tags/v0.128.0
ARG OTELCOL_OIDC_ISSUER=https://token.actions.githubusercontent.com
ARG TEMPO_VERSION=2.8.1
ARG TEMPO_SHA256=3ae537720814c7c5bea9e7ff82c373b62c44eea1271e1d0ac34d92922f53b223
ENV DEBIAN_FRONTEND=noninteractive

# apt: signed by the distribution, versions not pinned. Stated, not dressed up as a hash.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      git curl ca-certificates bubblewrap firejail postgresql postgresql-contrib iptables sudo jq xz-utils \
      procps ninja-build libcap2-bin util-linux \
 && rm -rf /var/lib/apt/lists/*

# cosign: the floor of the chain. Verified against the sha256 recorded above, which was read from the project's
# published cosign_checksums.txt on 2026-09-21.
RUN curl -fsSL -o /tmp/cosign "https://github.com/sigstore/cosign/releases/download/v${COSIGN_VERSION}/cosign-linux-amd64" \
 && echo "${COSIGN_SHA256}  /tmp/cosign" | sha256sum -c - \
 && install -m 0755 /tmp/cosign /usr/local/bin/cosign \
 && rm /tmp/cosign \
 && cosign version

# node: the official release tarball, verified against nodejs.org's SHASUMS256.txt. Replaces `curl … | bash`.
RUN curl -fsSL -o /tmp/node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
 && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
 && tar -C /usr/local --strip-components=1 -xJf /tmp/node.tar.xz \
      --exclude=CHANGELOG.md --exclude=LICENSE --exclude=README.md \
 && rm /tmp/node.tar.xz \
 && corepack enable \
 && corepack prepare "pnpm@${PNPM_VERSION}" --activate \
 && node --version && pnpm --version

# uv: the release archive, verified against the project's per-asset .sha256. Replaces `curl … | sh`.
RUN curl -fsSL -o /tmp/uv.tar.gz "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" \
 && echo "${UV_SHA256}  /tmp/uv.tar.gz" | sha256sum -c - \
 && tar -C /tmp -xzf /tmp/uv.tar.gz \
 && install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uv /usr/local/bin/uv \
 && install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/uvx \
 && rm -rf /tmp/uv.tar.gz /tmp/uv-x86_64-unknown-linux-gnu \
 && uv --version
ENV PATH="/usr/local/bin:${PATH}"

# otelcol-contrib: verified by cosign signature against a PINNED identity and issuer. This release publishes no
# .sha256 for the linux tarball; the signature binds the artifact to the release workflow that produced it, which is
# the stronger statement, and pinning the identity is what makes it a verification rather than "signed by someone".
RUN curl -fsSL -o /tmp/otelcol.tar.gz "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${OTELCOL_VERSION}/otelcol-contrib_${OTELCOL_VERSION}_linux_amd64.tar.gz" \
 && curl -fsSL -o /tmp/otelcol.sig "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${OTELCOL_VERSION}/otelcol-contrib_${OTELCOL_VERSION}_linux_amd64.tar.gz.sig" \
 && curl -fsSL -o /tmp/otelcol.pem "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${OTELCOL_VERSION}/otelcol-contrib_${OTELCOL_VERSION}_linux_amd64.tar.gz.pem" \
 && cosign verify-blob /tmp/otelcol.tar.gz \
      --signature /tmp/otelcol.sig \
      --certificate /tmp/otelcol.pem \
      --certificate-identity "${OTELCOL_IDENTITY}" \
      --certificate-oidc-issuer "${OTELCOL_OIDC_ISSUER}" \
 && sha256sum /tmp/otelcol.tar.gz \
 && tar -C /usr/local/bin -xzf /tmp/otelcol.tar.gz otelcol-contrib \
 && rm /tmp/otelcol.tar.gz /tmp/otelcol.sig /tmp/otelcol.pem \
 && otelcol-contrib --version

# tempo: verified against the project's published SHA256SUMS.
RUN curl -fsSL -o /tmp/tempo.tar.gz "https://github.com/grafana/tempo/releases/download/v${TEMPO_VERSION}/tempo_${TEMPO_VERSION}_linux_amd64.tar.gz" \
 && echo "${TEMPO_SHA256}  /tmp/tempo.tar.gz" | sha256sum -c - \
 && tar -C /usr/local/bin -xzf /tmp/tempo.tar.gz tempo \
 && rm /tmp/tempo.tar.gz \
 && (tempo --version || tempo -version)

# EVERY TOOL THIS IMAGE INSTALLS RUNS HERE, OR THE BUILD FAILS. The image shipped pnpm 11 on Node 20 for three
# months -- a pnpm that could not execute -- because every checksum passed and nothing ever ran the binary.
# "Installed" and "works" are different facts, and only the second is proven by running it. The apt-installed
# binaries are here too: capsh and unshare are what the isolation probe MEASURES, ninja is what vLLM's JIT execs,
# and a missing one of those does not fail at boot -- it fails later as a wrong measurement.
RUN set -eux; \
    node --version; pnpm --version; uv --version; cosign version; \
    otelcol-contrib --version; (tempo --version || tempo -version); \
    git --version; curl --version | head -1; jq --version; \
    bwrap --version; firejail --version | head -1; iptables --version; sudo --version | head -1; \
    capsh --print > /dev/null; unshare --help > /dev/null; runuser --help > /dev/null; \
    ninja --version; ps --version; psql --version; xz --version | head -1

RUN useradd --create-home --shell /bin/bash runner
COPY pod/setup.sh /opt/platform/setup.sh
# The repo, the locked Python environment and the vLLM environment are laid down by setup.sh at first boot
# (offline: from /workspace/mark.bundle and the side-loaded wheel/model caches), so no credential is baked in.
# setup.sh installs none of the tools above: it checks for them and stops, naming this image, if one is absent.
CMD ["/start.sh"]
