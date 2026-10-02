.PHONE: opencode claude
include .env

opencode:
	ai-memory install-hooks --agent opencode --apply --server-url ${AI_MEMORY_BASE_URL} --auth-token ${AI_MEMORY_AUTH_TOKEN}
	ai-memory install-mcp --client opencode --apply --server-url ${AI_MEMORY_BASE_URL} --auth-token ${AI_MEMORY_AUTH_TOKEN}

claude:
        ai-memory install-hooks --agent claude-code --apply --server-url ${AI_MEMORY_BASE_URL} --auth-token ${AI_MEMORY_AUTH_TOKEN}
        ai-memory install-mcp --client claude-code --apply --server-url ${AI_MEMORY_BASE_URL} --auth-token ${AI_MEMORY_AUTH_TOKEN}
