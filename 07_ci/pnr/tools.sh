#!/bin/bash
# tools.sh -- the place-and-route job's tools (drop 0.42, stream A2b), beside synth/tools.sh's (the OSS CAD Suite's
# Yosys, OpenSTA, the ASAP7 liberty files), which the job installs first for synth.py and prep.py:
#   the OpenROAD-flow-scripts docker image openroad/orfs:$ORFS_TAG -- OpenROAD, ORFS's flow/ and platforms, Yosys,
#                    KLayout, built by ORFS's release pipeline from the ORFS commit synth/tools.sh pins ($ORFS_COMMIT,
#                    `git describe` = $ORFS_TAG; one pin for both jobs). Why docker: no GitHub release ships an
#                    OpenROAD binary for this commit -- The-OpenROAD-Project/OpenROAD's quarterly releases (26Q1-26Q4)
#                    carry the source only, Precision Innovations moved its Ubuntu .deb builds off GitHub in 26Q1, and
#                    ORFS's GHCR images are tagged `latest` only -- and a source build of OpenROAD with its dependencies
#                    is an hour of the 6 h job before the first block. NEO_ORFS_IMAGE overrides the image (workflow input
#                    pnr_image). The image's flow/ is checked against the pin: flow/Makefile and scripts/variables.yaml
#                    of the image against the same files at $ORFS_COMMIT (a mismatch is printed, not fatal).
#   $T/FakeRAM2.0    tools/FakeRAM2.0 of the same ORFS commit (sparse checkout; pure Python): the memory generator
#                    ORFS's asap7 platform uses (the image leaves tools/ out except tools/install)
# $T = NEO_PNR_TOOLS (default /opt/neo-pnr; the CI job sets /home/runner/neo-pnr). The image name goes to $T/image.txt
# and, in CI, to $GITHUB_ENV as NEO_ORFS_IMAGE. Every download is a GitHub sparse checkout or the docker pull.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
T=${NEO_PNR_TOOLS:-/opt/neo-pnr}
ORFS_COMMIT=$(sed -n 's/^ORFS_COMMIT=//p' "$HERE/../synth/tools.sh")
ORFS_TAG=26Q4-124-g3451ca1f5
IMAGE=${NEO_ORFS_IMAGE:-openroad/orfs:$ORFS_TAG}
mkdir -p "$T"
cd "$T"
echo "== pnr tools into $T ($(date -u)); ORFS ${ORFS_COMMIT:0:9}, image $IMAGE =="
[ -n "$ORFS_COMMIT" ] || { echo "no ORFS_COMMIT in synth/tools.sh"; exit 1; }

# ---- the ORFS sources of the pin: FakeRAM2.0 and the files the image is checked against --------------------------------
if [ ! -f "$T/orfs/tools/FakeRAM2.0/run.py" ]; then
  rm -rf orfs && mkdir orfs && cd orfs
  git init -q && git remote add origin https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts
  git sparse-checkout init --no-cone
  git sparse-checkout set /tools/FakeRAM2.0/ /flow/Makefile /flow/scripts/variables.yaml
  git fetch -q --depth 1 --filter=blob:none origin "$ORFS_COMMIT"
  git checkout -q FETCH_HEAD
  cd "$T"
fi
ln -sfn "$T/orfs/tools/FakeRAM2.0" "$T/FakeRAM2.0"
echo "   FakeRAM2.0 of ORFS $(git -C "$T/orfs" rev-parse --short=9 HEAD): $(ls "$T/FakeRAM2.0/utils" | wc -l) modules"

# ---- the image ------------------------------------------------------------------------------------------------------
df -h / | tail -1
pulled=""
for i in 1 2 3; do
  if docker pull -q "$IMAGE"; then pulled=1; break; fi
  echo "   docker pull $IMAGE failed (attempt $i)"; sleep 20
done
if [ -z "$pulled" ]; then
  echo "!! the image $IMAGE is not available; ORFS images of 26Q4 on Docker Hub (the newest 100):"
  curl -sS --max-time 60 "https://hub.docker.com/v2/repositories/openroad/orfs/tags?page_size=100&name=26Q4" > hub_tags.json || true
  python3 - hub_tags.json <<'EOF' || true
import json, sys
try:
    tags = json.load(open(sys.argv[1])).get("results", [])
except (OSError, ValueError):
    tags = []
for t in tags:
    print("  ", t.get("name"), t.get("last_updated", ""))
print("   (%d tags listed)" % len(tags))
EOF
  exit 1
fi
docker image inspect "$IMAGE" --format '   image {{.Id}} {{.Size}} bytes, created {{.Created}}'
df -h / | tail -1
docker run --rm "$IMAGE" bash -c '
  /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -version 2>/dev/null | head -1 | sed "s/^/   openroad /"
  /OpenROAD-flow-scripts/tools/install/yosys/bin/yosys -V 2>/dev/null | sed "s/^/   /"
  (command -v klayout >/dev/null && klayout -v) | sed "s/^/   /"
  cd /OpenROAD-flow-scripts/flow && sha256sum Makefile scripts/variables.yaml' > image_check.txt
cat image_check.txt
mismatch=0
for f in flow/Makefile flow/scripts/variables.yaml; do
  want=$(sha256sum "$T/orfs/$f" | cut -d' ' -f1)
  grep -q "$want" image_check.txt || { echo "!! $f of the image differs from ORFS ${ORFS_COMMIT:0:9}'s"; mismatch=1; }
done
[ $mismatch = 0 ] && echo "   the image's flow/ matches ORFS ${ORFS_COMMIT:0:9} (Makefile, scripts/variables.yaml)"
echo "$IMAGE" > "$T/image.txt"
[ -n "${GITHUB_ENV:-}" ] && echo "NEO_ORFS_IMAGE=$IMAGE" >> "$GITHUB_ENV"
echo "== pnr tools ready ($(date -u)) =="
