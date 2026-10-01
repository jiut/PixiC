CREATE TABLE IF NOT EXISTS novel_unavailable(
  nid bigint NOT NULL PRIMARY KEY,
  status varchar(32) NOT NULL,
  reason varchar(255) NOT NULL,
  first_seen timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
