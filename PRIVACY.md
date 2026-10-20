# Privacy and data handling

Condor runs on infrastructure controlled by the operator. This repository has
removed usage telemetry and conversation publishing, including their collectors,
background jobs, API routes, consent settings, and dashboard controls.

## Local data

Configuration, credentials, conversations, attachments, memories, and generated
reports are stored on the deployment host. Operators control access, retention,
and backups. Deleting a local conversation does not delete copies in backups or
messages already delivered through an external service.

Existing installations may retain historical files or configuration from older
versions. This version does not process or transmit the former telemetry and
conversation-sharing queues. Removing these features does not withdraw data
that an earlier version already sent to an external service.

## External services

Normal features still communicate with the services they use:

- Telegram receives bot messages and attachments when Telegram is configured.
- Configured AI providers receive prompts, conversation context, tool results,
  and attachments required for agent requests. Speech transcription uses the
  configured provider when enabled.
- Hummingbot API, Gateway, exchanges, and market-data services receive requests
  needed for trading and market information.
- Browser push services deliver notifications when Web Push is enabled.
- Update checks fetch Git metadata from the checkout's `origin`. Dependency
  installation downloads packages from their configured registries.
- Agent tools and routines may contact additional services as configured or
  requested by users.

Review the configuration and each provider's policies before sending sensitive
data. The operator is responsible for securing credentials, deployment access,
and backups. See [Maintenance and deployment](docs/maintenance.md) for the
repository's update and recovery procedures.
