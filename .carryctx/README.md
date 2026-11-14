# Packaging CarryCtx

This directory versions configuration and small operating guides for the
[packaging repository](https://github.com/komawm/packaging). Runtime task,
session, and event state is stored under the Git common directory; never commit
or publish the raw database.

Use the `PKG` prefix, named sessions, exact scopes, checkpoints, and `just check`.
Read the [release workflow](https://github.com/komawm/packaging/blob/main/.carryctx/workflows/package-acceptance.md)
and [delivery rules](https://github.com/komawm/packaging/blob/main/.carryctx/rules/delivery.md).
Use the [reviewer guide](https://github.com/komawm/packaging/blob/main/.carryctx/personas/reviewer.md)
when independently checking package evidence. These Markdown guides are not
automatically activated preset packs.
