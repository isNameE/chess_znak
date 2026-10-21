CREATE TABLE schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE production_lines (id text PRIMARY KEY, name text NOT NULL);
INSERT INTO production_lines VALUES ('LINE-1','Линия 1'), ('LINE-2','Линия 2');
CREATE TABLE products (id text PRIMARY KEY, name text NOT NULL, gtin text UNIQUE, volume_l integer NOT NULL CHECK(volume_l > 0));
INSERT INTO products VALUES ('1L','Масло растительное 1 л',NULL,1),('5L','Масло растительное 5 л',NULL,5);
CREATE TABLE operators (id text PRIMARY KEY, display_name text NOT NULL);
INSERT INTO operators VALUES ('demo-operator','Демо оператор');
CREATE TABLE batches (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, line_id text NOT NULL REFERENCES production_lines,
    number text NOT NULL CHECK(length(number) BETWEEN 1 AND 80), UNIQUE(line_id,number));
CREATE TABLE station (id integer PRIMARY KEY CHECK(id=1), revision bigint NOT NULL DEFAULT 0, sequence bigint NOT NULL DEFAULT 0,
    product_id text NOT NULL REFERENCES products, line_id text NOT NULL REFERENCES production_lines,
    batch_id bigint NOT NULL REFERENCES batches, operator_id text NOT NULL REFERENCES operators,
    pallet_target integer CHECK(pallet_target BETWEEN 1 AND 100), online boolean NOT NULL DEFAULT true);
CREATE TABLE packing_recipes (product_id text PRIMARY KEY REFERENCES products, quantity integer NOT NULL CHECK(quantity BETWEEN 1 AND 100));
INSERT INTO packing_recipes VALUES ('1L',15),('5L',3);
CREATE TABLE station_settings (id integer PRIMARY KEY CHECK(id=1), pallet_size integer NOT NULL CHECK(pallet_size BETWEEN 1 AND 100));
INSERT INTO station_settings VALUES(1,50);
CREATE TABLE marking_codes (raw_code text PRIMARY KEY, product_id text NOT NULL REFERENCES products, gtin text,
    source text NOT NULL CHECK(source IN ('demo','true_api')), external_status text, checked_at timestamptz);
CREATE TABLE aggregates (id text PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('box','pallet')),
    product_id text NOT NULL REFERENCES products, line_id text NOT NULL REFERENCES production_lines,
    batch_id bigint NOT NULL REFERENCES batches, created_at timestamptz NOT NULL, print_opens integer NOT NULL DEFAULT 0 CHECK(print_opens >= 0));
CREATE TABLE box_members (code text PRIMARY KEY REFERENCES marking_codes, box_id text NOT NULL REFERENCES aggregates,
    position integer NOT NULL CHECK(position >= 0), UNIQUE(box_id,position));
CREATE TABLE pallet_members (box_id text PRIMARY KEY REFERENCES aggregates, pallet_id text NOT NULL REFERENCES aggregates,
    position integer NOT NULL CHECK(position >= 0), CHECK(box_id <> pallet_id), UNIQUE(pallet_id,position));
CREATE TABLE draft_codes (code text PRIMARY KEY REFERENCES marking_codes, position integer NOT NULL UNIQUE CHECK(position >= 0));
CREATE TABLE draft_pallet_boxes (box_id text PRIMARY KEY REFERENCES aggregates, position integer NOT NULL UNIQUE CHECK(position >= 0));
CREATE TABLE exchange_documents (id text PRIMARY KEY, aggregate_id text NOT NULL UNIQUE REFERENCES aggregates,
    kind text NOT NULL CHECK(kind IN ('box','pallet')), status text NOT NULL CHECK(status IN ('queued','accepted','rejected','unknown')),
    attempts integer NOT NULL CHECK(attempts >= 0), created_at timestamptz NOT NULL, message text NOT NULL,
    provider text NOT NULL DEFAULT 'simulator' CHECK(provider IN ('simulator','sandbox')), external_id text);
CREATE TABLE exchange_attempts (document_id text NOT NULL REFERENCES exchange_documents, number integer NOT NULL CHECK(number > 0),
    outcome text NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(document_id,number));
CREATE TABLE print_jobs (id uuid PRIMARY KEY, aggregate_id text NOT NULL REFERENCES aggregates,
    status text NOT NULL CHECK(status IN ('ready','preview_opened','failed')), created_at timestamptz NOT NULL);
CREATE TABLE audit_events (id uuid PRIMARY KEY, created_at timestamptz NOT NULL, level text NOT NULL,
    actor text NOT NULL REFERENCES operators, message text NOT NULL);
CREATE TABLE command_receipts (id uuid PRIMARY KEY, fingerprint text NOT NULL, error text, created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX box_members_box_idx ON box_members(box_id);
CREATE INDEX pallet_members_pallet_idx ON pallet_members(pallet_id);
CREATE INDEX aggregates_created_idx ON aggregates(created_at DESC);
CREATE INDEX documents_status_idx ON exchange_documents(status);
CREATE INDEX audit_created_idx ON audit_events(created_at DESC);

-- Membership invariants apply even to SQL clients outside the application.
CREATE FUNCTION check_membership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'box_members' THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(NEW.code, 0));
        IF (SELECT kind FROM aggregates WHERE id=NEW.box_id) IS DISTINCT FROM 'box' THEN
            RAISE EXCEPTION 'Child code requires a box';
        END IF;
        IF EXISTS(SELECT 1 FROM draft_codes WHERE code=NEW.code) THEN
            RAISE EXCEPTION 'Code is still in a draft';
        END IF;
    ELSIF TG_TABLE_NAME = 'draft_codes' THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(NEW.code, 0));
        IF EXISTS(SELECT 1 FROM box_members WHERE code=NEW.code) THEN
            RAISE EXCEPTION 'Code is already boxed';
        END IF;
    ELSIF TG_TABLE_NAME = 'pallet_members' THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(NEW.box_id, 1));
        IF (SELECT kind FROM aggregates WHERE id=NEW.pallet_id) IS DISTINCT FROM 'pallet'
           OR (SELECT kind FROM aggregates WHERE id=NEW.box_id) IS DISTINCT FROM 'box' THEN
            RAISE EXCEPTION 'Pallet membership requires a pallet and a box';
        END IF;
        IF EXISTS(SELECT 1 FROM draft_pallet_boxes WHERE box_id=NEW.box_id) THEN
            RAISE EXCEPTION 'Box is still in pallet draft';
        END IF;
    ELSE
        PERFORM pg_advisory_xact_lock(hashtextextended(NEW.box_id, 1));
        IF (SELECT kind FROM aggregates WHERE id=NEW.box_id) IS DISTINCT FROM 'box' THEN
            RAISE EXCEPTION 'Pallet draft requires a box';
        END IF;
        IF EXISTS(SELECT 1 FROM pallet_members WHERE box_id=NEW.box_id) THEN
            RAISE EXCEPTION 'Box is already palletized';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER box_members_check BEFORE INSERT OR UPDATE ON box_members FOR EACH ROW EXECUTE FUNCTION check_membership();
CREATE TRIGGER draft_codes_check BEFORE INSERT OR UPDATE ON draft_codes FOR EACH ROW EXECUTE FUNCTION check_membership();
CREATE TRIGGER pallet_members_check BEFORE INSERT OR UPDATE ON pallet_members FOR EACH ROW EXECUTE FUNCTION check_membership();
CREATE TRIGGER draft_pallet_boxes_check BEFORE INSERT OR UPDATE ON draft_pallet_boxes FOR EACH ROW EXECUTE FUNCTION check_membership();
