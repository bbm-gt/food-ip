CREATE TABLE IF NOT EXISTS director_sessions (
    id TEXT PRIMARY KEY CHECK(length(id) = 36 AND id = lower(id)
        AND substr(id, 9, 1) = '-' AND substr(id, 14, 1) = '-'
        AND substr(id, 15, 1) = '4' AND substr(id, 19, 1) = '-'
        AND substr(id, 20, 1) IN ('8', '9', 'a', 'b') AND substr(id, 24, 1) = '-'
        AND replace(id, '-', '') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) > 0),
    project_id TEXT NOT NULL CHECK(length(project_id) > 0),
    source_ready_content_id TEXT REFERENCES director_ready_content(id) ON DELETE RESTRICT,
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE', 'READY')),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24 AND created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    ready_at TEXT CHECK(ready_at IS NULL OR (length(ready_at) = 24 AND ready_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')),
    CHECK((lifecycle_status = 'ACTIVE' AND ready_at IS NULL) OR
          (lifecycle_status = 'READY' AND ready_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS director_turns (
    id TEXT PRIMARY KEY CHECK(length(id) = 36 AND id = lower(id)
        AND substr(id, 9, 1) = '-' AND substr(id, 14, 1) = '-'
        AND substr(id, 15, 1) = '4' AND substr(id, 19, 1) = '-'
        AND substr(id, 20, 1) IN ('8', '9', 'a', 'b') AND substr(id, 24, 1) = '-'
        AND replace(id, '-', '') NOT GLOB '*[^0-9a-f]*'),
    session_id TEXT NOT NULL REFERENCES director_sessions(id) ON DELETE RESTRICT,
    client_message_id TEXT NOT NULL CHECK(length(client_message_id) > 0),
    request_format_version INTEGER NOT NULL CHECK(request_format_version > 0 AND typeof(request_format_version) = 'integer'),
    normalized_request_json TEXT NOT NULL CHECK(json_valid(normalized_request_json) AND json_type(normalized_request_json) = 'object'),
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    pre_state_version INTEGER NOT NULL CHECK(pre_state_version >= 0 AND typeof(pre_state_version) = 'integer'),
    post_state_version INTEGER NOT NULL CHECK(post_state_version = pre_state_version + 1 AND typeof(post_state_version) = 'integer'),
    final_run_control TEXT NOT NULL CHECK(final_run_control IN ('WAIT_FOR_OWNER', 'READY')),
    target_stage TEXT NOT NULL CHECK(target_stage IN ('EXPLORE', 'DEEPEN', 'CREATE', 'REVIEW', 'READY')),
    transition_reason_code TEXT NOT NULL CHECK(transition_reason_code IN
        ('OWNER_INPUT_REQUIRED', 'DIRECTION_CONFIRMED', 'DIRECTION_INVALID', 'MATERIAL_GAP',
         'MATERIAL_SUFFICIENT', 'DRAFT_CREATED', 'WRITING_REPAIR', 'REVIEW_PASSED')),
    gate_outcome TEXT CHECK(gate_outcome IN ('PASSED', 'BLOCKED')),
    review_root_cause TEXT CHECK(review_root_cause IN ('WRITING_PROBLEM', 'MATERIAL_PROBLEM', 'DIRECTION_PROBLEM')),
    execution_format_version INTEGER NOT NULL CHECK(execution_format_version > 0 AND typeof(execution_format_version) = 'integer'),
    execution_trace_json TEXT NOT NULL CHECK(json_valid(execution_trace_json) AND json_type(execution_trace_json) = 'object'),
    response_format_version INTEGER NOT NULL CHECK(response_format_version > 0 AND typeof(response_format_version) = 'integer'),
    first_response_json TEXT NOT NULL CHECK(json_valid(first_response_json) AND json_type(first_response_json) = 'object'),
    snapshot_format_version INTEGER NOT NULL CHECK(snapshot_format_version > 0 AND typeof(snapshot_format_version) = 'integer'),
    post_state_snapshot_json TEXT NOT NULL CHECK(json_valid(post_state_snapshot_json) AND json_type(post_state_snapshot_json) = 'object'),
    post_state_sha256 TEXT NOT NULL CHECK(length(post_state_sha256) = 64 AND post_state_sha256 NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24 AND created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    UNIQUE(session_id, client_message_id),
    UNIQUE(session_id, post_state_version),
    UNIQUE(session_id, id)
);

CREATE TABLE IF NOT EXISTS director_messages (
    id TEXT PRIMARY KEY CHECK(length(id) = 36 AND id = lower(id)
        AND substr(id, 9, 1) = '-' AND substr(id, 14, 1) = '-'
        AND substr(id, 15, 1) = '4' AND substr(id, 19, 1) = '-'
        AND substr(id, 20, 1) IN ('8', '9', 'a', 'b') AND substr(id, 24, 1) = '-'
        AND replace(id, '-', '') NOT GLOB '*[^0-9a-f]*'),
    session_id TEXT NOT NULL REFERENCES director_sessions(id) ON DELETE RESTRICT,
    message_seq INTEGER NOT NULL CHECK(message_seq > 0 AND typeof(message_seq) = 'integer'),
    visible_role TEXT NOT NULL CHECK(visible_role IN ('OWNER', 'DIRECTOR')),
    content TEXT NOT NULL CHECK(length(content) > 0),
    turn_id TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK(length(created_at) = 24 AND created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    UNIQUE(session_id, message_seq),
    UNIQUE(session_id, turn_id, visible_role),
    UNIQUE(session_id, id),
    FOREIGN KEY(session_id, turn_id) REFERENCES director_turns(session_id, id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS director_working_state (
    session_id TEXT PRIMARY KEY REFERENCES director_sessions(id) ON DELETE RESTRICT,
    state_version INTEGER NOT NULL CHECK(state_version >= 0 AND typeof(state_version) = 'integer'),
    stage TEXT NOT NULL CHECK(stage IN ('EXPLORE', 'DEEPEN', 'CREATE', 'REVIEW', 'READY')),
    state_json TEXT NOT NULL CHECK(json_valid(state_json) AND json_type(state_json) = 'object' AND json_extract(state_json, '$.format_version') = 1),
    state_sha256 TEXT NOT NULL CHECK(length(state_sha256) = 64 AND state_sha256 NOT GLOB '*[^0-9a-f]*'),
    latest_successful_turn_id TEXT,
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24 AND updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    CHECK((state_version = 0 AND latest_successful_turn_id IS NULL) OR
          (state_version > 0 AND latest_successful_turn_id IS NOT NULL)),
    FOREIGN KEY(session_id, latest_successful_turn_id) REFERENCES director_turns(session_id, id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS director_context_checkpoints (
    id TEXT PRIMARY KEY CHECK(length(id) = 36 AND id = lower(id)
        AND substr(id, 9, 1) = '-' AND substr(id, 14, 1) = '-'
        AND substr(id, 15, 1) = '4' AND substr(id, 19, 1) = '-'
        AND substr(id, 20, 1) IN ('8', '9', 'a', 'b') AND substr(id, 24, 1) = '-'
        AND replace(id, '-', '') NOT GLOB '*[^0-9a-f]*'),
    session_id TEXT NOT NULL REFERENCES director_sessions(id) ON DELETE RESTRICT,
    covered_through_seq INTEGER NOT NULL CHECK(covered_through_seq > 0 AND typeof(covered_through_seq) = 'integer'),
    format_version INTEGER NOT NULL CHECK(format_version > 0 AND typeof(format_version) = 'integer'),
    checkpoint_json TEXT NOT NULL CHECK(json_valid(checkpoint_json) AND json_type(checkpoint_json) = 'object'),
    integrity_sha256 TEXT NOT NULL CHECK(length(integrity_sha256) = 64 AND integrity_sha256 NOT GLOB '*[^0-9a-f]*'),
    status TEXT NOT NULL CHECK(status IN ('VALID', 'DISCARDED')),
    discarded_at TEXT CHECK(discarded_at IS NULL OR (length(discarded_at) = 24 AND discarded_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')),
    discard_reason_code TEXT,
    created_at TEXT NOT NULL CHECK(length(created_at) = 24 AND created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    CHECK((status = 'VALID' AND discarded_at IS NULL AND discard_reason_code IS NULL) OR
          (status = 'DISCARDED' AND discarded_at IS NOT NULL AND discard_reason_code IS NOT NULL)),
    UNIQUE(session_id, covered_through_seq, format_version, integrity_sha256),
    FOREIGN KEY(session_id, covered_through_seq) REFERENCES director_messages(session_id, message_seq) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS director_ready_content (
    id TEXT PRIMARY KEY CHECK(length(id) = 36 AND id = lower(id)
        AND substr(id, 9, 1) = '-' AND substr(id, 14, 1) = '-'
        AND substr(id, 15, 1) = '4' AND substr(id, 19, 1) = '-'
        AND substr(id, 20, 1) IN ('8', '9', 'a', 'b') AND substr(id, 24, 1) = '-'
        AND replace(id, '-', '') NOT GLOB '*[^0-9a-f]*'),
    session_id TEXT NOT NULL UNIQUE REFERENCES director_sessions(id) ON DELETE RESTRICT,
    content_format_version INTEGER NOT NULL CHECK(content_format_version > 0 AND typeof(content_format_version) = 'integer'),
    final_content_json TEXT NOT NULL CHECK(json_valid(final_content_json) AND json_type(final_content_json) = 'object'),
    created_by_turn_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL CHECK(length(created_at) = 24 AND created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    FOREIGN KEY(session_id, created_by_turn_id) REFERENCES director_turns(session_id, id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_director_sessions_scope ON director_sessions(workspace_id, project_id, id);
CREATE INDEX IF NOT EXISTS idx_director_messages_session_seq ON director_messages(session_id, message_seq);
CREATE INDEX IF NOT EXISTS idx_director_turns_session_created ON director_turns(session_id, post_state_version DESC);
CREATE INDEX IF NOT EXISTS idx_director_checkpoints_latest ON director_context_checkpoints(session_id, status, covered_through_seq DESC, created_at DESC, id DESC);

CREATE TRIGGER IF NOT EXISTS director_sessions_source_scope_insert
BEFORE INSERT ON director_sessions WHEN NEW.source_ready_content_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM director_ready_content rc
        JOIN director_sessions source ON source.id = rc.session_id
        WHERE rc.id = NEW.source_ready_content_id
          AND source.lifecycle_status = 'READY'
          AND source.workspace_id = NEW.workspace_id
          AND source.project_id = NEW.project_id
          AND source.id <> NEW.id
    ) THEN RAISE(ABORT, 'invalid source ReadyContent scope') END;
END;

CREATE TRIGGER IF NOT EXISTS director_sessions_update_guard
BEFORE UPDATE ON director_sessions
BEGIN
    SELECT CASE WHEN NEW.id <> OLD.id OR NEW.workspace_id <> OLD.workspace_id
        OR NEW.project_id <> OLD.project_id
        OR NEW.source_ready_content_id IS NOT OLD.source_ready_content_id
        OR NEW.created_at <> OLD.created_at
        THEN RAISE(ABORT, 'immutable Director Session relationship') END;
    SELECT CASE WHEN NOT (OLD.lifecycle_status = 'ACTIVE' AND NEW.lifecycle_status = 'READY')
        THEN RAISE(ABORT, 'invalid Director Session lifecycle transition') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM director_ready_content rc
        WHERE rc.session_id = OLD.id AND rc.created_at = NEW.ready_at
    ) THEN RAISE(ABORT, 'READY lifecycle requires matching ReadyContent') END;
END;

CREATE TRIGGER IF NOT EXISTS director_sessions_delete_guard
BEFORE DELETE ON director_sessions BEGIN SELECT RAISE(ABORT, 'Director Sessions cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS director_turns_insert_guard
BEFORE INSERT ON director_turns
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM director_sessions s WHERE s.id = NEW.session_id AND s.lifecycle_status = 'ACTIVE')
        THEN RAISE(ABORT, 'Turn requires ACTIVE Session') END;
END;
CREATE TRIGGER IF NOT EXISTS director_turns_update_guard BEFORE UPDATE ON director_turns BEGIN SELECT RAISE(ABORT, 'Director Turns are immutable'); END;
CREATE TRIGGER IF NOT EXISTS director_turns_delete_guard BEFORE DELETE ON director_turns BEGIN SELECT RAISE(ABORT, 'Director Turns cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS director_messages_insert_guard
BEFORE INSERT ON director_messages
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM director_sessions s WHERE s.id = NEW.session_id AND s.lifecycle_status = 'ACTIVE')
        THEN RAISE(ABORT, 'Message requires ACTIVE Session') END;
END;
CREATE TRIGGER IF NOT EXISTS director_messages_update_guard BEFORE UPDATE ON director_messages BEGIN SELECT RAISE(ABORT, 'Director Messages are immutable'); END;
CREATE TRIGGER IF NOT EXISTS director_messages_delete_guard BEFORE DELETE ON director_messages BEGIN SELECT RAISE(ABORT, 'Director Messages cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS director_working_state_insert_guard
BEFORE INSERT ON director_working_state
BEGIN
    SELECT CASE WHEN NEW.state_version = 0 AND NOT EXISTS (
        SELECT 1 FROM director_sessions s
        WHERE s.id = NEW.session_id AND s.lifecycle_status = 'ACTIVE'
    ) THEN RAISE(ABORT, 'initial Working State requires ACTIVE Session') END;
    SELECT CASE WHEN NEW.state_version = 0 AND (
        NEW.stage <> 'EXPLORE' OR NEW.latest_successful_turn_id IS NOT NULL
    ) THEN RAISE(ABORT, 'initial Working State must be EXPLORE version 0') END;
    SELECT CASE WHEN NEW.state_version = 0 AND EXISTS (
        SELECT 1 FROM director_turns t WHERE t.session_id = NEW.session_id
    ) THEN RAISE(ABORT, 'version 0 Working State cannot coexist with a Turn') END;
    SELECT CASE WHEN NEW.state_version > 0 AND NOT EXISTS (
        SELECT 1
        FROM director_turns t
        WHERE t.session_id = NEW.session_id
          AND t.post_state_version = (
              SELECT MAX(max_t.post_state_version)
              FROM director_turns max_t WHERE max_t.session_id = NEW.session_id
          )
          AND t.id = NEW.latest_successful_turn_id
          AND t.post_state_version = NEW.state_version
          AND t.target_stage = NEW.stage
          AND t.post_state_sha256 = NEW.state_sha256
          AND json_extract(t.post_state_snapshot_json, '$.state_version') = NEW.state_version
          AND json_extract(t.post_state_snapshot_json, '$.stage') = NEW.stage
          AND json_extract(t.post_state_snapshot_json, '$.state_json') = NEW.state_json
    ) THEN RAISE(ABORT, 'Working State must match the maximum Turn snapshot') END;
    SELECT CASE WHEN NEW.state_version > 0 AND NEW.stage <> 'READY' AND NOT EXISTS (
        SELECT 1 FROM director_sessions s
        WHERE s.id = NEW.session_id AND s.lifecycle_status = 'ACTIVE'
    ) THEN RAISE(ABORT, 'ACTIVE Working State requires ACTIVE Session') END;
    SELECT CASE WHEN NEW.state_version > 0 AND NEW.stage <> 'READY' AND EXISTS (
        SELECT 1 FROM director_ready_content rc WHERE rc.session_id = NEW.session_id
    ) THEN RAISE(ABORT, 'ACTIVE Working State cannot have ReadyContent') END;
    SELECT CASE WHEN NEW.state_version > 0 AND NEW.stage = 'READY' AND NOT EXISTS (
        SELECT 1
        FROM director_sessions s
        JOIN director_ready_content rc ON rc.session_id = s.id
        JOIN director_turns t ON t.id = NEW.latest_successful_turn_id AND t.session_id = s.id
        WHERE s.id = NEW.session_id AND s.lifecycle_status = 'READY'
          AND rc.created_by_turn_id = t.id
          AND rc.content_format_version = 1
          AND json_extract(t.first_response_json, '$.ready_content_id') = rc.id
          AND json(rc.final_content_json) = json(json_extract(t.post_state_snapshot_json, '$.state_json.draft.content'))
    ) THEN RAISE(ABORT, 'READY Working State requires closed ReadyContent') END;
END;

CREATE TRIGGER IF NOT EXISTS director_working_state_update_guard
BEFORE UPDATE ON director_working_state
BEGIN
    SELECT CASE WHEN NEW.session_id <> OLD.session_id THEN RAISE(ABORT, 'Working State Session is immutable') END;
    -- Normal writes are N -> N+1. Recovery ignores damaged OLD projection
    -- fields and accepts only the deterministic v0 baseline or the current
    -- maximum successful Turn snapshot.
    SELECT CASE WHEN NOT (
        (NEW.state_version = 0 AND NEW.stage = 'EXPLORE'
         AND NEW.latest_successful_turn_id IS NULL
         AND EXISTS (
             SELECT 1 FROM director_sessions s
             WHERE s.id = NEW.session_id AND s.lifecycle_status = 'ACTIVE'
         )
         AND NOT EXISTS (
             SELECT 1 FROM director_turns t WHERE t.session_id = NEW.session_id
         ))
        OR
        (NEW.state_version > 0 AND EXISTS (
            SELECT 1
            FROM director_turns t
            WHERE t.session_id = NEW.session_id
              AND t.post_state_version = (
                  SELECT MAX(max_t.post_state_version)
                  FROM director_turns max_t WHERE max_t.session_id = NEW.session_id
              )
              AND t.id = NEW.latest_successful_turn_id
              AND t.post_state_version = NEW.state_version
              AND t.target_stage = NEW.stage
              AND t.post_state_sha256 = NEW.state_sha256
              AND json_extract(t.post_state_snapshot_json, '$.state_version') = NEW.state_version
              AND json_extract(t.post_state_snapshot_json, '$.stage') = NEW.stage
              AND json_extract(t.post_state_snapshot_json, '$.state_json') = NEW.state_json
        ))
    ) THEN RAISE(ABORT, 'Working State write must match v0 baseline or maximum Turn snapshot') END;
    SELECT CASE WHEN NEW.state_version = OLD.state_version + 1
        AND NEW.stage <> 'READY' AND NOT EXISTS (
        SELECT 1 FROM director_sessions s
        WHERE s.id = OLD.session_id AND s.lifecycle_status = 'ACTIVE'
    ) THEN RAISE(ABORT, 'normal Working State update requires ACTIVE Session') END;
    SELECT CASE WHEN NEW.state_version = OLD.state_version + 1 AND NOT EXISTS (
        SELECT 1
        FROM director_turns t
        WHERE t.session_id = NEW.session_id
          AND t.post_state_version = NEW.state_version
          AND t.post_state_version = (
              SELECT MAX(max_t.post_state_version)
              FROM director_turns max_t WHERE max_t.session_id = NEW.session_id
          )
          AND t.id = NEW.latest_successful_turn_id
          AND t.target_stage = NEW.stage
          AND t.post_state_sha256 = NEW.state_sha256
          AND json_extract(t.post_state_snapshot_json, '$.state_version') = NEW.state_version
          AND json_extract(t.post_state_snapshot_json, '$.stage') = NEW.stage
          AND json_extract(t.post_state_snapshot_json, '$.state_json') = NEW.state_json
    ) THEN RAISE(ABORT, 'normal Working State update must match the Turn snapshot') END;
    SELECT CASE WHEN NEW.state_version > 0
        AND NEW.stage <> 'READY' AND NOT EXISTS (
        SELECT 1 FROM director_sessions s
        WHERE s.id = NEW.session_id AND s.lifecycle_status = 'ACTIVE'
    ) THEN RAISE(ABORT, 'ACTIVE Working State recovery requires ACTIVE Session') END;
    SELECT CASE WHEN NEW.state_version > 0
        AND NEW.stage <> 'READY' AND EXISTS (
        SELECT 1 FROM director_ready_content rc WHERE rc.session_id = NEW.session_id
    ) THEN RAISE(ABORT, 'ACTIVE Working State recovery cannot have ReadyContent') END;
    SELECT CASE WHEN NEW.state_version > 0
        AND NEW.stage = 'READY'
        AND NOT (NEW.state_version = OLD.state_version + 1 AND EXISTS (
            SELECT 1 FROM director_sessions s
            WHERE s.id = NEW.session_id AND s.lifecycle_status = 'ACTIVE'
        ))
        AND NOT EXISTS (
        SELECT 1
        FROM director_sessions s
        JOIN director_ready_content rc ON rc.session_id = s.id
        JOIN director_turns t ON t.id = NEW.latest_successful_turn_id AND t.session_id = s.id
        WHERE s.id = NEW.session_id AND s.lifecycle_status = 'READY'
          AND rc.created_by_turn_id = t.id
          AND rc.content_format_version = 1
          AND json_extract(t.first_response_json, '$.ready_content_id') = rc.id
          AND json(rc.final_content_json) = json(json_extract(t.post_state_snapshot_json, '$.state_json.draft.content'))
    ) THEN RAISE(ABORT, 'READY Working State recovery requires closed ReadyContent') END;
END;
CREATE TRIGGER IF NOT EXISTS director_working_state_delete_guard BEFORE DELETE ON director_working_state BEGIN SELECT RAISE(ABORT, 'Working State cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS director_ready_content_insert_guard
BEFORE INSERT ON director_ready_content
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM director_sessions s
        JOIN director_turns t ON t.id = NEW.created_by_turn_id AND t.session_id = s.id
        WHERE s.id = NEW.session_id AND s.lifecycle_status = 'ACTIVE'
    ) THEN RAISE(ABORT, 'ReadyContent requires an ACTIVE same-Session Turn') END;
END;
CREATE TRIGGER IF NOT EXISTS director_ready_content_finish_session
AFTER INSERT ON director_ready_content
BEGIN
    UPDATE director_sessions SET lifecycle_status = 'READY', ready_at = NEW.created_at WHERE id = NEW.session_id;
END;
CREATE TRIGGER IF NOT EXISTS director_ready_content_update_guard BEFORE UPDATE ON director_ready_content BEGIN SELECT RAISE(ABORT, 'ReadyContent is immutable'); END;
CREATE TRIGGER IF NOT EXISTS director_ready_content_delete_guard BEFORE DELETE ON director_ready_content BEGIN SELECT RAISE(ABORT, 'ReadyContent cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS director_checkpoints_update_guard
BEFORE UPDATE ON director_context_checkpoints
BEGIN
    SELECT CASE WHEN NEW.id <> OLD.id OR NEW.session_id <> OLD.session_id
        OR NEW.covered_through_seq <> OLD.covered_through_seq OR NEW.format_version <> OLD.format_version
        OR NEW.checkpoint_json <> OLD.checkpoint_json OR NEW.integrity_sha256 <> OLD.integrity_sha256
        OR NEW.created_at <> OLD.created_at OR OLD.status <> 'VALID' OR NEW.status <> 'DISCARDED'
        THEN RAISE(ABORT, 'Checkpoint is immutable except VALID to DISCARDED') END;
END;
CREATE TRIGGER IF NOT EXISTS director_checkpoints_delete_guard BEFORE DELETE ON director_context_checkpoints BEGIN SELECT RAISE(ABORT, 'Checkpoints cannot be deleted'); END;
