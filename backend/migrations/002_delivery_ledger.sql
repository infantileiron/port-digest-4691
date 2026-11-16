--
-- 追补计划账本：订阅计划快照划界 + 上海日历应发时点账本。
-- 幂等：仅在对象不存在时创建；旧回执结构不变，新增回执状态 empty。
--

CREATE TABLE IF NOT EXISTS public.subscription_snapshots (
    id integer NOT NULL,
    subscription_id integer NOT NULL,
    effective_from character varying(40) NOT NULL,
    effective_to character varying(40),
    station character varying(30) NOT NULL,
    daily_at character varying(5) NOT NULL,
    recipient character varying(80) NOT NULL,
    enabled boolean NOT NULL,
    change_reason character varying(200) DEFAULT ''::character varying
);

CREATE SEQUENCE IF NOT EXISTS public.subscription_snapshots_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.subscription_snapshots_id_seq OWNED BY public.subscription_snapshots.id;
ALTER TABLE ONLY public.subscription_snapshots
    ALTER COLUMN id SET DEFAULT nextval('public.subscription_snapshots_id_seq'::regclass);
ALTER TABLE ONLY public.subscription_snapshots
    ADD CONSTRAINT subscription_snapshots_pkey PRIMARY KEY (id);
DO $$ BEGIN
    ALTER TABLE public.subscription_snapshots
        ADD CONSTRAINT uq_snapshot_sub_effective UNIQUE (subscription_id, effective_from);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE public.subscription_snapshots
        ADD CONSTRAINT fk_snapshot_subscription FOREIGN KEY (subscription_id)
        REFERENCES public.subscriptions(id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS public.delivery_ledger (
    id integer NOT NULL,
    subscription_id integer NOT NULL,
    snapshot_id integer NOT NULL,
    due_at character varying(40) NOT NULL,
    report_day character varying(10) NOT NULL,
    station character varying(30) NOT NULL,
    recipient character varying(80) NOT NULL,
    state character varying(20) NOT NULL DEFAULT 'pending',
    receipt_id integer,
    attempts integer NOT NULL DEFAULT 0,
    last_attempt_at character varying(40),
    basis text DEFAULT ''::text
);

CREATE SEQUENCE IF NOT EXISTS public.delivery_ledger_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.delivery_ledger_id_seq OWNED BY public.delivery_ledger.id;
ALTER TABLE ONLY public.delivery_ledger
    ALTER COLUMN id SET DEFAULT nextval('public.delivery_ledger_id_seq'::regclass);
ALTER TABLE ONLY public.delivery_ledger
    ADD CONSTRAINT delivery_ledger_pkey PRIMARY KEY (id);
DO $$ BEGIN
    ALTER TABLE public.delivery_ledger
        ADD CONSTRAINT uq_ledger_sub_due UNIQUE (subscription_id, due_at);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE public.delivery_ledger
        ADD CONSTRAINT fk_ledger_subscription FOREIGN KEY (subscription_id)
        REFERENCES public.subscriptions(id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE public.delivery_ledger
        ADD CONSTRAINT fk_ledger_snapshot FOREIGN KEY (snapshot_id)
        REFERENCES public.subscription_snapshots(id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE public.delivery_ledger
        ADD CONSTRAINT fk_ledger_receipt FOREIGN KEY (receipt_id)
        REFERENCES public.receipts(id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS ix_ledger_sub_state ON public.delivery_ledger (subscription_id, state);
CREATE INDEX IF NOT EXISTS ix_ledger_due ON public.delivery_ledger (due_at);
