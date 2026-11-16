"use strict";

// Launcher for the @koma-wm/jiku npm packages.
//
// The main package ships no binaries. Each platform package carries the real
// `jiku` / `jikud` executables and is selected by npm through `os`/`cpu` fields
// plus `optionalDependencies`, so an unsupported platform simply has no package
// installed and fails here with an actionable message.

const { spawn } = require("node:child_process");
const path = require("node:path");

const SCOPE = "@koma-wm";
const FORWARDED_SIGNALS = ["SIGINT", "SIGTERM", "SIGHUP"];

function platformPackage() {
    if (process.platform !== "linux") {
        return null;
    }
    return `${SCOPE}/jiku-linux-${process.arch}`;
}

function resolveBinary(binName) {
    const name = platformPackage();
    if (!name) {
        console.error(
            `@koma-wm/jiku supports Linux only; this host reported '${process.platform}'.`,
        );
        console.error(
            "Jiku drives a Koma Wayland compositor, so it has no Windows or macOS build.",
        );
        process.exit(1);
    }
    try {
        const manifest = require.resolve(`${name}/package.json`);
        return path.join(path.dirname(manifest), "bin", binName);
    } catch {
        console.error(`@koma-wm/jiku could not load ${name}.`);
        console.error(
            "The platform package is normally installed as an optional dependency.",
        );
        console.error(
            "Reinstall without --omit=optional, or install it directly:",
        );
        console.error(`  npm install -g ${name}`);
        process.exit(1);
    }
}

function main(binName) {
    const binary = resolveBinary(binName);
    const child = spawn(binary, process.argv.slice(2), { stdio: "inherit" });

    const handlers = new Map();
    for (const signal of FORWARDED_SIGNALS) {
        const handler = () => {
            child.kill(signal);
        };
        handlers.set(signal, handler);
        process.on(signal, handler);
    }

    child.on("error", (error) => {
        console.error(
            `@koma-wm/jiku failed to start ${binName}: ${error.message}`,
        );
        process.exit(1);
    });

    child.on("exit", (code, signal) => {
        for (const [name, handler] of handlers) {
            process.removeListener(name, handler);
        }
        if (signal) {
            process.kill(process.pid, signal);
            return;
        }
        process.exit(code === null ? 1 : code);
    });
}

module.exports = { main };
