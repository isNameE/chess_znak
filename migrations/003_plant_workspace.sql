ALTER TABLE operators ADD COLUMN role text NOT NULL DEFAULT 'operator' CHECK(role IN ('operator','supervisor','admin'));
ALTER TABLE operators ADD COLUMN password_hash text;
ALTER TABLE operators ADD COLUMN active boolean NOT NULL DEFAULT true;
UPDATE operators SET role='admin' WHERE id='demo-operator';
CREATE TABLE user_sessions (
    token_hash text PRIMARY KEY, operator_id text NOT NULL REFERENCES operators,
    expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE work_shifts (
    id text PRIMARY KEY, name text NOT NULL, state text NOT NULL CHECK(state IN ('open','closed')),
    opened_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz,
    opened_by text NOT NULL REFERENCES operators, closed_by text REFERENCES operators
);
CREATE UNIQUE INDEX one_open_shift ON work_shifts(state) WHERE state='open';
CREATE TABLE production_orders (
    id text PRIMARY KEY, line_id text NOT NULL REFERENCES production_lines, batch_id bigint NOT NULL REFERENCES batches,
    product_id text NOT NULL REFERENCES products, target_boxes integer NOT NULL CHECK(target_boxes BETWEEN 1 AND 100000),
    recipe_quantity integer NOT NULL CHECK(recipe_quantity BETWEEN 1 AND 100),
    pallet_quantity integer NOT NULL CHECK(pallet_quantity BETWEEN 1 AND 100),
    state text NOT NULL DEFAULT 'planned' CHECK(state IN ('planned','running','paused','completed','cancelled')),
    good_boxes integer NOT NULL DEFAULT 0 CHECK(good_boxes>=0), pallets integer NOT NULL DEFAULT 0 CHECK(pallets>=0),
    created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, completed_at timestamptz,
    created_by text NOT NULL REFERENCES operators, note text NOT NULL DEFAULT ''
);
ALTER TABLE station ADD COLUMN order_id text REFERENCES production_orders;
ALTER TABLE station ADD COLUMN shift_id text REFERENCES work_shifts;
ALTER TABLE aggregates ADD COLUMN order_id text REFERENCES production_orders;
ALTER TABLE aggregates ADD COLUMN shift_id text REFERENCES work_shifts;
ALTER TABLE aggregates ADD COLUMN lifecycle text NOT NULL DEFAULT 'closed' CHECK(lifecycle IN ('closed','quarantined','disassembled'));
ALTER TABLE aggregates ADD COLUMN unit_count integer NOT NULL DEFAULT 0 CHECK(unit_count>=0);
UPDATE aggregates a SET unit_count=(SELECT count(*) FROM box_members b WHERE b.box_id=a.id) WHERE kind='box';
UPDATE aggregates a SET unit_count=(SELECT count(*) FROM pallet_members p WHERE p.pallet_id=a.id) WHERE kind='pallet';
ALTER TABLE marking_codes ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE exchange_documents DROP CONSTRAINT exchange_documents_status_check;
ALTER TABLE exchange_documents ADD CONSTRAINT exchange_documents_status_check CHECK(status IN ('queued','accepted','rejected','unknown','cancelled'));
ALTER TABLE print_jobs DROP CONSTRAINT print_jobs_status_check;
ALTER TABLE print_jobs ADD CONSTRAINT print_jobs_status_check CHECK(status IN ('ready','preview_opened','failed','verified','cancelled'));
CREATE TABLE print_job_events (
    id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES print_jobs,
    state text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), actor text NOT NULL REFERENCES operators,
    message text NOT NULL
);
CREATE TABLE incidents (
    id uuid PRIMARY KEY, severity text NOT NULL CHECK(severity IN ('warning','error')),
    category text NOT NULL, reference text, message text NOT NULL,
    state text NOT NULL DEFAULT 'open' CHECK(state IN ('open','acknowledged','resolved')),
    order_id text REFERENCES production_orders, shift_id text REFERENCES work_shifts,
    created_at timestamptz NOT NULL DEFAULT now(), created_by text NOT NULL REFERENCES operators,
    assigned_to text REFERENCES operators, resolved_at timestamptz, resolution text
);
CREATE TABLE membership_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, box_id text NOT NULL REFERENCES aggregates,
    code text NOT NULL REFERENCES marking_codes, position integer NOT NULL,
    removed_at timestamptz NOT NULL DEFAULT now(), actor text NOT NULL REFERENCES operators, reason text NOT NULL
);
CREATE TABLE camera_frames (
    id uuid PRIMARY KEY, order_id text NOT NULL REFERENCES production_orders,
    payload_hash text NOT NULL, read_count integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE plant_command_receipts (
    id uuid PRIMARY KEY, fingerprint text NOT NULL, actor text NOT NULL REFERENCES operators,
    result jsonb NOT NULL, error text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX aggregates_day_kind ON aggregates(created_at,kind) INCLUDE(unit_count,lifecycle,line_id);
CREATE INDEX aggregates_order ON aggregates(order_id,created_at DESC,id DESC);
CREATE INDEX aggregates_page ON aggregates(created_at DESC,id DESC);
CREATE INDEX aggregates_prefix ON aggregates(id text_pattern_ops);
CREATE INDEX audit_page ON audit_events(created_at DESC,id DESC);
CREATE INDEX incidents_page ON incidents(created_at DESC,id DESC);
CREATE INDEX incidents_open ON incidents(state) WHERE state<>'resolved';
CREATE INDEX print_jobs_pending ON print_jobs(created_at,id) WHERE status IN ('ready','failed','preview_opened');
CREATE INDEX documents_pending ON exchange_documents(created_at,id) WHERE status IN ('queued','unknown','rejected');
CREATE INDEX production_orders_state ON production_orders(state,created_at DESC,id DESC);
CREATE UNIQUE INDEX one_running_order ON production_orders(state) WHERE state='running';
CREATE INDEX aggregates_shift ON aggregates(shift_id,kind) INCLUDE(unit_count,lifecycle);
CREATE INDEX shifts_page ON work_shifts(opened_at DESC,id DESC);
CREATE INDEX marking_codes_created ON marking_codes(created_at);
CREATE INDEX sessions_expiry ON user_sessions(expires_at);
CREATE INDEX print_jobs_aggregate ON print_jobs(aggregate_id,created_at DESC,id DESC);
CREATE INDEX membership_history_code ON membership_history(code);
CREATE TABLE capacity_settings(id integer PRIMARY KEY CHECK(id=1),shift_hours integer NOT NULL CHECK(shift_hours BETWEEN 1 AND 24),target_pallets integer NOT NULL CHECK(target_pallets BETWEEN 1 AND 100));
INSERT INTO capacity_settings VALUES(1,8,30);
