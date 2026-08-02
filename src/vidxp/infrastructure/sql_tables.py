from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    Text,
)


metadata = MetaData()

media = Table(
    "media",
    metadata,
    Column("media_id", String(32), primary_key=True),
    Column("sha256", String(64), nullable=False, unique=True),
    Column("created_at", Text, nullable=False),
    Column("payload", JSON, nullable=False),
)

artifacts = Table(
    "artifacts",
    metadata,
    Column("artifact_id", String(32), primary_key=True),
    Column(
        "media_id",
        String(32),
        ForeignKey("media.media_id"),
        nullable=False,
    ),
    Column("created_at", Text, nullable=False),
    Column("payload", JSON, nullable=False),
)
Index("artifacts_media_id", artifacts.c.media_id)

artifact_requests = Table(
    "artifact_requests",
    metadata,
    Column("request_key", String(64), primary_key=True),
    Column(
        "artifact_id",
        String(32),
        ForeignKey("artifacts.artifact_id"),
        nullable=False,
        unique=True,
    ),
)

media_import_requests = Table(
    "media_import_requests",
    metadata,
    Column("request_key", String(64), primary_key=True),
    Column("request_fingerprint", String(64), nullable=False),
    Column(
        "media_id",
        String(32),
        ForeignKey("media.media_id"),
        nullable=True,
    ),
)

upload_intents = Table(
    "upload_intents",
    metadata,
    Column("intent_id", String(32), primary_key=True),
    Column("request_key", String(64), nullable=False, unique=True),
    Column("original_filename", String(255), nullable=False),
    Column("byte_size", BigInteger, nullable=False),
    Column("declared_mime_type", String(127), nullable=True),
    Column("state", String(32), nullable=False),
    Column("created_at", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("upload_id", String(255), nullable=True, unique=True),
    Column("job_id", String(36), nullable=True, unique=True),
    Column(
        "media_id",
        String(32),
        ForeignKey("media.media_id"),
        nullable=True,
    ),
    Column("transfer_backend", String(32), nullable=False, default="tus"),
    Column("index_after_import", Boolean, nullable=False, default=True),
    Column("index_modalities", JSON, nullable=False, default=list),
    Column("index_job_id", String(36), nullable=True),
    Column("index_command", JSON, nullable=True),
    Column("source_path", Text, nullable=True),
    Column("content_sha256", String(64), nullable=True),
    Column("failure_code", String(128), nullable=True),
    Column("failure_message", String(512), nullable=True),
)
Index("upload_intents_index_job_id", upload_intents.c.index_job_id)

upload_sessions = Table(
    "upload_sessions",
    metadata,
    Column("session_id", String(32), primary_key=True),
    Column("request_key", String(64), nullable=False, unique=True),
    Column("selector", String(32), nullable=False, unique=True),
    Column("capability_digest", String(64), nullable=False, unique=True),
    Column("initiating_subject", String(255), nullable=False),
    Column("initiating_client_id", String(255), nullable=True),
    Column("repository_binding", String(64), nullable=False),
    Column("purpose", String(64), nullable=False),
    Column("state", String(32), nullable=False),
    Column("maximum_files", Integer, nullable=False),
    Column("maximum_file_bytes", BigInteger, nullable=False),
    Column("maximum_aggregate_bytes", BigInteger, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("browser_session_digest", String(64), nullable=True),
    Column("transfer_backend", String(32), nullable=False, default="tus"),
    Column("index_after_import", Boolean, nullable=False, default=True),
    Column("index_modalities", JSON, nullable=False, default=list),
)
Index("upload_sessions_expiry", upload_sessions.c.expires_at, upload_sessions.c.state)

upload_session_files = Table(
    "upload_session_files",
    metadata,
    Column(
        "session_id",
        String(32),
        ForeignKey("upload_sessions.session_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("client_file_key", String(255), primary_key=True),
    Column(
        "intent_id",
        String(32),
        ForeignKey("upload_intents.intent_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column("created_at", Text, nullable=False),
    Column("creation_grant_digest", String(64), nullable=True, unique=True),
    Column("creation_grant_expires_at", Text, nullable=True),
    Column("creation_grant_consumed_at", Text, nullable=True),
)

upload_quota = Table(
    "upload_quota",
    metadata,
    Column("singleton_id", String(1), primary_key=True),
    Column("reserved_bytes", BigInteger, nullable=False, default=0),
    CheckConstraint(
        "reserved_bytes >= 0",
        name="upload_quota_reserved_bytes_nonnegative",
    ),
    CheckConstraint(
        "singleton_id = '1'",
        name="upload_quota_singleton",
    ),
)

index_state = Table(
    "index_state",
    metadata,
    Column("singleton_id", String(1), primary_key=True),
    Column("active_snapshot_id", String(32), nullable=True),
    Column("active_snapshot_sha256", String(64), nullable=True),
    CheckConstraint(
        "singleton_id = '1'",
        name="index_state_singleton",
    ),
)

index_generations = Table(
    "index_generations",
    metadata,
    Column("generation_id", String(32), primary_key=True),
    Column("media_id", String(32), nullable=False),
    Column("manifest_sha256", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
)
Index(
    "index_generations_media",
    index_generations.c.media_id,
)

index_snapshots = Table(
    "index_snapshots",
    metadata,
    Column("snapshot_id", String(32), primary_key=True),
    Column("created_at", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
)
Index(
    "index_snapshots_created",
    index_snapshots.c.created_at,
)
Index(
    "upload_intents_expiry",
    upload_intents.c.expires_at,
    upload_intents.c.state,
)
