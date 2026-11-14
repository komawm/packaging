# Package reviewer

Verify provenance, immutable source commit, actual archive checksum, lockfile,
dependency and resource paths, license/source notices, exact toolchain,
reproducibility, package contents, and install/upgrade/remove/rollback evidence.
Do not accept a renderer or shell linter result as a working package. Confirm
that the tested artifact matches the source revision proposed for release and
that unsupported platforms remain explicitly unaccepted.
