# @koma-wm/jiku

Desktop orchestration daemon (`jikud`) and CLI/MCP controller (`jiku`) for
[Koma](https://github.com/komawm/koma), the agent-native Wayland compositor.

This npm package installs the release binaries and nothing else. **It is not a
standalone desktop tool**: `jiku` needs a running Koma compositor and its
`koma-agent.sock` on the same machine. Installing the package does not start or
configure Koma.

```bash
bun add -g @koma-wm/jiku
# or
npm install -g @koma-wm/jiku
```

```bash
jikud --headless-check      # validate configuration, no graphical session needed
jiku status                 # needs a running Koma session
jiku mcp --print-tools      # inspect the MCP surface
```

## Scope

The npm scope is `@koma-wm` because `komawm` was already registered on the
registry. The source repository is
[komawm/jiku](https://github.com/komawm/jiku); the npm artifacts are assembled and
published by [komawm/packaging](https://github.com/komawm/packaging).

## Supported platforms

Linux only, on `x86_64` (glibc) and `aarch64` (glibc, including 16 KiB-page
distributions such as Asahi Linux). No Windows or macOS builds exist.

The binaries are selected through the platform packages
`@koma-wm/jiku-linux-x64` and `@koma-wm/jiku-linux-arm64`, which are installed as
optional dependencies. If your package manager is configured with
`--omit=optional`, install the matching platform package explicitly.

## Agent skill

The repository also ships an installable agent skill:

```bash
npx skills add komawm/jiku --skill jiku-desktop-orchestration -g
```

## License

`GPL-3.0-or-later`. See the bundled `LICENSE`. The agent skill under `skills/` is
MIT licensed and carries its own `LICENSE` file.
