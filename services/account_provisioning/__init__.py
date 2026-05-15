"""Account provisioning workflow package (Phase 1.5).

Durable execution of unknown-business-domain approval via DBOS. The
``/approve`` route reserves the queue row synchronously then starts the
workflow at :func:`services.account_provisioning.workflow.account_provisioning_workflow`.
Plan: ``docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md``.
"""
