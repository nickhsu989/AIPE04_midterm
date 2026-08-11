-- setup_finance_app.sql — one-time MySQL setup for the Financial Analytics Platform.
-- Run as a MySQL admin (root):  mysql -u root -p < setup_finance_app.sql
-- Idempotent: safe to re-run.
CREATE DATABASE IF NOT EXISTS finance_app
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'user'@'localhost'
  IDENTIFIED BY 'user';
GRANT ALL PRIVILEGES ON finance_app.* TO 'user'@'localhost';
FLUSH PRIVILEGES;

USE finance_app;

CREATE TABLE IF NOT EXISTS instruments (
  symbol     VARCHAR(16)  PRIMARY KEY,
  name       VARCHAR(255),
  asset_type ENUM('equity','index') NOT NULL DEFAULT 'equity',
  currency   CHAR(3)      DEFAULT 'USD',
  sector     VARCHAR(64),
  last_sync  DATETIME
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS price_history (
  symbol     VARCHAR(16) NOT NULL,
  trade_date DATE        NOT NULL,
  open       DECIMAL(18,6),
  high       DECIMAL(18,6),
  low        DECIMAL(18,6),
  close      DECIMAL(18,6),
  adj_close  DECIMAL(18,6),
  volume     BIGINT,
  PRIMARY KEY (symbol, trade_date),
  INDEX idx_date (trade_date),
  CONSTRAINT fk_px_symbol FOREIGN KEY (symbol)
    REFERENCES instruments (symbol) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ingest_log (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  source       ENUM('api','csv') NOT NULL,
  symbol       VARCHAR(16),
  detail       VARCHAR(500),
  rows_written INT,
  status       ENUM('ok','error') NOT NULL,
  started_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  finished_at  DATETIME
) ENGINE=InnoDB;