# Package acceptance workflow

1. Record the selected distribution/architecture/backend and accepted product
   revision in a scoped CarryCtx task. Restore current context and check blockers.
2. Obtain real source/archive checksums in the authorized build task and render
   the local candidate. Review source paths, package metadata, and licenses.
3. Run repository checks, build in isolation, and exercise install, launch,
   shutdown, upgrade, removal, and rollback. Retain exact inputs and evidence.
4. Request independent review and resolve blocking defects. Record unsupported
   targets, failing checks, and follow-up tasks explicitly.
5. Checkpoint acceptance. Only a later explicitly authorized publication step
   may use narrowly scoped release credentials and upload accepted artifacts.
