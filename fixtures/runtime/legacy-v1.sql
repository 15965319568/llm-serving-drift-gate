
CREATE TABLE IF NOT EXISTS requests (
 request_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, prompt TEXT NOT NULL, max_tokens INTEGER NOT NULL,
 deadline_ms INTEGER, model_version TEXT NOT NULL, status TEXT NOT NULL,
 created_ms INTEGER NOT NULL, finished_ms INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
 output_tokens INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
 UNIQUE(tenant,key)
);

CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT INTO meta VALUES('clock','10000');
CREATE TABLE events(request_id TEXT,seq INTEGER,kind TEXT,data TEXT,PRIMARY KEY(request_id,seq));
CREATE TABLE invoices(request_id TEXT PRIMARY KEY,input_tokens INTEGER,output_tokens INTEGER);
INSERT INTO requests VALUES('legacy-001','enterprise','legacy','31d2ed60af485fec41a7d56acd2c7083db77ce6e52e2d427b6dcf4948908a387','archive',16,NULL,'orion-3.0','SUCCEEDED',9000,9020,1,1,4);
INSERT INTO events VALUES('legacy-001',1,'delta','{"text":"archived","token_count":1}');
INSERT INTO events VALUES('legacy-001',2,'terminal','{"status":"SUCCEEDED","error":null}');
INSERT INTO invoices VALUES('legacy-001',4,1);
PRAGMA user_version=1;
