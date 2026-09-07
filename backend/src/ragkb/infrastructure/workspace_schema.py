"""Additive schema shared by the workspace and conversation repositories."""

WORKSPACE_TABLES = {
    "workspace_metadata": """
        tenant_id VARCHAR(191) NOT NULL,
        resource_id VARCHAR(191) NOT NULL,
        description TEXT NOT NULL,
        PRIMARY KEY (tenant_id, resource_id)
    """,
    "publication_actions": """
        tenant_id VARCHAR(191) NOT NULL,
        version_id VARCHAR(191) NOT NULL,
        action_key VARCHAR(191) NOT NULL,
        payload_json TEXT NOT NULL,
        updated_at DOUBLE NOT NULL,
        PRIMARY KEY (tenant_id, version_id, action_key)
    """,
    "conversations": """
        id VARCHAR(191) PRIMARY KEY,
        tenant_id VARCHAR(191) NOT NULL,
        user_id VARCHAR(191) NOT NULL,
        space_id VARCHAR(191) NOT NULL,
        title VARCHAR(255) NOT NULL,
        archived INTEGER NOT NULL DEFAULT 0,
        active_turn_id VARCHAR(191),
        next_sequence INTEGER NOT NULL DEFAULT 1,
        created_at DOUBLE NOT NULL,
        updated_at DOUBLE NOT NULL
    """,
    "conversation_turns": """
        id VARCHAR(191) PRIMARY KEY,
        conversation_id VARCHAR(191) NOT NULL,
        request_id VARCHAR(191) NOT NULL,
        sequence_number INTEGER NOT NULL,
        original_question TEXT NOT NULL,
        resolved_question TEXT NOT NULL,
        state VARCHAR(32) NOT NULL,
        rag_run_id VARCHAR(191),
        result_json TEXT,
        error_code VARCHAR(128),
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        execution_token VARCHAR(191),
        lease_expires_at DOUBLE NOT NULL DEFAULT 0,
        created_at DOUBLE NOT NULL,
        updated_at DOUBLE NOT NULL,
        UNIQUE (conversation_id, request_id),
        UNIQUE (conversation_id, sequence_number),
        FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    """,
}

WORKSPACE_INDEXES = {
    "idx_conversations_subject": "conversations(tenant_id, user_id, archived, updated_at, id)",
    "idx_conversations_space": "conversations(tenant_id, space_id, updated_at, id)",
    "idx_conversation_recovery": "conversation_turns(state, lease_expires_at)",
}


def mysql_workspace_migrations() -> tuple[tuple[str, str], ...]:
    tables = tuple(
        (
            f"create_{name}",
            f"CREATE TABLE IF NOT EXISTS {name} ({body}) "
            "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci",
        )
        for name, body in WORKSPACE_TABLES.items()
    )
    indexes = tuple(
        (f"create_{name}", f"CREATE INDEX {name} ON {target}")
        for name, target in WORKSPACE_INDEXES.items()
    )
    return tables + indexes


def sqlite_workspace_schema() -> str:
    return "\n".join(
        [f"CREATE TABLE IF NOT EXISTS {name} ({body});" for name, body in WORKSPACE_TABLES.items()]
        + [
            f"CREATE INDEX IF NOT EXISTS {name} ON {target};"
            for name, target in WORKSPACE_INDEXES.items()
        ]
    )
