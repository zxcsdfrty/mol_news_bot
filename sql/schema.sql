-- =====================================================================
-- 勞動部新聞收錄系統 資料庫結構（Supabase / PostgreSQL）
-- 於 Supabase Dashboard → SQL Editor 貼上整份執行即可
-- =====================================================================

-- 中文沒有空白斷詞，PostgreSQL 內建全文檢索不適用；
-- 改用 pg_trgm 三字元索引，讓 ILIKE '%關鍵字%' 在大量資料下仍然快速。
create extension if not exists pg_trgm;

create table if not exists public.news (
    id               bigint generated always as identity primary key,
    url_hash         text        not null unique,
    url              text        not null,
    title            text        not null,
    summary          text        default '',
    source           text        default '',
    published_at     timestamptz not null,
    score            int         default 0,
    topics           text[]      default '{}',
    matched_keywords text[]      default '{}',
    sentiment        text        default '中性',
    title_key        text        default '',
    pushed_at        timestamptz,              -- null = 尚未推播
    dup_of           bigint references public.news(id) on delete set null,
    created_at       timestamptz default now(),
    -- 搜尋用合併欄位
    search_text      text generated always as (title || ' ' || coalesce(summary, '')) stored
);

create index if not exists news_published_idx on public.news (published_at desc);
create index if not exists news_pending_idx   on public.news (pushed_at) where pushed_at is null;
create index if not exists news_topics_idx    on public.news using gin (topics);
create index if not exists news_search_trgm   on public.news using gin (search_text gin_trgm_ops);

create table if not exists public.run_logs (
    id         bigint generated always as identity primary key,
    fetched    int, relevant int, "unique" int, inserted int,
    pushed     int, dups int, seconds numeric,
    created_at timestamptz default now()
);

-- ---------------------------------------------------------------------
-- 查詢函式：網頁呼叫 supabase.rpc('search_news', {...})
--   q        多個關鍵字以空白分隔，全部都要出現（AND）
--   p_topics 議題陣列，任一符合即可（OR）
-- ---------------------------------------------------------------------
create or replace function public.search_news(
    q            text        default '',
    p_topics     text[]      default null,
    p_sentiment  text        default null,
    p_source     text        default null,
    date_from    timestamptz default null,
    date_to      timestamptz default null,
    include_dups boolean     default false,
    page_size    int         default 20,
    page_no      int         default 1
)
returns table (
    id bigint, title text, summary text, url text, source text,
    published_at timestamptz, score int, topics text[],
    matched_keywords text[], sentiment text, dup_of bigint, total_count bigint
)
language sql stable
as $$
    with terms as (
        select t from unnest(regexp_split_to_array(trim(coalesce(q, '')), '\s+')) t
        where t <> ''
    )
    select n.id, n.title, n.summary, n.url, n.source, n.published_at, n.score,
           n.topics, n.matched_keywords, n.sentiment, n.dup_of,
           count(*) over () as total_count
    from public.news n
    where not exists (
              select 1 from terms where n.search_text not ilike '%' || terms.t || '%')
      and (p_topics is null or cardinality(p_topics) = 0 or n.topics && p_topics)
      and (p_sentiment is null or p_sentiment = '' or n.sentiment = p_sentiment)
      and (p_source is null or p_source = '' or n.source = p_source)
      and (date_from is null or n.published_at >= date_from)
      and (date_to   is null or n.published_at <  date_to)
      and (include_dups or n.dup_of is null)
    order by n.published_at desc
    limit least(page_size, 100) offset greatest(page_no - 1, 0) * least(page_size, 100);
$$;

-- 議題統計（網頁上方的議題分佈圖）
create or replace function public.topic_stats(
    date_from timestamptz default now() - interval '30 days',
    date_to   timestamptz default now()
)
returns table (topic text, cnt bigint, negative bigint)
language sql stable
as $$
    select t, count(*), count(*) filter (where n.sentiment = '負面')
    from public.news n, unnest(n.topics) t
    where n.published_at >= date_from and n.published_at < date_to and n.dup_of is null
    group by t order by 2 desc;
$$;

-- 來源清單（下拉選單用）
create or replace function public.source_list()
returns table (source text, cnt bigint)
language sql stable
as $$
    select n.source, count(*) from public.news n group by n.source order by 2 desc;
$$;

-- ---------------------------------------------------------------------
-- 權限（Row Level Security）
-- 排程程式使用 service_role key，不受 RLS 限制。
-- 網頁使用 anon / authenticated key，只能讀。
-- ---------------------------------------------------------------------
alter table public.news     enable row level security;
alter table public.run_logs enable row level security;

-- 【方案 A：網頁公開唯讀】新聞本身為公開資訊，最簡單
drop policy if exists news_read on public.news;
create policy news_read on public.news for select to anon, authenticated using (true);

-- 【方案 B：限登入者】若要限制只有新聞聯絡室同仁可查，改用下面這段，
--  並在 Supabase Auth 開啟 Email（Magic Link），網頁 index.html 設定 REQUIRE_LOGIN = true。
-- drop policy if exists news_read on public.news;
-- create policy news_read on public.news for select to authenticated
--   using ( (auth.jwt() ->> 'email') like '%@mol.gov.tw' );

grant select on public.news to anon, authenticated;
revoke all on public.run_logs from anon, authenticated;
grant execute on function public.search_news, public.topic_stats, public.source_list
  to anon, authenticated;
