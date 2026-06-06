-- QuickPoll schema (MySQL 8).
--
--   Poll   1---N  Option
--   Poll   1---N  Vote
--   Option 1---N  Vote
--
-- Double-voting is prevented by UNIQUE(poll_id, voter_token) on votes, backing
-- the per-poll voter cookie. UUIDs are stored as CHAR(36).
--
-- Tables are created parent-first so inline foreign keys resolve cleanly.

CREATE TABLE IF NOT EXISTS polls (
    id          CHAR(36)     NOT NULL PRIMARY KEY,
    admin_token CHAR(36)     NOT NULL,
    title       VARCHAR(300) NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP    NULL DEFAULT NULL,
    closed      TINYINT      NOT NULL DEFAULT 0,
    INDEX idx_polls_admin_token (admin_token)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS options (
    id       CHAR(36)     NOT NULL PRIMARY KEY,
    poll_id  CHAR(36)     NOT NULL,
    text     VARCHAR(200) NOT NULL,
    position INT          NOT NULL DEFAULT 0,
    INDEX idx_options_poll (poll_id),
    CONSTRAINT fk_options_poll FOREIGN KEY (poll_id)
        REFERENCES polls (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS votes (
    id          CHAR(36)  NOT NULL PRIMARY KEY,
    poll_id     CHAR(36)  NOT NULL,
    option_id   CHAR(36)  NOT NULL,
    voter_token VARCHAR(64) NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_poll_voter (poll_id, voter_token),
    INDEX idx_votes_poll (poll_id),
    INDEX idx_votes_option (option_id),
    CONSTRAINT fk_votes_poll FOREIGN KEY (poll_id)
        REFERENCES polls (id) ON DELETE CASCADE,
    CONSTRAINT fk_votes_option FOREIGN KEY (option_id)
        REFERENCES options (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
