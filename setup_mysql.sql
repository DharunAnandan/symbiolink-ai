-- SymbioLink AI -- one-time MySQL setup.
-- Open this file in MySQL Workbench (File -> Open SQL Script...) while
-- connected to your local MySQL server, then run it (the lightning-bolt
-- icon, or Ctrl+Shift+Enter to execute the whole script).
--
-- This only creates the two empty databases. Flask-SQLAlchemy creates the
-- actual tables inside them the first time database_migration.py or app.py
-- runs against them -- see README.md's "Database setup (MySQL)" section.

CREATE DATABASE IF NOT EXISTS symbiolink_dev;
CREATE DATABASE IF NOT EXISTS symbiolink;

-- Sanity check -- after running, this should list both databases below.
SHOW DATABASES LIKE 'symbiolink%';
