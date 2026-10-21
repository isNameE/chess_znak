INSERT INTO operators(id,display_name,role,active) VALUES('simulator-service','Сервис учебного обмена','operator',false);
CREATE TABLE service_heartbeats(id text PRIMARY KEY,last_seen timestamptz NOT NULL,alive_until timestamptz NOT NULL,processed bigint NOT NULL DEFAULT 0);
