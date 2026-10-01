CREATE TABLE IF NOT EXISTS novel(
  id int AUTO_INCREMENT PRIMARY KEY,
  nid bigint NOT NULL,
  uid int NOT NULL,
  userName varchar(100) NOT NULL,
  purl varchar(255) NOT NULL,
  title varchar(500) NOT NULL,
  description text,
  tag varchar(999) NOT NULL,
  createDate varchar(32) NOT NULL,
  updateDate varchar(32) NOT NULL,
  viewCount int NOT NULL,
  bookmarkCount int NOT NULL,
  is_r18 tinyint NOT NULL,
  path varchar(1000) NOT NULL,
  UNIQUE KEY uk_novel_nid (nid)
)ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4;
