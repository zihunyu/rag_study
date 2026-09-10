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

# Additive account state. Existing documents and user-owned conversations are untouched.
WORKSPACE_TABLES.update(
    {
        "auth_users": """
        id VARCHAR(191) PRIMARY KEY, tenant_id VARCHAR(191) NOT NULL,
        username VARCHAR(64) NOT NULL, display_name VARCHAR(100) NOT NULL,
        password_hash TEXT NOT NULL, global_role VARCHAR(32) NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1, must_change_password INTEGER NOT NULL DEFAULT 1,
        password_expires_at DOUBLE NOT NULL DEFAULT 0,
        auth_revision INTEGER NOT NULL DEFAULT 1, row_version INTEGER NOT NULL DEFAULT 1,
        created_at DOUBLE NOT NULL, updated_at DOUBLE NOT NULL,
        UNIQUE (tenant_id, username)
    """,
        "auth_sessions": """
        token_hash VARCHAR(64) PRIMARY KEY, tenant_id VARCHAR(191) NOT NULL,
        user_id VARCHAR(191), csrf_hash VARCHAR(64) NOT NULL,
        created_at DOUBLE NOT NULL, last_seen_at DOUBLE NOT NULL,
        expires_at DOUBLE NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
    """,
        "space_memberships": """
        tenant_id VARCHAR(191) NOT NULL, space_id VARCHAR(191) NOT NULL,
        user_id VARCHAR(191) NOT NULL, role VARCHAR(32) NOT NULL,
        assigned_by VARCHAR(191) NOT NULL, created_at DOUBLE NOT NULL,
        PRIMARY KEY (tenant_id, space_id, user_id)
    """,
        "space_access_state": """
        tenant_id VARCHAR(191) NOT NULL, space_id VARCHAR(191) NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0, row_version INTEGER NOT NULL DEFAULT 1,
        updated_at DOUBLE NOT NULL, PRIMARY KEY (tenant_id, space_id)
    """,
        "account_audit_events": """
        id VARCHAR(191) PRIMARY KEY, tenant_id VARCHAR(191) NOT NULL,
        actor_id VARCHAR(191) NOT NULL, action VARCHAR(100) NOT NULL,
        target_id VARCHAR(191) NOT NULL, space_id VARCHAR(191) NOT NULL,
        detail_json TEXT NOT NULL, created_at DOUBLE NOT NULL
    """,
        "account_commands": """
        tenant_id VARCHAR(191) NOT NULL, actor_id VARCHAR(191) NOT NULL,
        command_key VARCHAR(191) NOT NULL, payload_hash VARCHAR(64) NOT NULL,
        result_json TEXT NOT NULL, PRIMARY KEY (tenant_id, actor_id, command_key)
    """,
    }
)
WORKSPACE_INDEXES.update(
    {
        "idx_auth_users_tenant": "auth_users(tenant_id, enabled, username)",
        "idx_auth_sessions_user": "auth_sessions(tenant_id, user_id, revoked)",
        "idx_space_members_user": "space_memberships(tenant_id, user_id, space_id)",
        "idx_account_audit_scope": "account_audit_events(tenant_id, space_id, created_at, id)",
    }
)

# New relation avoids altering or reassigning any historical turn.
WORKSPACE_TABLES["conversation_turn_options"] = """
    turn_id VARCHAR(191) PRIMARY KEY,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (turn_id) REFERENCES conversation_turns(id)
"""


WORKSPACE_TABLES.update(
    {
        "acceptance_cases": """
        id VARCHAR(191) PRIMARY KEY, tenant_id VARCHAR(191) NOT NULL,
        space_id VARCHAR(191) NOT NULL, case_key VARCHAR(80) NOT NULL,
        revision INTEGER NOT NULL, payload_json LONGTEXT NOT NULL,
        updated_at DOUBLE NOT NULL, UNIQUE(tenant_id, space_id, case_key)
    """,
        "acceptance_case_revisions": """
        case_id VARCHAR(191) NOT NULL, revision INTEGER NOT NULL,
        payload_json LONGTEXT NOT NULL, actor_id VARCHAR(191) NOT NULL,
        created_at DOUBLE NOT NULL, PRIMARY KEY(case_id, revision)
    """,
        "acceptance_runs": """
        id VARCHAR(191) PRIMARY KEY, tenant_id VARCHAR(191) NOT NULL,
        space_id VARCHAR(191) NOT NULL, actor_id VARCHAR(191) NOT NULL,
        request_key VARCHAR(191) NOT NULL, payload_json LONGTEXT NOT NULL,
        state VARCHAR(32) NOT NULL, reason VARCHAR(191) NOT NULL DEFAULT '',
        call_limit INTEGER NOT NULL, calls_reserved INTEGER NOT NULL DEFAULT 0,
        execution_token VARCHAR(191) NOT NULL DEFAULT '',
        lease_until DOUBLE NOT NULL DEFAULT 0, pause_requested INTEGER NOT NULL DEFAULT 0,
        created_at DOUBLE NOT NULL, updated_at DOUBLE NOT NULL,
        UNIQUE(tenant_id, space_id, actor_id, request_key)
    """,
        "acceptance_attempts": """
        id VARCHAR(191) PRIMARY KEY, run_id VARCHAR(191) NOT NULL,
        case_id VARCHAR(191) NOT NULL, attempt INTEGER NOT NULL,
        state VARCHAR(32) NOT NULL, payload_json LONGTEXT NOT NULL,
        created_at DOUBLE NOT NULL, updated_at DOUBLE NOT NULL,
        UNIQUE(run_id, case_id, attempt)
    """,
        "acceptance_reviews": """
        id VARCHAR(191) PRIMARY KEY, attempt_id VARCHAR(191) NOT NULL,
        actor_id VARCHAR(191) NOT NULL, verdict VARCHAR(32) NOT NULL,
        note TEXT NOT NULL, created_at DOUBLE NOT NULL
    """,
        "acceptance_calls": """
        id VARCHAR(191) PRIMARY KEY, run_id VARCHAR(191) NOT NULL,
        attempt_id VARCHAR(191) NOT NULL, payload_json TEXT NOT NULL,
        created_at DOUBLE NOT NULL
    """,
        "acceptance_review_details": """
        review_id VARCHAR(191) PRIMARY KEY, payload_json LONGTEXT NOT NULL
    """,
        "acceptance_parsing_records": """
        id VARCHAR(191) PRIMARY KEY, tenant_id VARCHAR(191) NOT NULL,
        space_id VARCHAR(191) NOT NULL, kind VARCHAR(32) NOT NULL,
        revision INTEGER NOT NULL, actor_id VARCHAR(191) NOT NULL,
        payload_json LONGTEXT NOT NULL, updated_at DOUBLE NOT NULL
    """,
    }
)
WORKSPACE_INDEXES.update(
    {
        "idx_acceptance_cases_space": "acceptance_cases(tenant_id, space_id, updated_at)",
        "idx_acceptance_runs_space": "acceptance_runs(tenant_id, space_id, created_at)",
        "idx_acceptance_attempts_run": "acceptance_attempts(run_id, created_at)",
        "idx_acceptance_reviews_attempt": "acceptance_reviews(attempt_id, created_at)",
        "idx_acceptance_calls_run": "acceptance_calls(run_id, created_at)",
        "idx_acceptance_parsing_space": (
            "acceptance_parsing_records(tenant_id, space_id, kind, updated_at)"
        ),
    }
)


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
