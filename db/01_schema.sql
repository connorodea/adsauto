-- Схема adsauto. Применяется автоматически при первом старте контейнера db.
--
-- Главное правило схемы: ключевое слово невозможно записать без ссылки на
-- сохранённый сырой ответ API. Это делает выдуманные ключи не
-- маловероятными, а невозможными.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- --- клиенты и подключения -------------------------------------------

CREATE TABLE clients (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          text NOT NULL UNIQUE,          -- 'estaca', он же префикс g:id
    name          text NOT NULL,
    site_url      text NOT NULL,
    platform      text NOT NULL DEFAULT 'woocommerce',
    country       text NOT NULL DEFAULT 'UA',
    language      text NOT NULL DEFAULT 'uk',
    currency      text NOT NULL DEFAULT 'UAH',
    stop_words    text[] NOT NULL DEFAULT '{}',
    monthly_budget numeric(12,2),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    kind          text NOT NULL CHECK (kind IN
                    ('google_ads','merchant_center','ga4','search_console')),
    external_id   text NOT NULL,
    credentials   jsonb,
    linked_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, kind, external_id)
);

-- --- сырые ответы: фундамент достоверности ---------------------------

CREATE TABLE api_responses (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     uuid REFERENCES clients(id) ON DELETE CASCADE,
    source        text NOT NULL,
    endpoint      text NOT NULL,
    request       jsonb,
    payload       jsonb NOT NULL,
    fetched_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON api_responses (client_id, source, fetched_at DESC);

-- --- товары ----------------------------------------------------------

CREATE TABLE products (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    offer_id      text NOT NULL,
    item_group_id text,
    source_id     bigint,
    sku           text,
    title         text NOT NULL,
    description   text,
    link          text,
    image_link    text,
    price         numeric(12,2),
    sale_price    numeric(12,2),
    currency      text NOT NULL DEFAULT 'UAH',
    availability  text NOT NULL DEFAULT 'in_stock',
    brand         text,
    gtin          text,
    mpn           text,
    identifier_exists boolean NOT NULL DEFAULT true,
    google_product_category text,
    product_type  text,
    product_details jsonb NOT NULL DEFAULT '[]',
    -- статус со стороны Merchant Center, заполняется модулем Feed Doctor
    mc_status     text,
    mc_issues     jsonb,
    synced_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, offer_id)
);

CREATE INDEX ON products (client_id, item_group_id);
CREATE INDEX ON products (client_id, mc_status);

-- Проблемы фида, найденные нашим валидатором (до отправки в Google).
CREATE TABLE feed_issues (
    id            bigserial PRIMARY KEY,
    client_id     uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    run_id        uuid NOT NULL,
    offer_id      text NOT NULL,
    code          text NOT NULL,
    severity      text NOT NULL CHECK (severity IN ('error','warning','info')),
    message       text NOT NULL,
    fix           text,
    detected_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON feed_issues (client_id, run_id);
CREATE INDEX ON feed_issues (client_id, code, detected_at DESC);

-- --- семантическое ядро ----------------------------------------------

CREATE TABLE keyword_clusters (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name          text NOT NULL,
    intent        text CHECK (intent IN
                    ('commercial','informational','brand','irrelevant')),
    approved_at   timestamptz,
    approved_by   text
);

CREATE TABLE keywords (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    keyword       text NOT NULL,

    -- Провенанс. Без источника и без ссылки на сырой ответ строки не будет.
    source        text NOT NULL CHECK (source IN
                    ('search_terms','gsc','keyword_planner','feed','semrush')),
    source_class  text NOT NULL CHECK (source_class IN ('observed','estimated')),
    raw_response_id uuid NOT NULL REFERENCES api_responses(id),

    volume        integer,
    competition   numeric(4,3),
    cpc_low       numeric(10,2),
    cpc_high      numeric(10,2),

    cluster_id    uuid REFERENCES keyword_clusters(id) ON DELETE SET NULL,
    intent        text,
    intent_agreement boolean,          -- совпали ли вердикты двух моделей
    matched_products integer NOT NULL DEFAULT 0,  -- гейт привязки к товару

    status        text NOT NULL DEFAULT 'candidate' CHECK (status IN
                    ('candidate','approved','quarantine','negative','rejected')),
    collected_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, keyword, source)
);

CREATE INDEX ON keywords (client_id, status);
CREATE INDEX ON keywords (cluster_id);

-- --- генерация и проверка --------------------------------------------

CREATE TABLE campaign_drafts (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    cluster_id    uuid REFERENCES keyword_clusters(id) ON DELETE CASCADE,
    payload       jsonb NOT NULL,     -- структура кампании, заголовки, описания
    status        text NOT NULL DEFAULT 'draft' CHECK (status IN
                    ('draft','qa_failed','qa_passed','published','live','archived')),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE qa_reviews (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    draft_id      uuid NOT NULL REFERENCES campaign_drafts(id) ON DELETE CASCADE,
    verdict       text NOT NULL CHECK (verdict IN ('pass','fail')),
    model         text NOT NULL,
    stage         text NOT NULL CHECK (stage IN ('deterministic','llm')),
    -- каждый чек обязан нести evidence: цитату ключа, товара или правила
    checks        jsonb NOT NULL,
    reviewed_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON qa_reviews (draft_id, reviewed_at DESC);

-- --- метрики и аудит --------------------------------------------------

CREATE TABLE metrics_daily (
    client_id     uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    day           date NOT NULL,
    entity_type   text NOT NULL,      -- campaign | ad_group | keyword | product
    entity_id     text NOT NULL,
    impressions   bigint NOT NULL DEFAULT 0,
    clicks        bigint NOT NULL DEFAULT 0,
    cost          numeric(12,2) NOT NULL DEFAULT 0,
    conversions   numeric(10,2) NOT NULL DEFAULT 0,
    conv_value    numeric(12,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (client_id, day, entity_type, entity_id)
);

CREATE TABLE actions_log (
    id            bigserial PRIMARY KEY,
    client_id     uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    actor         text NOT NULL,      -- 'system' | 'ai' | email человека
    action        text NOT NULL,
    target        text,
    before        jsonb,
    after         jsonb,
    reason        text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON actions_log (client_id, created_at DESC);
