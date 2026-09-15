# Security and deployment notes

This project connects an LLM-enabled analytical layer to PostgreSQL. Treat deployment as a security-sensitive activity.

## Required practices

- Keep `.env` out of Git.
- Use a dedicated **read-only** PostgreSQL account for interactive analysis where possible.
- Restrict database/network access to trusted environments.
- Rotate any credential that has ever been committed to Git history.
- Do not log API keys or database passwords.
- Review generated SQL/code before execution in privileged environments.
- Keep development and production databases separate.
- Back up important databases before running migration/transfer utilities.

## PandasAI security setting

`PANDASAI_BYPASS_SECURITY=true` appears in the supplied configuration. Bypassing a library's security layer increases risk when generated code is executed. Do not expose such a configuration to untrusted users or data without a separate security review and sandboxing strategy.

## `uploader_optimized.r`

The transfer utility can create/modify destination tables depending on its run mode. Confirm the destination host/database/table and review `fresh_run` behaviour before execution.

## Public GitHub checklist

Before publishing:

```bash
grep -RniE 'api[_-]?key|password|secret|token|BEGIN .*PRIVATE KEY' . \
  --exclude-dir=.git --exclude='env.example'
```

Review all matches manually. Also inspect Git history if the repository was previously committed elsewhere; deleting a secret from the current working tree does not remove it from history.
