# Packaging delivery rules

Keep current work local-only and publishing disabled. Use a scoped task and
named session; record source revision, immutable inputs, verification commands,
observed results, and remaining work. Checkpoint before ending sessions.

No fabricated source checksums, mutable release source, silent dependency
patches, or automatic publication from a failing baseline. Build and test
installation/upgrade/removal/rollback in an isolated target environment.
Preserve product licensing separately from MIT packaging scripts. Shared external
Phodopus is read-only; never write through its workspace link.

Run checks locally and review package evidence. Generic CI, Issue/PR, and bot
review workflows are not required here. A later explicitly authorized CD step
uploads accepted artifacts to the owning product repository's GitHub Releases:
Koma to komawm/koma, and accepted future Jiku to komawm/jiku. Scope credentials
to that target repository; the packaging workflow's GITHUB_TOKEN is limited to
packaging. Record credential names and purpose, never values.
