# Docker image profiles

The default `Dockerfile` keeps the full Noto font set. External-agent builds can
set `INSTALL_EXTENDED_FONTS=false` to keep Noto core, CJK, UI core, mono, and
emoji while omitting the larger Noto extra, CJK extra, and UI extra packages.
`docker-compose.external-agent.yml` selects this smaller font profile. Use the
default profile when a deployment needs the additional scripts or font variants.
External-agent mode also omits spaCy, thinc, blis, and precompiled Python
bytecode. Mem0 is disabled in this mode; the default image retains those
dependencies and its startup bytecode.

Truth-Seeker's embedded editor needs its asset prefix at **build time**. Build
the complete image from this repository rather than copying a new Next.js build
over an older Presenton image:

```sh
docker build -f Dockerfile \
  --build-arg PRESENTON_EXTERNAL_AGENT=true \
  --build-arg INSTALL_EXTENDED_FONTS=false \
  --build-arg PRESENTON_HOST_ASSET_PREFIX=/api/presentation-editor-assets \
  --build-arg NEXT_PUBLIC_HOST_ASSET_PREFIX=/api/presentation-editor-assets \
  -t truth-presenton:issue-549-source-lean2-20260928 .
```

This exact local tag was built from the `presenton-ts-editor-549` worktree.
The build files can be reused in another checkout, but its source files must
also match to reproduce the same image.

On the 2026-09-28 local amd64 build, Docker reported 3.43 GB for this source
build versus 5.34 GB for the prior image layered over an existing 4.95 GB
Presenton image. The first source build, before removing external-agent NLP
packages and bytecode, was 3.93 GB. This is an image size comparison, not a
claim about total disk space: build cache, older images, the container's
writable layer, and the bind-mounted `/app_data` are separate. Python may
regenerate bytecode in the writable layer after startup.

The candidate was checked with authenticated template listing and workflow
preparation, Chinese text rendered by Chromium, and a five-page PPTX/PDF export.
The smaller second build also completed a fresh five-page PPTX/PDF export.
Additional font families and scripts need separate visual checks before using
the compact profile outside this integration.
