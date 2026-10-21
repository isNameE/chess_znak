CREATE TABLE sandbox_submissions (
    id uuid PRIMARY KEY,
    payload_sha256 text NOT NULL UNIQUE CHECK(length(payload_sha256)=64),
    raw_payload bytea NOT NULL,
    document_type text NOT NULL,
    product_group text NOT NULL,
    state text NOT NULL CHECK(state IN ('unknown','submitted','response_received')),
    external_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    message text
);
