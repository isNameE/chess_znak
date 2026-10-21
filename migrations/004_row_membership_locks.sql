-- Per-code advisory locks exhaust max_locks_per_transaction during large imports.
-- Lock the existing code/box row instead: one serialization point across draft and closed membership.
CREATE OR REPLACE FUNCTION check_membership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'box_members' THEN
        PERFORM 1 FROM marking_codes WHERE raw_code=NEW.code FOR UPDATE;
        IF (SELECT kind FROM aggregates WHERE id=NEW.box_id) IS DISTINCT FROM 'box' THEN
            RAISE EXCEPTION 'Child code requires a box';
        END IF;
        IF EXISTS(SELECT 1 FROM draft_codes WHERE code=NEW.code) THEN
            RAISE EXCEPTION 'Code is still in a draft';
        END IF;
    ELSIF TG_TABLE_NAME = 'draft_codes' THEN
        PERFORM 1 FROM marking_codes WHERE raw_code=NEW.code FOR UPDATE;
        IF EXISTS(SELECT 1 FROM box_members WHERE code=NEW.code) THEN
            RAISE EXCEPTION 'Code is already boxed';
        END IF;
    ELSIF TG_TABLE_NAME = 'pallet_members' THEN
        PERFORM 1 FROM aggregates WHERE id=NEW.box_id FOR UPDATE;
        IF (SELECT kind FROM aggregates WHERE id=NEW.pallet_id) IS DISTINCT FROM 'pallet'
           OR (SELECT kind FROM aggregates WHERE id=NEW.box_id) IS DISTINCT FROM 'box' THEN
            RAISE EXCEPTION 'Pallet membership requires a pallet and a box';
        END IF;
        IF EXISTS(SELECT 1 FROM draft_pallet_boxes WHERE box_id=NEW.box_id) THEN
            RAISE EXCEPTION 'Box is still in pallet draft';
        END IF;
    ELSE
        PERFORM 1 FROM aggregates WHERE id=NEW.box_id FOR UPDATE;
        IF (SELECT kind FROM aggregates WHERE id=NEW.box_id) IS DISTINCT FROM 'box' THEN
            RAISE EXCEPTION 'Pallet draft requires a box';
        END IF;
        IF EXISTS(SELECT 1 FROM pallet_members WHERE box_id=NEW.box_id) THEN
            RAISE EXCEPTION 'Box is already palletized';
        END IF;
    END IF;
    RETURN NEW;
END $$;
